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

The toolchain is included **unmodified**. Every file of the pinned official archive for the host is in the app byte for byte; only the folder is renamed `arm-toolchain`, and the app adds files only under `arm-toolchain/distro-info/R5-notices/`. [`packaging/build_app.py`](packaging/build_app.py) checks this after it builds the app and again in the unpacked Windows `.zip`. It also writes `R5-notices/THIRD-PARTY-NOTICE.txt`, which names the toolchain, version, host archive and its SHA-256, the shipped licence folders and the source packages, and copies the toolchain licences and notices into `Licenses/GNU Arm toolchain` next to the app in the `.dmg` and `.zip`. xPack builds 15.2.1-1.1 from Arm GNU Toolchain 15.2.Rel1 sources: GCC 15.2.1 and Newlib 4.5.0 from Arm's source archives, and Binutils 2.45 and GDB 16.3 from xPack branches whose version dates (`20251203`, `20250906`) match the trees in Arm's source package.

Additional licenses accompany each host in `arm-toolchain/distro-info/R5-notices/`. They include xPack's MIT terms (Copyright © 2019–2026 Liviu Ionescu) and OpenSSL's Apache 2.0 license. OpenSSL 3.6.1 is Copyright © 1998–2026 The OpenSSL Project Authors. OpenSSL 3.0.18 in Windows Python is Copyright © 1998–2025 The OpenSSL Project. Both credit Eric A. Young and Tim J. Hudson, Copyright © 1995–1998. [OpenSSL 3.6.1](https://github.com/openssl/openssl/tree/openssl-3.6.1#copyright) · [OpenSSL 3.0.18](https://github.com/openssl/openssl/tree/openssl-3.0.18#copyright)

macOS packages also carry the LLVM licenses for libc++, libc++abi and libunwind, and the GPLv3 text from GNU Readline 8.3 (`readline-8.3-COPYING.txt`): GDB on macOS links `libexec/libreadline.8.3.dylib`, and the xPack archive has no Readline folder in `distro-info/licenses`. Windows packages carry the MinGW-w64 notices and the original license from the CPython 3.13.12 embeddable distribution used by GDB. Its **Additional Conditions for this Windows binary build** apply to the Microsoft Distributable Code. The Windows archive includes `vcruntime140.dll` and `vcruntime140_1.dll`. Both differ from the files in Python’s official embeddable ZIP. Their PE headers refer to certificate tables beyond the ends of the xPack files. The Windows publisher must establish permission to distribute these specific copies and meet the downstream terms before releasing them. [Python 3.13.12 embeddable ZIP](https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip) · [Microsoft redistribution terms](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files)

### Preparing a public desktop release

Every `app-v*` release attaches Arm's source package for the toolchain's Arm release. The `toolchain-source` job of [`desktop-app.yml`](.github/workflows/desktop-app.yml) downloads [`arm-gnu-toolchain-src-snapshot-15.2.rel1.tar.xz`](https://developer.arm.com/-/media/Files/downloads/gnu/15.2.rel1/srcrel/arm-gnu-toolchain-src-snapshot-15.2.rel1.tar.xz) (358,772,144 bytes) and requires both the SHA-256 pinned in `build_app.py` and the value Arm publishes in the `.sha256asc` file beside it: `06f4e5792b566a771c300ac6f46c3422e23e34f4e3a5d44a8cb5594ead6fb82e`. Pull requests check only Arm's published value; tags and manual runs download and check the file. It contains GCC, Binutils, GDB, Newlib, GMP, MPFR 3.1.6, MPC 1.0.3, ISL 0.15, libiconv 1.15 and Expat 2.7.1. It doesn't contain xPack's build scripts and patches, GNU Readline, ncurses 6.6 (Arm's copy is 5.9), zlib, zstd, xz, gettext, libunistring, Texinfo, Python and its libraries, OpenSSL, LLVM's runtime libraries or the MinGW-w64 runtime. Those remain the job of the corresponding-source companion below.

Distribute the complete corresponding sources of the covered programs, including the scripts that control their compilation and installation, from the same release page as the app. Preserve all applicable license and copyright notices. Provide the source download at no additional charge and keep it available while the binaries are offered. [GNU GPLv3 sections 1 and 6(d)](https://www.gnu.org/licenses/gpl-3.0.html) · [GNU GPLv2 section 3](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)

1. Collect the archives listed in the [source-input inventory](packaging/xpack-source-inputs.tsv). Include the xPack recipe at `e6e900a`, xbb-helper 5.4.1 with its patches, Arm's 15.2.rel1 GCC and Newlib, and the separate xPack Binutils and GDB revisions. The source archive generated by GitHub for the xPack release contains only the packaging repository. The recipe selects additional source inputs per host. [Version selection](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/blob/e6e900a4c40f0be10dae62f089a8269ac69b5899/build-assets/scripts/versioning.sh) · [Helper and patches](https://github.com/xpack-dev-tools/xbb-helper-xpack/tree/6b99c8980ab3ca639dbc24bbf73ca4999b957879)
2. Check the actual Mach-O libraries, Windows DLLs, executables, target libraries and other installed files in all three pinned archives against the collected sources. Include the build scripts and patches for copied host runtimes, plus the external sources used for Windows Python's extension modules. Include each archive's origin and SHA-256 and instructions for applying the recorded recipes. The inventory is a collection aid. Its rows have not been verified as a complete buildable source bundle for all three hosts. [Host build configuration](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/blob/e6e900a4c40f0be10dae62f089a8269ac69b5899/build-assets/package.json) · [CPython Windows build inputs](https://github.com/python/cpython/blob/v3.13.12/PCbuild/get_externals.bat)
3. Package the reviewed sources, scripts and notices as `xpack-15.2.1-1.1-corresponding-source.tar.gz`. Set repository variables `XPACK_15_2_1_1_1_SOURCE_URL` (HTTPS download) and `XPACK_15_2_1_1_1_SOURCE_SHA256` (SHA-256 of those exact bytes). The desktop release workflow checks the digest and attaches the archive beside the installers on an `app-v*` release. The publisher owns the contents review and Windows redistribution decision before configuring these variables. A local build or a successful digest check alone does not establish release readiness.

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
