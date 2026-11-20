# Third-Party Notices and Attribution

## Magic Lantern (GPL-2.0-or-later)

Portions of the boot entry prefix, footer marker, and whole-image XOR checksum tooling in this repository are derived from the Magic Lantern project (licensed under the GNU General Public License, Version 2 or later — see [`LICENSE`](LICENSE)):

- **Upstream repository:** `magiclantern_simplified`
- **Pinned upstream commit:** `142472e95054d73640f50789ac0fd66325499d82`
- **Derived / adapted components:**
  - [`src/r5_221/handoff.S`](src/r5_221/handoff.S) and [`src/experimental/xpan/resident/handoff.S`](src/experimental/xpan/resident/handoff.S): ARM/Thumb dual-entry prefix and self-XOR integrity check convention adapted from Trammell Hudson's `src/reboot.c` (GPL-2.0-or-later).
  - [`src/r5_221/footer.S`](src/r5_221/footer.S): Footer marker (`0xE12FFF13`) and checksum placeholder layout adapted from `src/footer.c` (GPL-2.0-or-later).
  - [`tools/image_format.py`](tools/image_format.py): Little-endian word XOR checksum finalization and verification adapted from `build_tools/xor_chk.c` (GPL-2.0-or-later).

All original GPL-2.0-or-later copyright and license notices are preserved in the corresponding source files and [`LICENSE`](LICENSE).

## Clean-Room / Derived Build Inputs (No Bundled Canon or Font Binaries)

- This repository bundles **no** Canon firmware pages, vector resource payloads, or proprietary font files (`.ttf`/`.otf`).
- [`src/experimental/xpan/tools/xpan_font.json`](src/experimental/xpan/tools/xpan_font.json) contains clean-room path coordinates for the `"XPan"` menu label (`Arial-BoldMT` metric outline), combined at offline build time with the user-supplied verified ROM resource `0xE38F614C` into `--output/menu-vector.bin`.

## External Build Toolchain (Not Bundled)

Building the target binary requires an external `arm-none-eabi` GNU toolchain (reference GCC `15.2.1`, e.g., xPack GNU Arm Embedded GCC) supplied by the user via `--toolchain`. No toolchain binaries are bundled in this repository.

## macOS App (R5 Mod Assistant `.dmg`)

The unsigned macOS app built by [`packaging/macos/`](packaging/macos/) carries this repository's tracked files unchanged, plus third-party runtime components that keep their own licenses:

- the CPython `3.13` runtime and standard library (Python Software Foundation License Version 2, with the licenses of the libraries that build includes; see <https://docs.python.org/3.13/license.html>). Python's `LICENSE.txt` is shipped in the `.dmg`'s `Licenses` folder;
- Tcl/Tk, used by Tkinter (Tcl/Tk license, <https://www.tcl-lang.org/software/tcltk/license.html>);
- the PyInstaller bootloader (GPL-2.0-or-later with the PyInstaller bootloader exception, <https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt>).

The app contains no Canon firmware, ROM data, built `autoexec.bin` images or toolchain binaries.

## User-Supplied Firmware Dumps and Audio Files

This repository contains **no** proprietary Canon firmware binaries, ROM dumps, or audio clips.

- Any Canon EOS R5 firmware dump supplied by the user via `--rom` remains subject to Canon's rights and terms. This project makes no license claim over Canon firmware or ROM images and has no affiliation with or endorsement from Canon Inc.
- Any startup or shutter audio WAV clips supplied by the user via `--startup-wav` and `--shutter-wav` remain subject to their respective owners' rights and licenses. This project makes no license claim over user-supplied audio assets.
