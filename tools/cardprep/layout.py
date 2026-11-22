"""Parse a camera-formatted card's MBR and boot area, and plan Canon boot markers.

Pure functions on bytes: nothing here opens a file or a device.

The "boot area" is the first 24 sectors (12,288 bytes) of the card's single
partition. That covers the FAT32 boot sector, FSInfo and backup boot sector, and
both exFAT boot regions (main sectors 0..11, backup sectors 12..23).

Markers (R5 2.2.1 bootloader, see docs/sdxc.md and docs/usage.md):

- FAT32: ``EOS_DEVELOP`` at byte 71 (0x47) and ``BOOTDISK`` at byte 92 (0x5C) of
  the primary boot sector. The backup boot sector is left unchanged, as on the
  16 GB lab card that booted.
- exFAT: ``BOOTDISK`` at byte 122 and ``EOS_DEVELOP`` at byte 130 of both the
  main and the backup boot region, and each region's checksum sector (sector 11
  of the region) recomputed per the Microsoft exFAT specification, section 3.4.

Only 512-byte sectors are handled. Anything unexpected is refused with a
CardPrepError whose message says why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import struct

from messages import message, phrase

SECTOR = 512
AREA_SECTORS = 24
AREA_BYTES = AREA_SECTORS * SECTOR
MBR_BYTES = SECTOR

FAT32 = "FAT32"
EXFAT = "exFAT"

BOOTDISK = b"BOOTDISK"
EOS_DEVELOP = b"EOS_DEVELOP"
SCRIPT = b"SCRIPT"
SCRIPT_OFFSET = 0x1F0  # byte 496 of the FAT32 primary boot sector
SDHC_MAX_SECTORS = 0x4000000  # 67,108,864 sectors (~34.36 GB / 32 GiB); >= uses LBA-1 (untested)

FAT32_MARKERS = (("EOS_DEVELOP", 0x47, EOS_DEVELOP), ("BOOTDISK", 0x5C, BOOTDISK))
EXFAT_MARKERS = (("BOOTDISK", 122, BOOTDISK), ("EOS_DEVELOP", 130, EOS_DEVELOP))

MBR_TYPES = {FAT32: frozenset({0x0B, 0x0C}), EXFAT: frozenset({0x07})}

EXFAT_REGION_SECTORS = 12
EXFAT_REGION_BYTES = EXFAT_REGION_SECTORS * SECTOR
EXFAT_CHECKSUMMED_BYTES = 11 * SECTOR
EXFAT_CHECKSUM_OFFSET = 11 * SECTOR
# VolumeFlags (106, 107) and PercentInUse (112) are excluded from the checksum
# and may legitimately differ between the regions and change on every mount.
EXFAT_DYNAMIC = frozenset({106, 107, 112})
EXFAT_BOOTCODE_FILL = 0xF4  # what the camera (and macOS newfs_exfat) put in BootCode
EXFAT_REGIONS = (("backup", EXFAT_REGION_BYTES), ("main", 0))  # write order: backup first

FAT32_MIN_CLUSTERS = 65525
FAT32_MAX_CLUSTERS = 0x0FFFFFF5 - 2
EXFAT_MAX_CLUSTERS = 0xFFFFFFF5 - 2


class CardPrepError(Exception):
    """A refusal: the card or file is not something this tool will change."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(ok: bool, code: str, message: str) -> None:
    if not ok:
        raise CardPrepError(code, message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- MBR ----------------------------------------------------------------------


@dataclass(frozen=True)
class MbrFacts:
    slot: int
    status: int
    type: int
    start_lba: int
    sectors: int
    sha256: str

    @property
    def start_bytes(self) -> int:
        return self.start_lba * SECTOR

    @property
    def size_bytes(self) -> int:
        return self.sectors * SECTOR


def parse_mbr(mbr: bytes, *, disk_bytes: int) -> MbrFacts:
    """The card's partition table: exactly one primary partition inside the disk."""
    _require(len(mbr) == MBR_BYTES, "mbr_length", f"The MBR must be {MBR_BYTES} bytes, got {len(mbr)}.")
    _require(mbr[510:512] == b"\x55\xaa", "mbr_signature",
             "The card has no MBR partition table (signature 55AA missing at byte 510).")
    _require(mbr[3:11] != b"EXFAT   " and mbr[82:90] != b"FAT32   ", "no_partition_table",
             "The card has a filesystem but no partition table. A card formatted in the camera has one; "
             "format the card in the camera first.")
    entries = [(i, mbr[446 + 16 * i: 462 + 16 * i]) for i in range(4)]
    used = [(i, e) for i, e in entries if e != bytes(16)]
    _require(len(used) == 1, "mbr_partition_count",
             f"Expected exactly one partition on the card, found {len(used)}.")
    slot, entry = used[0]
    status, ptype = entry[0], entry[4]
    start, count = struct.unpack_from("<II", entry, 8)
    _require(ptype != 0xEE, "gpt", "The card uses a GPT partition table; a card formatted in the camera uses MBR.")
    _require(status in (0x00, 0x80), "mbr_status", f"MBR partition status 0x{status:02x} is invalid.")
    _require(start >= 1 and count >= 1, "mbr_range", "The MBR partition has no valid start or size.")
    _require((start + count) * SECTOR <= disk_bytes, "mbr_beyond_disk",
             f"The MBR partition ends at byte {(start + count) * SECTOR:,}, beyond the card's {disk_bytes:,} bytes.")
    return MbrFacts(slot, status, ptype, start, count, sha256(mbr))


# -- exFAT checksum -----------------------------------------------------------


def exfat_checksum_step(checksum: int, value: int) -> int:
    """Rotate the 32-bit checksum right by one, then add the byte, modulo 2**32.

    (The upstream edit_card_flags.py wrote ``(c << 31) | (c >> 1) + value``, where
    ``+`` binds before ``|``; that differs when the addition carries into bit 31.)"""
    return ((((checksum & 1) << 31) | (checksum >> 1)) + value) & 0xFFFFFFFF


def exfat_boot_checksum(region: bytes) -> int:
    """Microsoft exFAT spec 3.4: the step above over sectors 0..10 of a boot region,
    skipping VolumeFlags (bytes 106, 107) and PercentInUse (byte 112)."""
    _require(len(region) >= EXFAT_CHECKSUMMED_BYTES, "exfat_length", "Need 11 sectors for the exFAT checksum.")
    checksum = 0
    for index in range(EXFAT_CHECKSUMMED_BYTES):
        if index not in EXFAT_DYNAMIC:
            checksum = exfat_checksum_step(checksum, region[index])
    return checksum


def exfat_checksum_sector(checksum: int) -> bytes:
    return struct.pack("<I", checksum) * (SECTOR // 4)


# -- volume facts ---------------------------------------------------------------


@dataclass(frozen=True)
class Marker:
    name: str
    offset: int  # partition-relative byte offset
    value: bytes
    present: bool


@dataclass(frozen=True)
class VolumeFacts:
    filesystem: str
    mbr: MbrFacts
    serial: int
    volume_sectors: int
    cluster_bytes: int
    markers: tuple[Marker, ...]
    details: dict = field(default_factory=dict)

    @property
    def markers_present(self) -> bool:
        return all(m.present for m in self.markers)

    @property
    def script_present(self) -> bool:
        return bool(self.details.get("canon_basic_script_marker"))

    @property
    def serial_hex(self) -> str:
        return f"{self.serial:08X}"

    def identity(self) -> dict:
        """Values that name this exact volume; they must not change between steps."""
        return {"filesystem": self.filesystem, "volume_serial": self.serial_hex,
                "partition_start_lba": self.mbr.start_lba, "partition_sectors": self.mbr.sectors,
                "volume_sectors": self.volume_sectors}


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def detect_filesystem(sector0: bytes) -> str:
    if sector0[3:11] == b"EXFAT   ":
        return EXFAT
    if sector0[82:90] == b"FAT32   ":
        return FAT32
    if sector0[54:62] in (b"FAT16   ", b"FAT12   ", b"FAT     "):
        raise CardPrepError("fat16", "The card is FAT16/FAT12. Format it in the camera (it uses FAT32 or exFAT).")
    if sector0[3:11] == b"NTFS    ":
        raise CardPrepError("ntfs", "The card is NTFS. Format it in the camera first.")
    raise CardPrepError("unknown_filesystem", "The card's partition is not FAT32 or exFAT. Format it in the camera first.")


def _fat32_facts(area: bytes, mbr: MbrFacts) -> VolumeFacts:
    s = area[:SECTOR]
    _require(s[510:512] == b"\x55\xaa", "fat32_signature", "FAT32 boot sector signature 55AA is missing.")
    _require((s[0] == 0xEB and s[2] == 0x90) or s[0] == 0xE9, "fat32_jump", "FAT32 boot sector has no jump instruction.")
    bps, spc, reserved, fats = _u16(s, 11), s[13], _u16(s, 14), s[16]
    _require(bps == SECTOR, "sector_size",
             f"The filesystem uses {bps}-byte sectors; only 512-byte sectors are supported.")
    _require(spc in (1, 2, 4, 8, 16, 32, 64, 128), "fat32_geometry", f"Invalid sectors per cluster ({spc}).")
    _require(reserved >= 1 and fats in (1, 2), "fat32_geometry", "Invalid reserved-sector or FAT count.")
    _require(_u16(s, 17) == 0 and _u16(s, 19) == 0 and _u16(s, 22) == 0, "fat32_geometry",
             "The boot sector carries FAT12/16 fields; it is not a valid FAT32 volume.")
    total, fat_size, hidden = _u32(s, 32), _u32(s, 36), _u32(s, 28)
    _require(_u16(s, 42) == 0, "fat32_version", "Unsupported FAT32 version.")
    _require(s[66] == 0x29, "fat32_bootsig",
             "The FAT32 boot sector has no extended boot signature (0x29), so it has no volume label field.")
    first_data = reserved + fats * fat_size
    _require(fat_size > 0 and total > first_data, "fat32_geometry", "The FAT32 volume has no valid data region.")
    clusters = (total - first_data) // spc
    _require(FAT32_MIN_CLUSTERS <= clusters <= FAT32_MAX_CLUSTERS, "fat32_geometry",
             f"{clusters:,} clusters is outside the FAT32 range.")
    _require(fat_size * SECTOR // 4 >= clusters + 2, "fat32_geometry", "The FAT is too small for the data region.")
    _require(2 <= _u32(s, 44) < clusters + 2, "fat32_geometry", "The root directory cluster is outside the volume.")
    fsinfo, backup = _u16(s, 48), _u16(s, 50)
    _require(1 <= fsinfo < reserved, "fat32_geometry", "The FSInfo sector is outside the reserved region.")
    _require(1 <= backup < min(reserved, AREA_SECTORS) and backup != fsinfo, "fat32_backup",
             f"The backup boot sector ({backup}) is missing or not where this tool expects it.")
    copy = area[backup * SECTOR:(backup + 1) * SECTOR]
    _require(copy[11:64] == s[11:64] and copy[510:512] == b"\x55\xaa", "fat32_backup",
             f"The backup boot sector (sector {backup}) does not match the primary one.")
    _require(hidden == mbr.start_lba, "geometry_mismatch",
             f"The boot sector says the partition starts at sector {hidden:,}, the MBR says {mbr.start_lba:,}.")
    _require(total <= mbr.sectors, "geometry_mismatch",
             f"The volume ({total:,} sectors) is larger than its partition ({mbr.sectors:,} sectors).")

    markers = []
    for name, offset, value in FAT32_MARKERS:
        observed = s[offset:offset + len(value)]
        present = observed == value
        if not present:
            if name == "EOS_DEVELOP":
                _require(all(0x20 <= c <= 0x7E for c in observed), "unexpected_marker_bytes",
                         "The FAT32 volume label field (byte 71) holds unexpected bytes.")
            else:
                _require(observed == bytes(len(value)), "unexpected_marker_bytes",
                         "The FAT32 boot sector has unexpected bytes where BOOTDISK goes (byte 92). "
                         "A card formatted in the camera has zeros there; a computer format puts boot code "
                         "there. Back up your photos, format the card in the camera, then try again.")
        markers.append(Marker(name, offset, observed, present))
    return VolumeFacts(FAT32, mbr, _u32(s, 67), total, bps * spc, tuple(markers), {
        "oem": s[3:11].decode("latin-1"), "reserved_sectors": reserved, "fats": fats, "fat_sectors": fat_size,
        "clusters": clusters, "fsinfo_sector": fsinfo, "backup_boot_sector": backup,
        "label_field": s[71:82].decode("latin-1"), "canon_basic_script_marker": s[496:502] == b"SCRIPT",
    })


def _exfat_region(region: bytes, name: str, base: int, mbr: MbrFacts) -> dict:
    where = f"exFAT {name} boot region"
    _require(region[0:3] == b"\xeb\x76\x90", "exfat_jump", f"{where}: JumpBoot is not EB 76 90.")
    _require(region[3:11] == b"EXFAT   ", "exfat_name", f"{where}: FileSystemName is not EXFAT.")
    _require(region[11:64] == bytes(53), "exfat_mustbezero", f"{where}: MustBeZero bytes are not zero.")
    _require(region[510:512] == b"\x55\xaa", "exfat_signature", f"{where}: boot signature 55AA is missing.")
    shift, spc_shift, fats = region[108], region[109], region[110]
    _require(shift == 9, "sector_size",
             f"{where}: the filesystem uses {1 << shift if shift < 32 else '?'}-byte sectors; only 512 is supported.")
    _require(spc_shift <= 25 - shift, "exfat_geometry", f"{where}: invalid SectorsPerClusterShift {spc_shift}.")
    _require(fats in (1, 2), "exfat_geometry", f"{where}: NumberOfFats must be 1 or 2.")
    revision = _u16(region, 104)
    _require(revision >> 8 == 1, "exfat_revision", f"{where}: unsupported revision 0x{revision:04x}.")
    part_offset, volume_length = struct.unpack_from("<QQ", region, 64)
    fat_offset, fat_length, heap, clusters, root, serial = struct.unpack_from("<IIIIII", region, 80)
    spc = 1 << spc_shift
    _require(volume_length >= 2048, "exfat_geometry", f"{where}: VolumeLength is below 1 MiB.")
    _require(fat_offset >= AREA_SECTORS and fat_length >= 1, "exfat_geometry", f"{where}: FAT overlaps the boot regions.")
    _require(fat_offset + fat_length * fats <= heap < volume_length, "exfat_geometry",
             f"{where}: the FAT and cluster heap overlap or lie outside the volume.")
    _require(1 <= clusters <= EXFAT_MAX_CLUSTERS and heap + clusters * spc <= volume_length, "exfat_geometry",
             f"{where}: ClusterCount does not fit the volume.")
    _require(fat_length * SECTOR // 4 >= clusters + 2, "exfat_geometry", f"{where}: the FAT is too small.")
    _require(2 <= root <= clusters + 1, "exfat_geometry", f"{where}: the root directory cluster is outside the heap.")
    _require(part_offset == mbr.start_lba, "geometry_mismatch",
             f"{where}: PartitionOffset {part_offset:,} differs from the MBR partition start {mbr.start_lba:,}.")
    _require(volume_length <= mbr.sectors, "geometry_mismatch",
             f"{where}: the volume ({volume_length:,} sectors) is larger than its partition ({mbr.sectors:,}).")
    for sector in range(1, 9):
        _require(region[sector * SECTOR + 508:(sector + 1) * SECTOR] == b"\x00\x00\x55\xaa", "exfat_extended",
                 f"{where}: extended boot sector {sector} has no 000055AA signature.")
    computed = exfat_boot_checksum(region)
    stored = region[EXFAT_CHECKSUM_OFFSET:EXFAT_REGION_BYTES]
    _require(stored == exfat_checksum_sector(computed), "exfat_checksum",
             f"{where}: the boot checksum sector does not match (computed {computed:08X}). "
             "The card may be damaged; check it in the camera or with Disk Utility First Aid.")
    markers = []
    for marker, offset, value in EXFAT_MARKERS:
        observed = region[offset:offset + len(value)]
        present = observed == value
        _require(present or observed == bytes([EXFAT_BOOTCODE_FILL]) * len(value), "unexpected_marker_bytes",
                 f"{where}: unexpected boot code where {marker} goes (byte {offset}). "
                 "A card formatted in the camera has F4 bytes there.")
        markers.append(Marker(marker, base + offset, observed, present))
    return {
        "geometry": (part_offset, volume_length, fat_offset, fat_length, heap, clusters, root, serial,
                     revision, shift, spc_shift, fats, region[111]),
        "serial": serial, "volume_length": volume_length, "cluster_bytes": SECTOR * spc,
        "volume_flags": _u16(region, 106), "percent_in_use": region[112],
        "checksum": computed, "markers": markers,
    }


def _exfat_facts(area: bytes, mbr: MbrFacts) -> VolumeFacts:
    regions = {name: _exfat_region(area[base:base + EXFAT_REGION_BYTES], name, base, mbr)
               for name, base in (("main", 0), ("backup", EXFAT_REGION_BYTES))}
    _require(regions["main"]["geometry"] == regions["backup"]["geometry"], "exfat_backup",
             "The exFAT main and backup boot regions describe different volumes.")
    main = regions["main"]
    return VolumeFacts(EXFAT, mbr, main["serial"], main["volume_length"], main["cluster_bytes"],
                       tuple(regions["main"]["markers"] + regions["backup"]["markers"]), {
                           "checksums": {n: f"{r['checksum']:08X}" for n, r in regions.items()},
                           "volume_flags": {n: r["volume_flags"] for n, r in regions.items()},
                           "percent_in_use": {n: r["percent_in_use"] for n, r in regions.items()},
                       })


def check_partition_start(mbr: bytes, area: bytes, *, disk_bytes: int, reported_bytes: int | None,
                          reported_size: int | None) -> int:
    """The partition start in bytes, only if three sources agree: the MBR entry's LBA x 512,
    the operating system's report, and the boot sector's own field (exFAT PartitionOffset
    at byte 64, FAT32 HiddenSectors at byte 28, both in sectors)."""
    facts = parse_mbr(mbr, disk_bytes=disk_bytes)
    s = area[:SECTOR]
    if s[3:11] == b"EXFAT   ":
        field, name = struct.unpack_from("<Q", s, 64)[0], "exFAT PartitionOffset"
    elif s[82:90] == b"FAT32   ":
        field, name = _u32(s, 28), "FAT32 HiddenSectors"
    else:
        raise CardPrepError("geometry_mismatch", "The partition's boot sector is not FAT32 or exFAT, so its "
                            "position cannot be confirmed. Format the card in the camera first.")
    _require(reported_bytes == facts.start_bytes and reported_size == facts.size_bytes, "geometry_mismatch",
             f"The MBR partition ({facts.start_bytes:,}+{facts.size_bytes:,} bytes) does not match the partition "
             f"macOS reports ({reported_bytes}+{reported_size}).")
    _require(field * SECTOR == facts.start_bytes, "geometry_mismatch",
             f"The boot sector's {name} ({field:,} sectors) does not match the MBR partition start "
             f"({facts.start_lba:,} sectors).")
    return facts.start_bytes


def inspect(mbr: bytes, area: bytes, *, disk_bytes: int) -> VolumeFacts:
    """Validate the MBR and boot area of a camera-formatted card."""
    _require(len(area) == AREA_BYTES, "area_length", f"The boot area must be {AREA_BYTES} bytes, got {len(area)}.")
    facts = parse_mbr(mbr, disk_bytes=disk_bytes)
    filesystem = detect_filesystem(area[:SECTOR])
    _require(facts.type in MBR_TYPES[filesystem], "geometry_mismatch",
             f"The MBR partition type 0x{facts.type:02x} does not match a {filesystem} volume.")
    return _fat32_facts(area, facts) if filesystem == FAT32 else _exfat_facts(area, facts)


# -- plans ----------------------------------------------------------------------


@dataclass(frozen=True)
class ByteRange:
    offset: int
    before: bytes
    after: bytes
    label: str

    @property
    def end(self) -> int:
        return self.offset + len(self.after)

    def to_dict(self) -> dict:
        return {"start": self.offset, "end": self.end, "length": len(self.after), "label": self.label,
                "before_hex": self.before.hex(), "after_hex": self.after.hex()}


@dataclass(frozen=True)
class SectorWrite:
    offset: int
    before: bytes
    after: bytes

    @property
    def sector(self) -> int:
        return self.offset // SECTOR


@dataclass(frozen=True)
class Plan:
    kind: str  # "mark" or "restore"
    volume: VolumeFacts
    before_area: bytes
    after_area: bytes
    changes: tuple[ByteRange, ...]
    writes: tuple[SectorWrite, ...]  # in write order
    notes: tuple[str, ...] = ()

    @property
    def filesystem(self) -> str:
        return self.volume.filesystem

    @property
    def writes_needed(self) -> bool:
        return bool(self.writes)

    @property
    def expected_before(self) -> bytes:
        return b"".join(w.before for w in self.writes)

    @property
    def after_bytes(self) -> bytes:
        return b"".join(w.after for w in self.writes)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "filesystem": self.filesystem, "identity": self.volume.identity(),
            "before_area_sha256": sha256(self.before_area), "after_area_sha256": sha256(self.after_area),
            "writes_needed": self.writes_needed,
            "sectors_in_write_order": [w.sector for w in self.writes],
            "expected_before_sha256": sha256(self.expected_before), "after_bytes_sha256": sha256(self.after_bytes),
            "changed_ranges": [c.to_dict() for c in self.changes], "notes": list(self.notes),
        }


def _diff(before: bytes, after: bytes, labels) -> tuple[ByteRange, ...]:
    """Contiguous changed byte ranges, never spanning a sector boundary."""
    ranges, i = [], 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        start, limit = i, (i // SECTOR + 1) * SECTOR
        while i < limit and before[i] != after[i]:
            i += 1
        ranges.append(ByteRange(start, before[start:i], after[start:i], labels(start)))
    return tuple(ranges)


def _sector_writes(before: bytes, after: bytes, order) -> tuple[SectorWrite, ...]:
    changed = [s for s in range(AREA_SECTORS) if before[s * SECTOR:(s + 1) * SECTOR] != after[s * SECTOR:(s + 1) * SECTOR]]
    changed.sort(key=order)
    return tuple(SectorWrite(s * SECTOR, before[s * SECTOR:(s + 1) * SECTOR], after[s * SECTOR:(s + 1) * SECTOR])
                 for s in changed)


def _exfat_order(current: bytes):
    """Write order for exFAT: a region whose checksum is currently invalid first (so the
    other, valid region stays valid until it is repaired), otherwise the backup region
    first; within a region the marker sector before the checksum sector. Each step then
    leaves at least one region valid."""
    invalid = {base for _, base in EXFAT_REGIONS
               if current[base + EXFAT_CHECKSUM_OFFSET:base + EXFAT_REGION_BYTES]
               != exfat_checksum_sector(exfat_boot_checksum(current[base:base + EXFAT_REGION_BYTES]))}

    def key(sector: int) -> tuple[int, int, int]:
        base = EXFAT_REGION_BYTES if sector >= EXFAT_REGION_SECTORS else 0
        return (0 if base in invalid else 1, 0 if base else 1, sector)
    return key


def _label(filesystem: str):
    def label(offset: int) -> str:
        if filesystem == FAT32:
            for name, start, value in FAT32_MARKERS:
                if start <= offset < start + len(value):
                    return f"{name} marker, primary boot sector byte {start}"
            if SCRIPT_OFFSET <= offset < SCRIPT_OFFSET + len(SCRIPT):
                return f"SCRIPT marker, primary boot sector byte {SCRIPT_OFFSET}"
            return f"sector {offset // SECTOR} byte {offset % SECTOR}"
        region = "backup" if offset >= EXFAT_REGION_BYTES else "main"
        local = offset % EXFAT_REGION_BYTES
        if local >= EXFAT_CHECKSUM_OFFSET:
            return f"{region} boot checksum sector (partition sector {offset // SECTOR})"
        if 122 <= local < 141:
            return f"{region} boot region BOOTDISK/EOS_DEVELOP markers (region bytes 122..140)"
        return f"{region} boot region byte {local}"
    return label


def _mark(area: bytes, filesystem: str) -> bytes:
    after = bytearray(area)
    if filesystem == FAT32:
        for _, offset, value in FAT32_MARKERS:
            after[offset:offset + len(value)] = value
    else:
        for _, base in EXFAT_REGIONS:
            for _, offset, value in EXFAT_MARKERS:
                after[base + offset:base + offset + len(value)] = value
            region = bytes(after[base:base + EXFAT_REGION_BYTES])
            after[base + EXFAT_CHECKSUM_OFFSET:base + EXFAT_REGION_BYTES] = exfat_checksum_sector(exfat_boot_checksum(region))
    return bytes(after)


def plan_markers(mbr: bytes, area: bytes, *, disk_bytes: int) -> Plan:
    """The exact writes that add the boot markers; none if they are already there."""
    volume = inspect(mbr, area, disk_bytes=disk_bytes)
    after = area if volume.markers_present else _mark(area, volume.filesystem)
    order = _exfat_order(area) if volume.filesystem == EXFAT else (lambda s: s)
    writes = _sector_writes(area, after, order)
    check = inspect(mbr, after, disk_bytes=disk_bytes)
    _require(check.markers_present and check.identity() == volume.identity(), "plan_self_check",
             "Internal check failed: the planned boot area does not validate.")
    notes = []
    if volume.filesystem == FAT32:
        notes.append(f"The backup boot sector (sector {volume.details['backup_boot_sector']}) is left unchanged, "
                     "as on the lab card that booted.")
        if volume.details["canon_basic_script_marker"]:
            notes.append("A Canon Basic SCRIPT marker is present at byte 496; it is left unchanged.")
    return Plan("mark", volume, area, after, _diff(area, after, _label(volume.filesystem)), writes, tuple(notes))


def plan_script(mbr: bytes, area: bytes, *, disk_bytes: int) -> Plan:
    """The exact write that adds the Canon Basic SCRIPT marker on a FAT32 SDHC card; none if already there."""
    volume = inspect(mbr, area, disk_bytes=disk_bytes)
    _require(volume.filesystem == FAT32, "script_requires_fat32",
             "Canon Basic card preparation requires a camera-formatted FAT32 SDHC card (up to 32 GB). "
             "exFAT / SDXC cards use a different SCRIPT offset that has not been tested on hardware yet.")
    _require(disk_bytes < SDHC_MAX_SECTORS * SECTOR and volume.mbr.start_lba + volume.mbr.sectors < SDHC_MAX_SECTORS,
             "script_sdxc_untested",
             "This card has 0x4000000 or more sectors (SDXC). On SDXC cards the R5 checks the sector before the "
             "partition for SCRIPT, which has not been tested on hardware yet. Use a camera-formatted FAT32 SDHC card "
             "(up to 32 GB).")
    _require(not any(m.present for m in volume.markers), "native_markers_present",
             "This card carries native EOS_DEVELOP / BOOTDISK boot markers. Use a card without native boot markers "
             "(format it in the camera or use Step 7 Undo first) so a BootDisk-enabled camera starts normal Canon "
             "firmware for Canon Basic.")
    observed = area[SCRIPT_OFFSET:SCRIPT_OFFSET + len(SCRIPT)]
    _require(observed in (SCRIPT, bytes(len(SCRIPT))), "unexpected_marker_bytes",
             "The FAT32 boot sector has unexpected bytes where SCRIPT goes (byte 496). "
             "A card formatted in the camera has zeros there.")
    if volume.script_present:
        after = area
    else:
        buf = bytearray(area)
        buf[SCRIPT_OFFSET:SCRIPT_OFFSET + len(SCRIPT)] = SCRIPT
        after = bytes(buf)
    writes = _sector_writes(area, after, lambda s: s)
    check = inspect(mbr, after, disk_bytes=disk_bytes)
    _require(check.script_present and not any(m.present for m in check.markers) and check.identity() == volume.identity(),
             "plan_self_check", "Internal check failed: the planned boot area does not validate.")
    notes = (f"The backup boot sector (sector {volume.details['backup_boot_sector']}) is left unchanged, "
             "as on the lab card.",)
    return Plan("script", volume, area, after, _diff(area, after, _label(volume.filesystem)), writes, notes)


def _owned(filesystem: str) -> list[tuple[int, int]]:
    if filesystem == FAT32:
        return [(offset, offset + len(value)) for _, offset, value in FAT32_MARKERS] + [
            (SCRIPT_OFFSET, SCRIPT_OFFSET + len(SCRIPT))
        ]
    owned = []
    for _, base in sorted(EXFAT_REGIONS, key=lambda r: r[1]):
        owned.append((base + 122, base + 141))
        owned.append((base + EXFAT_CHECKSUM_OFFSET, base + EXFAT_REGION_BYTES))
    return owned


def _protected(filesystem: str) -> list[int]:
    """Bytes that must be unchanged since the backup (outside the marker ranges)."""
    owned = {i for start, end in _owned(filesystem) for i in range(start, end)}
    if filesystem == FAT32:  # FSInfo and other reserved sectors change with normal use
        span = range(SECTOR)
        dynamic: set[int] = set()
    else:
        span = range(AREA_BYTES)
        dynamic = {base + o for _, base in EXFAT_REGIONS for o in EXFAT_DYNAMIC}
    return [i for i in span if i not in owned and i not in dynamic]


def plan_restore(original_mbr: bytes, original_area: bytes, current_mbr: bytes, current_area: bytes,
                 *, disk_bytes: int) -> Plan:
    """Put the backed-up marker bytes (and exFAT checksum sectors) back.

    The current card must be the backed-up volume: same MBR, and every byte outside
    the marker ranges equal to the backup (exFAT VolumeFlags and PercentInUse may
    differ and are kept as they are now). Each marker range must hold either the
    original bytes or the prepared bytes; anything else is refused."""
    original = inspect(original_mbr, original_area, disk_bytes=disk_bytes)
    _require(len(current_area) == AREA_BYTES, "area_length", "The current boot area has the wrong size.")
    _require(current_mbr == original_mbr, "restore_identity",
             "The card's MBR differs from the backup: this is not the backed-up card, or it was reformatted.")
    _require(detect_filesystem(current_area[:SECTOR]) == original.filesystem, "restore_identity",
             "The card's filesystem differs from the backup.")
    changed = [i for i in _protected(original.filesystem) if current_area[i] != original_area[i]]
    _require(not changed, "restore_identity",
             f"{len(changed)} boot-area byte(s) outside the markers differ from the backup (first at byte {changed[0] if changed else 0}). "
             "This is not the backed-up card, or it was reformatted since.")
    prepared = _mark(original_area, original.filesystem) if not original.markers_present else original_area
    after = bytearray(current_area)
    for start, end in _owned(original.filesystem):
        now = current_area[start:end]
        if original.filesystem == FAT32 and (start, end) == (SCRIPT_OFFSET, SCRIPT_OFFSET + len(SCRIPT)):
            allowed = (original_area[start:end], SCRIPT) if original_area[start:end] in (bytes(len(SCRIPT)), SCRIPT) \
                else (original_area[start:end],)
        else:
            allowed = (original_area[start:end], prepared[start:end])
        _require(now in allowed, "restore_unknown_bytes",
                 f"Boot-area bytes {start}..{end - 1} match neither the backup nor the prepared markers; "
                 "refusing to guess.")
        after[start:end] = original_area[start:end]
    after = bytes(after)
    check = inspect(original_mbr, after, disk_bytes=disk_bytes)
    _require(check.identity() == original.identity() and
             [m.present for m in check.markers] == [m.present for m in original.markers] and
             check.script_present == original.script_present,
             "plan_self_check", "Internal check failed: the restored boot area does not match the backup.")
    order = _exfat_order(current_area) if original.filesystem == EXFAT else (lambda s: s)
    return Plan("restore", original, current_area, after,
                _diff(current_area, after, _label(original.filesystem)),
                _sector_writes(current_area, after, order))


def verify_written(plan: Plan, expected_mbr: bytes, mbr: bytes, area: bytes, *, disk_bytes: int) -> list[str]:
    """Problems found when comparing a read-back after writing with the plan; empty means all good.

    Every written sector must equal the plan byte for byte, the MBR must be unchanged,
    and every byte this tool watches must equal the planned boot area."""
    problems = []
    if mbr != expected_mbr:
        problems.append("The MBR reads back differently from before (it is never written).")
    if len(area) != AREA_BYTES:
        return problems + ["The boot area read back has the wrong size."]
    for w in plan.writes:
        if area[w.offset:w.offset + SECTOR] != w.after:
            problems.append(message("Sector {sector} does not read back as written.", sector=w.sector))
    owned = {i for start, end in _owned(plan.filesystem) for i in range(start, end)}
    watched = sorted(set(_protected(plan.filesystem)) | owned)
    differing = [i for i in watched if area[i] != plan.after_area[i]]
    if differing:
        problems.append(message("{count} boot-area byte(s) differ from the plan (first at byte {first}).",
            count=len(differing), first=differing[0]))
    try:
        facts = inspect(mbr, area, disk_bytes=disk_bytes)
        expected = inspect(expected_mbr, plan.after_area, disk_bytes=disk_bytes)
        if facts.identity() != expected.identity():
            problems.append("The volume identity read back differs from the plan.")
        if [m.present for m in facts.markers] != [m.present for m in expected.markers]:
            problems.append("The boot markers read back are not in the planned state.")
        if facts.script_present != expected.script_present:
            problems.append("The Canon Basic SCRIPT marker read back is not in the planned state.")
    except CardPrepError as exc:
        problems.append(message("The boot area no longer validates: {error}", error=exc))
    return problems


def describe_plan(plan: Plan) -> list[str]:
    """Plain lines for the window: what will be written where, before and after."""
    v = plan.volume
    lines = [message("{filesystem} volume, serial {serial}, partition at sector {start:,} ({gb:,.1f} GB).",
        filesystem=v.filesystem, serial=v.serial_hex, start=v.mbr.start_lba, gb=v.mbr.size_bytes / 1000 ** 3)]
    if not plan.writes_needed:
        if plan.kind == "mark":
            lines.append("Nothing to write: the boot markers are already present and valid.")
        elif plan.kind == "script":
            lines.append("The Canon Basic SCRIPT marker is already present; nothing needs to be written.")
        else:
            lines.append("Nothing to write: the card already matches the backup.")
        return lines + list(plan.notes)
    lines.append(message("Sectors to write, in this order: {sectors} ({size:,} bytes; nothing else is written).",
        sectors=", ".join(str(w.sector) for w in plan.writes), size=len(plan.writes) * SECTOR))
    for c in plan.changes:
        def show(b: bytes) -> str:
            if len(b) > 1 and len(set(b)) == 1:
                return f"{b[0]:02x} x{len(b)}"
            if len(b) > 24:
                return f"{b[:4].hex()} x{len(b) // 4}" if b == b[:4] * (len(b) // 4) else b[:12].hex() + "..."
            return repr(b.decode("ascii")) if b.isascii() and b.decode("ascii").isprintable() else b.hex()
        lines.append(message("- {label}: bytes {start}..{end}: {before} -> {after}",
            label=phrase(c.label), start=c.offset, end=c.end - 1, before=show(c.before), after=show(c.after)))
    return lines + list(plan.notes)
