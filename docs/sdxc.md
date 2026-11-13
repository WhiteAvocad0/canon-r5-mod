# `128 GB` SDXC & exFAT Migration Status

**Current Status: Planned Route / Hardware-Unverified.**
Only `16 GB` FAT32 SDHC has user-confirmed `build36` execution and playback. Native `BOOTDISK` booting of `autoexec.bin` on a full-capacity `128 GB` exFAT SDXC card has **not** yet been tested or verified on R5 `2.2.1` hardware.

---

## 1. Why Earlier `128 GB` Failures Do Not Prove an SDHC-Only Limit

Earlier experiments on a `128 GB` SDXC card failed, **but those failures do not establish that the EOS R5 `2.2.1` bootloader restricts `BOOTDISK` to SDHC cards**:

- **Pre-activation state**: All earlier `128 GB` SDXC tests ran *before* the camera's persistent `BootDisk` flag was verified enabled (`0xFFFFFFFF` at ROM/NVRAM boot-info readback; it had initially been `0x0`).
- **Different loader path**: Most earlier `128 GB` tests targeted Canon Basic `SCRIPT` (`+496` on FAT32) rather than native `BOOTDISK` on exFAT.
- **Confounded volume layouts**: Earlier `128 GB` tests also mixed full-capacity FAT32 (which Canon cameras reject; Canon's [EOS R5 Formatting Manual](https://cam.start.canon/en/C003/manual/html/UG-07_Set-up_0070.html) specifies FAT32 for SD/SDHC and exFAT for SDXC) and a temporary `32 GB` FAT32 partition.
- **Current card state**: The `128 GB` SDXC control card was restored to a clean, full-capacity MBR/exFAT layout without native markers or `autoexec.bin`, and has not yet been mounted or hardware-tested with `build36`.

---

## 2. R5 `2.2.1` Bootloader & exFAT Specification Facts

Static inspection of the EOS R5 `2.2.1` bootloader (`0xE0004FD6`) and the [Microsoft exFAT File System Specification](https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification) establishes the following facts for the planned full-capacity exFAT route:

| Property | Observed R5 `2.2.1` / Spec Fact |
| --- | --- |
| Bootloader filesystem branch (`fs_type == 4`) | Checks exFAT VBR offset **`+122`** (`0x7A`, 8 bytes) for `BOOTDISK` and **`+130`** (`0x82`, 11 bytes) for `EOS_DEVELOP`. |
| Main & Backup VBR regions | exFAT maintains both a **Main Boot Region** (sectors `0..11`) and a **Backup Boot Region** (sectors `12..23`), `24` sectors total (`12,288` bytes at `512` B/sector). |
| VBR Boot Checksum (sectors `11` and `23`) | Computed across the first `11` sectors (`512 * 11 = 5,632` bytes), skipping byte offsets `{106, 107, 112}` (`VolumeFlags` and `PercentInUse`), and repeated `128` times as little-endian `u32` in sector `11` (and sector `23` for the Backup Boot Region), per the [Microsoft exFAT File System Specification](https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification). |
| **Known upstream `edit_card_flags.py` defect** | Legacy ML `edit_card_flags.py` wrote `(checksum << 31) \| (checksum >> 1) + value`, where Python's `+` binds tighter than `\|`, corrupting bit 31 when `checksum` is odd and `(checksum >> 1) + value >= 0x80000000`. The spec-compliant rotation/addition is `(((checksum << 31) \| (checksum >> 1)) + value) & 0xffffffff`. |

---

## 3. Planned Route vs. Deferred / Rejected Shortcuts

- **Preserve full exFAT capacity (planned primary route)**: Keep the single full-capacity MBR/exFAT partition (no `32 GB` FAT32 shortcut on SDXC), back up all `24` boot-region sectors (`0..23`) plus files, apply `BOOTDISK` (`+122`) and `EOS_DEVELOP` (`+130`) with spec-compliant VBR checksums to both Main and Backup VBRs, verify with `fsck_exfat -n` and readback, and test `autoexec.bin` on hardware.
- **Canon Basic `SCRIPT` on exFAT is distinct and deferred**: `SCRIPT` is a Canon Basic interpreter marker, is **not** needed for native `BOOTDISK` `autoexec.bin` loading, and remains deferred.
- **Do not use untested marker writers**: This repository does **not** ship or endorse an unverified raw exFAT marker writer.

### Remaining Hardware Unknowns

1. Whether the R5 `2.2.1` bootloader (`0xE0004DA2` / `0xE0005E48`) accepts host-formatted (`newfs_exfat`) exFAT volumes or requires an in-camera exFAT format ([Canon EOS R5 Formatting Manual](https://cam.start.canon/en/C003/manual/html/UG-07_Set-up_0070.html)) prior to marker placement.
2. Whether strict VBR checksums, `BytesPerSectorShift == 9`, or specific BPB/cluster alignments are required by the R5 `2.2.1` `AUTOEXEC.bin` reader on `128 GB` SDXC media.
