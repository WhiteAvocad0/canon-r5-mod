# Setup assistant (prototype)

> [!WARNING]
> **Prototype.** This is [issue #16](https://github.com/WhiteAvocad0/canon-r5-mod/issues/16): a step-by-step setup wizard. It builds `autoexec.bin` on your computer and can write the two boot markers onto a card you formatted in the camera (macOS only; the exFAT path was verified on the owner's Mac and camera, FAT32 is untested). Then it copies `autoexec.bin` to the card. It runs the same builder and checks as the [command line](usage.md#4-build-offline). Every build is a public repackaged build and is **hardware-untested**. Read the [Getting started guide](getting-started.md) first. You use this at your own risk.

The setup assistant is a desktop window that guides an owner of an original Canon EOS R5 on firmware `2.2.1` through everything, one step at a time, without a terminal. The guided ROM-dump and BootDisk steps (2 and 3) are **coming soon**. Until then you need your own ROM dump, and BootDisk already enabled in the camera ([one-time prerequisites](usage.md#1-prerequisites)). On a Mac the assistant can make a camera-formatted SD card bootable ([step 7](#step-7-prepare-the-boot-card)); elsewhere you need an already bootable card.

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

*Screenshot, described:* a red warning line runs across the top, followed by three buttons that open the **Getting started guide**, the **Prerequisites** and this page. On the left is a list of the nine steps. Each has a mark and a status: ✓ *done*, • *needs action*, – *skipped* or ✗ *blocked*. The current step fills the right side. At the bottom are **< Back** and **Next >**, with a short note saying what is still needed.

Every step has the same shape:
- what it is and why, in plain language;
- **What you do**;
- the step's controls;
- the app's own check, shown as *Check passed*, *To do* or *Blocked*;
- a **Details** button that opens the technical facts.

**Next** works only when the step's check passes (or the step was skipped). **Back** always works. Clicking a step in the list jumps to it, unless it is blocked; a blocked step says what must be finished first.

**Progress is saved** in `~/.r5-build-assistant/wizard.json` after every change, so you can close the window and continue later. On reopening, anything that can be checked again is checked again before it counts: the ROM, the WAV files, the toolchain, and whether the last build still matches its `build.json`. A copy to the card counts only for the same, still-verified build. The file holds paths and your confirmations, never ROM or card data.

**Returning owners:** once the one-time steps (1 to 3) are done, step 1 offers **Update my card**. The short path is Features, Build tools (only if they no longer pass), Build and Copy. **Set up from the start** shows all nine steps again.

### Step 1. Welcome and camera check

*Screenshot, described:* the text says the mod works only on an original EOS R5 on firmware 2.2.1. **What you do** explains where to look: press MENU, open the yellow Setup (wrench) tab, and find *Firmware* on the last Setup page, which must read *Firmware Ver. 2.2.1*. The body of the camera must say *EOS R5*, with no *C* or *Mark II*. Then tick "My camera is an original EOS R5 and shows 'Firmware Ver. 2.2.1'." Below are **Set up from the start** and **Update my card (Features > Build > Copy)**; the second is greyed out until steps 1 to 3 are done. **Details** lists what the assistant never does.

This is your confirmation. The ROM check in step 2 verifies the firmware itself.

### Step 2. ROM dump (one time; guided dump coming soon)

*Screenshot, described:* a sentence explains that the ROM stays on this computer. Below it is a read-only path box and a **Choose ROM...** button. After you pick a file, a status line turns green: "ROM accepted for xpan-crop: Canon EOS R5 firmware 2.2.1, 67,108,864 bytes, 178 fingerprinted regions match." If the ROM is refused, the line is red and gives the builder's reason, for example "Input size must be exactly 67108864 bytes" or "firmware signature mismatch".

The check is the builder's own strict admission (`build.check_rom`) for the profile and record indicator you selected. It reads the ROM only: it doesn't need the toolchain and writes nothing. Changing the profile or the indicator runs the check again, because an XPan build and a build with the indicator check extra fingerprinted pages. There is no way to force a refused ROM.

If you don't have a dump yet, the box *Make a ROM dump (coming soon)* will guide you. The app will prepare a Canon Basic card, and the camera saves its ROM to it; dumping only reads and changes nothing in the camera. It uses the same safeguards as the boot card. Its exact R5 details are still being researched, so for now the button is greyed out.

### Step 3. Enable BootDisk (one time; guided version coming soon)

*Screenshot, described:* the text explains that a stock R5 ignores programs on the card. The BootDisk setting tells it to look for one at power-on. It is stored inside the camera and is **permanent**: formatting cards doesn't clear it, and there is no official way back. A box headed *Guided BootDisk (coming soon)* holds the greyed-out **Prepare the BootDisk card...** and **Disable BootDisk...** buttons. Below it are a required acknowledgement ("I understand that BootDisk is a permanent setting inside the camera, that there is no official way back, and that I use this at my own risk.") and "BootDisk is already enabled on my camera (it was confirmed with a readback when it was enabled)". The second box only works after the acknowledgement.

For now this step only records your confirmation; the check says *confirmed by you (not verified by the app)*. The owner-approved guided version will work like this:
- the app prepares a Canon Basic card with the same backup, confirmation, read-back and undo safeguards as the boot card;
- you run it yourself in the camera; nothing happens automatically;
- the result is verified by readback;
- a **Disable BootDisk** path undoes it.

The app itself never writes the camera's firmware or NVRAM. The exact R5 card contents, trigger, enable/disable calls and the readback location are still being researched.

### Step 4. Choose features

*Screenshot, described:* two radio buttons, **Custom startup and shutter sounds (shutter-feedback)** and **XPan 2.71:1 crop (silent) (xpan-crop)**. Each has a short description and a grey status line, for example "Status: Verified on a private lab build. Worked on the maintainer's private lab builds. Those builds share the feature code but also carry extra diagnostics, so their compiled images differ. Your public build is hardware-untested."

Below them is a **Movie record indicator** box with a drop-down menu (Off, Ding, Chirp, Tone). The text under the menu gives the status of the chosen cue. Ding was tested on the REC press on a private lab build. Chirp ran in a private A/B trial with the same trigger, and Tone ran on an earlier private build with a different trigger (the start request). Neither has run in this public single-cue configuration, so both are marked experimental. With **shutter-feedback** selected, the menu is greyed out and says "The record indicator is part of the XPan profile only (it needs xpan-crop)."

Then come two boxes, **Startup sound (WAV)** and **Shutter sound (WAV)**, each with a **Choose WAV...** button. They work only for shutter-feedback. Under xpan-crop they are greyed out with the reason "Not used by xpan-crop: this profile adds no custom sounds." Each WAV is checked by the builder's own WAV admission, which needs 48 kHz mono 16-bit PCM. A green line then shows its length, for example "WAV accepted: 48 kHz mono 16-bit, 300 ms, 28,800 bytes embedded." A red line gives the reason the WAV was refused. [usage.md](usage.md#3-convert-your-audio-clips) has an `ffmpeg` command to convert clips.

XPan and custom sounds aren't offered together. The XPan profile is silent by design, and private lab estimates suggest both together would exceed the camera's free memory. That combination has not been tested.

### Step 5. Build tools

*Screenshot, described:* installation instructions, a button that opens the xPack releases page, a path box with **Choose bin folder...** and **Detect again** buttons, and a status line.

At start-up the assistant looks for an existing GNU Arm `15.2.1` toolchain. It looks on your `PATH`, under `~/tools/`, your home folder and `~/Downloads/`, in xPack's own install folders and in the usual system folders. Each candidate goes through the builder's toolchain admission (target `arm-none-eabi`, GCC `15.2.1`). If nothing is found, download **xPack GNU Arm Embedded GCC 15.2.1-1.1** yourself, unpack it, and choose its `bin` folder. **This prototype never downloads anything.**

### Step 6. Build and verify

*Screenshot, described:* an output folder path with a **Change...** button, then a yellow list headed "Before you can build:" (for example "Pick your ROM dump."), or "Ready to build." Below these are the **Build autoexec.bin** button, a scrolling log, a result area, a horizontal memory bar and two buttons, **Open output folder** and **Open 'what's in this build'**.

- The output folder must be new. By default it's `build/gui-<profile>[-<cue>]-<date>-<time>` inside the repository, which git ignores.
- **Build** runs `python3 tools/build.py ...` with the selected arguments in a separate process. The log shows each step (for example `gcc observer.o`, `ld handoff.elf`). The first line is the exact command, so you can repeat it in a terminal.
- On success, the result area shows the path of `autoexec.bin` and its SHA-256, which the assistant recalculates and checks against `build.json`. It also shows a memory line, for example "Canon memory left after this build: 907,256 bytes (safety floor 524,304; margin 382,952 bytes)." The bar shows the remaining Canon memory as a share of the pool.
- It also writes `BUILD-CONTENTS.txt` in the output folder: a plain-language list of the profile, options and their status, the WAVs used, the image's SHA-256, the memory margin, the `source_manifest_sha256` to quote in test reports, and what the build does *not* include.
- If the builder refuses, the result area shows its last error line in red.

### Step 7. Prepare the boot card

> [!CAUTION]
> **Writes raw boot sectors. Prototype, macOS only.** The owner decided on 18 November 2026 to offer this step in the assistant. The exFAT path was verified on 18 November 2026 on the owner's Mac (macOS 27.3) with a camera-formatted 128 GB exFAT card, which the camera then booted; FAT32 cards, Windows and Linux are untested. On that run sectors 12, 23, 0 and 11 were written, the read-back and `diskutil verifyVolume` passed, a second Prepare said *Already prepared*, and the camera booted the card and ran a private lab build. The command-line builder still never writes cards.

*Screenshot, described:* a bold red warning, "Format this card IN THE CAMERA first. Formatting erases everything on the card — copy your photos and videos to your computer before formatting.", then a short explanation and a red line saying the real-device path is untested. Below them are a **Find SD cards** button with a drop-down of the cards found, the chosen card's details (model, size, filesystem, volume name, mount state, device), and a "Not offered:" line naming every other disk with its reason. Then come the **Prepare card**, **Undo (restore the latest backup)** and **Restore from a chosen backup...** buttons, a yellow "Next:" list, the result line and a box with its details.

This step writes the two Canon boot markers (`EOS_DEVELOP`, `BOOTDISK`) so that a camera whose BootDisk setting is already enabled loads `autoexec.bin` from the card. It does not enable BootDisk and never writes the camera. If your card is already prepared, **My card is already prepared (skip this step)** marks the step *skipped*.

1. **Format in the camera first.** The warning at the top says so. The assistant never formats, partitions or erases anything; the owner decided formatting stays out of the tool. The warning is advice. The real check is the boot-sector layout: on FAT32 the boot code where `BOOTDISK` goes must be empty, and on exFAT it must be the `F4` fill. A card formatted by Windows, or a FAT32 card formatted on a Mac, is refused. A card formatted as exFAT by macOS can't be told apart from a camera-formatted one (both use `F4`), which is another reason to format in the camera. Formatting removes the markers, so prepare the card again after every format.
2. **Choose the card.** **Find SD cards** lists only cards in an SD card slot (macOS reports the *Secure Digital* protocol). The card must be removable, ejectable, physical media with 512-byte sectors and an MBR with exactly one FAT32 or exFAT partition, writable, and between 2 GB and 2 TiB. The disk macOS runs from, internal disks, disk images, system media, partitions mounted outside `/Volumes` and USB devices are refused with their reason. That includes USB card readers, because macOS can't tell them from other USB drives. An unmounted card is still listed (marked *not mounted*), so a half-written card can be undone.
3. **Prepare card.** One button does the rest:
   - **Read and back up, automatically.** The assistant checks that it is still the chosen card and unmounts its partition (`diskutil unmount diskNsM`; it never ejects). macOS's authorization window opens for read-only access to the card's whole-disk device, through `authopen` (see below). The assistant reads the 512-byte MBR and the partition's first 24 sectors (12,288 bytes). It checks that the partition start agrees between the MBR, macOS's report and the boot sector itself, the boot sector's own geometry, and the exFAT boot checksums. It saves `mbr.bin`, `boot-area.bin` and `manifest.json` to a new folder, `~/R5-card-backups/<date>-<time>-<microseconds>-<FAT32|exFAT>-<serial>-before-marking/`, and reads them back to compare. A backup folder is never reused (a `-2` suffix avoids any collision) and never deleted by the assistant. The backup path appears in the result details.
   - **Already prepared?** If valid markers are already there, the card is mounted again and the result says **Already prepared: nothing to write**. There is no dialog and no second password prompt.
   - **Confirm.** Otherwise one dialog names the card from that read, for example "Prepare EOS_DIGITAL (126.9 GB exFAT SD card)?", with the model, the device, the sectors to write and the backup folder. The card stays unmounted while the dialog is open, so nothing on the Mac can change its boot sectors between the read and the write. **Cancel** mounts it again and reports *Cancelled. Nothing was written.*
   - **Write and verify.** After **OK** the assistant checks that the confirmed read is still the current one and that it is still the same card. macOS's authorization window opens again, this time for read/write access. The assistant writes only if the MBR, the whole 24-sector boot area and the target sectors still hash to the values from the read. Otherwise it reports *refused. Nothing was written*. It writes the sectors in the planned order with a flush after each, and reads back the whole boot area and the MBR. The assistant compares every written byte, checks that the MBR is unchanged and that the boot area validates with the markers present, runs `diskutil verifyVolume diskNsM` and mounts the card again. It records the outcome in `result-<time>.json` in the backup folder.

   On FAT32 one sector is written. `EOS_DEVELOP` replaces the boot-sector volume label field at byte 71 (`0x47`); Finder's volume name, which lives in the root directory, doesn't change. `BOOTDISK` goes at byte 92 (`0x5C`). The backup boot sector is left unchanged, as on the lab card that booted. Disk Utility's check may therefore report that the backup boot sector differs; that is by design, and the result says so. exFAT uses different offsets: `BOOTDISK` at byte 122 and `EOS_DEVELOP` at byte 130 of both boot regions, plus both checksum sectors. That makes four sectors, 12, 23, 0 and 11: the backup region first, and a region with a currently invalid checksum before the other. Every intermediate state keeps at least one valid exFAT boot region.
4. **Undo or restore.** **Undo** reads the card first, then uses the newest backup taken before its markers were written (`...-before-marking`, never `...-before-restore`) whose MBR and boot-area bytes outside the markers match the card as read, so it can't pick another card's backup or a half-marked state. **Restore from a chosen backup...** uses a backup folder you choose; its files are checked against its manifest. Both work like Prepare: read (also from an unmounted, half-written or unmountable card, identified by its disk and partition, not by a mount), an automatic backup of the current state (`...-before-restore`), one confirmation dialog, then write and verify. The card must be the backed-up one: same size, partition, slot and model, the same MBR, and every boot-area byte outside the marker ranges unchanged. The exFAT `VolumeFlags` and `PercentInUse` bytes are exempt and kept as they are now. Each marker range must hold either the original or the prepared bytes.
5. **Report.** The result says *done and verified*, *Already prepared*, *Cancelled. Nothing was written*, *refused. Nothing was written* (for example after the card changed since it was read) or *not written* (for example if the card was removed). Otherwise it says which sectors were written, and which were *possibly written* (a write that was started but not confirmed), what failed, and the backup to undo from. A hanging `diskutil` never loses the report. The card is mounted again in every case, or you're told to reinsert it.

Only one card operation runs at a time. While one runs, or while the confirmation is open, the [Copy to card](#step-8-copy-autoexecbin) tab is blocked, and the other way round.

**What runs, exactly (macOS).** As you: `diskutil info -plist /`, `diskutil list -plist physical` and `diskutil info -plist diskN` / `diskNsM` to find and re-check cards, then `diskutil unmount diskNsM`, `diskutil mount diskNsM` and `diskutil verifyVolume diskNsM`. Every `diskutil` call has a timeout.

Raw access uses macOS's own `authopen`, the method Raspberry Pi Imager uses. **No code runs as root**, and the assistant never sees your password. For each step the assistant starts:

```text
/usr/libexec/authopen -stdoutpipe -o 0 /dev/rdiskN     # read step  (open(2) flags O_RDONLY)
/usr/libexec/authopen -stdoutpipe -o 2 /dev/rdiskN     # write step (open(2) flags O_RDWR)
```

`authopen` asks macOS for the right to open exactly that whole-disk device (`sys.openfile.readonly./dev/rdiskN` or `sys.openfile.readwrite./dev/rdiskN`). macOS shows its own authorization window. `authopen` then opens only that device and passes the open file descriptor back to the assistant over a UNIX socket (`SCM_RIGHTS`); its standard output is one end of a socket pair. The assistant then checks the descriptor:

- it must be a character device whose device number (`st_rdev`) equals that of `/dev/rdiskN` for the card you chose (diskutil identity is re-checked too);
- its access mode must be the one requested.

All reads and writes happen in the assistant's own process on that descriptor, at absolute offsets: the MBR at byte 0 and the boot area at the partition start. The descriptor is always closed at the end of the step. The partition start must agree three ways: the MBR entry's LBA × 512, diskutil's `PartitionMapPartitionOffset`, and the boot sector's own field (exFAT PartitionOffset at byte 64, FAT32 HiddenSectors at byte 28, in sectors). Any disagreement is refused.

Writes keep the rules the earlier root helper enforced:

- only single 512-byte sectors chosen from partition sectors 0, 11, 12 and 23 (the only sectors any plan touches), at most four, written in the planned order;
- and only if the MBR, the whole 24-sector boot area and the target sectors still hash to the values from the planning read.

Each sector is flushed with `F_FULLFSYNC` (falling back to `fsync` where the device doesn't support it). Then the target sectors, the whole boot area and the MBR are read back. A sector whose write was started but not confirmed is reported as *possibly written*. The MBR is only ever read. For the lab's 128 GB exFAT layout the four writes go to partition sectors 12, 23, 0 and 11; for FAT32 it is sector 0.

**What macOS grants.** The right covers only the one device named, for one open. The authorization window is macOS's own and names `authopen`, not this assistant.

**Residual limit.** Malware already running as you could show its own fake password window or change the assistant itself. These checks can't protect against that.

**Windows and Linux:** **Find SD cards** lists cards on a best-effort basis (PowerShell `Get-Disk` / `lsblk`), **untested**. Preparing a card is offered on macOS only.

### Step 8. Copy autoexec.bin

*Screenshot, described:* an explanation, a path box with **Choose card folder...**, a report (folder, drive size, whether a `DCIM` folder exists, any existing `autoexec.bin`, plus warnings), a confirmation checkbox, and the **Copy autoexec.bin to card** button.

This is only for a card that is **already bootable**. Choose the card's top folder, for example `/Volumes/EOS_DIGITAL` on a Mac, `E:\` on Windows, or the card's mount folder on Linux. Then:

1. The built `autoexec.bin` must still match `build.json` and pass the image-format check.
2. The folder is checked. The copy is **refused** if the folder is the Windows system drive (from `%SystemDrive%`, usually `C:\`) or the Windows folder, a system folder such as `/`, `/usr` or `/Volumes` itself, or your home folder or a folder that contains it. It is also refused if the folder isn't writable, has more than one spelling of `autoexec.bin`, or has an `autoexec.bin` that isn't a regular file. A removable drive root such as `E:\`, `/Volumes/EOS_DIGITAL`, `/media/<you>/<card>` or `/run/media/<you>/<card>` is allowed. The copy **asks for confirmation** if the folder isn't the root of a drive, has no `DCIM` folder, or the drive size is unusual for an SD card (under 2 GB or over 2 TB). The confirmation covers only the warnings you were shown: a new warning found at copy time stops the copy.
3. An existing `autoexec.bin`, found in any upper/lower-case spelling, is copied first to `card-backup/` inside the build folder **on your computer** and read back. If it's already identical to the new build, nothing is written.
4. The new file is written to a temporary `R5GUI-XXXXXXXX.TMP` on the card, read back and compared. It is then renamed to `AUTOEXEC.BIN`, or to the existing file's spelling.
5. The final file is read back and its SHA-256 compared with `build.json`. If they differ, you're told where the backup is and not to use the card.
6. You're told to eject the card using your operating system (Finder eject, "Safely Remove Hardware", or your file manager).

The copy step **never** formats or partitions a card, never opens a raw disk, never writes boot sectors, and never deletes anything except its own temporary file from the current copy. Temporary files left by an interrupted earlier copy are reported and left alone.

### Step 9. First camera test

*Screenshot, described:* a checklist for the first power-on:
- camera off, with USB and HDMI unplugged and only the prepared SD card inserted;
- powered on normally;
- a full-frame control photo;
- the features in this build;
- waited for the card light before switching off.

It is blocked until this build has been copied in step 8. **Open the test report form** opens the right GitHub form for the profile you built. If the camera freezes or shows an error, remove the battery, then the card, and power on without the card. Never upload your ROM, photos or card images.

### What this tool cannot do

Step 1's **Details** lists what is out of scope. The assistant never changes the camera by itself: the ROM dump and BootDisk steps are things you run in the camera (their guided versions are coming soon). It doesn't format or partition cards, write the MBR, write anything except the boot-marker sectors shown in [Prepare card](#step-7-prepare-the-boot-card), write the camera's firmware or NVRAM, download the toolchain, or test anything on the camera. See [Getting started → Step 2](getting-started.md#step-2-check-the-one-time-prerequisites).

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
- **Prepare card: verified for exFAT on one Mac and camera only.** On 18 November 2026 the owner's Mac (macOS 27.3) prepared a camera-formatted 128 GB exFAT card through `authopen`. The read-back and `diskutil verifyVolume` passed, a second Prepare said *Already prepared*, and the camera booted it. FAT32 cards, other Macs and macOS versions, Windows and Linux are untested. The host tests use image files only: synthetic FAT32 and exFAT boot areas (including ones rebuilt from the lab cards' recorded geometry that reproduce the lab's recorded hashes and checksums), plus `fsck_msdos` on a real FAT32 image file.
- Only the Mac's built-in SD slot is offered. USB card readers are refused.
- "Camera-formatted" is checked from the boot sectors, not proven. A FAT32 card formatted on a computer usually has boot code where the markers go and is refused. An exFAT card formatted by macOS looks the same as a camera-formatted one and is accepted, which is why the warning says to format in the camera.
- Each Prepare or Undo opens macOS's authorization window twice (once to read, once to write) unless nothing needs writing. Whether macOS remembers the first authorization for the second is not known yet.
- Still to observe: `authopen`'s exit status and message when its window is cancelled, and whether Disk Utility flags the unmarked FAT32 backup boot sector. On the owner's Mac a root shell started by `osascript` was denied raw access to the card, which is why the assistant uses `authopen`.

## Troubleshooting

- **"Fixed build profile refuses ambient overrides"**: your environment sets a variable such as `CFLAGS` or `PYTHONPATH`. Start the assistant from a shell without it.
- **"The output folder already exists"**: choose another folder; the builder never reuses one.
- **ROM refused**: the ROM isn't an exact R5 `2.2.1` dump, or it's damaged. This is a safety check working.
- **Prepare card refuses the card** ("unexpected bytes where BOOTDISK goes", "geometry mismatch", "not an MBR partition table"): format the card in the camera and try again. These checks protect cards the tool doesn't understand.
- **"refused. Nothing was written" / "changed since it was read, or it is another card"**: the MBR or boot sectors differ from the read the plan was made from (the card was swapped, reinserted or changed). Nothing was written; press Prepare again.
- **Password prompt or confirmation cancelled**: nothing was written, and the card is mounted again.
- **"authopen could not open /dev/rdiskN"**: macOS refused access to the device; the message gives authopen's own reason. Nothing was written.
- **The card won't mount after a failed write**: it is still listed (*not mounted*). Choose it and press **Undo**.
