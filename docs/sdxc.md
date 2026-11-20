# `128 GB` SDXC & exFAT

**Status: first functional camera trial passed by user report on 2026-11-14.**

On the reference original EOS R5 running firmware `2.2.1`, the unchanged private `build36` played the startup clip, played Mambo on shutter release and shot normally with the full-capacity `128 GB` SDXC exFAT card. The user reported:

> Amazing, i just tested, it play startup sounds, play shutter release mambo sound, and shoots normally

The card kept its existing full-capacity MBR/exFAT partition. No formatting, repartitioning, capacity reduction or new camera firmware/NVRAM write was needed. This resolves the suspected SDHC-only limit for this camera, firmware, card and payload. It does not certify every SDXC card or shooting mode. See [resolved issue #2](https://github.com/WhiteAvocad0/canon-r5-mod/issues/2).

## 1. Earlier Limitations and Their Resolution

Earlier `128 GB` trials did not establish an SDHC-only boot restriction:

- **Camera boot activation had not been established then.** Those trials preceded verified persistent `BootDisk` activation. Native markers alone do not enable this camera flag. The successful trial used a camera whose flag was already verified enabled.
- **Two loader paths had been mixed.** Canon Basic `SCRIPT` and native `BOOTDISK` loading are separate. The successful exFAT trial used native `BOOTDISK` / `EOS_DEVELOP` with `AUTOEXEC.BIN`; it did not require solving exFAT `SCRIPT` placement, which remains deferred.
- **FAT32 workarounds obscured the test.** Full-card FAT32 and a temporary `32 GB` partition were earlier experiments. The successful route retained normal full-capacity exFAT. Canon documents FAT32 for SD/SDHC and exFAT for SDXC in the [EOS R5 formatting guide](https://cam.start.canon/en/C003/manual/html/UG-07_Set-up_0070.html).
- **The legacy checksum expression was wrong.** A pinned upstream `edit_card_flags.py` used `(checksum << 31) | (checksum >> 1) + value`; Python binds `+` before `|`. The new offline planner explicitly rotates then adds, masks to `u32`, and regenerates both checksum sectors. The defect was verified in source and tests; it is not established as the sole cause of earlier camera failures.

A separate host finalization bug appeared *after* successful writes and readback: a read-only macOS mount reported aggregate `Writable == false`, which the preparer mistook for physically unwritable media. Physical `WritableMedia` and mounted-volume `WritableVolume` must be checked separately. The historical helper failure remains part of the record; camera success does not establish that its final verification/eject stage completed. No repeat raw writes are required to fix that host check.

## 2. R5 `2.2.1` Native exFAT Markers and Checksums

Static inspection of the R5 `2.2.1` bootloader at `0xE0004FD6` found the `fs_type == 4` marker checks. The [Microsoft exFAT specification](https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification) defines the boot-region checksum.

| Field | Value for the tested layout |
| --- | --- |
| Native marker | `BOOTDISK`, 8 ASCII bytes at VBR offset `+122` (`0x7A`) |
| Native development marker | `EOS_DEVELOP`, 11 ASCII bytes at `+130` (`0x82`) |
| Main boot region | Sectors `0..11` |
| Backup boot region | Sectors `12..23` |
| Sector size | `512` bytes; inspect actual geometry before applying these sector counts |
| Checksum input | First 11 sectors of each boot region; omit bytes `106`, `107`, `112` of that region |
| Checksum update per input byte | `(((checksum << 31) | (checksum >> 1)) + value) & 0xffffffff` |
| Checksum sectors | Main `11` and backup `23`, each containing the checksum repeated as 128 little-endian `u32` values |

Markers were applied to both boot copies and both checksums were regenerated. MBR, partition geometry, allocation bitmap and data area were preserved. Successful execution on the tested layout does not prove that other sector sizes, cluster alignments or format origins work.

## 3. Preparation Route That Passed the First Trial

The command-line builder is **offline only** and never writes cards, and there is no boot-activation tool. R5 Mod Assistant's opt-in [Prepare card](gui.md#step-7-prepare-the-boot-card) step (macOS, prototype; verified on 18 November 2026 on the owner's Mac (macOS 27.3) with a camera-formatted 128 GB exFAT card, which the camera then booted; FAT32 cards, Windows and Linux are untested) re-implements steps 2, 5 and 6 below for camera-formatted cards: the same four sectors in the same order, the same checksum, a backup of the MBR and all 24 boot-region sectors, full read-back, an unchanged-MBR check and `diskutil verifyVolume`. The host tests reproduce this trial's recorded before/after boot-region hashes and checksums from its recorded geometry. The following describes the reviewed private preparation; it is not a command to run an arbitrary legacy utility.

1. Back up your files and freshly identify the physical card, partition geometry and actual exFAT filesystem. Do not identify a card solely by a cached disk number. macOS may label MBR type `0x07` as `Windows_NTFS` even when the filesystem is exFAT.
2. Preserve the MBR and all 24 main/backup boot-region sectors before mutation. The reference user had already backed up files and requested no duplicate file backup.
3. Copy the admitted target payload to the card root as **`AUTOEXEC.BIN`**, and verify its SHA-256 against the build manifest. Avoid case-insensitive duplicate names.
4. Normally unmount, re-identify the device and verify filesystem integrity without repairs (`fsck_exfat -n` on macOS).
5. Plan only the two markers and checksum-sector changes, checking both boot copies and the unchanged geometry. For the tested `512`-byte layout, only sectors `12`, `23`, `0`, `11` needed writes, in backup-first order.
6. Verify exact boot-region readback, both checksums, unchanged MBR and filesystem integrity. Verify the staged payload after mounting read-only, then eject normally. Keep execution-stage results separate if final verification or ejection fails.
7. With the camera off and its existing `BootDisk` flag enabled, insert the prepared card, test startup audio and separated single-photo captures, and verify photo saving. Removing CFexpress makes the storage test unambiguous.

The first trial reused `build36`, `562,144` bytes, SHA-256 `3579cecf0ab9349035b4d4320ceb6e7fe34719f15f00bc80e0d34bbdf3b312c2`, preserving the known-working sound implementation.

## 4. Evidence and Remaining Validation

- **Host-observed:** full partition geometry retained, payload staged and hash-verified, four-sector write/readback passed, unchanged MBR and matching main/backup checksums. The host finalization error described above is retained.
- **User-reported on camera:** startup sound, Mambo shutter sound and normal shooting on the prepared `128 GB` card.
- **Not inspected:** returned SDXC photos or diagnostic logs. Shutter mode and absence of a second storage card were not separately reconfirmed in the final report.
- **Still unverified:** repeated cold boots, longer shooting sessions, full-capacity fill/read validation, other SDXC cards, burst/video, blocked shots, card-error paths, measured latency, shutdown margin and immediate mute.
- **Public build distinction:** this trial used the private baseline (`build36`). An earlier `128 GB` silent XPan trial (`1b7bfcfa`) also ran before the latest `4838eb81` portrait/EVF playback extension. Neither public source-repackaged build (`shutter-feedback` or `xpan-crop`) has itself been hardware-tested.

Canon Basic exFAT `SCRIPT` lookup remains a separate deferred research question. Other camera models still require their own firmware-specific ports.
