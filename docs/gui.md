# Build assistant (prototype)

> [!WARNING]
> **Prototype.** This is Phase 1 of [issue #16](https://github.com/WhiteAvocad0/canon-r5-mod/issues/16). It builds `autoexec.bin` on your computer and copies it to a card that is **already** prepared. It runs the same builder and checks as the [command line](usage.md#4-build-offline). Every build is a public repackaged build and is **hardware-untested**. Read the [Getting started guide](getting-started.md) first. You use this at your own risk.

The build assistant is a small desktop window for owners of an original Canon EOS R5 on firmware `2.2.1` who would rather not use a terminal to build. You still need the [one-time prerequisites](usage.md#1-prerequisites): your own ROM dump, BootDisk enabled in the camera, and a bootable SD card.

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

The window has six tabs. A red warning line runs across the top of every tab, followed by three buttons that open the **Getting started guide**, the **Prerequisites** and this page.

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

### 5. Copy to card

*Screenshot, described:* an explanation, a path box with **Choose card folder...**, a report (folder, drive size, whether a `DCIM` folder exists, any existing `autoexec.bin`, plus warnings), a confirmation checkbox, and the **Copy autoexec.bin to card** button.

This is only for a card that is **already bootable**. Choose the card's top folder, for example `/Volumes/EOS_DIGITAL` on a Mac, `E:\` on Windows, or the card's mount folder on Linux. Then:

1. The built `autoexec.bin` must still match `build.json` and pass the image-format check.
2. The folder is checked. The copy is **refused** if the folder is your home folder or a system drive root, isn't writable, has more than one spelling of `autoexec.bin`, or has an `autoexec.bin` that isn't a regular file. It **asks for confirmation** if the folder isn't the root of a drive, has no `DCIM` folder, or the drive size is unusual for an SD card (under 2 GB or over 2 TB).
3. An existing `autoexec.bin`, found in any upper/lower-case spelling, is copied first to `card-backup/` inside the build folder **on your computer** and read back. If it's already identical to the new build, nothing is written.
4. The new file is written to a temporary `R5GUI-XXXXXXXX.TMP` on the card, read back and compared. It is then renamed to `AUTOEXEC.BIN`, or to the existing file's spelling.
5. The final file is read back and its SHA-256 compared with `build.json`. If they differ, you're told where the backup is and not to use the card.
6. You're told to eject the card using your operating system (Finder eject, "Safely Remove Hardware", or your file manager).

The assistant **never** formats or partitions a card, never opens a raw disk, never writes boot sectors, and never deletes anything except its own temporary file from the current copy. Temporary files left by an interrupted earlier copy are reported and left alone.

### What this tool cannot do

The last tab lists what is out of scope. The assistant does **not** dump the ROM, enable **BootDisk** in the camera, or write the card's **boot markers** (`EOS_DEVELOP` / `BOOTDISK`). It doesn't format cards, download the toolchain, or test anything on the camera. See [Getting started → Step 2](getting-started.md#step-2-check-the-one-time-prerequisites).

## One feature list for the builder and the assistant

[`config/features.json`](../config/features.json) is the single feature manifest. It lists the profiles, the WAV inputs each profile requires or forbids, opt-in options with their choices and the profiles they need, and a status for each feature (verified on a private lab build, hardware-untested public build, or experimental). [`tools/features.py`](../tools/features.py) loads and checks it.

`tools/build.py` builds its `--profile`, `--startup-wav`, `--shutter-wav` and `--record-indicator` arguments and its selection rules from this file, and refuses to start if the record indicator choices disagree with the cue owner (`tone_ref.py`). The assistant builds its checklist from the same file, so it can't offer a combination the builder would refuse. The host tests check this for every combination the window can produce.

Adding an option takes more than a manifest entry: the builder must implement it, and `check_feature_catalog` in `tools/build.py` lists what it implements.

## Known limitations

- **Prototype.** Checked by host tests, and on macOS (Apple Silicon, Python 3.11 with Tk 9.0) by a scripted run of the real window: a real-ROM build, then a copy to an ordinary temporary folder. It hasn't been tried on Windows, Linux or a real card.
- No memory estimate *before* building: the margin is reported from `build.json` after a build.
- The final read-back may come from the operating system's cache rather than the card itself. Ejecting properly flushes the card.
- Copying needs a build from the current session; it can't pick an older build folder.
- No consented toolchain download yet. You install the toolchain yourself.
- The assistant can't tell whether the card is bootable or whether BootDisk is enabled.

## Troubleshooting

- **"Fixed build profile refuses ambient overrides"**: your environment sets a variable such as `CFLAGS` or `PYTHONPATH`. Start the assistant from a shell without it.
- **"The output folder already exists"**: choose another folder; the builder never reuses one.
- **ROM refused**: the ROM isn't an exact R5 `2.2.1` dump, or it's damaged. This is a safety check working.
