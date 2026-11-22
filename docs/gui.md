# R5 Mod Assistant (prototype)

> [!WARNING]
> **Prototype.** This is [issue #16](https://github.com/WhiteAvocad0/canon-r5-mod/issues/16): a step-by-step setup wizard. It builds `autoexec.bin` on your computer and can write the two boot markers onto a card you formatted in the camera (macOS only; the exFAT path was verified on the owner's Mac and camera, FAT32 is untested). Then it copies `autoexec.bin` to the card. It runs the same builder and checks as the [command line](usage.md#4-build-offline). Every build is a public repackaged build and is **hardware-untested**. Follow the steps in the Assistant. The Help menu includes the guides for reference. You use this at your own risk.

R5 Mod Assistant is a desktop window that guides an owner of an original Canon EOS R5 on firmware `2.2.1` through everything, one step at a time, without a terminal. In steps 2 and 3 it can prepare a `<= 32 GB` FAT32 SDHC Canon Basic card for you to run in the camera to make a read-only ROM dump or enable/disable BootDisk, and verify the returned files by readback (macOS gets one-click raw `SCRIPT` boot-sector marking through `authopen`; other systems can stage `extend.m` and `script.req` onto a card that already carries `SCRIPT` at `0x1F0`). On a Mac the assistant can also make a camera-formatted SD card bootable for `autoexec.bin` ([step 7](#step-7-prepare-the-boot-card)); elsewhere you need an already bootable card.

## Download the app

There is a ready-made, **unsigned** app for macOS and Windows on the repository's [Releases](https://github.com/WhiteAvocad0/canon-r5-mod/releases) page (prereleases tagged `app-v<version>`). It is an untested prototype like the rest of the assistant. On Linux, [launch it from source](#launch).

| Computer | Download |
|---|---|
| Mac with Apple Silicon | `R5-Mod-Assistant-<version>-macos-arm64.dmg` |
| Intel Mac | `R5-Mod-Assistant-<version>-macos-x86_64.dmg` |
| Windows 10 or 11 (64-bit, x64) | `R5-Mod-Assistant-<version>-windows-x64.zip` |

`SHA256SUMS.txt` on the same page lists each file's SHA-256.

Either way you still need the **GNU Arm toolchain** with GCC `15.2.1` ([step 5](#step-5-build-tools): xPack `arm-none-eabi-gcc` `15.2.1-1.1`, or Arm GNU Toolchain `15.2.Rel1`) and your own ROM dump. The app contains neither, and it never downloads anything.

### macOS

1. Open the `.dmg` and drag **R5 Mod Assistant** onto **Applications**.
2. Open it from Applications. Because the app isn't signed with an Apple Developer ID or notarized, macOS first says it can't verify the app:
   - **macOS 15 or later:** click **Done**, open **System Settings > Privacy & Security**, scroll to the message about R5 Mod Assistant and click **Open Anyway**, then confirm.
   - **macOS 14 or earlier:** Control-click (or right-click) the app, choose **Open**, then **Open** again.

   You only do this once per version. Advanced: `xattr -dr com.apple.quarantine "/Applications/R5 Mod Assistant.app"` removes the download mark instead; only do that for a `.dmg` you downloaded from this repository.
3. macOS may ask whether the app may use your **Downloads** folder (the toolchain search looks there; you can decline and choose the toolchain's `bin` folder yourself) and **removable volumes** (the card you choose).

### Windows

1. Right-click the `.zip` and choose **Extract All...**, for example into your Documents folder. Run the app from the extracted **R5 Mod Assistant** folder, not from inside the zip, and keep its `_internal` folder next to `R5 Mod Assistant.exe`.
2. Open **R5 Mod Assistant.exe**. Because the app isn't signed with a code-signing certificate, Microsoft Defender SmartScreen may say *Windows protected your PC*: click **More info**, then **Run anyway**. Windows remembers this for that copy of the app.
3. Install the toolchain from Arm's Windows installer or unpack the xPack or Arm `.zip`; the app looks in `%USERPROFILE%`, its `tools` and `Downloads` folders, `%APPDATA%\xPacks`, `C:\xpack-arm-none-eabi-gcc-15.2.1-*` and `Program Files (x86)\Arm GNU Toolchain arm-none-eabi\15.2*`, or choose its `bin` folder (the one with `arm-none-eabi-gcc.exe`) yourself.
4. **Card preparation is macOS only.** Writing a card's boot markers ([step 7](#step-7-prepare-the-boot-card)) and the one-click Canon Basic cards in steps 2 and 3 are offered on a Mac only. On Windows, prepare the card on a Mac first, tick *My card is already prepared* in step 7, then copy `autoexec.bin` to that card in [step 8](#step-8-copy-autoexecbin).

The Windows app has only been checked by its CI self-test on GitHub's `windows-latest` runner. A real build (toolchain, ROM) and the copy to a card haven't been tried on Windows.

### What is different from running it from source

- New build folders go to `R5-builds` in your home folder (`~/R5-builds` on macOS, `%USERPROFILE%\R5-builds` on Windows), because the app's own folder isn't meant to change. Saved progress is the same `~/.r5-build-assistant/wizard.json`, so the app and a checkout share it.
- The app carries this repository's tracked files from the tagged commit byte for byte (`Read me first.txt` and, on macOS, `R5SourceCommit` in `Contents/Info.plist` name the commit), plus Python `3.13` and Tcl/Tk. It runs the same builder and checks, so a build from the app has the same source manifest as a command-line build of that commit.
- On Windows, the programs the app starts (GCC, PowerShell, the builder) run without a console window.

### Building the app yourself

Each package is built by the `desktop-app` GitHub Actions workflow, which also starts the built app with `--self-test` (it builds the window hidden, shows every step, starts the builder with arguments it refuses, and exits; on Windows it runs from the unzipped folder). To build it yourself on a Mac or a Windows PC, use a Python `3.11` or newer with Tkinter (python.org) and a clean checkout:

```sh
python3 -m pip install -r packaging/requirements.txt
python3 packaging/build_app.py --self-test
```

It writes the app under `build/desktop-app/dist/` and `build/desktop-app/R5-Mod-Assistant-<version>-<macos|windows>-<arch>.<dmg|zip>`. The version is `VERSION` in [`tools/gui/bundle.py`](../tools/gui/bundle.py).

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

*Screenshot, described:* on the left is a grey sidebar with the app's name, a *Prototype* badge and the list of the nine steps, with a *Tools* section under it that holds [Screenshots](#screenshots). Each step shows a round mark and its status under its name: a ✓ for *Done*, – for *Skipped*, and the step number for *To do* or *Locked* (locked steps are greyed out). The current step is highlighted with a grey background and bold title. Steps that need an SD card also name its size under the title, and the step's heading repeats it as a badge: **SD card ≤ 32 GB** for the Canon Basic steps 2 and 3 (FAT32 SDHC), **Any SD card** for steps 7 to 9. Under the steps, a line names the path you are on, with a link to switch: **Update my card instead** (once steps 1 to 3 are done) or **Show all nine steps**. The current step fills the right side. Reference guides are available from the application’s **Help** menu. At the bottom are **Back** and the **Next**, with a short note saying what is still needed.

The window uses the system font (SF Pro on macOS, Segoe UI on Windows, Cantarell, Ubuntu or DejaVu Sans on Linux), rounded push buttons, sentence-case labels and thin dividers. A blue outline marks keyboard focus, and small amber and red icons flag warnings and errors. On macOS, Windows and GNOME it starts in the desktop's light or dark appearance; set `R5_ASSISTANT_APPEARANCE=light` or `dark` to choose one yourself. The fonts, colours and spacing are defined in [`tools/gui/theme.py`](../tools/gui/theme.py).

Each step opens on the current task. Additional instructions, equipment lists and technical details expand in place. The two camera workflows keep the computer, camera and returned-card actions on separate screens.

**Next** works only when the step's check passes (or the step was skipped); on Features, pressing **Next** is what confirms your choice. On the last step the button reads **Finish** and works once that step is done; it then says what to do next. **Back** always works. Clicking a step in the list jumps to it, unless it is locked; clicking a locked step shows, next to **Next**, what must be finished first.

**Progress is saved** in `~/.r5-build-assistant/wizard.json` after every change, so you can close the window and continue later. On reopening, anything that can be checked again is checked again before it counts: the ROM, the WAV files, the toolchain, and whether the last build still matches its `build.json`. A copy to the card counts only for the same, still-verified build. The file holds paths and your confirmations, never ROM or card data.

**Returning owners:** once the one-time steps (1 to 3) are done, step 1 and the sidebar offer **Update my card**. The short path is Features, Build tools (only if they no longer pass), Build, Prepare the boot card and Copy. The boot card is checked again because formatting the card in the camera removes its markers; **Prepare card** writes nothing when they are still there, and **My card is already prepared** skips it. **Show all nine steps** in the sidebar returns to the full setup from any step.

### Step 1. Welcome and camera check

Check the body for **EOS R5**, then look for **Firmware Ver. 2.2.1** in the camera’s Setup menu. Confirm the match before continuing. **What will I need for setup?** lists equipment for the full wizard. Returning owners who completed the one-time setup can choose **Update my card**. The screenshot converter is in the sidebar’s Tools section.

This is your confirmation. The ROM check in step 2 verifies the firmware itself.

### Step 2. ROM dump (one time)

The wizard calls this step **Copy camera firmware**. It remembers your place if you close the window while the card is in the camera.

1. **At your computer**, back up the photos on your camera-formatted FAT32 SDHC card (32 GB or smaller). Insert it in the Mac’s built-in slot and choose **Find my SD card**. Select the card, then **Prepare this card**. Check the named card before confirming. For an existing script-marked card, open **Which SD card can I use?** for the folder staging option.
2. **At your camera**, follow the on-screen checklist. Keep it open until you have returned the card to the computer.
3. **Back at your computer**, choose **Import and check firmware copy** and select the returned card. The Assistant saves a private copy locally and checks it with the builder. **Continue** becomes available after verification.

If you already have the firmware file from your own camera, choose **I already have a firmware copy**. If you already ran the camera step, choose **I’m returning with a card from the camera**. Both routes are available on Windows and Linux too.

### Step 3. Enable BootDisk (one time)

**Enable card startup** explains the persistent BootDisk setting. Read the acknowledgement before preparing a card. Select the small camera-formatted card and confirm the named device. After preparation, follow the camera checklist. Return the card to your computer and choose **Check the returned card**. The Assistant verifies the setting from the returned files.

For an earlier setup, expand **My camera already starts from a card**. Checking an existing firmware copy can verify the setting. Confirming a previous check yourself records an owner confirmation. If step 2 already verified the setting, continue straight to the feature choices.

**Other card options and recovery** contains staging for a script-marked card and the hardware-untested disable operation. The camera-side action always runs in the camera when you use the prepared card.

### Step 4. Choose features

*Screenshot, described:* two cards, each with a radio button, **Custom startup and shutter sounds** (`shutter-feedback`) and **XPan 2.71:1 crop (silent)** (`xpan-crop`); the chosen one has a graphite outline. Each has a short description. Open **What has been tested?** for its evidence, for example "Status: Verified on a private lab build. Worked on the maintainer's private lab builds. Those builds share the feature code but also carry extra diagnostics, so their compiled images differ. Your public build is hardware-untested."

Below them is a **Movie record indicator** box with a drop-down menu (Off, Ding, Chirp, Tone). The text under the menu gives the status of the chosen cue. Ding was tested on the REC press on a private lab build. Chirp ran in a private A/B trial with the same trigger, and Tone ran on an earlier private build with a different trigger (the start request). Neither has run in this public single-cue configuration, so both are marked experimental. Below it, a **Movie record stop cue** box (Off, Dong) adds a lower two-note dong once when a take ends. It is independent of the start cue. Dong was tested together with the ding on a private lab build; without a start cue it has not run on hardware. Both menus are shown only with **xpan-crop** selected; they are part of the XPan profile only.

Then come two boxes, **Startup sound (WAV)** and **Shutter sound (WAV)**, each with a **Choose WAV...** button. They are shown only for shutter-feedback; xpan-crop adds no custom sounds. Each WAV is checked by the builder's own WAV admission, which needs 48 kHz mono 16-bit PCM. A check mark then shows its length, for example "WAV accepted: 48 kHz mono 16-bit, 300 ms, 28,800 bytes embedded." An error message gives the reason the WAV was refused. [usage.md](usage.md#3-convert-your-audio-clips) has an `ffmpeg` command to convert clips.

XPan and custom sounds aren't offered together. The XPan profile is silent by design, and private lab estimates suggest both together would exceed the camera's free memory. That combination has not been tested.

### Step 5. Build tools

The compiler check appears first. If the compiler is missing, the page offers the xPack download and instructions for choosing its bin folder. You can also choose a different folder or search again.

At start-up the assistant looks for an existing GNU Arm `15.2.1` toolchain. It looks on your `PATH`, under `~/tools/`, your home folder and `~/Downloads/`, in xPack's own install folders and in the usual system folders. Each candidate goes through the builder's toolchain admission (target `arm-none-eabi`, GCC `15.2.1`). If nothing is found, download **xPack GNU Arm Embedded GCC 15.2.1-1.1** yourself, unpack it, and choose its `bin` folder. **This prototype never downloads anything.**

### Step 6. Build and verify

Check the requirements, then choose **Build my mod**. **Build folder** shows where the next build will be saved and lets you change it. The result shows whether the file verified, with the output folder, contents and build log available below.

- The output folder must be new, so after each build the box shows a fresh folder for the next one; the result box names where the build went. By default it's `build/gui-<profile>[-<cue>][-<stop cue>]-<date>-<time>` inside the repository, which git ignores.
- **Build** runs `python3 tools/build.py ...` with the selected arguments in a separate process. The log shows each step (for example `gcc observer.o`, `ld handoff.elf`). The first line is the exact command, so you can repeat it in a terminal.
- On success, the result area shows the path of `autoexec.bin` and its SHA-256, which the assistant recalculates and checks against `build.json`. It also shows a memory line, for example "Canon memory left after this build: 907,256 bytes (safety floor 524,304; margin 382,952 bytes)." The bar shows the remaining Canon memory as a share of the pool.
- It also writes `BUILD-CONTENTS.txt` in the output folder: a plain-language list of the profile, options and their status, the WAVs used, the image's SHA-256, the memory margin, the `source_manifest_sha256` to quote in test reports, and what the build does *not* include.
- If the builder refuses, a red result box headed "Build refused or failed" shows its last error line.

### Step 7. Prepare the boot card

> [!CAUTION]
> **Writes raw boot sectors. Prototype, macOS only.** The owner decided on 18 November 2026 to offer this step in the assistant. The exFAT path was verified on 18 November 2026 on the owner's Mac (macOS 27.3) with a camera-formatted 128 GB exFAT card, which the camera then booted; FAT32 cards, Windows and Linux are untested. On that run sectors 12, 23, 0 and 11 were written, the read-back and `diskutil verifyVolume` passed, a second Prepare said *Already prepared*, and the camera booted the card and ran a private lab build. The command-line builder still never writes cards.

*Screenshot, described:* a warning panel with the bold headline "Format this card IN THE CAMERA first. Formatting erases everything on the card — copy your photos and videos to your computer before formatting." and, under it, what has been tested on this system (FAT32 cards are untested). Below it are a **Find SD cards** button with a drop-down of the cards found, the chosen card's details (model, size, filesystem, volume name, mount state, device), and a "Not offered:" line naming every other disk with its reason. Then come the **Prepare card** button, a progress bar while a card operation runs, a "Next:" list, the result headline and a box with its details. **Restore a card backup** expands to show **Undo (restore the latest backup)** and **Restore from a chosen backup...**.

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

*Screenshot, described:* an explanation, a path box with **Choose card folder...**, a report (folder, drive size, whether a `DCIM` folder exists, any existing `autoexec.bin`, plus warnings), a confirmation checkbox that appears only with warnings, and the **Copy mod to this card** button once a card folder is selected. If the card chosen in step 7 is mounted, its folder is chosen and checked for you when the step opens.

This is only for a card that is **already bootable**. Choose the card's top folder, for example `/Volumes/EOS_DIGITAL` on a Mac, `E:\` on Windows, or the card's mount folder on Linux. Then:

1. The built `autoexec.bin` must still match `build.json` and pass the image-format check.
2. The folder is checked. The copy is **refused** if the folder is the Windows system drive (from `%SystemDrive%`, usually `C:\`) or the Windows folder, a system folder such as `/`, `/usr` or `/Volumes` itself, or your home folder or a folder that contains it. It is also refused if the folder isn't writable, has more than one spelling of `autoexec.bin`, or has an `autoexec.bin` that isn't a regular file. A removable drive root such as `E:\`, `/Volumes/EOS_DIGITAL`, `/media/<you>/<card>` or `/run/media/<you>/<card>` is allowed. The copy **asks for confirmation** if the folder isn't the root of a drive, has no `DCIM` folder, or the drive size is unusual for an SD card (under 2 GB or over 2 TB). The confirmation covers only the warnings you were shown: a new warning found at copy time stops the copy.
3. An existing `autoexec.bin`, found in any upper/lower-case spelling, is copied first to `card-backup/` inside the build folder **on your computer** and read back. If it's already identical to the new build, nothing is written.
4. The new file is written to a temporary `R5GUI-XXXXXXXX.TMP` on the card, read back and compared. It is then renamed to `AUTOEXEC.BIN`, or to the existing file's spelling.
5. The final file is read back and its SHA-256 compared with `build.json`. If they differ, you're told where the backup is and not to use the card.
6. For a build with the **One-press screenshot**, the empty folder `DCIM/R5SHOTS` that the camera writes screenshots into is created inside the card's existing `DCIM` folder. A folder that is already there, and everything in it, is left alone; with no `DCIM` folder nothing is created and you're told to take one photo first (the camera then makes `DCIM`) and create the folder yourself.
7. You're told to eject the card using your operating system (Finder eject, "Safely Remove Hardware", or your file manager).

The copy step **never** formats or partitions a card, never opens a raw disk, never writes boot sectors, and never deletes anything except its own temporary file from the current copy. Temporary files left by an interrupted earlier copy are reported and left alone.

### Step 9. First camera test

*Screenshot, described:* a checklist for the first power-on:
- camera off, with USB and HDMI unplugged and only the prepared SD card inserted;
- powered on normally;
- a full-frame control photo;
- the features in this build;
- waited for the card light before switching off.

It is blocked until this build has been copied in step 8. **Open the test report form** opens the right GitHub form for the profile you built. **Finish** works once every item is ticked. If the camera freezes or shows an error, remove the battery, then the card, and power on without the card. Never upload your ROM, photos or card images.

### What this tool cannot do

Step 1's **Details** lists what is out of scope. The assistant never changes the camera by itself: the ROM dump and BootDisk steps prepare a Canon Basic card that you run yourself in the camera, and verify the returned files by readback. It doesn't format or partition cards, write the MBR, write anything except the planned boot-sector markers (`SCRIPT` at `0x1F0` on FAT32 in steps 2–3, or `EOS_DEVELOP`/`BOOTDISK` in [Prepare card](#step-7-prepare-the-boot-card)), write the camera's firmware or NVRAM directly, download the toolchain, or test anything on the camera. See [Getting started → Step 2](getting-started.md#step-2-check-the-one-time-prerequisites).

## Screenshots

> [!NOTE]
> **The camera saves these files with the One-press screenshot option** (`--screenshot rate`, [docs/screenshot.md](screenshot.md), [issue #15](https://github.com/WhiteAvocad0/canon-r5-mod/issues/15)). Its JPEG screenshots are ordinary photos and need no conversion; this tool turns the on-screen display files (`SS_nnnnn.YCA`) into PNG.

A screenshot build also saves what the camera's screen shows as `DCIM/R5SHOTS/SS_nnnnn.YCA` on the SD card. The camera's own ▶ playback can't show these files, so this tool turns them into ordinary PNG pictures on your computer. It isn't one of the setup steps: open it from **Screenshots** in the sidebar's *Tools* section or from the button on step 1. **← Back to setup** returns to the step you were on.

*Screenshot, described:* the eyebrow says *Tool · Not part of setup* above the heading *Screenshots*. A note says which build saves these files. Below are two path boxes: **Choose card or folder...** for where the screenshots are, and **Change...** for where the PNG files go, each with a status line. Then come the **Convert to PNG** button and **Open output folder**, a progress bar while it runs, and a result box with one line per file.

1. Put the card in your computer, or copy its `R5SHOTS` folder to it. Choose the card's top folder (for example `/Volumes/EOS_DIGITAL`), its `DCIM` folder, the `R5SHOTS` folder itself, or a folder that holds a copied `R5SHOTS`. The status line says how many `SS_*.YCA` files it found and whether they're on a camera card.
2. The PNG files go to `~/Pictures/R5 screenshots/<today's date>` unless you choose another folder. The folder must be on your computer: a folder on the card, inside the folder you chose, or the home folder or a system folder is refused.
3. Press **Convert to PNG**. The result says how many were converted, how many were already converted, and which files couldn't be converted and why. **Open output folder** opens the folder. Eject the card before you remove it.

What it does and doesn't do:
- It **only reads** the card and the folder you chose. It never writes, renames or deletes anything there.
- It **never overwrites** a file. A screenshot already converted into the same output folder (same name, size and contents, recorded in `.r5-screenshots.json` there) is skipped. If a different `SS_nnnnn.png` is already there, for example after the card was formatted and the numbers started again, the new picture is saved as `SS_nnnnn-<8 hex digits>.png` next to it.
- Each file must be exactly what the lab build writes: an `R5SS` header (version 3 with a 512-byte header, or the older 32-byte versions 1 and 2), the `UYVA` layout (4:2:2 YCbCr with alpha), and the rear LCD's 1024×682 or the viewfinder's 1600×1200 layer, with a file size that adds up. Anything else is refused and listed, and the other files are still converted.
- The picture is the camera's on-screen display layer only (menus, shooting and playback information). The live image under it isn't saved, so those areas show the colour the layer stores there. The colours use the BT.601 matrix at full range, which is what the lab used, and alpha isn't used. On the lab's seven sample files the PNGs are byte-for-byte the same as the lab converter's.

The same conversion works without the window, using only the Python standard library:

```sh
python3 tools/screenshots.py /Volumes/EOS_DIGITAL "$HOME/Pictures/R5 screenshots/copy"
```

The first argument is the card's top folder, `DCIM`, `R5SHOTS` or a folder holding a copy; the second is the output folder (created if needed). `--limited-range` treats the colours as limited range (16-235) and `--matrix 709` uses BT.709; neither is needed for the lab build's files. It exits with 1 if any file wasn't converted, and with 2 if the folder or the output was refused.

## One feature list for the builder and the assistant

[`config/features.json`](../config/features.json) is the single feature manifest. It lists the profiles, the WAV inputs each profile requires or forbids, opt-in options with their choices and the profiles they need, and a status for each feature (verified on a private lab build, hardware-untested public build, or experimental). [`tools/features.py`](../tools/features.py) loads and checks it.

`tools/build.py` builds its `--profile`, `--startup-wav`, `--shutter-wav`, `--record-indicator` and `--record-stop-indicator` arguments and its selection rules from this file, and refuses to start if the start or stop cue choices disagree with the cue owner (`tone_ref.py`). The assistant builds its checklist from the same file, so it can't offer a combination the builder would refuse. The host tests check this for every combination the window can produce.

Adding an option takes more than a manifest entry: the builder must implement it, and `check_feature_catalog` in `tools/build.py` lists what it implements.

## Known limitations

- **Prototype.** Checked by host tests, and on macOS (Apple Silicon, Python 3.11 with Tk 9.0) by a scripted run of the real window: a real-ROM build, then a copy to an ordinary temporary folder. The Prepare card tab had its own scripted run of the real window, with the device calls replaced by image files. It hasn't been tried on Windows, Linux or a real card.
- The window's look (fonts, colours, light and dark) is checked by host tests that build every step in a hidden window (macOS, Tk 9.0). It hasn't been looked at on Windows or Linux, and it doesn't follow an appearance change while it is open.
- **Screenshots: host-tested only.** The conversion was checked against the lab's seven sample files on the owner's Mac; the window's Screenshots tool was checked by host tests with synthetic files, not with a card. At the smallest window height the sidebar's *Tools* section can be cut off; the button on step 1 always opens it.
- No memory estimate *before* building: the margin is reported from `build.json` after a build.
- The final read-back may come from the operating system's cache rather than the card itself. Ejecting properly flushes the card.
- Copying needs a build from the current session; it can't pick an older build folder.
- No consented toolchain download yet. You install the toolchain yourself.
- **Desktop app: unsigned.** The macOS app has only PyInstaller's ad-hoc signature, so Gatekeeper asks once per version, and Windows SmartScreen may warn about the Windows app (see [Download the app](#download-the-app)). Their CI self-tests run on GitHub's macOS 15 (Intel), latest macOS (Apple Silicon) and latest Windows runners; neither app has been tried on other systems, and GitHub's last Intel macOS runner image is announced to be retired in 2027. The Windows app has not built `autoexec.bin` with a real toolchain and ROM yet.
- The assistant verifies whether BootDisk is enabled or disabled only when you give it the camera's returned `16 KiB` BootInfo dump (`gang200.bin` or `gang201.bin` from `0xE8004000`, offset `+0x04`); without a returned dump it records only your manual confirmation.
- **Canon Basic ROM dump and BootDisk steps: host-tested only.** The Canon Basic card preparation (`SCRIPT` at FAT32 `0x1F0`, `script.req`, `extend.m`) and `gang200.bin`/`gang201.bin` BootInfo readback verifier are tested on synthetic FAT32 images and lab-derived fixtures. `EnableBootDisk()` and `SaveAllRomImageToFile()` match the script used to capture the reference camera's `gang100.bin`/`gang200.bin`/`gang101.bin`/`gang201.bin`, whereas the `DisableBootDisk()` script (`0xE0B38D74`) is derived from ROM disassembly and is **hardware-untested**. Canon Basic requires a `<= 32 GB` FAT32 SDHC card; `> 32 GB` SDXC exFAT cards are refused for Canon Basic even though they work for native `autoexec.bin` booting in step 7.
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
