"""Find removable SD cards, run diskutil, and open the card for raw access (macOS).

Only a card in an SD card slot is offered: on macOS ``diskutil`` must report the
Secure Digital protocol, removable and ejectable physical media, an MBR with one
FAT32 or exFAT partition (mounted under /Volumes, or not mounted at all, so that a
half-written card can still be restored), 512-byte sectors and a size between
2 GB and 2 TiB. Internal and boot disks, disk images, USB devices
(including USB card readers, which macOS cannot tell from other USB drives),
system images and anything else are refused with a reason.

Raw reads and writes use rawio.RawAccess: macOS ``authopen`` opens only the chosen
whole-disk device after the system password window and passes the descriptor to
this process. No code runs as root, and this module never sees a password.

Windows and Linux: best-effort enumeration only, **untested**; preparing a card
is offered on macOS only.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import plistlib
import re
import subprocess
import sys
from typing import Callable

from messages import message, phrase

from . import rawio
from .rawio import Cancelled, DeviceError  # noqa: F401  (shared with the raw-access layer)

SD_MIN_BYTES = 2 * 1000**3
SD_MAX_BYTES = 2 * 1024**4
WHOLE_ID_RE = re.compile(r"disk[0-9]{1,3}")
PART_ID_RE = re.compile(r"disk([0-9]{1,3})s([0-9]{1,2})")
MAC_FAT32_CONTENT = frozenset({"Windows_FAT_32", "DOS_FAT_32"})
DISKUTIL = "/usr/sbin/diskutil"

UNTESTED_NOTE = {
    "darwin": "macOS: verified with a camera-formatted 128 GB exFAT card on the owner's Mac; FAT32 cards are untested.",
    "linux": "Linux: detection only, untested. Preparing a card is offered on macOS only.",
    "win32": "Windows: detection only, untested. Preparing a card is offered on macOS only.",
}


@dataclass(frozen=True)
class Candidate:
    platform: str
    whole_id: str
    partition_id: str
    model: str
    bus: str
    disk_bytes: int | None
    partition_offset: int | None
    partition_bytes: int | None
    filesystem: str | None
    volume_name: str
    mount_point: str
    location: str = ""
    refusals: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.refusals

    @property
    def raw_disk(self) -> str:
        return f"/dev/r{self.whole_id}"

    @property
    def raw_partition(self) -> str:
        return f"/dev/r{self.partition_id}"

    def identity(self) -> dict:
        """The card as chosen; recorded in backups."""
        return {k: getattr(self, k) for k in ("platform", "whole_id", "partition_id", "model", "bus", "disk_bytes",
                                              "partition_offset", "partition_bytes", "filesystem", "volume_name",
                                              "location")}

    @property
    def mounted(self) -> bool:
        return bool(self.mount_point)

    def label(self) -> str:
        """For the confirmation dialog, e.g. 'EOS_DIGITAL (126.9 GB exFAT SD card)'."""
        size = f"{self.disk_bytes / 1000**3:,.1f} GB" if self.disk_bytes else phrase("unknown size")
        kind = message("{filesystem} SD card", filesystem=self.filesystem) if self.filesystem else phrase("SD card")
        return message("{name} ({size} {kind})", name=self.volume_name or phrase("Unnamed card"), size=size, kind=kind)

    def summary(self) -> str:
        state = "" if self.mounted else phrase(", not mounted")
        return message("{label} - {model}, {device}{state}", label=self.label(), model=self.model or phrase("unknown model"),
                       device=self.whole_id, state=state)

    def details(self) -> list[str]:
        size = message("{gb:,.2f} GB ({size:,} bytes)", gb=self.disk_bytes / 1000**3,
                       size=self.disk_bytes) if self.disk_bytes else phrase("unknown")
        return [
            message("Model: {model} ({bus})", model=self.model or phrase("unknown"),
                    bus=self.bus or phrase("unknown connection")),
            message("Size: {size}", size=size),
            message("Filesystem: {filesystem}", filesystem=self.filesystem or phrase("unknown")),
            message("Volume name: {name}", name=self.volume_name or phrase("(none)")),
            message("Mounted at: {mount}", mount=self.mount_point or phrase("(not mounted)")),
            message("Device: {device}, partition {partition}", device=self.whole_id, partition=self.partition_id),
        ]


# Device identity that does not depend on the card being mounted.
FIXED_IDENTITY = ("platform", "whole_id", "partition_id", "model", "bus", "disk_bytes", "partition_offset",
                  "partition_bytes", "location")


def same_card(chosen: Candidate, now: Candidate) -> bool:
    """Whether a fresh query still describes the chosen card.

    The whole-disk and partition identity must be equal. The filesystem and volume
    name are compared only when both queries report them, because an unmounted or
    half-written card may not report them at all."""
    if any(getattr(chosen, k) != getattr(now, k) for k in FIXED_IDENTITY):
        return False
    return all(not (getattr(chosen, k) and getattr(now, k)) or getattr(chosen, k) == getattr(now, k)
               for k in ("filesystem", "volume_name"))


def _size_ok(size) -> bool:
    return isinstance(size, int) and not isinstance(size, bool) and SD_MIN_BYTES <= size <= SD_MAX_BYTES


def _size_reason(size) -> str:
    if not isinstance(size, int):
        return "The size could not be read."
    return message("The size ({size:,.1f} GB) is outside the SD card range (2 GB to 2 TiB).", size=size / 1000 ** 3)


# -- macOS ----------------------------------------------------------------------


def boot_wholes_macos(root_info: dict) -> set[str]:
    """Whole disks that hold the running system (from ``diskutil info -plist /``)."""
    wholes = set()
    for value in [root_info.get("ParentWholeDisk"), root_info.get("APFSContainerReference")] + [
            s.get("APFSPhysicalStore") for s in root_info.get("APFSPhysicalStores") or [] if isinstance(s, dict)]:
        if isinstance(value, str):
            m = re.match(r"(disk[0-9]+)", value)
            if m:
                wholes.add(m.group(1))
    if not wholes:
        raise DeviceError("Could not tell which disk macOS is running from; refusing to list cards.")
    return wholes


def classify_macos(whole: dict, parts: list[dict], boot_wholes: set[str]) -> Candidate:
    """Accept only a camera-style SD card in an SD slot; otherwise list every reason."""
    r: list[str] = []
    wid = whole.get("DeviceIdentifier") if isinstance(whole.get("DeviceIdentifier"), str) else ""
    bus = whole.get("BusProtocol") or ""
    size = whole.get("Size")
    if not WHOLE_ID_RE.fullmatch(wid) or whole.get("WholeDisk") is not True:
        r.append("Not a whole disk.")
    if wid in boot_wholes:
        r.append("This is the disk macOS is running from.")
    if whole.get("VirtualOrPhysical") == "Virtual" or bus == "Disk Image":
        r.append("This is a disk image or virtual disk, not a card.")
    elif whole.get("VirtualOrPhysical") != "Physical":
        r.append("macOS does not report this as a physical disk.")
    if bus != "Secure Digital":
        r.append(message("Connected over {bus}, not an SD card slot. USB card readers are not offered because macOS "
            "cannot tell them from other USB drives.",
            bus=bus or phrase("an unknown bus")))
    if not (whole.get("Removable") is True and whole.get("RemovableMedia") is True and whole.get("Ejectable") is True):
        r.append("Not removable, ejectable media.")
    if whole.get("SystemImage") is not False or whole.get("OSInternalMedia") is True:
        r.append("macOS marks this as system media.")
    if whole.get("DeviceBlockSize") != 512:
        r.append(message("Sector size {sector_size} is not 512 bytes.", sector_size=whole.get('DeviceBlockSize')))
    if whole.get("Content") != "FDisk_partition_scheme":
        r.append(message("Not an MBR partition table ({kind}); format the card in the camera.",
            kind=whole.get('Content') or phrase("none")))
    if whole.get("WritableMedia") is not True:
        r.append("The card is write-protected (check its lock switch).")
    if not _size_ok(size):
        r.append(_size_reason(size))
    part: dict = parts[0] if len(parts) == 1 else {}
    if len(parts) != 1:
        r.append(message("The card has {count} partitions; a card formatted in the camera has one.", count=len(parts)))
    pid = part.get("DeviceIdentifier") if isinstance(part.get("DeviceIdentifier"), str) else ""
    fs_type, content = part.get("FilesystemType"), part.get("Content")
    filesystem = "FAT32" if fs_type == "msdos" and content in MAC_FAT32_CONTENT else "exFAT" if fs_type == "exfat" else None
    mount = part.get("MountPoint") or ""
    offset, psize = part.get("PartitionMapPartitionOffset"), part.get("Size")
    name = part.get("VolumeName") or ""
    if any((p.get("MountPoint") or "") == "/" for p in parts if isinstance(p, dict)):
        r.append("A partition is mounted at /, the running system.")
    if part:  # the remaining checks describe the single partition
        m = PART_ID_RE.fullmatch(pid)
        if m is None or f"disk{m.group(1)}" != wid or part.get("ParentWholeDisk") != wid \
                or part.get("WholeDisk") is not False:
            r.append("The partition does not belong to this disk.")
        # The mount state is an observation, not a requirement: an unmounted, half-written or
        # unmountable card must stay reachable for restore. Its boot sectors are checked later.
        if filesystem is None and (fs_type or mount):
            r.append(message("The partition is not FAT32 or exFAT ({filesystem}).",
                filesystem=part.get('FilesystemName') or fs_type or content or phrase("unknown")))
        if mount and mount != "/" and not mount.startswith("/Volumes/"):
            r.append(message("The partition is mounted at {mount}, which is a system location.", mount=mount))
        if part.get("DeviceBlockSize") != 512 or part.get("WritableMedia") is not True:
            r.append("The partition is not writable 512-byte-sector media.")
        if not (isinstance(offset, int) and isinstance(psize, int) and isinstance(size, int)
                and 0 < offset and offset + psize <= size):
            r.append("The partition position could not be read.")
    return Candidate("darwin", wid, pid, whole.get("MediaName") or whole.get("IORegistryEntryName") or "", bus,
                     size if isinstance(size, int) else None, offset if isinstance(offset, int) else None,
                     psize if isinstance(psize, int) else None, filesystem, name, mount,
                     whole.get("DeviceTreePath") or "", tuple(r))


def run_plist(argv: list[str]) -> dict:
    proc = subprocess.run(argv, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise DeviceError(f"{' '.join(argv)} failed: {proc.stderr.decode(errors='replace').strip()}")
    try:
        value = plistlib.loads(proc.stdout)
    except Exception as exc:  # malformed plist is a device-query failure, not a crash
        raise DeviceError(f"{' '.join(argv)} returned an unreadable plist: {exc}") from exc
    if not isinstance(value, dict):
        raise DeviceError(f"{' '.join(argv)} did not return a dictionary.")
    return value


def enumerate_macos(run: Callable[[list[str]], dict] = run_plist) -> list[Candidate]:
    boot = boot_wholes_macos(run([DISKUTIL, "info", "-plist", "/"]))
    listing = run([DISKUTIL, "list", "-plist", "physical"])
    found = []
    for entry in listing.get("AllDisksAndPartitions") or []:
        wid = entry.get("DeviceIdentifier") if isinstance(entry, dict) else None
        if not isinstance(wid, str) or not WHOLE_ID_RE.fullmatch(wid):
            continue
        whole = run([DISKUTIL, "info", "-plist", wid])
        parts = [run([DISKUTIL, "info", "-plist", p["DeviceIdentifier"]])
                 for p in entry.get("Partitions") or [] if isinstance(p, dict) and isinstance(p.get("DeviceIdentifier"), str)]
        found.append(classify_macos(whole, parts, boot))
    return found


# -- Linux (untested) -------------------------------------------------------------

LINUX_SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/home", "[SWAP]")


def _mounts(dev: dict) -> list[str]:
    value = dev.get("mountpoints", dev.get("mountpoint"))
    values = value if isinstance(value, list) else [value]
    return [v for v in values if isinstance(v, str) and v]


def classify_linux(dev: dict) -> Candidate:
    """One lsblk --json --bytes disk entry. Best effort, untested."""
    r: list[str] = []
    name = dev.get("name") or ""
    size = dev.get("size")
    size = int(size) if isinstance(size, str) and size.isdigit() else size
    children = dev.get("children") or []
    if dev.get("type") != "disk":
        r.append("Not a whole disk.")
    if not re.fullmatch(r"mmcblk[0-9]+", name):
        r.append(message("Not an SD card slot (connected over {bus}).", bus=dev.get('tran') or phrase("an unknown bus")))
    if not (dev.get("rm") in (True, "1", 1) or dev.get("hotplug") in (True, "1", 1)):
        r.append("Not removable media.")
    if dev.get("ro") in (True, "1", 1):
        r.append("The card is write-protected.")
    if str(dev.get("log-sec", "512")) != "512":
        r.append("Sector size is not 512 bytes.")
    if dev.get("pttype") != "dos":
        r.append("Not an MBR partition table.")
    if not _size_ok(size):
        r.append(_size_reason(size))
    every = _mounts(dev) + [m for c in children for m in _mounts(c)]
    if any(m in LINUX_SYSTEM_MOUNTS for m in every):
        r.append("It holds a system mount.")
    part = children[0] if len(children) == 1 else {}
    if len(children) != 1:
        r.append(message("The card has {count} partitions; a card formatted in the camera has one.", count=len(children)))
    fs = {"vfat": "FAT32", "exfat": "exFAT"}.get(part.get("fstype"))
    if children and fs is None:
        r.append("The partition is not FAT32 or exFAT.")
    mounts = _mounts(part)
    return Candidate("linux", name, part.get("name") or "", (dev.get("model") or "").strip(), dev.get("tran") or "mmc",
                     size if isinstance(size, int) else None, None, None, fs, part.get("label") or "",
                     mounts[0] if mounts else "", dev.get("path") or "", tuple(r))


def enumerate_linux(run_json: Callable[[list[str]], dict] | None = None) -> list[Candidate]:
    def default(argv):
        return json.loads(subprocess.run(argv, capture_output=True, check=True, timeout=60, text=True).stdout)
    data = (run_json or default)(["lsblk", "--json", "--bytes", "--output",
                                  "NAME,PATH,TYPE,SIZE,RM,HOTPLUG,RO,TRAN,MODEL,FSTYPE,LABEL,MOUNTPOINT,LOG-SEC,PTTYPE"])
    return [classify_linux(d) for d in data.get("blockdevices") or [] if isinstance(d, dict) and d.get("type") == "disk"]


# -- Windows (untested) -----------------------------------------------------------


def _as_list(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def classify_windows(disk: dict, partitions: list[dict], volumes: list[dict], system_drive: str) -> Candidate:
    """One Get-Disk entry with its Get-Partition / Get-Volume rows. Best effort, untested."""
    r: list[str] = []
    number = disk.get("Number")
    size = disk.get("Size")
    bus = str(disk.get("BusType") or "")
    if bus not in ("SD", "MMC"):
        r.append(message("Connected over {bus}, not an SD card slot.", bus=bus or phrase("an unknown bus")))
    if disk.get("IsSystem") or disk.get("IsBoot"):
        r.append("This is the disk Windows is running from.")
    if disk.get("IsReadOnly"):
        r.append("The card is write-protected.")
    if disk.get("PartitionStyle") != "MBR":
        r.append("Not an MBR partition table.")
    if disk.get("LogicalSectorSize") != 512:
        r.append("Sector size is not 512 bytes.")
    if not _size_ok(size):
        r.append(_size_reason(size))
    parts = [p for p in partitions if p.get("DiskNumber") == number]
    if len(parts) != 1:
        r.append(message("The card has {count} partitions; a card formatted in the camera has one.", count=len(parts)))
    part = parts[0] if len(parts) == 1 else {}
    letter = str(part.get("DriveLetter") or "").strip("\x00 ")
    volume = next((v for v in volumes if letter and str(v.get("DriveLetter") or "") == letter), {})
    fs = {"FAT32": "FAT32", "exFAT": "exFAT"}.get(volume.get("FileSystem"))
    if parts and fs is None:
        r.append("The partition is not FAT32 or exFAT.")
    if parts and not letter:
        r.append("The card has no drive letter.")
    if letter and system_drive.upper().startswith(letter.upper() + ":"):
        r.append("This is the Windows system drive.")
    return Candidate("win32", f"PhysicalDrive{number}", f"{letter}:" if letter else "", disk.get("FriendlyName") or "",
                     bus, size if isinstance(size, int) else None, part.get("Offset"), part.get("Size"), fs,
                     volume.get("FileSystemLabel") or "", f"{letter}:\\" if letter else "", "", tuple(r))


def enumerate_windows(run_json: Callable[[str], object] | None = None) -> list[Candidate]:
    def default(command):
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                             capture_output=True, check=True, timeout=60, text=True).stdout
        return json.loads(out) if out.strip() else None
    run = run_json or default
    disks = _as_list(run("Get-Disk | Select-Object Number,FriendlyName,BusType,Size,IsSystem,IsBoot,IsReadOnly,"
                         "PartitionStyle,LogicalSectorSize | ConvertTo-Json -Depth 3"))
    parts = _as_list(run("Get-Partition | Select-Object DiskNumber,PartitionNumber,Offset,Size,DriveLetter | ConvertTo-Json"))
    vols = _as_list(run("Get-Volume | Select-Object DriveLetter,FileSystem,FileSystemLabel | ConvertTo-Json"))
    drive = os.environ.get("SystemDrive", "C:")
    return [classify_windows(d, parts, vols, drive) for d in disks if isinstance(d, dict)]


def enumerate_candidates(platform: str | None = None) -> list[Candidate]:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return enumerate_macos()
    if platform.startswith("linux"):
        return enumerate_linux()
    if platform.startswith("win"):
        return enumerate_windows()
    return []


def prepare_supported(platform: str | None = None) -> bool:
    return (sys.platform if platform is None else platform) == "darwin"


# -- macOS diskutil actions and raw access -----------------------------------------


def _completed(run, argv: list[str], timeout: int, **kwargs) -> subprocess.CompletedProcess:
    """Run a command; a timeout or start failure becomes a failed result instead of an exception."""
    try:
        return run(argv, capture_output=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "" if kwargs.get("text") else b"",
                                           f"{' '.join(argv)} did not finish within {timeout} s."
                                           if kwargs.get("text") else b"timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "" if kwargs.get("text") else b"",
                                           str(exc) if kwargs.get("text") else str(exc).encode())


def verification_start_error(output: str) -> bool:
    """Recognize diskutil's unrecognized-filesystem status in a verification diagnostic."""
    for line in output.splitlines():
        line = line.strip()
        if (line.startswith("Error starting file system verification for ")
                and line.endswith("(-69846)")) or line.startswith("Error: -69846:"):
            return True
    return False


class MacOps:
    """diskutil as the person; raw sector access through authopen (no code runs as root)."""

    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 raw: rawio.RawAccess | None = None):
        self.run = run
        self.raw = raw or rawio.RawAccess()

    def _diskutil(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return _completed(self.run, [DISKUTIL, *args], timeout, text=True)

    def _plist(self, *args: str) -> dict:
        proc = _completed(self.run, [DISKUTIL, *args], 60)
        if proc.returncode != 0:
            raise DeviceError(f"diskutil {' '.join(args)} failed.")
        try:
            value = plistlib.loads(proc.stdout)
        except Exception as exc:
            raise DeviceError(f"diskutil {' '.join(args)} returned an unreadable plist.") from exc
        if not isinstance(value, dict):
            raise DeviceError(f"diskutil {' '.join(args)} returned no dictionary.")
        return value

    def refresh(self, cand: Candidate) -> Candidate:
        """Query the card again by its whole-disk and partition identity (mounted or not);
        refuse if it is no longer exactly the card the person chose."""
        boot = boot_wholes_macos(self._plist("info", "-plist", "/"))
        listing = self._plist("list", "-plist", cand.whole_id)
        entries = [e for e in listing.get("AllDisksAndPartitions") or []
                   if isinstance(e, dict) and e.get("DeviceIdentifier") == cand.whole_id]
        if len(entries) != 1:
            raise DeviceError(f"{cand.whole_id} is gone. Was the card removed? Search for cards again.")
        whole = self._plist("info", "-plist", cand.whole_id)
        parts = [self._plist("info", "-plist", p["DeviceIdentifier"]) for p in entries[0].get("Partitions") or []
                 if isinstance(p, dict) and isinstance(p.get("DeviceIdentifier"), str)]
        now = classify_macos(whole, parts, boot)
        if not now.accepted:
            raise DeviceError("The card is no longer acceptable: " + " ".join(now.refusals))
        if not same_card(cand, now):
            raise DeviceError("The card changed since you chose it (another card, or it was reinserted). "
                              "Search for cards again.")
        return now

    def unmount(self, cand: Candidate) -> None:
        proc = self._diskutil("unmount", cand.partition_id)
        if proc.returncode != 0:
            raise DeviceError(f"Could not unmount {cand.partition_id}: {(proc.stdout + proc.stderr).strip()} "
                              "Close any files or windows using the card and try again.")

    def mount(self, cand: Candidate) -> tuple[bool, str]:
        proc = self._diskutil("mount", cand.partition_id)
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()

    def verify(self, cand: Candidate) -> tuple[bool, str]:
        proc = self._diskutil("verifyVolume", cand.partition_id, timeout=1800)
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()

    def _device(self, cand: Candidate) -> str:
        if not WHOLE_ID_RE.fullmatch(cand.whole_id) or not isinstance(cand.partition_offset, int):
            raise DeviceError(f"{cand.whole_id} is not a whole-disk device this tool opens.")
        return cand.raw_disk

    def raw_read(self, cand: Candidate, length: int) -> tuple[bytes, bytes]:
        """One password window (read-only): the MBR and the partition's first ``length`` bytes."""
        return self.raw.read(self._device(cand), cand.partition_offset, length, confirm=lambda: self.refresh(cand))

    def raw_write(self, cand: Candidate, ranges: list[tuple[int, int]], expect_sha256: str, after: bytes, *,
                  expect_mbr_sha256: str, expect_area_sha256: str) -> dict:
        """One password window (read/write): the planned partition sectors, with all checks."""
        return self.raw.write(self._device(cand), cand.partition_offset, ranges, after, expect_sha256=expect_sha256,
                              expect_mbr_sha256=expect_mbr_sha256, expect_area_sha256=expect_area_sha256,
                              confirm=lambda: self.refresh(cand))
