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

## GNU Arm toolchain

The source checkout uses a caller-supplied `arm-none-eabi` toolchain through `--toolchain`. Bundled desktop builds include the complete xPack GNU Arm Embedded GCC **15.2.1-1.1** distribution for their host. Its `arm-toolchain/README.md` and `arm-toolchain/distro-info/licenses/` contain upstream attribution and component-specific terms, including the GCC Runtime Library Exception for covered runtime code. xPack's MIT license covers its own packaging work. GCC, Binutils, GDB, Newlib and the other libraries each retain their own licenses. [Upstream binary release](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/tag/v15.2.1-1.1)

The toolchain is included **unmodified**. Every file of the pinned official archive for the host is in the app byte for byte; only the folder is renamed `arm-toolchain`, and the app adds files only under `arm-toolchain/distro-info/R5-notices/`. [`packaging/build_app.py`](packaging/build_app.py) checks this after it builds the app and again in the unpacked Windows `.zip`. It also writes `R5-notices/THIRD-PARTY-NOTICE.txt`, which names the toolchain, version, host archive and its SHA-256, the shipped licence folders and the source packages, and copies the toolchain licences and notices into `Licenses/GNU Arm toolchain` next to the app in the `.dmg` and `.zip`. xPack builds 15.2.1-1.1 from Arm GNU Toolchain 15.2.Rel1 sources: GCC 15.2.1 and Newlib 4.5.0 from Arm's source archives, and Binutils 2.45 and GDB 16.3 from xPack tags that point at Arm's manifest revisions (`cdfcefe12f17` and `214bb42ce0e5`).

Additional licenses accompany each host in `arm-toolchain/distro-info/R5-notices/`. They include xPack's MIT terms (Copyright © 2019–2026 Liviu Ionescu) and OpenSSL's Apache 2.0 license. OpenSSL 3.6.1 is Copyright © 1998–2026 The OpenSSL Project Authors. OpenSSL 3.0.18 in Windows Python is Copyright © 1998–2025 The OpenSSL Project. Both credit Eric A. Young and Tim J. Hudson, Copyright © 1995–1998. [OpenSSL 3.6.1](https://github.com/openssl/openssl/tree/openssl-3.6.1#copyright) · [OpenSSL 3.0.18](https://github.com/openssl/openssl/tree/openssl-3.0.18#copyright)

macOS packages also carry the LLVM licenses for libc++, libc++abi and libunwind, and the GPLv3 text from GNU Readline 8.3 (`readline-8.3-COPYING.txt`): GDB on macOS links `libexec/libreadline.8.3.dylib`, and the xPack archive has no Readline folder in `distro-info/licenses`. Windows packages carry the MinGW-w64 notices and the original license from the CPython 3.13.12 embeddable distribution used by GDB. Its **Additional Conditions for this Windows binary build** apply to the Microsoft Distributable Code. [Python 3.13.12 embeddable ZIP](https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip)

### Microsoft Visual C++ redistributable files (Windows)

`arm-toolchain/bin/DLLs/vcruntime140.dll` and `vcruntime140_1.dll` in the Windows package are Microsoft Visual C++ redistributable files, Copyright © Microsoft Corporation. They are distributed under the Visual Studio redistribution terms, unchanged from the xPack Windows archive, for GDB's Python runtime. They are not covered by the GPL or this project's license. The Windows package carries this notice as `Microsoft-Visual-C++-Redistributable-NOTICE.txt` in `Licenses/GNU Arm toolchain` and `arm-toolchain/distro-info/R5-notices`. Owner decision, 23 November 2026: ship them with this notice. They differ from the copies in Python's official embeddable ZIP; their PE headers refer to certificate tables beyond the ends of the files. [Visual Studio redistribution](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution) · [Redistributing Visual C++ files](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files)

### Source code of the bundled toolchain

Every `app-v*` release attaches two source packages, both pinned by SHA-256 in [`packaging/build_app.py`](packaging/build_app.py) (`--print-toolchain-source`). The `toolchain-source` job of [`desktop-app.yml`](.github/workflows/desktop-app.yml) downloads and checks both before anything is uploaded. Pull requests check only Arm's published value.

1. **Arm's source package** [`arm-gnu-toolchain-src-snapshot-15.2.rel1.tar.xz`](https://developer.arm.com/-/media/Files/downloads/gnu/15.2.rel1/srcrel/arm-gnu-toolchain-src-snapshot-15.2.rel1.tar.xz), 358,772,144 bytes, SHA-256 `06f4e5792b566a771c300ac6f46c3422e23e34f4e3a5d44a8cb5594ead6fb82e`. The pin must also equal the value in Arm's `.sha256asc`. It holds GCC, Newlib, Binutils and GDB at the revisions xPack built:
   - xPack's GCC and Newlib inputs (Arm's `src/gcc.tar.xz` and `src/newlib-cygwin.tar.xz`, checked against Arm's `.sha256asc`) have the same files as its `gcc/` and `newlib-cygwin/`.
   - xPack's Binutils and GDB tags resolve to Arm's manifest commits. Every file of its `binutils-gdb/` and `binutils-gdb--gdb/` is identical; xPack's archives add only `sim/`, which the build configures out (`--disable-sim`).
2. **The xPack corresponding-source companion** `xpack-15.2.1-1.1-corresponding-source.tar`, 543,897,600 bytes, SHA-256 `1c1183fe067b958296aed1aa12365e10721bc64e6bb201adead7863afc91e488`, kept on the [`toolchain-src-xpack-15.2.1-1.1`](https://github.com/WhiteAvocad0/canon-r5-mod/releases/tag/toolchain-src-xpack-15.2.1-1.1) release. [`packaging/xpack_source_package.py`](packaging/xpack_source_package.py) builds it reproducibly from [`packaging/xpack-source-inputs.tsv`](packaging/xpack-source-inputs.tsv), which lists every component with its version, hosts, upstream URL, SHA-256, license, the shipped files it accounts for and how the download was verified. It contains:
   - xPack's build scripts at the release tags with their patches: arm-none-eabi-gcc-xpack `e6e900a`, xbb-helper 5.4.1, and the clang-xpack 20.1.8-1.1 and mingw-w64-gcc-xpack 15.2.0-2.1 recipes with their helpers. The only patch applied is xbb-helper's `gdb-16.3-cross.git.patch`.
   - The host libraries: libiconv, zlib, GMP, MPFR, MPC, ISL, xz, zstd, Expat, ncurses, Readline, bzip2, libffi, mpdecimal, SQLite, libxcrypt, OpenSSL, Python 3.13.12, Texinfo, gettext and libunistring. Texinfo, gettext and libunistring are built but not linked into any shipped binary; they are included because the recipe builds them.
   - The LLVM 20.1.8 sources of the macOS libc++, libc++abi and libunwind.
   - GCC 15.2.0 and MinGW-w64 13.0.0 for the Windows `libstdc++-6.dll`, `libgcc_s_seh-1.dll` and the statically linked MinGW-w64 runtime.
   - The CPython 3.13.12 Windows build inputs (`get_externals.bat`).

   Signed releases were checked against their OpenPGP signatures, and others against their publishers' checksums where available. The inventory records each result.

To publish the companion for a new toolchain version, update the inventory and pins, run `python3 packaging/xpack_source_package.py --output DIR`, upload the archive (with `MANIFEST.tsv` and `SHA256SUMS`) to a `toolchain-src-xpack-<version>` release, and set `TOOLCHAIN_COMPANION_SHA256` to the printed digest. Provide the sources at no charge and keep them available while the binaries are offered. [GNU GPLv3 sections 1 and 6(d)](https://www.gnu.org/licenses/gpl-3.0.html)

## Desktop App (R5 Mod Assistant `.dmg` and `.zip`)

The unsigned macOS and Windows app built by [`packaging/`](packaging/) carries this repository's tracked files unchanged, plus third-party runtime components that keep their own licenses:

- the CPython `3.13` runtime and standard library (Python Software Foundation License Version 2, with the licenses of the libraries that build includes; see <https://docs.python.org/3.13/license.html>). Python's `LICENSE.txt` is shipped in the `Licenses` folder of the `.dmg` and `.zip`;
- Tcl/Tk, used by Tkinter (Tcl/Tk license, <https://www.tcl-lang.org/software/tcltk/license.html>);
- the PyInstaller bootloader (GPL-2.0-or-later with the PyInstaller bootloader exception, <https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt>; shipped as `Licenses/PyInstaller COPYING.txt`).

The app contains no Canon firmware, ROM data or built `autoexec.bin` images.

## User-Supplied Firmware Dumps and Audio Files

This repository contains **no** proprietary Canon firmware binaries, ROM dumps, or audio clips.

- Any Canon EOS R5 firmware dump supplied by the user via `--rom` remains subject to Canon's rights and terms. This project makes no license claim over Canon firmware or ROM images and has no affiliation with or endorsement from Canon Inc.
- Any startup or shutter audio WAV clips supplied by the user via `--startup-wav` and `--shutter-wav` remain subject to their respective owners' rights and licenses. This project makes no license claim over user-supplied audio assets.
