"""Canon Basic card staging, BootInfo verification, and returned ROM dump collection (R5 2.2.1).

Standard library only, no Tkinter imports.

Research basis (EOS R5 firmware 2.2.1):
- Canon Basic on SDHC FAT32 (< 0x4000000 sectors) reads B:/script.req (17 B
  "for DC_scriptdisk") and B:/extend.m when SCRIPT (6 B) is at FAT32 VBR +0x1F0
  and the user presses Playback then SET once.
- SaveAllRomImageToFile() writes gang1NN.bin (0xE0000000..0xE4000000, 64 MiB)
  and gang2NN.bin to A:/ if CFexpress is mounted, otherwise B:/ (SD card).
- EnableBootDisk() (0xE0B38D62) writes 0xFFFFFFFF to BootInfo +0x04 (file
  offset 0x3FF8004); DisableBootDisk() (0xE0B38D74) writes 0x00000000.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
import sys
from typing import Collection, Mapping

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from messages import joined, message, phrase

from gui import card

ROM_BYTES = 0x4000000  # 67,108,864 bytes (64 MiB)
ROM_BASE = 0xE0000000
BOOTINFO_OFFSET = 0x3FF8000  # VA 0xE3FF8000
BOOTINFO_BYTES = 64
BOOTINFO_LOADER_BYTES = 48
MIN_FREE_BYTES = 300 * 1024 * 1024  # 300 MiB (two 64 MiB dumps plus margin)
MAX_SCRIPT_BYTES = 1024 * 1024

SCRIPT_REQ_NAME = "script.req"
SCRIPT_REQ_BYTES = b"for DC_scriptdisk"  # 17 B, SHA-256 60e6a567...1767
EXTEND_M_NAME = "extend.m"

DUMP_SCRIPT = b"private sub Initialize()\n  SaveAllRomImageToFile()\nend sub\n"
ENABLE_BOOTDISK_SCRIPT = b"private sub Initialize()\n  EnableBootDisk()\n  SaveAllRomImageToFile()\nend sub\n"
DISABLE_BOOTDISK_SCRIPT = b"private sub Initialize()\n  DisableBootDisk()\n  SaveAllRomImageToFile()\nend sub\n"

SCRIPTS = {
    "dump": DUMP_SCRIPT,
    "enable_bootdisk": ENABLE_BOOTDISK_SCRIPT,
    "disable_bootdisk": DISABLE_BOOTDISK_SCRIPT,
}

# Code regions outside the builder's contracts that establish BootInfo/IsScriptMode semantics for R5 2.2.1.
WIZARD_FINGERPRINTS = {
    "bootinfo_procs_registration": (
        0xE0B38CD4,
        0xE0B390F0,
        "cba2edcb039009605fbc3f9a1c6560f6605da65950af219122feecca5c0f518b",
    ),
    "loader_bootdisk_gate": (
        0xE00068C0,
        0xE0006940,
        "35db5bc4975dd55567127724250b2491428258dedccacac77064aca985830cd2",
    ),
    "loader_bootinfo_copy": (
        0xE0006FC4,
        0xE0007038,
        "c22c6119c7ee27880f47780b419f3acb4b6fa22170e9de22d18072cf1f91f50a",
    ),
    "sd_is_script_mode": (
        0xE06F73DC,
        0xE06F74B6,
        "6094bb8acdb36b4c3a78a7a31b723608610cb05e92439d9d99064eb9a5c89145",
    ),
}

DEFAULT_DUMP_ROOT = Path.home() / ".r5-build-assistant" / "rom-dumps"
GANG1_RE = re.compile(r"^gang1([0-9]{2,3})\.bin$", re.IGNORECASE)


class CBasicError(Exception):
    """A Canon Basic staging, verification, or dump collection refusal."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BootInfoFacts:
    state: str  # "enabled" or "disabled"
    boot_disk_raw: int
    rom_sha256: str
    block_hex: str
    before_compared: bool = False
    before_sha256: str | None = None


@dataclass(frozen=True)
class StagedCard:
    task: str
    card_root: Path
    backup_dir: Path
    baseline_gang1: dict[str, str]
    script_req_sha256: str
    extend_m_sha256: str
    messages: list[str]


@dataclass(frozen=True)
class ReturnedDump:
    card_file: Path
    local_path: Path
    sha256: str
    rom_summary: dict
    bootinfo: BootInfoFacts
    cleaned_files: list[str]
    messages: list[str]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem, suffix = os.path.splitext(filename)
    for n in range(1, 1000):
        candidate = directory / (filename if n == 1 else f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise CBasicError("backup_collision", message("Could not choose a new filename for {filename} in {directory}.",
        filename=filename, directory=directory))


def _write_backup(backup_dir: Path, name: str, data: bytes) -> Path:
    expected = _sha256(data)
    dest = _unique_path(backup_dir, f"{name}-{expected[:12]}")
    with open(dest, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if _sha256(card._read_file(dest, limit=max(len(data), 1))) != expected:
        raise CBasicError("backup_verify_failed", message("The backup at {backup} did not read back correctly.", backup=dest))
    return dest


def _check_card_dir(card_root: Path) -> Path:
    root = Path(card_root)
    if not root.is_dir():
        raise CBasicError("card_refused", message("{folder} is not a folder.", folder=root))
    resolved = root.resolve()
    protected = card.protected_location(
        resolved, windows=os.name == "nt", home=Path.home().resolve(), environ=os.environ
    )
    if protected:
        raise CBasicError("card_refused", protected)
    return resolved


def inspect_bootinfo(
    rom_bytes: bytes,
    *,
    before_bytes: bytes | None = None,
    fingerprints: Mapping[str, tuple[int, int, str]] | None = None,
) -> BootInfoFacts:
    """Verify wizard BootInfo/loader/IsScriptMode fingerprints, loader invariants, and BootDisk state."""
    if len(rom_bytes) != ROM_BYTES:
        raise CBasicError("rom_length", message("ROM dump must be exactly {expected:,} bytes, got {actual:,}.",
            expected=ROM_BYTES, actual=len(rom_bytes)))
    fps = WIZARD_FINGERPRINTS if fingerprints is None else fingerprints
    mismatches = []
    for name, (start_va, end_va, expected) in fps.items():
        got = _sha256(rom_bytes[start_va - ROM_BASE : end_va - ROM_BASE])
        if got != expected:
            mismatches.append(message("{region} (got {actual}..., expected {expected}...)",
                region=name, actual=got[:12], expected=expected[:12]))
    if mismatches:
        raise CBasicError(
            "wizard_fingerprint_mismatch",
            message("Wizard BootInfo/script fingerprints do not match EOS R5 2.2.1: {details}", details=joined("; ", mismatches)),
        )

    blk = rom_bytes[BOOTINFO_OFFSET : BOOTINFO_OFFSET + BOOTINFO_BYTES]
    failures = []
    if _u32(blk, 0x00) != 0:
        failures.append(message("+0x00 main_firm is 0x{value:08X}, expected 0x00000000", value=_u32(blk, 0x00)))
    if _u32(blk, 0x0C) != 0xFFFFFFFF:
        failures.append(message("+0x0C firmware is 0x{value:08X}, expected 0xFFFFFFFF", value=_u32(blk, 0x0C)))
    if blk[0x10:0x20] != b"\xff" * 16:
        failures.append("+0x10..+0x1F loader flags are not all 0xFF")
    if _u32(blk, 0x24) != 0xFFFFFFFF:
        failures.append(message("+0x24 alt_path is 0x{value:08X}, expected 0xFFFFFFFF", value=_u32(blk, 0x24)))
    if failures:
        raise CBasicError("bootinfo_invariant", message("Unexpected BootInfo invariants: {details}", details=joined("; ", failures)))

    bd = _u32(blk, 0x04)
    if bd == 0xFFFFFFFF:
        state = "enabled"
    elif bd == 0x00000000:
        state = "disabled"
    else:
        raise CBasicError(
            "unexpected_bootdisk_value",
            message("BootInfo +0x04 has unexpected value 0x{value:08X} (expected 0x00000000 or 0xFFFFFFFF).",
                value=bd),
        )

    before_compared = False
    before_sha = None
    if before_bytes is not None:
        before = inspect_bootinfo(before_bytes, fingerprints=fps)
        before_blk = before_bytes[BOOTINFO_OFFSET : BOOTINFO_OFFSET + BOOTINFO_BYTES]
        if blk[:4] != before_blk[:4] or blk[8:BOOTINFO_LOADER_BYTES] != before_blk[8:BOOTINFO_LOADER_BYTES]:
            raise CBasicError(
                "bootinfo_unexpected_diff",
                "BootInfo bytes outside +0x04..+0x07 differed between the before and after ROM dumps.",
            )
        before_compared = True
        before_sha = before.rom_sha256

    return BootInfoFacts(
        state=state,
        boot_disk_raw=bd,
        rom_sha256=_sha256(rom_bytes),
        block_hex=blk.hex(),
        before_compared=before_compared,
        before_sha256=before_sha,
    )


def scan_gang1_files(card_root: Path) -> list[Path]:
    """Return regular 64 MiB gang1NN.bin files at the top of card_root, sorted by numeric index."""
    root = Path(card_root)
    matches: list[tuple[int, str, Path]] = []
    for entry in root.iterdir():
        m = GANG1_RE.fullmatch(entry.name)
        if not m:
            continue
        st = os.lstat(entry)
        if stat.S_ISLNK(st.st_mode):
            raise CBasicError("symlink_refused", message("{file} on the card is a symbolic link; refusing it.", file=entry.name))
        if stat.S_ISREG(st.st_mode) and st.st_size == ROM_BYTES:
            matches.append((int(m.group(1)), entry.name.casefold(), entry))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in matches]


def _write_card_file(root: Path, filename: str, content: bytes, backup_dir: Path, messages: list[str]) -> None:
    expected_sha = _sha256(content)
    matches = sorted(p for p in root.iterdir() if p.name.casefold() == filename.casefold())
    if len(matches) > 1:
        raise CBasicError(
            "ambiguous_file",
            message("More than one {filename} spelling exists on the card: {names}",
                    filename=filename, names=", ".join(p.name for p in matches)),
        )
    existing = matches[0] if matches else None
    existing_sha: str | None = None
    if existing is not None:
        st = os.lstat(existing)
        if not stat.S_ISREG(st.st_mode):
            raise CBasicError("not_regular_file", message("{file} on the card is not a regular file.", file=existing.name))
        old = card._read_file(existing, limit=MAX_SCRIPT_BYTES)
        existing_sha = _sha256(old)
        if existing_sha == expected_sha:
            messages.append(message("{file} on the card already has the exact expected bytes.", file=existing.name))
            return
        backup = _write_backup(backup_dir, existing.name, old)
        messages.append(message("Backed up existing {file} to {backup}.", file=existing.name, backup=backup))

    target = existing if existing is not None else root / filename
    temp = root / f"{card.TEMP_PREFIX}{secrets.token_hex(4).upper()}{card.TEMP_SUFFIX}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(temp, flags, 0o644)
    created = True
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if _sha256(card._read_file(temp, limit=MAX_SCRIPT_BYTES)) != expected_sha:
            raise CBasicError("write_verify_failed", message("Temporary copy for {file} did not read back correctly.", file=filename))
        now_matches = sorted(p for p in root.iterdir() if p.name.casefold() == filename.casefold())
        if existing is None:
            if now_matches:
                raise CBasicError("card_changed", message("{file} appeared on the card during staging.", file=filename))
        elif now_matches != [existing] or _sha256(card._read_file(existing, limit=MAX_SCRIPT_BYTES)) != existing_sha:
            raise CBasicError("card_changed", message("{file} on the card changed during staging.", file=existing.name))
        os.replace(temp, target)
        created = False
    finally:
        if created:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
    if _sha256(card._read_file(target, limit=MAX_SCRIPT_BYTES)) != expected_sha:
        raise CBasicError("write_verify_failed", message("{file} on the card did not read back with the expected SHA-256.", file=target.name))
    messages.append(message("Staged and verified {file} ({size} bytes, SHA-256 {digest}...).",
        file=target.name, size=len(content), digest=expected_sha[:12]))


def stage_script_files(
    card_root: Path,
    task: str,
    backup_dir: Path,
    *,
    confirmed_warnings: Collection[str] = (),
) -> StagedCard:
    """Back up any existing script files and atomically stage script.req + extend.m for task."""
    if task not in SCRIPTS:
        raise ValueError(f"unknown Canon Basic task {task!r}")
    info = card.inspect_card(card_root)
    if info.errors:
        raise CBasicError("card_refused", joined(" ", info.errors))
    if info.existing is not None:
        raise CBasicError(
            "autoexec_present",
            message("{name} is on this card. Remove or back up AUTOEXEC.BIN first so a BootDisk-enabled camera "
                "boots normal Canon firmware during Canon Basic steps.",
                name=info.existing.name),
        )
    unconfirmed = [w for w in info.warnings if w not in confirmed_warnings]
    if unconfirmed:
        raise card.NeedsConfirmation(joined(" ", unconfirmed))
    try:
        free_bytes = shutil.disk_usage(info.root).free
    except OSError as exc:
        raise CBasicError("free_space_unknown", message("Could not check free space on {folder}: {error}", folder=info.root, error=exc)) from exc
    if free_bytes < MIN_FREE_BYTES:
        raise CBasicError(
            "insufficient_free_space",
            message("Need at least 300 MiB free on the card for ROM dumps (only {free:.1f} MiB free).",
                free=free_bytes / (1024 * 1024)),
        )

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    baseline_gang1 = {p.name.casefold(): _sha256(card._read_file(p, ROM_BYTES)) for p in scan_gang1_files(info.root)}

    messages: list[str] = []
    _write_card_file(info.root, SCRIPT_REQ_NAME, SCRIPT_REQ_BYTES, backup_dir, messages)
    _write_card_file(info.root, EXTEND_M_NAME, SCRIPTS[task], backup_dir, messages)

    req_sha = _sha256(SCRIPT_REQ_BYTES)
    ext_sha = _sha256(SCRIPTS[task])
    record = {
        "task": task,
        "card_root": str(info.root),
        "baseline_gang1": baseline_gang1,
        "script_req_sha256": req_sha,
        "extend_m_sha256": ext_sha,
    }
    (backup_dir / "cbasic-staged.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    messages.append(card.eject_hint())
    return StagedCard(task, info.root, backup_dir, baseline_gang1, req_sha, ext_sha, messages)


def remove_script_files(card_root: Path, backup_dir: Path | None = None) -> list[str]:
    """Remove extend.m and script.req from the card root so a stray SET cannot repeat a script."""
    root = _check_card_dir(card_root)
    removed: list[str] = []
    for target_name in (EXTEND_M_NAME, SCRIPT_REQ_NAME):
        matches = sorted(p for p in root.iterdir() if p.name.casefold() == target_name.casefold())
        for entry in matches:
            st = os.lstat(entry)
            if not stat.S_ISREG(st.st_mode):
                continue
            if backup_dir is not None:
                data = card._read_file(entry, limit=MAX_SCRIPT_BYTES)
                _write_backup(Path(backup_dir), entry.name, data)
            entry.unlink()
            removed.append(entry.name)
    return removed


def collect_returned_dump(
    card_root: Path,
    dest_dir: Path = DEFAULT_DUMP_ROOT,
    *,
    baseline_gang1: Mapping[str, str] | None = None,
    require_new: bool = False,
    before_rom: Path | None = None,
    profile: str = "xpan-crop",
    record_indicator: str | None = None,
    options: Mapping[str, str | None] | None = None,
    clean_scripts: bool = True,
    backup_dir: Path | None = None,
) -> ReturnedDump:
    """Find the returned gang1NN.bin dump, verify admission and BootInfo, copy privately, and clean scripts."""
    import build

    root = _check_card_dir(card_root)
    candidates = scan_gang1_files(root)
    if baseline_gang1 is not None:
        new_files = [
            p
            for p in candidates
            if p.name.casefold() not in baseline_gang1
            or _sha256(card._read_file(p, ROM_BYTES)) != baseline_gang1[p.name.casefold()]
        ]
        if require_new and not new_files:
            raise CBasicError(
                "no_new_dump",
                "No new gang1NN.bin dump was found on the card: the Canon Basic script did not run. "
                "Check that the card is a FAT32 SDHC card (up to 32 GB) with the SCRIPT marker, any CFexpress "
                "card was removed, and you pressed Playback then SET once and waited for the card light.",
            )
        chosen = new_files[-1] if new_files else (candidates[-1] if candidates else None)
    else:
        chosen = candidates[-1] if candidates else None

    if chosen is None:
        raise CBasicError("no_dump_found", "No gang1NN.bin ROM dump (67,108,864 bytes) was found at the top of the card.")

    data = card._read_file(chosen, ROM_BYTES)
    if len(data) != ROM_BYTES:
        raise CBasicError("rom_length", message("{file} must be exactly {expected:,} bytes, got {actual:,}.",
            file=chosen.name, expected=ROM_BYTES, actual=len(data)))

    opts: dict[str, str | None] = dict(options or {})
    if record_indicator is not None and "record_indicator" not in opts:
        opts["record_indicator"] = record_indicator
    try:
        rom_summary = build.check_rom(chosen, profile=profile, **opts)
    except (ValueError, OSError) as exc:
        raise CBasicError("rom_refused", message("{file} was refused by the ROM check: {error}", file=chosen.name, error=exc)) from exc

    before_bytes = card._read_file(Path(before_rom), ROM_BYTES) if before_rom is not None else None
    bootinfo = inspect_bootinfo(data, before_bytes=before_bytes)

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    rom_sha = _sha256(data)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    local_path = _unique_path(dest_dir, f"{stamp}-{chosen.stem}-{rom_sha[:12]}.bin")
    temp = dest_dir / f".{local_path.name}.{secrets.token_hex(4)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(temp, flags, 0o600)
    created = True
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if _sha256(card._read_file(temp, ROM_BYTES)) != rom_sha:
            raise CBasicError("copy_verify_failed", "Local copy of the ROM dump did not read back with the expected SHA-256.")
        os.replace(temp, local_path)
        created = False
    finally:
        if created:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
    if _sha256(card._read_file(local_path, ROM_BYTES)) != rom_sha:
        raise CBasicError("copy_verify_failed", message("Saved ROM dump at {path} failed final readback verification.", path=local_path))

    cleaned = remove_script_files(root, backup_dir or dest_dir) if clean_scripts else []
    regions = rom_summary["verified_code_regions"] + rom_summary.get("verified_xpan_regions", 0)
    messages = [
        message("Imported {file} ({size:,} bytes, SHA-256 {digest}) to {path}.",
            file=chosen.name, size=len(data), digest=rom_sha, path=local_path),
        message("ROM admission passed for {profile}: {count} fingerprinted regions match.",
            profile=profile, count=regions),
        message("BootInfo check passed: BootDisk is {state} (+0x04 = 0x{value:08X}).",
            state=phrase(bootinfo.state.upper()), value=bootinfo.boot_disk_raw),
    ]
    if bootinfo.before_compared:
        messages.append("Compared 48-byte BootInfo block with previous dump: only +0x04..+0x07 changed.")
    if cleaned:
        messages.append(message("Removed Canon Basic script file(s) from the card: {files}.", files=", ".join(cleaned)))
    messages.append("Keep this ROM dump private; never upload or share it. gang2NN.bin is not needed by the builder.")
    return ReturnedDump(chosen, local_path, rom_sha, rom_summary, bootinfo, cleaned, messages)
