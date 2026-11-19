"""Prepare a card's Canon boot markers, or restore them from a backup, in one session.

``start`` re-checks the chosen card, unmounts its partition if it is mounted (never
ejects), reads the MBR and the 24-sector boot area through authopen (one password
window, read-only), checks that the partition start agrees three ways, makes the plan and saves
a backup that is verified by re-reading. When nothing needs writing it mounts the
card again and the session carries the finished report. Otherwise the card stays
unmounted until ``write`` or ``cancel``, so nothing on the Mac can change the boot
sectors between the read and the write.

``write`` re-checks the card again, then (one more password window, read/write)
writes only the planned sectors, and only if the MBR, the whole boot area and the target sectors still hash to the
read's values. It reads everything back, runs the volume check and mounts the card
again. ``write`` and ``cancel`` never raise: every outcome is a Report that names
the backup folder and is saved next to it as result-*.json.

The card is identified by its whole-disk and partition identity, not by a mount,
so an unmounted, half-written or unmountable card can still be read and restored.

The ``ops`` object is devices.MacOps on macOS, or an image-file stand-in in the tests:
refresh, unmount, mount, verify, raw_read and raw_write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import stat

from . import layout
from .devices import Cancelled, Candidate
from .layout import AREA_BYTES, MBR_BYTES, CardPrepError, Plan, sha256

BACKUP_FORMAT = "r5-cardprep-backup"
BACKUP_VERSION = 1
DEFAULT_BACKUP_ROOT = Path.home() / "R5-card-backups"
MANIFEST = "manifest.json"
MBR_FILE = "mbr.bin"
AREA_FILE = "boot-area.bin"
MAX_MANIFEST_BYTES = 1024 * 1024
RESTORE_IDENTITY = ("disk_bytes", "partition_offset", "partition_bytes", "model", "location")


class PrepFailure(Exception):
    """A card step did not complete; the message says what state the card is in."""


@dataclass(frozen=True)
class Snapshot:
    candidate: Candidate
    mbr: bytes
    area: bytes
    volume: layout.VolumeFacts


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
    nothing_written: bool = False


@dataclass
class Session:
    snapshot: Snapshot
    plan: Plan
    backup: Backup
    report: Report | None = None  # set when the session finished without needing a write
    source: Backup | None = None  # the backup being restored (restore sessions only)

    @property
    def kind(self) -> str:
        return self.plan.kind

    @property
    def needs_write(self) -> bool:
        return self.report is None


def _identity_matches(cand: Candidate, volume: layout.VolumeFacts) -> None:
    """The filesystem macOS reports (when it reports one) must be the one in the boot sector.
    (The partition position is checked three ways by layout.check_partition_start.)"""
    if cand.filesystem and volume.filesystem != cand.filesystem:
        raise CardPrepError("geometry_mismatch",
                            f"macOS reports {cand.filesystem}, but the boot sector is {volume.filesystem}.")


def _mount(ops, cand: Candidate) -> tuple[bool, str]:
    try:
        return ops.mount(cand)
    except Exception as exc:  # a mount problem must never hide the outcome of the step
        return False, str(exc)


def _mount_line(mounted: bool, out: str) -> str:
    return "The card is mounted again." if mounted else \
        f"The card could not be mounted again ({out or 'no details'}). Remove and reinsert it."


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


def _new_folder(root: Path, name: str) -> Path:
    """A folder that did not exist before; a numeric suffix avoids any collision."""
    root.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        folder = root / (name if n == 1 else f"{name}-{n}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise PrepFailure(f"Could not create a new backup folder in {root}.")


def save_backup(snapshot: Snapshot, plan: Plan, root: Path = DEFAULT_BACKUP_ROOT, *,
                purpose: str = "before-marking", now: datetime | None = None) -> Backup:
    """Save the MBR and boot area to a new timestamped folder, then verify it by re-reading.

    Folders are never reused or deleted by this tool."""
    now = datetime.now().astimezone() if now is None else now
    v = snapshot.volume
    folder = _new_folder(Path(root), f"{now.strftime('%Y%m%d-%H%M%S-%f')}-{v.filesystem}-{v.serial_hex}-{purpose}")
    _write_new(folder / MBR_FILE, snapshot.mbr)
    _write_new(folder / AREA_FILE, snapshot.area)
    manifest = {
        "format": BACKUP_FORMAT, "version": BACKUP_VERSION, "purpose": purpose, "created_at": now.isoformat(),
        "card": snapshot.candidate.identity(), "volume": v.identity(),
        "markers_present": v.markers_present,
        "script_present": v.script_present,
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


LATEST = "latest"  # restore_from value for Undo: choose the backup after reading the card


def undo_backups(cand: Candidate, root: Path = DEFAULT_BACKUP_ROOT):
    """Valid backups taken before markers or SCRIPT were written to a card in this slot with
    this size and partition, newest first. Only "before-marking" (without markers) or
    "before-script" (without SCRIPT) qualify; "before-restore" backups never do."""
    try:
        folders = sorted((p for p in Path(root).iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    except OSError:
        return
    for folder in folders:
        try:
            backup = load_backup(folder)
        except PrepFailure:
            continue
        card = backup.manifest.get("card") or {}
        purpose = backup.manifest.get("purpose")
        eligible = ((purpose == "before-marking" and backup.manifest.get("markers_present") is False)
                    or (purpose == "before-script" and backup.manifest.get("script_present") is False))
        if eligible and all(card.get(k) == getattr(cand, k) for k in RESTORE_IDENTITY):
            yield backup


def latest_backup(cand: Candidate, root: Path = DEFAULT_BACKUP_ROOT) -> Backup | None:
    """The newest candidate for Undo (the final choice is made after reading the card)."""
    return next(undo_backups(cand, root), None)


def _undo_plan(cand: Candidate, mbr: bytes, area: bytes, root: Path) -> tuple[Plan, Backup]:
    """The newest before-marking backup that is of THIS card (same MBR and boot-area bytes
    outside the markers, so plan_restore accepts it), with its restore plan."""
    reasons = []
    for backup in undo_backups(cand, root):
        try:
            return restore_plan(cand, mbr, area, backup), backup
        except CardPrepError as exc:
            reasons.append(f"{backup.folder.name}: {exc}")
    raise CardPrepError("no_backup", "No backup taken before this card was prepared matches it"
                        + (" (" + "; ".join(reasons[:3]) + ")" if reasons else f" in {root}")
                        + ". Use 'Restore from a chosen backup...' if you have one.")


def _record(backup: Backup, report: Report, extra: dict) -> Path | None:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    path = backup.folder / f"result-{stamp}.json"
    try:
        _write_new(path, (json.dumps({"ok": report.ok, "title": report.title, "lines": report.lines,
                                      "written_sectors": report.written_sectors,
                                      "nothing_written": report.nothing_written, **extra}, indent=2) + "\n").encode())
    except OSError:
        return None
    return path


# -- plans --------------------------------------------------------------------------


def restore_plan(cand: Candidate, mbr: bytes, area: bytes, backup: Backup) -> Plan:
    """The writes that put a backup's marker bytes back on the same card."""
    card = backup.manifest.get("card") or {}
    for key in RESTORE_IDENTITY + (("filesystem",) if cand.filesystem else ()):
        if card.get(key) != getattr(cand, key):
            raise CardPrepError("restore_identity",
                                f"This backup belongs to another card or slot ({key}: backup {card.get(key)!r}, "
                                f"card {getattr(cand, key)!r}).")
    return layout.plan_restore(backup.mbr, backup.area, mbr, area, disk_bytes=cand.disk_bytes)


# -- the session ----------------------------------------------------------------------


def start(cand: Candidate, ops, backup_root: Path = DEFAULT_BACKUP_ROOT, *,
          restore_from: Backup | str | None = None, kind: str = "mark", now: datetime | None = None) -> Session:
    """Read the card, save a verified backup and plan the writes.

    restore_from is None (prepare: kind="mark" for native markers, or kind="script" for
    Canon Basic SCRIPT), a chosen Backup (restore), or LATEST (Undo: the newest
    before-marking or before-script backup that matches the card as read).

    Nothing is ever written to the card here. On any failure the card is mounted
    again and the exception says why; a plan that needs writing leaves the card
    unmounted for write() or cancel()."""
    if kind not in ("mark", "script"):
        raise ValueError(f"unknown plan kind {kind!r}")
    cand = ops.refresh(cand)
    if cand.mounted:
        try:
            ops.unmount(cand)
        except Exception as exc:
            mounted, out = _mount(ops, cand)
            raise PrepFailure(f"{exc} Nothing was read or written. " + _mount_line(mounted, out)) from exc
    try:
        mbr, area = ops.raw_read(cand, AREA_BYTES)
        if len(mbr) != MBR_BYTES or len(area) != AREA_BYTES:
            raise PrepFailure("The card read returned the wrong number of bytes.")
        layout.check_partition_start(mbr, area, disk_bytes=cand.disk_bytes, reported_bytes=cand.partition_offset,
                                     reported_size=cand.partition_bytes)
        if restore_from is not None:
            # The current boot area need not validate on its own (an interrupted write can
            # leave one exFAT region with a stale checksum); plan_restore checks it byte
            # for byte against the backup and validates the result instead.
            if restore_from == LATEST:
                plan, restore_from = _undo_plan(cand, mbr, area, Path(backup_root))
            else:
                plan = restore_plan(cand, mbr, area, restore_from)
            volume, purpose = plan.volume, "before-restore"
        else:
            volume = layout.inspect(mbr, area, disk_bytes=cand.disk_bytes)
            _identity_matches(cand, volume)
            if kind == "script":
                plan = layout.plan_script(mbr, area, disk_bytes=cand.disk_bytes)
                purpose = "before-script"
            else:
                plan = layout.plan_markers(mbr, area, disk_bytes=cand.disk_bytes)
                purpose = "before-marking"
        snapshot = Snapshot(cand, mbr, area, volume)
        backup = save_backup(snapshot, plan, backup_root, purpose=purpose, now=now)
    except Exception as exc:
        mounted, out = _mount(ops, cand)
        note = " Nothing was written. " + _mount_line(mounted, out)
        if isinstance(exc, CardPrepError):
            raise CardPrepError(exc.code, str(exc) + note) from exc
        if isinstance(exc, Cancelled):
            raise Cancelled(str(exc) + note) from exc
        raise PrepFailure(str(exc) + note) from exc
    session = Session(snapshot, plan, backup, source=restore_from)
    if not plan.writes_needed:
        mounted, out = _mount(ops, cand)
        title = "Already prepared: nothing to write." if plan.kind in ("mark", "script") else "Nothing to restore."
        report = Report(True, title, layout.describe_plan(plan) + [_mount_line(mounted, out),
                                                                  f"Backup: {backup.folder}"], nothing_written=True)
        report.record = _record(backup, report, {"plan": plan.to_dict()})
        session.report = report
    return session


def cancel(session: Session, ops) -> Report:
    """The person declined the write: mount the card again; nothing was written."""
    mounted, out = _mount(ops, session.snapshot.candidate)
    report = Report(False, "Cancelled. Nothing was written.",
                    [_mount_line(mounted, out), f"Backup: {session.backup.folder}"], nothing_written=True)
    report.record = _record(session.backup, report, {"plan": session.plan.to_dict()})
    return report


def write(session: Session, ops) -> Report:
    """Write exactly the planned sectors, read everything back, check the volume, mount again."""
    snapshot, plan, backup = session.snapshot, session.plan, session.backup
    what = "Boot markers" if plan.kind == "mark" else ("Canon Basic SCRIPT marker" if plan.kind == "script" else "Restore")
    report = Report(False, f"{what}: FAILED.")
    result: dict = {}
    cand = snapshot.candidate
    if not session.needs_write or backup.mbr != snapshot.mbr or backup.area != snapshot.area \
            or plan.before_area != snapshot.area:
        report.title, report.nothing_written = f"{what}: refused. Nothing was written.", True
        report.lines.append("The backup and plan are not from this read of the card.")
        return report
    try:
        try:
            cand = ops.refresh(snapshot.candidate)
            if cand.mounted:  # mounted again by someone since the read
                ops.unmount(cand)
        except Exception as exc:
            report.title, report.nothing_written = f"{what}: not written.", True
            report.lines.append(f"The card could not be re-checked before writing: {exc} Nothing was written.")
            return report
        try:
            result = ops.raw_write(cand, [(w.offset, len(w.after)) for w in plan.writes],
                                   sha256(plan.expected_before), plan.after_bytes,
                                   expect_mbr_sha256=sha256(snapshot.mbr), expect_area_sha256=sha256(snapshot.area))
        except Cancelled:
            report.title, report.nothing_written = f"{what}: cancelled. Nothing was written.", True
            report.lines.append("The password prompt was cancelled.")
            return report
        except Exception as exc:
            report.title = f"{what}: card state unknown."
            report.lines += [f"The write did not report a result: {exc}",
                             "Read the card again before using it (Prepare or Undo do that first)."]
            return report
        written = [o // layout.SECTOR for o in result.get("written") or []]
        possibly = [o // layout.SECTOR for o in result.get("possibly_written") or result.get("written") or []]
        report.written_sectors = possibly
        if result.get("code") == "before_mismatch":
            report.title, report.nothing_written = f"{what}: refused. Nothing was written.", True
            report.lines.append(f"The card's {result.get('mismatch') or 'boot sectors'} changed since it was read, "
                                "or it is another card. Try again.")
            return report
        if not result.get("ok") and "area_hex" not in result and result.get("possibly_written") == []:
            report.title, report.nothing_written = f"{what}: not written.", True
            report.lines.append(f"It stopped before writing anything: {result.get('error')} "
                                "Nothing was written.")
            return report
        if not result.get("ok") and "area_hex" not in result:
            report.lines += [f"The write stopped: {result.get('error')}",
                             f"Sectors written: {written or 'none'}; possibly written (started but not confirmed): "
                             f"{[s for s in possibly if s not in written] or 'none'}.",
                             "Do not use the card yet. Undo restores it from the backup below."]
            return report
        try:
            problems = layout.verify_written(plan, snapshot.mbr, bytes.fromhex(result.get("mbr_hex", "")),
                                             bytes.fromhex(result.get("area_hex", "")),
                                             disk_bytes=cand.disk_bytes)
        except (TypeError, ValueError) as exc:
            problems = [f"The read-back could not be decoded ({exc})."]
        if not result.get("ok"):
            problems.insert(0, f"The write reported {result.get('code') or 'a failure'}.")
        try:
            verified, output = ops.verify(cand)
        except Exception as exc:  # a timeout or crash here must not hide what was written
            verified, output = False, f"diskutil verifyVolume did not complete: {exc}"
        last = output.strip().splitlines()[-1] if output.strip() else "(no output)"
        report.lines.append(f"Sectors written: {', '.join(map(str, possibly)) or 'none'} "
                            f"({len(possibly) * layout.SECTOR:,} bytes).")
        if problems:
            report.lines += ["Read-back check FAILED:"] + [f"- {p}" for p in problems]
            report.lines.append("Do not use the card yet. Undo restores it from the backup below.")
        else:
            report.lines.append("Read-back check passed: every written byte matches the plan, the MBR is unchanged "
                                "and the boot area validates.")
        report.lines.append(("Volume check passed: " if verified else "Volume check FAILED: ") + last)
        report.ok = not problems and verified
        if report.ok:
            report.title = f"{what}: done and verified."
        elif not problems:
            report.lines.append("The written bytes are correct but the volume check did not pass. On FAT32 this can "
                                "be the backup boot sector, which is left unmarked by design. Check the card with "
                                "Disk Utility First Aid, or use Undo.")
        return report
    except Exception as exc:  # never lose the report
        report.title = f"{what}: card state unknown."
        report.lines.append(f"Unexpected error: {exc}")
        return report
    finally:
        mounted, out = _mount(ops, cand)
        report.lines.append(_mount_line(mounted, out))
        report.lines.append(f"Backup: {backup.folder}")
        report.record = _record(backup, report, {"plan": plan.to_dict(), "raw_write": {
            k: v for k, v in result.items() if k not in ("area_hex", "mbr_hex")}})
