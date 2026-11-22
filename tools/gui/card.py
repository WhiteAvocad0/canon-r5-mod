"""Copy a built autoexec.bin to an already-prepared card's mounted folder.

Ordinary file operations only. This module never formats, never opens a raw
device, never writes boot sectors and never deletes anything except the one
temporary file it created itself in the current copy. For a --screenshot build
it may also create the empty DCIM/R5SHOTS folder the camera writes into. Making a card bootable
(boot markers) is the separate "Prepare card" step (tools/cardprep, prep_state.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import secrets
import shutil
import stat
import sys
from typing import Collection, Mapping

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from image_format import FormatError, inspect_image

TARGET_NAME = "AUTOEXEC.BIN"
SCREENSHOT_FOLDER = "R5SHOTS"  # under DCIM: where a --screenshot build writes (it never creates it)
TEMP_PREFIX = "R5GUI-"
TEMP_SUFFIX = ".TMP"
MAX_IMAGE_BYTES = 16 * 1024 * 1024  # far above any build (~80 KiB); bounds reads of card files
MIN_CARD_BYTES = 2 * 1000**3
MAX_CARD_BYTES = 2 * 1000**4  # SDXC maximum


# POSIX folders that are never a camera card: refused exactly, or with everything inside them.
POSIX_REFUSED = frozenset(map(PurePosixPath, (
    "/", "/Users", "/home", "/root", "/var", "/private", "/tmp", "/opt", "/srv",
    "/Volumes", "/media", "/mnt", "/run", "/run/media",
)))
POSIX_REFUSED_TREES = tuple(map(PurePosixPath, (
    "/System", "/Library", "/Applications", "/usr", "/bin", "/sbin", "/etc", "/dev", "/proc", "/sys", "/boot",
)))


def protected_location(path: str | PurePath, *, windows: bool, home: str | PurePath,
                       environ: Mapping[str, str]) -> str | None:
    """Why a folder is a system or home location rather than a card, or None.

    Pure path logic, so Windows rules can be tested anywhere. A removable drive root
    (E:\\, /Volumes/EOS_DIGITAL, /media/me/CARD, /run/media/me/CARD) is allowed; the
    system drive, the Windows folder, the home folder and its parents are refused."""
    flavour = PureWindowsPath if windows else PurePosixPath
    path, home = flavour(path), flavour(home)
    if windows:
        system_drives = {home.drive.casefold()} if home.drive else set()
        windows_dirs = []
        for var in ("SystemDrive", "SystemRoot", "windir"):
            value = environ.get(var)
            if value:
                system_drives.add(flavour(value).drive.casefold())
                if var != "SystemDrive":
                    windows_dirs.append(flavour(value))
        system_drives.discard("")
        if path.drive.casefold() in system_drives and path == flavour(path.anchor):
            return f"{path} is the Windows system drive, not a camera card."
        if any(path == d or path.is_relative_to(d) for d in windows_dirs):
            return f"{path} is inside the Windows folder, not a camera card."
    elif path in POSIX_REFUSED or any(path == t or path.is_relative_to(t) for t in POSIX_REFUSED_TREES):
        return f"{path} is a system folder, not a camera card."
    if path == home or home.is_relative_to(path):
        return f"{path} is your home folder or contains it, not a camera card."
    return None


class CardError(Exception):
    pass


class NeedsConfirmation(CardError):
    """The folder does not look like a camera card; the person must confirm first."""


@dataclass
class CardInspection:
    root: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    existing: Path | None = None
    has_dcim: bool = False
    is_mount_point: bool = False
    total_bytes: int | None = None
    leftover_temps: list[str] = field(default_factory=list)


@dataclass
class CopyResult:
    target: Path
    sha256: str
    bytes: int
    backup: Path | None
    replaced_existing: bool
    already_identical: bool
    messages: list[str]


def eject_hint(platform: str | None = None) -> str:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        how = "eject it in Finder (or drag it to the Trash/Eject icon)"
    elif platform.startswith("win"):
        how = "use 'Safely Remove Hardware' / 'Eject' in File Explorer"
    else:
        how = "use your file manager's Eject / Unmount"
    return f"Before removing the card, {how} and wait until it is safe to remove."


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_file(path: Path, limit: int = MAX_IMAGE_BYTES) -> bytes:
    """Read a regular file without following a symlink, refusing anything larger than limit."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise CardError(f"{path} is not a regular file")
    if st.st_size > limit:
        raise CardError(f"{path} is larger than {limit:,} bytes")
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise CardError(f"{path} grew past {limit:,} bytes while reading")
    return data


def _matches(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.name.casefold() == TARGET_NAME.casefold())


def inspect_card(root: Path) -> CardInspection:
    """Look at a folder before copying. Errors refuse the copy; warnings need confirmation."""
    root = Path(root)
    info = CardInspection(root)
    if not root.is_dir():
        info.errors.append(f"{root} is not a folder.")
        return info
    resolved = root.resolve()
    info.root = resolved
    protected = protected_location(resolved, windows=os.name == "nt", home=Path.home().resolve(), environ=os.environ)
    if protected:
        info.errors.append(protected)
    if not os.access(resolved, os.W_OK):
        info.errors.append(f"{resolved} is not writable (is the card's lock switch on?).")
    try:
        entries = list(resolved.iterdir())
    except OSError as exc:
        info.errors.append(f"Cannot list {resolved}: {exc}")
        return info
    matches = [p for p in entries if p.name.casefold() == TARGET_NAME.casefold()]
    if len(matches) > 1:
        info.errors.append("More than one autoexec.bin spelling exists here: " + ", ".join(p.name for p in matches)
                           + ". Resolve that yourself first.")
    elif matches:
        st = os.lstat(matches[0])
        if not stat.S_ISREG(st.st_mode):
            info.errors.append(f"{matches[0].name} on the card is not a regular file.")
        else:
            info.existing = matches[0]
    info.leftover_temps = sorted(p.name for p in entries if p.name.startswith(TEMP_PREFIX) and p.name.endswith(TEMP_SUFFIX))
    info.has_dcim = any(p.name.casefold() == "dcim" and p.is_dir() for p in entries)
    info.is_mount_point = os.path.ismount(resolved)
    try:
        info.total_bytes = shutil.disk_usage(resolved).total
    except OSError:
        info.total_bytes = None
    if not info.is_mount_point:
        info.warnings.append("This folder is not the top (root) of a drive. The camera only looks at the card root.")
    if not info.has_dcim:
        info.warnings.append("There is no DCIM folder, so this may not be a card that has been used in the camera.")
    if info.total_bytes is None:
        info.warnings.append("The drive size could not be read.")
    elif not MIN_CARD_BYTES <= info.total_bytes <= MAX_CARD_BYTES:
        info.warnings.append(f"The drive size ({info.total_bytes / 1000**3:,.1f} GB) is unusual for an SD card.")
    if info.leftover_temps:
        info.warnings.append("Leftover temporary files from an earlier copy were found and left in place: "
                             + ", ".join(info.leftover_temps) + ". You can delete them yourself.")
    return info


def verify_image(image: Path, expected_sha256: str) -> bytes:
    """The built image must be the one build.json describes and carry a valid footer."""
    data = _read_file(Path(image))
    if _sha256(data) != expected_sha256:
        raise CardError("autoexec.bin in the build folder does not match build.json; rebuild before copying.")
    try:
        inspect_image(data)
    except FormatError as exc:
        raise CardError(f"autoexec.bin failed the image format check: {exc}") from exc
    return data


def copy_to_card(
    image: Path,
    expected_sha256: str,
    card_root: Path,
    backup_dir: Path,
    *,
    confirmed_warnings: Collection[str] = (),
    now: datetime | None = None,
    screenshot_folder: bool = False,
) -> CopyResult:
    """Back up any existing autoexec.bin to the computer, then copy atomically and verify.

    confirmed_warnings are the warnings the person saw and accepted; any other warning
    found now (for example one that appeared since) stops the copy for confirmation.
    screenshot_folder (a --screenshot build) then makes sure DCIM/R5SHOTS exists."""
    data = verify_image(image, expected_sha256)
    info = inspect_card(card_root)
    if info.errors:
        raise CardError(" ".join(info.errors))
    unconfirmed = [w for w in info.warnings if w not in confirmed_warnings]
    if unconfirmed:
        raise NeedsConfirmation(" ".join(unconfirmed))
    root = info.root
    messages: list[str] = []
    backup: Path | None = None
    existing_sha: str | None = None

    if info.existing is not None:
        old = _read_file(info.existing)
        existing_sha = _sha256(old)
        if existing_sha == expected_sha256:
            done = ["The card already has this exact autoexec.bin; nothing was written."]
            if screenshot_folder:
                done.append(ensure_screenshot_folder(root))
            return CopyResult(info.existing, expected_sha256, len(data), None, False, True, [*done, eject_hint()])
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = (datetime.now() if now is None else now).strftime("%Y%m%d-%H%M%S")
        backup = backup_dir / f"card-{info.existing.stem}-{stamp}-{existing_sha[:12]}{info.existing.suffix}"
        with open(backup, "xb") as f:
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        if _sha256(_read_file(backup)) != existing_sha:
            raise CardError(f"The backup at {backup} does not read back correctly; the card was not changed.")
        messages.append(f"Backed up the card's previous {info.existing.name} to {backup}.")

    target = info.existing if info.existing is not None else root / TARGET_NAME
    temp = root / f"{TEMP_PREFIX}{secrets.token_hex(4).upper()}{TEMP_SUFFIX}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(temp, flags, 0o644)
    created = True
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if _sha256(_read_file(temp)) != expected_sha256:
            raise CardError("The temporary copy on the card did not read back correctly; the card's autoexec.bin was not changed.")
        # Re-check the card just before replacing, so a file that appeared or changed meanwhile is never lost.
        now_matches = _matches(root)
        if info.existing is None:
            if now_matches:
                raise CardError("An autoexec.bin appeared on the card during the copy; nothing was replaced. Try again.")
        elif now_matches != [info.existing] or _sha256(_read_file(info.existing)) != existing_sha:
            raise CardError("The card's autoexec.bin changed during the copy; nothing was replaced. Try again.")
        os.replace(temp, target)
        created = False
    finally:
        if created:
            try:
                os.unlink(temp)  # our own temporary file only
            except FileNotFoundError:
                pass
    readback = _read_file(target)
    if _sha256(readback) != expected_sha256:
        restore = f" Your previous file is backed up at {backup}." if backup else ""
        raise CardError(f"{target} does not match the build after copying. Do not use this card yet.{restore}")
    messages.append(f"Copied and verified {target.name} (SHA-256 {expected_sha256}).")
    if screenshot_folder:
        messages.append(ensure_screenshot_folder(root))
    messages.append(eject_hint())
    return CopyResult(target, expected_sha256, len(data), backup, info.existing is not None, False, messages)


def ensure_screenshot_folder(root: Path) -> str:
    """Create the empty DCIM/R5SHOTS folder a --screenshot build needs, inside an existing DCIM.

    Creates nothing else and changes nothing that exists; returns a message for the person."""
    root = Path(root)
    dcim = [p for p in root.iterdir() if p.name.casefold() == "dcim" and p.is_dir() and not p.is_symlink()]
    if len(dcim) != 1:
        return ("There is no DCIM folder on the card yet, so the screenshot folder was not created. "
                "Format the card in the camera, then create DCIM/R5SHOTS before taking screenshots.")
    found = [p for p in dcim[0].iterdir() if p.name.casefold() == SCREENSHOT_FOLDER.casefold()]
    if found:
        if len(found) == 1 and found[0].is_dir() and not found[0].is_symlink():
            return f"The screenshot folder {dcim[0].name}/{found[0].name} is already on the card."
        return (f"{dcim[0].name}/{found[0].name} exists but is not a plain folder; screenshots will fail "
                "until it is one. Nothing was changed.")
    try:
        (dcim[0] / SCREENSHOT_FOLDER).mkdir()
    except OSError as exc:  # the mod itself is already copied and verified
        return (f"The screenshot folder {dcim[0].name}/{SCREENSHOT_FOLDER} could not be created ({exc}). "
                "Create it yourself before taking screenshots.")
    return f"Created the empty screenshot folder {dcim[0].name}/{SCREENSHOT_FOLDER} on the card."
