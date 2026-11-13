# Detailed Usage, Card Preparation, and Safety Notes

> [!CAUTION]
> **Source / Developer Preview for Already Boot-Enabled Cameras Only**
> This repository is **not** a turnkey installer, **not** a full Magic Lantern port, and includes **no** camera-side `EnableBootDisk` automator or card-formatting/raw-sector writer. Read every requirement and disclaimer below before putting a built image on an SD card.

---

## 1. Prerequisites

1. **Camera & Firmware**:
   - An **original Canon EOS R5** running **firmware `2.2.1`** (verify in the camera's Setup Menu -> Firmware Ver.).
   - All other models (R5 C, R5 Mark II, R6, R6 Mark II, R6 Mark III) and all other EOS R5 firmware versions are **unsupported**. **Never** downgrade firmware or cross-flash an R5 `2.2.1` binary onto another body.
2. **Persistent `BootDisk` Flag Already Enabled**:
   - Your camera must already have its nonvolatile `BootDisk` flag enabled through a separate, model-reviewed workflow (for example, Canon Basic `EnableBootDisk()` verified by ROM readback).
   - **Card markers and copying `autoexec.bin` do NOT enable the camera's internal `BootDisk` flag.**
   - Activation is a persistent camera-side change. This preview does not provide a general activation or uninstaller workflow; preserve the evidence and backups required by the model-reviewed workflow you use.
3. **Your Own `64 MiB` ROM Dump (`0xE0000000..0xE4000000`)**:
   - Obtain your own `67,108,864`-byte (`64 MiB`) main ROM dump (commonly `gang100.bin` via a trusted read-only Canon Basic `SaveAllRomImageToFile()` workflow on a FAT32 SDHC card).
   - **Never** upload, commit, or share Canon ROM dumps or firmware `.FIR` files.
4. **Host Tools**:
   - **Python `>= 3.11`** (no external Python packages; no automatic installs).
   - **GNU Arm Embedded GCC `15.2.1`** (reference toolchain directory containing `arm-none-eabi-gcc`, `arm-none-eabi-ld`, `arm-none-eabi-nm`, `arm-none-eabi-objcopy`, `arm-none-eabi-objdump`, `arm-none-eabi-readelf`, e.g., xPack GNU Arm Embedded GCC `15.2.1-1.1`).
   - **Existing `ffmpeg`** (only if converting source audio clips to the required WAV format).
5. **Known-Working Card Baseline**:
   - Start with a **known-working `16 GB` FAT32 SDHC** card. See [docs/sdxc.md](sdxc.md) for the unverified status of `128 GB` SDXC / exFAT cards.

---

## 2. Firmware Guard & Provenance

`tools/build.py` enforces strict immutable target-code checks on your `--rom` input:

- Verifies exact `64 MiB` (`67,108,864`-byte) dump length.
- Verifies the `256 KiB` (`65,536` `u32` words) additive checksum at `0xE0100000` equals `0x22C94093` and checks exact startup instruction bytes before patching.
- Allows per-camera settings variance outside immutable target code while **recording your actual full ROM SHA-256** as per-camera provenance inside `build.json` / `source-manifest.json`.
- **There is no `--force` or skip-check flag.** Never bypass a ROM mismatch, never edit target checks to force another firmware version, and never copy another camera model's binary.

---

## 3. Convert Your Audio Clips

This project bundles **no** audio files (no Windows XP or Mambo recordings) and performs **no** automatic normalization, trimming, or sample-rate conversion.

Prepare two separate `48 kHz`, mono, 16-bit signed little-endian PCM WAV files using `ffmpeg`:

```sh
ffmpeg -i startup-input.mp3 -map 0:a:0 -ac 1 -ar 48000 -c:a pcm_s16le startup-48k-mono.wav
ffmpeg -i shutter-input.mp3 -map 0:a:0 -ac 1 -ar 48000 -c:a pcm_s16le shutter-48k-mono.wav
```

- **Startup role (`--startup-wav`)**: Played once after Canon init returns and audio readiness passes.
- **Shutter role (`--shutter-wav`)**: Played by the `"r5shutter"` worker task when the release counter advances.
- **DMA alignment & size limit**: The builder embeds each WAV's `data` chunk unmodified and pads only the tail with up to 31 zero bytes to meet 32-byte DMA alignment. Total clip duration is **not** capped at an arbitrary number of seconds; instead, the linker and reservation guard enforce `_reserved_end < 0x002AC218` (`FW_OBJECTS_START`) while leaving at least `524,304` bytes (`0x80010`) of Canon user memory free (out of the `1,125,744`-byte pool starting at `0x001994A8`). If your combined clips exceed that budget, the build fails cleanly at the memory guard.

---

## 4. Build Offline

Run the portable offline builder (`shutter-feedback` is the baseline profile):

```sh
python3 tools/build.py \
  --rom /path/to/eos-r5-221-rom.bin \
  --startup-wav /path/to/startup-48k-mono.wav \
  --shutter-wav /path/to/shutter-48k-mono.wav \
  --toolchain /path/to/arm-none-eabi-15.2.1/bin \
  --output build/r5-sound
```

The builder reads the input files you select and writes only to your selected output directory. It does not format cards, modify raw boot sectors, activate camera flags, or contact a camera. Verify the output directory contains:

- `autoexec.bin` — finalized ML-formatted image (`0xE12FFF13` footer + zero 32-bit LE XOR).
- `build.json` — ROM/audio/toolchain provenance, reservation footprint, and SHA-256 digests.
- `symbols.json` and linker `.map` — exact resident addresses for your specific WAV sizes.

---

You can inspect the finalized image format and its SHA-256 before copying it:

```sh
python3 tools/image_format.py verify build/r5-sound/autoexec.bin
```

Compare the printed `sha256` with `build.json` → `format.sha256`. The footer/XOR check detects format corruption; it does not authenticate a downloaded payload or establish model compatibility.

## 5. Card Preparation (`16 GB` FAT32 SDHC Baseline)

> [!WARNING]
> **The public builder does NOT write cards.**
> Do **not** blindly run unreviewed raw-device commands or `dd` entire disk/partition images onto an SD card.

1. **Back up the card completely first**: Copy all existing photos, `DCIM`/`MISC` files, and verify hashes before touching boot sectors.
2. **Use a `16 GB` FAT32 SDHC card first**: The user-confirmed `build36` run used a `16 GB` SDHC card formatted as FAT32.
3. **Keep native `BOOTDISK` markers distinct from Canon Basic `SCRIPT`**:
   - Canon Basic uses `SCRIPT` at offset `496` (`0x1F0`) plus `script.req`/`extend.m` — that is a **different** loader path.
   - Native `autoexec.bin` booting on FAT32 requires **both** ASCII markers in the **primary FAT32 boot sector** (and matching geometry):
     - `EOS_DEVELOP` (`11` ASCII bytes) at byte offset **`71`** (`0x47`)
     - `BOOTDISK` (`8` ASCII bytes) at byte offset **`92`** (`0x5C`)
4. **Use a reviewed native-marker preparation workflow**:
   - Re-verify the target removable SD device and FAT32 BPB geometry on your host OS before writing.
   - Save a raw backup of the original primary boot sector (and backup boot sector if applicable) before applying `EOS_DEVELOP` and `BOOTDISK`.
   - Verify both the written boot-sector markers and filesystem integrity (`fsck` / readback) after applying markers.
5. **Copy `autoexec.bin`**:
   - Once the card's FAT32 markers and sector backup are verified, directly copy your built `autoexec.bin` to the root directory of the card (`AUTOEXEC.BIN` / `autoexec.bin`), verify its SHA-256 matches `build.json`, and safely eject the card.

---

## 6. Camera Test Procedure & Runtime Behavior

1. **Before powering on**:
   - Disconnect any USB cable.
   - Ensure the battery is well charged.
   - Insert the prepared `16 GB` FAT32 SDHC card (with camera powered off).
2. **Startup**:
   - Power on the camera normally and listen for your **Startup** clip through the built-in speaker.
3. **Single-shot Shutter Feedback**:
   - Test **mechanical single-shot** captures after startup playback finishes.
   - If using autofocus, half-press to focus and wait for the camera's AF confirmation beep before completing the shutter press.
   - Note that the mechanical shutter's physical sound remains alongside speaker playback.
4. **Busy / Overlap Policy**:
   - A currently playing clip finishes naturally; there is no mid-clip cancellation or delayed replay queue.
   - If another shot occurs while playback is active or audio readiness is busy (`readiness_limit = 1`), that trigger is dropped cleanly.
5. **Diagnostic Notes (No Repeated `R5AUD001.TXT` Writes)**:
   - The default `shutter-feedback` feature profile intentionally **skips** writing `B:/R5AUD001.TXT` on boot to avoid duplicate FAT directory entries on repeated reboots.
   - Changing Canon's **Beep** menu setting (`0x80000023`) triggers an `R5O2` diagnostic snapshot in RAM (`DebugMsg`); it is a **diagnostic dump trigger, NOT an immediate mute control**.
   - Capture callbacks never play audio, wait, or perform file I/O.
   - Optional developer debugging (`DbgRA*.bin` RAM export via Canon Basic) is not required for normal audio playback.

---

## 7. Disclaimer, Known Limits, and Recovery

- **Experimental software**: Only limited functional single-shot testing on an original EOS R5 `2.2.1` with a `16 GB` FAT32 SDHC card has been reported (`build36`, user-confirmed XP startup + Mambo shutter; returned logs from that `build36` run have not been inspected; prior `build29` logs verified release counter `113072 -> 113073 -> 113074` across two new CR3 frames). The public source-repackaged rebuild has not itself been hardware-tested.
- **Risks**: Running resident firmware code may cause camera hangs, Canon assertions (`ERR70` or similar), lost or corrupted card data, or require recovery.
- **No warranty / no Canon affiliation**: Provided under `GPL-2.0-or-later` with zero warranty and no affiliation with or endorsement by Canon Inc.
- **Persistent flag & no uninstaller**: `BootDisk` remains set in camera NVRAM across card formats and card removal. This repository provides no flash/NVRAM writer and no guaranteed safe installer or uninstaller.
- **Observed recovery**: On the reference R5 `2.2.1` body, removing the experimental SD card and power-cycling without it restored normal camera operation (user-confirmed). Treat that as an observation on that camera, **not** a brick-proof guarantee.
- **Unverified areas**:
  - **Burst shooting, movie recording, blocked shots, card errors, shutdown margin, and an immediate user mute control** are **unverified**.
  - **Physical audio latency is unmeasured**: Never cite the worker's `20 ms` polling interval as a measured shutter-to-speaker physical latency.
