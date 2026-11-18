"""Synthetic card images for the card-preparation tests (no real card bytes are committed).

The "lab layout" images rebuild a camera-formatted card's MBR and boot area from
the geometry recorded in the maintainer's private lab notes: a 128 GB exFAT card
and a 16 GB FAT32 card, both formatted in the EOS R5. Every byte is derived here
from those public-safe fields (sizes, offsets, serial, flags); the camera's own
formatter filled the rest with zeros and 0xF4 boot code. The hashes and checksums
the lab recorded for the real cards are used as oracles.
"""
from __future__ import annotations

import struct

SECTOR = 512

# 128 GB SDXC formatted in the R5, recorded before boot markers were applied.
LAB_EXFAT = dict(
    disk_bytes=126_877_696_000, start_lba=32768, sectors=247_775_232,
    fat_offset=16384, fat_length=16384, heap_offset=32768, clusters=967_744, root_cluster=4,
    serial=0x1D731468, spc_shift=8, fats=1, main_flags=2, backup_flags=0, percent=0,
)
LAB_EXFAT_ORACLE = dict(
    before_sha256="2f84a9988896c7c25486d75b367b3d195200005aad8d18a00e9a13ede46eabad",
    after_sha256="e0dc8e877a8dab507bd8e7b0fbf02c04cf46f10337bfd857a2d31c6b6c2a4194",
    checksum_before=0x216DAA31, checksum_after=0xA1444CD3,
    ranges=[(122, 141), (5632, 6144), (6266, 6285), (11776, 12288)],
    marker_after_hex="424f4f544449534b454f535f444556454c4f50",
    sectors_in_write_order=[12, 23, 0, 11],
)
# 16 GB SDHC formatted in the R5 (FAT32). The recorded sector also carried a Canon
# Basic SCRIPT marker at byte 496 from an earlier experiment.
LAB_FAT32 = dict(
    disk_bytes=15_931_539_456, start_lba=8192, sectors=31_108_096,
    spc=64, reserved=598, fats=2, fat_sectors=3797, serial=0, label=b"EOS_DIGITAL",
)
LAB_FAT32_ORACLE = dict(
    before_sha256="f496436a4e5998961064e7b7b14830bd17da5bdde1094ab6ee08ead7a7823ddb",
    after_sha256="f6ea7e47e441348e4d1ad3970277f539bae85a8f1fca49d6e4e238072185b44d",
    changed=[(76, 82), (92, 100)],
)


def mbr(start_lba: int, sectors: int, ptype: int, *, chs_start: bytes = b"\x0a\x09\x02",
        chs_end: bytes = b"\xfe\xff\xff", slot: int = 0) -> bytes:
    buf = bytearray(SECTOR)
    entry = bytes([0x00]) + chs_start + bytes([ptype]) + chs_end + struct.pack("<II", start_lba, sectors)
    buf[446 + 16 * slot:462 + 16 * slot] = entry
    buf[510:512] = b"\x55\xaa"
    return bytes(buf)


def exfat_checksum(region: bytes) -> int:
    """Independent spec implementation: slice out the excluded bytes, then rotate and add."""
    data = region[:106] + region[108:112] + region[113:11 * SECTOR]
    c = 0
    for value in data:
        c = (c >> 1) + (0x80000000 if c & 1 else 0) + value
        c %= 1 << 32
    return c


def exfat_region(*, start_lba, sectors, fat_offset, fat_length, heap_offset, clusters, root_cluster, serial,
                 spc_shift, fats, flags, percent, shift=9, revision=0x0100, bootcode=0xF4, **_):
    s0 = bytearray(SECTOR)
    s0[0:3] = b"\xeb\x76\x90"
    s0[3:11] = b"EXFAT   "
    struct.pack_into("<QQIIIIII", s0, 64, start_lba, sectors, fat_offset, fat_length, heap_offset, clusters,
                     root_cluster, serial)
    struct.pack_into("<HHBBBBB", s0, 104, revision, flags, shift, spc_shift, fats, 0x80, percent)
    s0[120:510] = bytes([bootcode]) * 390
    s0[510:512] = b"\x55\xaa"
    region = bytearray(s0) + bytearray(11 * SECTOR)
    for sector in range(1, 9):
        region[sector * SECTOR + 510:(sector + 1) * SECTOR] = b"\x55\xaa"
    region[11 * SECTOR:12 * SECTOR] = struct.pack("<I", exfat_checksum(bytes(region))) * 128
    return bytes(region)


def exfat_area(geometry: dict = LAB_EXFAT, **override) -> bytes:
    g = {**geometry, **override}
    main = exfat_region(**g, flags=g["main_flags"])
    backup = exfat_region(**g, flags=g["backup_flags"])
    return main + backup


def exfat_card(geometry: dict = LAB_EXFAT, **override) -> tuple[bytes, bytes, int]:
    g = {**geometry, **override}
    return mbr(g["start_lba"], g["sectors"], 0x07), exfat_area(g), g["disk_bytes"]


def fat32_sector(*, start_lba, sectors, spc, reserved, fats, fat_sectors, serial, label, script=False,
                 bootcode_zero=True, bps=512, oem=b"CanonEOS", **_) -> bytes:
    s = bytearray(SECTOR)
    s[0:3] = b"\xeb\x58\x90"
    s[3:11] = oem
    struct.pack_into("<HBHBHHBHHHII", s, 11, bps, spc, reserved, fats, 0, 0, 0xF8, 0, 63, 128, start_lba, sectors)
    struct.pack_into("<IHHIHH", s, 36, fat_sectors, 0, 0, 2, 1, 6)
    s[64], s[65], s[66] = 0x80, 0, 0x29
    struct.pack_into("<I", s, 67, serial)
    s[71:82] = label
    s[82:90] = b"FAT32   "
    if not bootcode_zero:
        s[90:120] = bytes(range(1, 31))
    if script:
        s[496:502] = b"SCRIPT"
    s[510:512] = b"\x55\xaa"
    return bytes(s)


def fat32_area(sector0: bytes) -> bytes:
    area = bytearray(24 * SECTOR)
    fsinfo = bytearray(SECTOR)
    fsinfo[0:4], fsinfo[484:488], fsinfo[508:512] = b"RRaA", b"rrAa", b"\x00\x00\x55\xaa"
    struct.pack_into("<II", fsinfo, 488, 0xFFFFFFFF, 0xFFFFFFFF)
    area[0:SECTOR] = sector0
    area[SECTOR:2 * SECTOR] = fsinfo
    area[6 * SECTOR:7 * SECTOR] = sector0
    area[7 * SECTOR:8 * SECTOR] = fsinfo
    return bytes(area)


def fat32_card(geometry: dict = LAB_FAT32, **override) -> tuple[bytes, bytes, int]:
    g = {**geometry, **override}
    return mbr(g["start_lba"], g["sectors"], 0x0C), fat32_area(fat32_sector(**g)), g["disk_bytes"]


# -- diskutil plists shaped like the ones macOS returned for the lab cards ----------

SD_TREE = "IODeviceTree:/arm-io@10F00000/apcie0@B0000000/pci-bridge1@1/pcie-sdreader@0"


def mac_whole(disk="disk4", size=LAB_FAT32["disk_bytes"], **override) -> dict:
    info = {
        "BusProtocol": "Secure Digital", "Content": "FDisk_partition_scheme", "DeviceBlockSize": 512,
        "DeviceIdentifier": disk, "DeviceNode": f"/dev/{disk}", "DeviceTreePath": SD_TREE, "Ejectable": True,
        "Internal": True, "IORegistryEntryName": "Apple SDXC Reader Media", "MediaName": "Built In SDXC Reader",
        "MountPoint": "", "OSInternalMedia": False, "ParentWholeDisk": disk, "Removable": True, "RemovableMedia": True,
        "RemovableMediaOrExternalDevice": True, "Size": size, "SystemImage": False, "VirtualOrPhysical": "Physical",
        "WholeDisk": True, "Writable": True, "WritableMedia": True, "WritableVolume": False,
    }
    info.update(override)
    return info


def mac_part(disk="disk4", index=1, *, fs="msdos", content="Windows_FAT_32", offset=LAB_FAT32["start_lba"] * 512,
             size=LAB_FAT32["sectors"] * 512, name="EOS_DIGITAL", mount="/Volumes/EOS_DIGITAL", **override) -> dict:
    info = {
        "BusProtocol": "Secure Digital", "Content": content, "DeviceBlockSize": 512,
        "DeviceIdentifier": f"{disk}s{index}", "DeviceTreePath": SD_TREE, "Ejectable": True,
        "FilesystemType": fs, "MountPoint": mount, "OSInternalMedia": False, "ParentWholeDisk": disk,
        "PartitionMapPartition": True, "PartitionMapPartitionOffset": offset, "Removable": True,
        "RemovableMedia": True, "Size": size, "SystemImage": False, "VolumeName": name,
        "VolumeUUID": "00000000-0000-0000-0000-000000000000", "WholeDisk": False, "Writable": True,
        "WritableMedia": True, "WritableVolume": True,
    }
    info.update(override)
    return info


def mac_exfat_part(disk="disk4", **override) -> dict:
    return mac_part(disk, fs="exfat", content="Windows_NTFS", offset=LAB_EXFAT["start_lba"] * 512,
                    size=LAB_EXFAT["sectors"] * 512, **override)
