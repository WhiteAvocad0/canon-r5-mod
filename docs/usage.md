# Detailed Usage, Card Preparation, and Safety Notes

> [!CAUTION]
> **Source / Developer Preview for EOS R5 `2.2.1` Only**
> This repository is **not** a turnkey installer, **not** a full Magic Lantern port, and includes **no** automatic camera-side `EnableBootDisk` runner and **no** card formatter. The command-line builder never writes cards. Only R5 Mod Assistant offers opt-in card steps: guided Canon Basic card preparation for a read-only ROM dump or user-run BootDisk enable/disable on a `<= 32 GB` FAT32 SDHC card ([gui.md](gui.md#step-2-rom-dump-one-time)), and boot-marker preparation ([Card preparation](#5-card-preparation)). Read every requirement and disclaimer below before putting a built image on an SD card.

---

## 1. Prerequisites

1. **Camera & Firmware**:
   - An **original Canon EOS R5** running **firmware `2.2.1`** (verify in the camera's Setup Menu -> Firmware Ver.).
   - All other models (R5 C, R5 Mark II, R6, R6 Mark II, R6 Mark III) and all other EOS R5 firmware versions have **porting / testing pending** and require their own verified implementation. **Never** downgrade firmware or cross-flash an R5 `2.2.1` binary onto another body.
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
   - The private baseline has user-reported success on **`16 GB` FAT32 SDHC** and **full-capacity `128 GB` exFAT SDXC**. See [SDXC preparation and test evidence](sdxc.md) for the exFAT route and remaining limits.

---

## 2. Firmware Guard & Provenance

`tools/build.py` enforces strict immutable target-code checks on your `--rom` input:

- Verifies exact `64 MiB` (`67,108,864`-byte) dump length.
- Verifies the `256 KiB` (`65,536` `u32` words) additive checksum at `0xE0100000` equals `0x22C94093` and checks exact startup instruction bytes before patching.
- For `--profile xpan-crop`, additionally verifies all `148` XPan 4 KiB code/table pages, `3` boot MMU table slices, `34` hook windows (`35` and `3` more fingerprinted pages with `--record-indicator` and/or `--record-stop-indicator`), the `3,360`-byte framing table (`0xE20CFC64`), and the `968`-byte stock menu vector resource (`0xE38F614C`) before deriving any build-time headers or vectors into `--output`.
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

Run the portable offline builder (`shutter-feedback` is the default profile):

```sh
python3 tools/build.py \
  --rom /path/to/eos-r5-221-rom.bin \
  --startup-wav /path/to/startup-48k-mono.wav \
  --shutter-wav /path/to/shutter-48k-mono.wav \
  --toolchain /path/to/arm-none-eabi-15.2.1/bin \
  --output build/r5-sound
```

Or build the silent experimental XPan crop profile (`--profile xpan-crop`; **no WAV files** — `--startup-wav` and `--shutter-wav` are forbidden):

```sh
python3 tools/build.py \
  --profile xpan-crop \
  --rom /path/to/eos-r5-221-rom.bin \
  --toolchain /path/to/arm-none-eabi-15.2.1/bin \
  --output build/r5-xpan
```

To add the opt-in movie record indicator (a short generated cue when you press the Movie REC button), add `--record-indicator ding` to the `xpan-crop` command. Add `--record-stop-indicator dong` for a second cue when a take ends. See [Movie record indicator](#movie-record-indicator---record-indicator) below. Neither needs audio files, and both are off by default.

```sh
python3 tools/build.py \
  --profile xpan-crop \
  --record-indicator ding \
  --rom /path/to/eos-r5-221-rom.bin \
  --toolchain /path/to/arm-none-eabi-15.2.1/bin \
  --output build/r5-xpan-indicator
```

The builder reads the input files you select and writes only to your selected output directory. It does not format cards, modify raw boot sectors, activate camera flags, or contact a camera. Verify the output directory contains:

- `autoexec.bin` — finalized ML-formatted image (`0xE12FFF13` footer + zero 32-bit LE XOR).
- `build.json` — ROM/toolchain (and audio for `shutter-feedback`) provenance, reservation footprint, and SHA-256 digests.
- `symbols.json` and linker `.map` — exact resident symbol addresses for your build.

---

You can inspect the finalized image format and its SHA-256 before copying it:

```sh
python3 tools/image_format.py verify build/r5-sound/autoexec.bin
```

Compare the printed `sha256` with `build.json` → `format.sha256`. The footer/XOR check detects format corruption; it does not authenticate a downloaded payload or establish model compatibility.

## 5. Card Preparation

> [!WARNING]
> **The command-line builder does NOT write cards.** [R5 Mod Assistant](gui.md#step-7-prepare-the-boot-card) can write the two boot markers for you: opt-in, macOS only, and a **prototype that has not been tested on a real card** until the owner's first verification. Do **not** blindly run unreviewed raw-device commands or `dd` entire disk/partition images onto an SD card.

1. **Back up the card completely first**: Copy all existing photos, `DCIM`/`MISC` files, and verify hashes before touching boot sectors.
2. **Format the card in the camera**: Formatting erases everything on the card, so copy your photos and videos to your computer first. No tool in this project formats cards. The camera picks the filesystem (FAT32 on SD/SDHC, exFAT on SDXC). The baseline passed on `16 GB` FAT32 SDHC and full-capacity `128 GB` exFAT SDXC. The FAT32 offsets below apply to SDHC; the exFAT route is in [sdxc.md](sdxc.md#3-preparation-route-that-passed-the-first-trial). Do not shrink SDXC to `32 GB` as a workaround. Formatting removes the markers, so prepare the card again after every format.
3. **Keep native `BOOTDISK` markers distinct from Canon Basic `SCRIPT`**:
   - Canon Basic uses `SCRIPT` at offset `496` (`0x1F0`) plus `script.req`/`extend.m` — that is a **different** loader path.
   - Native `autoexec.bin` booting on FAT32 requires **both** ASCII markers in the **primary FAT32 boot sector** (and matching geometry):
     - `EOS_DEVELOP` (`11` ASCII bytes) at byte offset **`71`** (`0x47`)
     - `BOOTDISK` (`8` ASCII bytes) at byte offset **`92`** (`0x5C`)
   - On exFAT, `BOOTDISK` goes at `+122` and `EOS_DEVELOP` at `+130` of **both** boot regions, and both boot checksum sectors must be recomputed ([sdxc.md](sdxc.md#2-r5-221-native-exfat-markers-and-checksums)).
4. **Apply the markers** in one of two ways:
   - **R5 Mod Assistant (macOS):** in the [Prepare card](gui.md#step-7-prepare-the-boot-card) tab, choose the card from the list of detected SD cards (only removable cards in an SD card slot are offered) and press **Prepare card**. It reads the card and backs up the MBR and the partition's first 24 sectors to a timestamped folder on your computer automatically, checking the backup by re-reading it. If the markers are already there it says so and writes nothing. Otherwise one dialog names the card, for example "Prepare EOS_DIGITAL (126.9 GB exFAT SD card)?". After OK it writes only the planned sectors: one on FAT32 (the backup boot sector stays unchanged, as on the lab card that booted, so a disk check may flag it) and four on exFAT. Root writes them only if the MBR and the boot area are exactly as read. It then reads every byte back, runs `diskutil verifyVolume` and mounts the card again. **Undo** restores the latest backup with the same checks, also on a card that no longer mounts. Raw access goes through macOS's own `authopen`: macOS's authorization window grants access to just that card's whole-disk device for that step, and no code runs as root. It refuses anything unexpected, such as a card whose boot sectors don't look camera-formatted, a geometry mismatch or sectors other than 512 bytes.
   - **Any other reviewed native-marker workflow:** re-verify the target removable SD device and BPB geometry on your host OS before writing. Save a raw backup of the original boot sector(s) (both exFAT boot regions, and the MBR) before applying `EOS_DEVELOP` and `BOOTDISK`. Verify the written markers, the exFAT checksums and filesystem integrity (`fsck` / readback) afterwards.
5. **Copy `autoexec.bin`**:
   - Once the card's markers and sector backup are verified, directly copy your built `autoexec.bin` to the root directory of the card (`AUTOEXEC.BIN` / `autoexec.bin`), verify its SHA-256 matches `build.json`, and safely eject the card. R5 Mod Assistant's [Copy to card](gui.md#step-8-copy-autoexecbin) tab does this with a read-back check.

Neither the builder nor the assistant enables the camera's BootDisk setting, writes the camera's firmware or NVRAM, or formats or partitions a card.

---

## 6. Camera Test Procedure & Runtime Behavior

### Audio Profile (`shutter-feedback`)

1. **Before powering on**:
   - Disconnect any USB cable.
   - Ensure the battery is well charged.
   - Insert the prepared SD card (with camera powered off). For a controlled SDXC test, remove CFexpress so saved photos exercise the SD card.
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
   - Optional developer debugging (`DbgRA*.bin` RAM export via Canon Basic) is not required for normal playback.

### Silent XPan Profile (`--profile xpan-crop`)

1. With the camera off, insert the prepared SD card. For an initial controlled test, remove CFexpress and disconnect USB/HDMI. Added startup/shutter clips are disabled; Canon's own Beep setting is unchanged.
2. Select **RAW + Large JPEG** and **XPan** (the replaced 16:9 option) through the Shooting Menu or Q screen. Record the Auto-Rotate setting; the admitted playback scope uses effective rotation `r7=0` (for example Auto-Rotate Off or Computer-Only).
3. Take Full-frame control and XPan landscape/portrait photos. Check **automatic review immediately after the shot separately** from normal ▶ playback. Record which guide/mask appears first, the display used, orientation and any visible delay. With the current profile an XPan shot should show XPan bars at once with no 16:9 outline, a Full-frame shot its stock review, and a burst the stock review without bars (by design). This was verified on a private lab build on 18 November 2026, and the public `xpan-crop` + `ding` image passed the same check that day. **JPEG only (RAW off):** the immediate review of a Large JPEG XPan shot should now look the same as with RAW + JPEG, with no vertical stretch ([#31](https://github.com/WhiteAvocad0/canon-r5-mod/issues/31)). The fix was reported working on a private lab build on 23 November 2026; the public image is not yet camera-checked. M/S JPEG XPan shots, bursts and the EVF review are expected to keep the stretched stock review ([details](xpan.md#31-jpeg-only-review-31)).
4. Open each XPan photo in normal ▶ playback for the first time and inspect it before navigating away/back. Check rear LCD and EVF, landscape and portrait, LCD↔EVF switching, and that the Full-frame control retains its normal framing. The owner reported the private `4838eb81` four-case normal ▶ session working on 17 November 2026; the **public repackaged image is still hardware-untested**.
5. Check saved JPEG dimensions and RAW crop metadata using the [documented AspectInfo fields](xpan.md#stored-crop-tags). Other still qualities/modes (M/S/C-RAW/burst/movie), active in-camera rotation `r7!=0`, and cached orientations outside `{0,2}` remain unverified or declined.
6. Wait for card activity to stop before power-off/removal. Submit text hashes/observations through the [XPan report form](https://github.com/WhiteAvocad0/canon-r5-mod/issues/new?template=xpan-test-report.yml); **do not upload ROM dumps, photos or card images**. Optional Canon Basic debug exports are not a prerequisite for this visual test.

### Movie record indicator (`--record-indicator`)

Issue [#14](https://github.com/WhiteAvocad0/canon-r5-mod/issues/14) adds two independent `xpan-crop` options. Both are **off by default**, and with both `off` the build links none of their code:

- `--record-indicator {off,ding,chirp2,2k150loud}`: a **start cue** when you press Movie REC.
- `--record-stop-indicator {off,dong}`: a **stop cue** once when a take ends.

You can use either one alone or both together. The cues are:

| Option | Choice | Cue | Length |
| --- | --- | --- | --- |
| both | `off` (default) | none | — |
| start | `ding` | struck-bar chime, 2093 Hz with partials at 5768 Hz (−9 dB) and 11311 Hz (−18 dB), exponential decay | 250 ms |
| start | `chirp2` | 1760 Hz then 2637 Hz, 20 ms gap | 120 ms |
| start | `2k150loud` | single 2 kHz tone | 150 ms |
| stop | `dong` | two struck-bar notes falling a fifth: G6 1568 Hz for 90 ms, then C6 1047 Hz for 160 ms, each with a partial at 2.756× (−9 dB) | 250 ms |

All cues are generated by integer code in the camera (`tone_gen.c`), 48 kHz mono, about −0.5 dBFS peak, never clipped. Each cue gets its own 32-byte-aligned buffer (24,000 bytes for `ding` or `dong`). No audio file is read or bundled.

For the start and stop cues together:

```sh
python3 tools/build.py --profile xpan-crop \
  --rom /path/to/eos-r5-221-rom.bin \
  --toolchain /path/to/arm-none-eabi-15.2.1/bin \
  --record-indicator ding --record-stop-indicator dong \
  --output build/r5-xpan-indicator
```

**Evidence boundary.**

- **Start cue.** The owner tested the `ding` on the REC press on a private lab build on 18 November 2026 ("worked correctly": the cue over at least five takes in one boot, clip playback and the AF beep). Earlier private runs had 5/5 and 7/7 cues settle without errors. `chirp2` ran in a private A/B trial with the same press trigger. `2k150loud` ran on an earlier private build that used a later trigger, the start request; neither has run in this public configuration. The public `ding` image `0adff001` (built from `05e9612`) played the cue in a short owner check on 18 November 2026.
- **Stop cue.** On 18 November 2026 the owner tested private lab build `fbf31293` (`ding` at the press, `dong` at the end of a take) and reported: "Tested, inserted, and confirmed that the dong works perfectly good." The lab's camera test plan for that build was three takes, a quick start-and-stop take, clip playback checked for the dong, and the AF beep; results per item and counter dumps were not recorded. The public port follows that build's trigger and state machine. The public `ding` + `dong` image `01956513` (built from `f4b2848`) then passed a short owner check on 18 November 2026: boot, XPan framing and review, the ding and dong over 2–3 takes, a clip played to the end without the dong, and the AF beep ("done testing, and all passed").
- **Every indicator build from this version is a new image.** That includes `ding` alone, because it now links the stop-capable worker even when no stop cue is selected. The `0adff001` check does not cover it. `--record-stop-indicator dong` without a start cue has not run on hardware in any form.

How the start cue behaves:

1. The cue plays when Canon's movie button handler accepts a REC press (RecState `0 -> 1`). Other start paths (touch REC, remote, self-timer, timelapse) are not expected to cue, because the trigger counts only this handler's press. This is untested.
2. The worker submits it within one 20 ms tick through Canon's own speaker API. If the speaker is busy it retries for about 200 ms, then drops the cue. A cue is never replayed late, and it is dropped if recording has already stopped or the start was refused.
3. Several presses while a cue is pending or playing count as one cue.
4. **The cue is quiet in the recorded clip.** In private trials the press-triggered ding reached only about −32 dBFS in the clip (its tail), against about −15 dBFS for a cue that an earlier lab build triggered later, at the start request. The speaker is acoustically picked up; it is not mixed into the movie track.
5. A Canon sound requested during the first ~60 ms of the 250 ms ding can cut the ding short. Canon's AF beep still worked in the private trial.
6. If the speaker never confirms a cue within about 500 ms, the indicator disables itself until the next power cycle (fault `3` in the counters below). This stops both cues.

How the stop cue behaves:

1. It plays once per take, when RecState returns to idle (`0`) after the normal confirmed start (`2 -> 3` from the movie start sequence) of the same take. In private hardware logs, manual stops and the automatic time-limit stop follow the same RecState sequence. An error ending (`3 -> 0`) also counts, but that comes from ROM reading only.
2. A start that never reached recording (refused, cancelled, or a start error before recording began) gives no dong. Timelapse and streaming takes give none either. A take started some other way that reaches the normal confirmed start would get a dong without a ding. This is untested.
3. The dong waits until Canon's audio controller (AudioCtrl) has left its recording state `5`. It plays only in AudioCtrl state `4` or `1`, retries for about 200 ms, and is then dropped, never late.
4. One cue plays at a time, so the dong never overlaps the ding. A new REC press drops a pending dong. A take that ends before its ding could play drops that ding, and a take that ends while its ding is still playing loses its dong. A REC press while the dong is still playing (within about 0.3 s) loses that take's ding.
5. How long after the stop press the dong comes is not measured. It plays when Canon returns RecState to idle after the clip is finalized, within one 20 ms tick. Like the ding, a Canon sound in the first ~60 ms of the dong can cut it short; an AF half-press right after stopping may do that.

Both cues use the same single `GUI_SetRecordingState` observer hook and the same fingerprinted pages, so any selection has `35` hooks, `21` pages, `10` L2 tables and `139` witnesses. The build refuses an image that falls below Canon's heap floor. With the reference ROM, `ding` plus `dong` leaves `883,224` bytes against the `524,304`-byte floor.

Optional counters: the build appends five `R5T1` DebugMsg rows to the existing report bundle. From a developer KUWA RAM export you can decode them with:

```sh
python3 src/experimental/xpan/tools/record_indicator_log.py path/to/DbgRA001.bin --build build/r5-xpan-indicator
```

They count presses, submissions of both cues, settled/stopped/rejected/timeout outcomes and drops. The stop row counts ended takes (`W=`), dong submissions (`B=`), and dongs dropped because another cue was in flight (`K=`), a new press arrived while the dong was pending (`H=`), or RecState was not idle at its tick (`I=`; in a build without a start cue a new take lands here). The hook counts the end before Canon stores it, so a rare worker tick in that window drops the dong; `I=` counts this. The counters record what the worker saw, not what was audible. Exports from builds before the stop cue (four rows, such as `0adff001`) decode only with the decoder of their own source version.

### One-press screenshot (`--screenshot`)

`--screenshot rate` (an `xpan-crop` option, off by default) makes RATE in ▶ save a 2400×1600 JPEG of the whole screen next to your photos. Create the folder `DCIM/R5SHOTS` on the SD card before the first use. Add it to any `xpan-crop` command, alone or with the record cues:

```sh
python3 tools/build.py --profile xpan-crop \
  --rom /path/to/eos-r5-221-rom.bin \
  --toolchain /path/to/arm-none-eabi-15.2.1/bin \
  --screenshot rate \
  --output build/r5-xpan-screenshot
```

Test it like this: show a JPEG photo full screen in ▶, press RATE once, wait for the click, and check that a new 2400×1600 JPEG appears after it. Then press RATE once in live view: you should hear a click and get no JPEG. The sounds, the files it writes and the evidence boundary (the public image is hardware-untested) are in [docs/screenshot.md](screenshot.md).

---

## 7. Disclaimer, Known Limits, and Recovery

- **Experimental software**: Limited functional photo testing on an original EOS R5 `2.2.1` with `16 GB` FAT32 SDHC and full-capacity `128 GB` exFAT SDXC cards has been reported (`build36`, user-confirmed XP startup + Mambo shutter; returned logs from that `build36` run have not been inspected; prior `build29` logs verified release counter `113072 -> 113073 -> 113074` across two new CR3 frames). The public source-repackaged rebuild has not itself been hardware-tested.
- **Risks**: Running resident firmware code may cause camera hangs, Canon assertions (`ERR70` or similar), lost or corrupted card data, or require recovery.
- **No warranty / no Canon affiliation**: Provided under `GPL-2.0-or-later` with zero warranty and no affiliation with or endorsement by Canon Inc.
- **Persistent flag & no uninstaller**: `BootDisk` remains set in camera NVRAM across card formats and card removal. This repository provides no flash/NVRAM writer and no guaranteed safe installer or uninstaller.
- **Observed recovery**: On the reference R5 `2.2.1` body, removing the experimental SD card and power-cycling without it restored normal camera operation (user-confirmed). Treat that as an observation on that camera, **not** a brick-proof guarantee.
- **Unverified areas**:
  - **Burst shooting, movie recording, blocked shots, card errors, shutdown margin, and an immediate user mute control** are **unverified**.
  - **Physical audio latency is unmeasured**: Never cite the worker's `20 ms` polling interval as a measured shutter-to-speaker physical latency.
