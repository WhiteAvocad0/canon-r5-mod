"""Read, back up, write and verify Canon boot markers, and restore from a backup.

The card steps take an ``ops`` object (devices.MacOps on macOS, an image-file fake
in the tests) with refresh, unmount, mount, verify, raw_read and raw_write. Every
step re-checks the card's identity first, unmounts only its partition (never
ejects), and mounts it again before returning, also after a failure or a
cancelled password prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import stat

from . import layout
from .devices import Cancelled, Candidate, DeviceError
from .layout import AREA_BYTES, MBR_BYTES, CardPrepError, Plan, sha256

BACKUP_FORMAT = "r5-cardprep-backup"
BACKUP_VERSION = 1
DEFAULT_BACKUP_ROOT = Path.home() / "R5-card-backups"
MANIFEST = "manifest.json"
MBR_FILE = "mbr.bin"
AREA_FILE = "boot-area.bin"
MAX_MANIFEST_BYTES = 1024 * 1024


class PrepFailure(Exception):
    """A card step did not complete; the message says what state the card is in."""


@dataclass(frozen=True)
class Snapshot:
    candidate: Candidate
    mbr: bytes
    area: bytes
    volume: layout.VolumeFacts
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Backup:
    folder: Path
    mbr: bytes
    area: bytes
    manifest: dict


@dataclass
class Report:
    ok: bool
    title: str
    lines: list[str] = field(default_factory=list)
    written_sectors: list[int] = field(default_factory=list)
    record: Path | None = None


def _identity_matches(cand: Candidate, volume: layout.VolumeFacts) -> None:
    if volume.filesystem != cand.filesystem:
        raise CardPrepError("geometry_mismatch",
                            f"macOS reports {cand.filesystem}, but the boot sector is {volume.filesystem}.")
    if (volume.mbr.start_bytes, volume.mbr.size_bytes) != (cand.partition_offset, cand.partition_bytes):
        raise CardPrepError("geometry_mismatch",
                            "The MBR partition does not match the partition macOS reports "
                            f"(MBR {volume.mbr.start_bytes:,}+{volume.mbr.size_bytes:,}, "
                            f"macOS {cand.partition_offset}+{cand.partition_bytes}).")


def read_card(cand: Candidate, ops) -> Snapshot:
    """Unmount, read the MBR and the 24-sector boot area as root, mount again, then validate."""
    cand = ops.refresh(cand)
    ops.unmount(cand)
    try:
        mbr, area = ops.raw_read(cand, AREA_BYTES)
    except Exception as exc:
        mounted, out = ops.mount(cand)
        if not mounted:
            raise PrepFailure(f"{exc} The card also could not be mounted again ({out}); remove and reinsert it.") from exc
        raise
    mounted, out = ops.mount(cand)
    warnings = () if mounted else (f"The card could not be mounted again ({out}). Remove and reinsert it.",)
    if len(mbr) != MBR_BYTES or len(area) != AREA_BYTES:
        raise PrepFailure("The helper returned the wrong number of bytes; nothing was written.")
    volume = layout.inspect(mbr, area, disk_bytes=cand.disk_bytes)
    _identity_matches(cand, volume)
    return Snapshot(cand, mbr, area, volume, warnings)


# -- backups ------------------------------------------------------------------------


def _write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _read_regular(path: Path, limit: int) -> bytes:
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
        raise PrepFailure(f"{path} is not a backup file of the expected size.")
    with open(path, "rb") as f:
        return f.read(limit + 1)


def save_backup(snapshot: Snapshot, plan: Plan, root: Path = DEFAULT_BACKUP_ROOT, *,
                purpose: str = "before-marking", now: datetime | None = None) -> Backup:
    """Save the MBR and boot area to a new timestamped folder, then verify it by re-reading."""
    now = datetime.now().astimezone() if now is None else now
    v = snapshot.volume
    folder = Path(root) / f"{now.strftime('%Y%m%d-%H%M%S')}-{v.filesystem}-{v.serial_hex}-{purpose}"
    folder.mkdir(parents=True, exist_ok=False)
    _write_new(folder / MBR_FILE, snapshot.mbr)
    _write_new(folder / AREA_FILE, snapshot.area)
    manifest = {
        "format": BACKUP_FORMAT, "version": BACKUP_VERSION, "purpose": purpose, "created_at": now.isoformat(),
        "card": snapshot.candidate.identity(), "volume": v.identity(),
        "markers_present": v.markers_present,
        "mbr_file": MBR_FILE, "mbr_bytes": MBR_BYTES, "mbr_sha256": sha256(snapshot.mbr),
        "area_file": AREA_FILE, "area_bytes": AREA_BYTES, "area_sha256": sha256(snapshot.area),
        "area_note": "The first 24 sectors of the card's partition (both exFAT boot regions, or the FAT32 "
                     "boot sector, FSInfo and backup boot sector).",
        "plan": plan.to_dict(),
    }
    _write_new(folder / MANIFEST, (json.dumps(manifest, indent=2) + "\n").encode())
    backup = load_backup(folder)
    if backup.mbr != snapshot.mbr or backup.area != snapshot.area:
        raise PrepFailure(f"The backup in {folder} does not read back correctly; the card was not changed.")
    return backup


def load_backup(folder: Path) -> Backup:
    """A backup folder made by save_backup, with every file checked against its manifest."""
    folder = Path(folder)
    try:
        manifest = json.loads(_read_regular(folder / MANIFEST, MAX_MANIFEST_BYTES))
    except (OSError, ValueError) as exc:
        raise PrepFailure(f"{folder} is not a card backup folder ({exc}).") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT or manifest.get("version") != BACKUP_VERSION:
        raise PrepFailure(f"{folder} is not a card backup made by this assistant.")
    files = {}
    for key, name, size in (("mbr", MBR_FILE, MBR_BYTES), ("area", AREA_FILE, AREA_BYTES)):
        try:
            data = _read_regular(folder / name, size)
        except OSError as exc:
            raise PrepFailure(f"The backup file {name} cannot be read: {exc}") from exc
        if len(data) != size or sha256(data) != manifest.get(f"{key}_sha256"):
            raise PrepFailure(f"The backup file {name} in {folder} is damaged (size or SHA-256 differs).")
        files[key] = data
    return Backup(folder, files["mbr"], files["area"], manifest)


def _record(backup: Backup, report: Report, extra: dict) -> Path | None:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    path = backup.folder / f"result-{stamp}.json"
    try:
        _write_new(path, (json.dumps({"ok": report.ok, "title": report.title, "lines": report.lines,
                                      "written_sectors": report.written_sectors, **extra}, indent=2) + "\n").encode())
    except OSError:
        return None
    return path


# -- write and verify -----------------------------------------------------------------


def apply_plan(snapshot: Snapshot, plan: Plan, backup: Backup, ops) -> Report:
    """Write exactly the planned sectors, read everything back, verify the volume, mount again.

    Refuses unless the verified backup holds exactly the bytes the plan was made from."""
    if backup.mbr != snapshot.mbr or backup.area != snapshot.area or plan.before_area != snapshot.area:
        raise PrepFailure("The backup and plan are not from this read of the card; read the card again.")
    what = "Boot markers" if plan.kind == "mark" else "Restore"
    if not plan.writes_needed:
        report = Report(True, f"{what}: nothing to write.", layout.describe_plan(plan))
        report.record = _record(backup, report, {"plan": plan.to_dict()})
        return report

    cand = ops.refresh(snapshot.candidate)
    ops.unmount(cand)
    report = Report(False, f"{what}: FAILED.")
    result: dict = {}
    try:
        try:
            result = ops.raw_write(cand, [(w.offset, len(w.after)) for w in plan.writes],
                                   sha256(plan.expected_before), plan.after_bytes)
        except Cancelled:
            report.title = f"{what}: cancelled. Nothing was written."
            report.lines.append("The password prompt was cancelled.")
            return report
        except DeviceError as exc:
            report.lines += [f"The helper did not report a result: {exc}",
                             "The card's state is unknown. Read the card again before using it; "
                             f"if it differs from the plan, restore it from {backup.folder}."]
            return report
        written = [o // layout.SECTOR for o in result.get("written") or []]
        report.written_sectors = written
        if result.get("code") == "before_mismatch":
            report.title = f"{what}: refused. Nothing was written."
            report.lines.append("The card changed since it was read (for example a file was copied to it). "
                                "Read the card again.")
            return report
        if not result.get("ok") and "area_hex" not in result:
            report.lines += [f"The helper stopped: {result.get('error')}",
                             f"Sectors written before it stopped: {written or 'none'}.",
                             f"Do not use the card yet. Restore it from {backup.folder}."]
            return report
        problems = layout.verify_written(plan, snapshot.mbr, bytes.fromhex(result.get("mbr_hex", "")),
                                         bytes.fromhex(result.get("area_hex", "")),
                                         disk_bytes=snapshot.candidate.disk_bytes)
        if not result.get("ok"):
            problems.insert(0, f"The helper reported {result.get('code') or 'a failure'}.")
        verified, output = ops.verify(cand)
        last = output.strip().splitlines()[-1] if output.strip() else "(no output)"
        report.lines.append(f"Sectors written: {', '.join(map(str, written)) or 'none'} "
                            f"({len(written) * layout.SECTOR:,} bytes).")
        if problems:
            report.lines += ["Read-back check FAILED:"] + [f"- {p}" for p in problems]
            report.lines.append(f"Do not use the card yet. Restore it from {backup.folder}.")
        else:
            report.lines.append("Read-back check passed: every written byte matches the plan, the MBR is unchanged "
                                "and the boot area validates.")
        report.lines.append(("Volume check passed: " if verified else "Volume check FAILED: ") + last)
        report.ok = not problems and verified
        if report.ok:
            report.title = f"{what}: done and verified."
        elif not problems:
            report.lines.append("The written bytes are correct but the filesystem check did not pass. Check the card "
                                f"with Disk Utility First Aid, or restore it from {backup.folder}.")
        return report
    finally:
        mounted, out = ops.mount(cand)
        report.lines.append("The card is mounted again." if mounted else
                            f"The card could not be mounted again ({out}). Remove and reinsert it.")
        report.record = _record(backup, report, {"plan": plan.to_dict(), "helper": {
            k: v for k, v in result.items() if k not in ("area_hex", "mbr_hex")}})


def restore_plan(snapshot: Snapshot, backup: Backup) -> Plan:
    """The writes that put a backup's marker bytes back on the same card."""
    card = backup.manifest.get("card") or {}
    now = snapshot.candidate.identity()
    for key in ("disk_bytes", "partition_offset", "partition_bytes", "filesystem", "model", "location"):
        if card.get(key) != now.get(key):
            raise CardPrepError("restore_identity",
                                f"This backup belongs to another card or slot ({key}: backup {card.get(key)!r}, "
                                f"card {now.get(key)!r}).")
    return layout.plan_restore(backup.mbr, backup.area, snapshot.mbr, snapshot.area,
                               disk_bytes=snapshot.candidate.disk_bytes)
