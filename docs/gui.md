# Build assistant (prototype)

> [!WARNING]
> **Prototype.** This is Phases 1 and 2 of [issue #16](https://github.com/WhiteAvocad0/canon-r5-mod/issues/16). It builds `autoexec.bin` on your computer, can write the two boot markers onto a card you formatted in the camera (macOS only, **not yet tested on a real card**), and copies `autoexec.bin` to the card. It runs the same builder and checks as the [command line](usage.md#4-build-offline). Every build is a public repackaged build and is **hardware-untested**. Read the [Getting started guide](getting-started.md) first. You use this at your own risk.

The build assistant is a small desktop window for owners of an original Canon EOS R5 on firmware `2.2.1` who would rather not use a terminal to build. You still need the [one-time prerequisites](usage.md#1-prerequisites): your own ROM dump and BootDisk enabled in the camera. On a Mac the assistant can make a camera-formatted SD card bootable ([Prepare card](#5-prepare-card)); elsewhere you need an already bootable card.

## Launch

```sh
python3 tools/gui/app.py
```

It needs **Python 3.11 or newer with Tkinter**. No other installs are needed, and it runs on macOS (Intel and Apple Silicon), Windows and Linux.

- **macOS and Windows:** the installers from <https://www.python.org/downloads/> include Tkinter.
- **Homebrew Python on macOS** has no Tkinter until you install the matching `python-tk` formula, for example `brew install python-tk@3.13`.
- **Linux:** install your distribution's `python3-tk` package.

If Tkinter is missing, the assistant prints these hints and exits. The command-line builder works without Tkinter.

## What it does

The window has seven tabs. A red warning line runs across the top of every tab, followed by three buttons that open the **Getting started guide**, the **Prerequisites** and this page.

### 1. ROM

*Screenshot, described:* a sentence explains that the ROM stays on this computer. Below it is a read-only path box and a **Choose ROM...** button. After you pick a file, a status line turns green: "ROM accepted for xpan-crop: Canon EOS R5 firmware 2.2.1, 67,108,864 bytes, 178 fingerprinted regions match." If the ROM is refused, the line is red and gives the builder's reason, for example "Input size must be exactly 67108864 bytes" or "firmware signature mismatch".

The check is the builder's own strict admission (`build.check_rom`) for the profile and record indicator you selected. It reads the ROM only: it doesn't need the toolchain and writes nothing. Changing the profile or the indicator runs the check again, because an XPan build and a build with the indicator check extra fingerprinted pages. There is no way to force a refused ROM.

### 2. Features

*Screenshot, described:* two radio buttons, **Custom startup and shutter sounds (shutter-feedback)** and **XPan 2.71:1 crop (silent) (xpan-crop)**. Each has a short description and a grey status line, for example "Status: Verified on a private lab build. Worked on the maintainer's private lab builds. Those builds share the feature code but also carry extra diagnostics, so their compiled images differ. Your public build is hardware-untested."

Below them is a **Movie record indicator** box with a drop-down menu (Off, Ding, Chirp, Tone). The text under the menu gives the status of the chosen cue. Ding was tested on the REC press on a private lab build. Chirp ran in a private A/B trial with the same trigger, and Tone ran on an earlier private build with a different trigger (the start request). Neither has run in this public single-cue configuration, so both are marked experimental. With **shutter-feedback** selected, the menu is greyed out and says "The record indicator is part of the XPan profile only (it needs xpan-crop)."

Then come two boxes, **Startup sound (WAV)** and **Shutter sound (WAV)**, each with a **Choose WAV...** button. They work only for shutter-feedback. Under xpan-crop they are greyed out with the reason "Not used by xpan-crop: this profile adds no custom sounds." Each WAV is checked by the builder's own WAV admission, which needs 48 kHz mono 16-bit PCM. A green line then shows its length, for example "WAV accepted: 48 kHz mono 16-bit, 300 ms, 28,800 bytes embedded." A red line gives the reason the WAV was refused. [usage.md](usage.md#3-convert-your-audio-clips) has an `ffmpeg` command to convert clips.

XPan and custom sounds aren't offered together. The XPan profile is silent by design, and private lab estimates suggest both together would exceed the camera's free memory. That combination has not been tested.

### 3. Toolchain

*Screenshot, described:* installation instructions, a button that opens the xPack releases page, a path box with **Choose bin folder...** and **Detect again** buttons, and a status line.

At start-up the assistant looks for an existing GNU Arm `15.2.1` toolchain. It looks on your `PATH`, under `~/tools/`, your home folder and `~/Downloads/`, in xPack's own install folders and in the usual system folders. Each candidate goes through the builder's toolchain admission (target `arm-none-eabi`, GCC `15.2.1`). If nothing is found, download **xPack GNU Arm Embedded GCC 15.2.1-1.1** yourself, unpack it, and choose its `bin` folder. **This prototype never downloads anything.**

### 4. Build

*Screenshot, described:* an output folder path with a **Change...** button, then a yellow list headed "Before you can build:" (for example "Pick your ROM dump."), or "Ready to build." Below these are the **Build autoexec.bin** button, a scrolling log, a result area, a horizontal memory bar and two buttons, **Open output folder** and **Open 'what's in this build'**.

- The output folder must be new. By default it's `build/gui-<profile>[-<cue>]-<date>-<time>` inside the repository, which git ignores.
- **Build** runs `python3 tools/build.py ...` with the selected arguments in a separate process. The log shows each step (for example `gcc observer.o`, `ld handoff.elf`). The first line is the exact command, so you can repeat it in a terminal.
- On success, the result area shows the path of `autoexec.bin` and its SHA-256, which the assistant recalculates and checks against `build.json`. It also shows a memory line, for example "Canon memory left after this build: 907,256 bytes (safety floor 524,304; margin 382,952 bytes)." The bar shows the remaining Canon memory as a share of the pool.
- It also writes `BUILD-CONTENTS.txt` in the output folder: a plain-language list of the profile, options and their status, the WAVs used, the image's SHA-256, the memory margin, the `source_manifest_sha256` to quote in test reports, and what the build does *not* include.
- If the builder refuses, the result area shows its last error line in red.

### 5. Prepare card

> [!CAUTION]
> **Writes raw boot sectors. Prototype, macOS only, not yet tested on a real card.** The owner decided on 18 November 2026 to offer this step in the assistant. Its real-device path stays untested until the owner's first verification. The command-line builder still never writes cards.

*Screenshot, described:* an explanation, a red line saying the real-device path is untested, a checkbox "I formatted this card in the camera and copied off any photos I want to keep.", a **Find SD cards** button with a drop-down of the cards found, the card's details, a "Not offered:" line naming every other disk with its reason, a "Type the volume name to confirm:" box, the **Read card and save backup** and **Write boot markers** buttons, a restore row (**Choose backup to restore...**, **Read card for restore**), a yellow "Next:" list, a box with the plan and the result.

This step writes the two Canon boot markers (`EOS_DEVELOP`, `BOOTDISK`) so that a camera whose BootDisk setting is already enabled loads `autoexec.bin` from the card. It does not enable BootDisk and never writes the camera.

1. **Explain.** Format the card **in the camera** first (Setup menu, Format card). The assistant never formats, partitions or erases anything, and it refuses a card that doesn't look camera-formatted: on FAT32 the boot code where `BOOTDISK` goes must be empty, and on exFAT it must be the `F4` fill the camera writes. Formatting removes the markers, so you run this step again after every format. Tick the checkbox to go on.
2. **Select.** **Find SD cards** lists only cards in an SD card slot (macOS reports the *Secure Digital* protocol). The card must be removable, ejectable, physical media with 512-byte sectors and an MBR with exactly one FAT32 or exFAT partition. It must be mounted under `/Volumes`, writable, and between 2 GB and 2 TiB. The disk macOS runs from, internal disks, disk images, system media and USB devices are refused with their reason. That includes USB card readers, because macOS can't tell them from other USB drives.
3. **Show.** The details give the model (for example *Built In SDXC Reader*), the size, the filesystem, the volume name, the mount point and the device (`disk4`, partition `disk4s1`).
4. **Confirm.** Type the volume name exactly, for example `EOS_DIGITAL` (case and spaces count). Choosing another card, editing the name or searching again throws away any read or plan.
5. **Read and back up.** **Read card and save backup** opens the macOS password window, which says it will *read* the boot sectors. The assistant then:
   - unmounts the card's partition (`diskutil unmount diskNsM`; it never ejects);
   - reads the 512-byte MBR from `/dev/rdiskN` and the partition's first 24 sectors (12,288 bytes) from `/dev/rdiskNsM`;
   - mounts the card again.

   It checks that the MBR has one partition whose position and size match macOS's report, the boot sector's own geometry, and the exFAT boot checksums. It saves `mbr.bin`, `boot-area.bin` and `manifest.json` to a new folder `~/R5-card-backups/<date>-<time>-<FAT32|exFAT>-<serial>-before-marking/`, then reads them back and compares. The box then shows the plan, for example:

   ```text
   exFAT volume, serial 1D731468, partition at sector 32,768 (126.9 GB).
   Sectors to write, in this order: 12, 23, 0, 11 (2,048 bytes; nothing else is written).
   - main boot region BOOTDISK/EOS_DEVELOP markers (region bytes 122..140): bytes 122..140: f4 x19 -> 'BOOTDISKEOS_DEVELOP'
   - main boot checksum sector (partition sector 11): bytes 5632..6143: 31aa6d21 x128 -> d34c44a1 x128
   - backup boot region ... (bytes 6266..6284, and sector 23)
   ```

   On FAT32 the plan is one sector: `EOS_DEVELOP` replaces the boot-sector volume label field at byte 71 (Finder's volume name, kept in the root directory, doesn't change), and `BOOTDISK` goes at byte 92. The backup boot sector is left unchanged, as on the lab card that booted. If valid markers are already there, the plan says **Nothing to write** and no second password prompt is needed.
6. **Write and verify.** **Write boot markers** asks you to confirm the sector numbers, then opens the password window again, which says it will *write*. The assistant checks again that it is the same card, unmounts the partition and runs the helper. The helper writes only if the sectors still hold exactly the bytes that were read (SHA-256), writes them in the planned order with an `fsync` after each, and reads back the whole boot area and the MBR. The assistant then compares every written byte, checks that the MBR is unchanged and that the boot area validates with the markers present, runs `diskutil verifyVolume diskNsM`, and mounts the card again. It records the outcome in `result-<time>.json` in the backup folder.
7. **Restore.** **Choose backup to restore...** loads a backup folder and checks its files against the manifest. **Read card for restore** reads the card as in step 5. The card must be the backed-up one: same size, partition, filesystem, slot and model, the same MBR, and every boot-area byte outside the marker ranges unchanged. The exFAT `VolumeFlags` and `PercentInUse` bytes are exempt and kept as they are now. Each marker range must hold either the original or the prepared bytes. The current state is backed up again (`...-before-restore`), and **Write restore** puts the original bytes back with the same checks as step 6.
8. **Report.** The result says *done and verified*, *nothing to write*, *cancelled. Nothing was written*, or *refused. Nothing was written* (for example after the card changed since it was read). Otherwise it says which sectors were written, what failed, and the backup folder to restore from. The card is mounted again in every case, or you're told to reinsert it.

**What runs, exactly (macOS).** As you: `diskutil info -plist /`, `diskutil list -plist physical` and `diskutil info -plist diskN` / `diskNsM` to find cards, then `diskutil unmount diskNsM`, `diskutil mount diskNsM` and `diskutil verifyVolume diskNsM`. As root, through `osascript -e 'do shell script "..." with prompt "..." with administrator privileges'`, a short `/bin/sh` text runs. It copies `tools/cardprep/helper.py` into a new root-owned folder made by `mktemp -d`, checks the copy's SHA-256 against the file the assistant just hashed, and runs it with the assistant's own Python, isolated (`-I -S`). It then removes the folder. The helper accepts only:

```text
helper.py version
helper.py read  --disk /dev/rdiskN --partition /dev/rdiskNsM --length 12288
helper.py write --disk /dev/rdiskN --partition /dev/rdiskNsM --range OFFSET:LENGTH [...]
                --expect-sha SHA256 --after FILE --after-sha SHA256
```

Write ranges must be single 512-byte sectors chosen from partition sectors 0, 11, 12 and 23 (the only sectors any plan touches), at most four and non-overlapping. Reads cover at most the first 24 sectors. Devices must be raw nodes `/dev/rdiskN` and `/dev/rdiskNsM` of the same disk. The MBR is only ever read. For the lab's 128 GB exFAT layout the write is `--range 6144:512 --range 11776:512 --range 0:512 --range 5632:512` (sectors 12, 23, 0, 11); for FAT32 it is `--range 0:512`. The assistant never asks for, sees or stores your password: macOS's own window handles it.

**Windows and Linux:** **Find SD cards** lists cards on a best-effort basis (PowerShell `Get-Disk` / `lsblk`), **untested**. Preparing a card is offered on macOS only.

### 6. Copy to card

*Screenshot, described:* an explanation, a path box with **Choose card folder...**, a report (folder, drive size, whether a `DCIM` folder exists, any existing `autoexec.bin`, plus warnings), a confirmation checkbox, and the **Copy autoexec.bin to card** button.

This is only for a card that is **already bootable**. Choose the card's top folder, for example `/Volumes/EOS_DIGITAL` on a Mac, `E:\` on Windows, or the card's mount folder on Linux. Then:

1. The built `autoexec.bin` must still match `build.json` and pass the image-format check.
2. The folder is checked. The copy is **refused** if the folder is the Windows system drive (from `%SystemDrive%`, usually `C:\`) or the Windows folder, a system folder such as `/`, `/usr` or `/Volumes` itself, or your home folder or a folder that contains it. It is also refused if the folder isn't writable, has more than one spelling of `autoexec.bin`, or has an `autoexec.bin` that isn't a regular file. A removable drive root such as `E:\`, `/Volumes/EOS_DIGITAL`, `/media/<you>/<card>` or `/run/media/<you>/<card>` is allowed. The copy **asks for confirmation** if the folder isn't the root of a drive, has no `DCIM` folder, or the drive size is unusual for an SD card (under 2 GB or over 2 TB). The confirmation covers only the warnings you were shown: a new warning found at copy time stops the copy.
3. An existing `autoexec.bin`, found in any upper/lower-case spelling, is copied first to `card-backup/` inside the build folder **on your computer** and read back. If it's already identical to the new build, nothing is written.
4. The new file is written to a temporary `R5GUI-XXXXXXXX.TMP` on the card, read back and compared. It is then renamed to `AUTOEXEC.BIN`, or to the existing file's spelling.
5. The final file is read back and its SHA-256 compared with `build.json`. If they differ, you're told where the backup is and not to use the card.
6. You're told to eject the card using your operating system (Finder eject, "Safely Remove Hardware", or your file manager).

The copy step **never** formats or partitions a card, never opens a raw disk, never writes boot sectors, and never deletes anything except its own temporary file from the current copy. Temporary files left by an interrupted earlier copy are reported and left alone.

### What this tool cannot do

The last tab lists what is out of scope. The assistant does **not** dump the ROM or enable **BootDisk** in the camera. It doesn't format or partition cards, write the MBR, write anything except the boot-marker sectors shown in [Prepare card](#5-prepare-card), write the camera's firmware or NVRAM, download the toolchain, or test anything on the camera. See [Getting started → Step 2](getting-started.md#step-2-check-the-one-time-prerequisites).

## One feature list for the builder and the assistant

[`config/features.json`](../config/features.json) is the single feature manifest. It lists the profiles, the WAV inputs each profile requires or forbids, opt-in options with their choices and the profiles they need, and a status for each feature (verified on a private lab build, hardware-untested public build, or experimental). [`tools/features.py`](../tools/features.py) loads and checks it.

`tools/build.py` builds its `--profile`, `--startup-wav`, `--shutter-wav` and `--record-indicator` arguments and its selection rules from this file, and refuses to start if the record indicator choices disagree with the cue owner (`tone_ref.py`). The assistant builds its checklist from the same file, so it can't offer a combination the builder would refuse. The host tests check this for every combination the window can produce.

Adding an option takes more than a manifest entry: the builder must implement it, and `check_feature_catalog` in `tools/build.py` lists what it implements.

## Known limitations

- **Prototype.** Checked by host tests, and on macOS (Apple Silicon, Python 3.11 with Tk 9.0) by a scripted run of the real window: a real-ROM build, then a copy to an ordinary temporary folder. The Prepare card tab had its own scripted run of the real window, with the device calls replaced by image files. It hasn't been tried on Windows, Linux or a real card.
- No memory estimate *before* building: the margin is reported from `build.json` after a build.
- The final read-back may come from the operating system's cache rather than the card itself. Ejecting properly flushes the card.
- Copying needs a build from the current session; it can't pick an older build folder.
- No consented toolchain download yet. You install the toolchain yourself.
- The assistant can't tell whether BootDisk is enabled in the camera.
- **Prepare card is untested on a real card.** Its host tests use image files only: synthetic FAT32 and exFAT boot areas, including ones rebuilt from the lab cards' recorded geometry that reproduce the lab's recorded hashes and checksums, plus `fsck_msdos` on a real FAT32 image file. No raw device has been read or written by this code yet. In particular these are unverified: the `osascript` password prompt, raw access to `/dev/rdisk*` as root (macOS privacy settings could still deny it), `diskutil unmount`/`mount`/`verifyVolume` on a real card, and the camera booting a card prepared this way.
- Only the Mac's built-in SD slot is offered. USB card readers are refused.
- Only camera-formatted cards are accepted. A card formatted on a computer usually has boot code where the markers go and is refused; format it in the camera.
- The privileged helper runs with the same Python that runs the assistant. Use a Python installed by an administrator (for example from python.org). A Python that your own account can modify (some Homebrew or user installs) is trusted as much as your account is.

## Troubleshooting

- **"Fixed build profile refuses ambient overrides"**: your environment sets a variable such as `CFLAGS` or `PYTHONPATH`. Start the assistant from a shell without it.
- **"The output folder already exists"**: choose another folder; the builder never reuses one.
- **ROM refused**: the ROM isn't an exact R5 `2.2.1` dump, or it's damaged. This is a safety check working.
- **Prepare card refuses the card** ("unexpected bytes where BOOTDISK goes", "geometry mismatch", "not an MBR partition table"): format the card in the camera and try again. These checks protect cards the tool doesn't understand.
- **"The card changed since it was read"**: something wrote to the card between the read and the write (for example a copy). Nothing was written; read the card again.
- **Password prompt cancelled**: nothing was read or written, and the card is mounted again.
