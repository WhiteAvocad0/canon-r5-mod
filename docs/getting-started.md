# Getting started: build and install for your own camera

This guide is for EOS R5 owners who are comfortable copying commands into a terminal but are new to camera modding. It explains what each piece is, walks through **building** the mod on your computer and **installing** it on a prepared card, and tells you how to test it and recover.

> [!IMPORTANT]
> **What this guide does not cover:** three one-time preparation steps. These are dumping your camera's ROM, enabling the camera's **BootDisk** setting, and making an SD card **bootable**. This guide explains what they are and how to check they're done, and the [Prerequisites](usage.md#1-prerequisites) section points to the background. This project does not provide tools or step-by-step instructions for them. If you haven't done them, stop at [Step 2](#step-2-check-the-one-time-prerequisites).

> [!WARNING]
> This is **experimental** software for one exact camera and firmware. It can hang the camera, show a Canon error such as `ERR70`, or lose card data. The BootDisk setting is a **permanent** change inside the camera, and there is no uninstaller. The public `xpan-crop` + `ding` build passed a short check on the maintainer's camera on 18 November 2026. Most other features were confirmed on private lab builds, and the public `shutter-feedback` build is **hardware-untested**. You use this at your own risk.

---

## How it works

Three separate things must all be true before the camera runs this mod:

| Piece | Where it lives | What it does | Done how often |
| --- | --- | --- | --- |
| **BootDisk setting** | Inside the camera | Tells the camera to look for a program on the card at power-on. A stock R5 ignores the card. | Once per camera; it stays on |
| **Boot markers** | The SD card's boot sector | Two short markers (`EOS_DEVELOP`, `BOOTDISK`) that say "this card has a program to run". | Once per card. **Formatting the card usually removes them** |
| **`autoexec.bin`** | A file at the card root | The mod itself, which you build from your own camera's ROM. | Every time you change features or update |

**Why there is no download:** the build uses small parts of Canon's own firmware, read from *your* camera's ROM dump. Those parts can't legally be redistributed, so everyone builds their own copy locally. That also protects you: the builder refuses any ROM that isn't an exact EOS R5 firmware `2.2.1` image.

**So copying files alone is not enough.** A card from a friend won't work in a camera without BootDisk enabled, and an `autoexec.bin` on a card without boot markers is ignored.

---

## Step 1: Check your camera

1. You need an **original Canon EOS R5**: not an R5 C, R5 Mark II or R6 family.
2. In the camera menu, open **Setup → Firmware**. It must say **2.2.1**.
3. Anything else: **stop**. Don't downgrade or upgrade firmware to make it fit, and never run this on another model.

**Checkpoint:** "EOS R5" and "Firmware 2.2.1".

## Step 2: Check the one-time prerequisites

| Prerequisite | How to tell it's done |
| --- | --- |
| Your own ROM dump | You have a file of exactly **67,108,864 bytes** (64 MiB) from *your* camera, often named `gang100.bin`. Keep it private: never upload or share it. |
| BootDisk enabled in the camera | Whoever enabled it should have confirmed it with a readback. If you're not sure, assume it is **not** enabled. |
| A bootable SD card | The card's boot sector carries the `EOS_DEVELOP` and `BOOTDISK` markers (FAT32 cards up to 32 GB, or exFAT with the extra checksum described in [sdxc.md](sdxc.md)). |

If any row isn't done, stop here. See [usage.md → Prerequisites](usage.md#1-prerequisites) and the community documentation it refers to. This project hasn't verified any third-party card-preparation tool, so check any tool's output yourself and keep backups.

## Step 3: Back up

- Copy **everything** from the card you'll use (photos, `DCIM`, `MISC`) to your computer, and check the copies open.
- Keep your ROM dump in a safe private folder.

## Step 4: Install the build tools (one time)

1. **Python 3.11 or newer.** Check it in a terminal:
   ```sh
   python3 --version
   ```
   It should print `Python 3.11` or higher. If not, install it from <https://www.python.org/downloads/>.
2. **GNU Arm toolchain 15.2.1.** Download the **xPack GNU Arm Embedded GCC 15.2.1-1.1** archive for your computer from <https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases>, then unpack it anywhere, for example `~/tools/`. You'll need the path of its `bin` folder, which contains `arm-none-eabi-gcc`.
   ```sh
   ~/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin/arm-none-eabi-gcc --version
   ```
   This should print a version containing `15.2.1`.
3. **Get the source and run the self-tests.** These need no camera and no ROM:
   ```sh
   git clone https://github.com/WhiteAvocad0/canon-r5-mod.git
   cd canon-r5-mod
   python3 -m unittest discover -s tests
   ```
   **Checkpoint:** the last line says `OK`.

## Step 5: Choose your features

| Profile | What you get | Notes |
| --- | --- | --- |
| `xpan-crop` | A **2.71:1 XPan** crop mode replacing 16:9: menu/Q label, live LCD/EVF framing bars, saved JPEG/RAW crop, playback masking, and immediate XPan bars in automatic review | Silent: no custom sounds |
| `xpan-crop` + `--record-indicator ding` | All of the above, plus a short **ding when you press movie REC** | The cue is generated in the camera; no audio files needed |
| `shutter-feedback` | Your own **startup** and **shutter** sounds through the speaker | Needs two WAV files; no XPan |

XPan and custom sounds aren't offered together. The XPan profile is silent by design, and private lab estimates suggest both together would exceed the camera's free memory. That combination has not been tested.

## Step 6: Build

Use your own paths for the ROM and the toolchain `bin` folder. For XPan with the record indicator:

```sh
python3 tools/build.py \
  --profile xpan-crop \
  --record-indicator ding \
  --rom ~/r5-private/gang100.bin \
  --toolchain ~/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin \
  --output build/my-r5
```

For XPan without the indicator, leave out `--record-indicator ding`. For custom sounds, see [usage.md → Build Offline](usage.md#4-build-offline). That also covers converting your clips to the required 48 kHz mono WAV format.

**Checkpoint:** `build/my-r5/` now contains `autoexec.bin` and `build.json`. Verify the image:

```sh
python3 tools/image_format.py verify build/my-r5/autoexec.bin
```

The printed `sha256` must equal `format.sha256` in `build/my-r5/build.json`.

**If the build refuses your ROM**, that's a safety check working. The ROM isn't an exact R5 `2.2.1` dump, or it's damaged. There is deliberately no way to force it.

## Step 7: Install on your bootable card

1. Insert the **already-bootable** card (from Step 2) into your computer.
2. Copy `build/my-r5/autoexec.bin` to the **root** of the card. If an older `AUTOEXEC.BIN` is there, keep a copy of it on your computer first, then replace it.
3. Check the copy on the card has the same SHA-256 as `build.json`:
   ```sh
   shasum -a 256 /Volumes/EOS_DIGITAL/AUTOEXEC.BIN    # macOS (Windows: certutil -hashfile E:\AUTOEXEC.BIN SHA256)
   ```
4. **Eject the card properly** (Finder eject, or "Safely remove" on Windows) before pulling it out.

Don't format the card in the camera afterwards: that usually removes the boot markers.

## Step 8: First test

1. With the camera **off**, insert the card. For the first test, use the SD slot only and disconnect USB and HDMI.
2. Power on. Choose **RAW + Large JPEG** and select **XPan** (it replaces 16:9) in the menu or Q screen.
3. Take a **full-frame** photo first as a control: everything should look normal.
4. Take a few **XPan** photos. The automatic review should show the XPan bars immediately, and live view should show the XPan framing guide again afterwards.
5. Open the photos with **▶ playback** on the rear LCD and in the viewfinder.
6. With the record indicator: in movie mode, press **REC**. You should hear a short ding as it starts. Play the clip back.
7. Wait for the card light to go out before switching off.

Report what you saw, including what didn't work, using the [XPan test report](https://github.com/WhiteAvocad0/canon-r5-mod/issues/new?template=xpan-test-report.yml). Include the `source_manifest_sha256` from `build.json`. **Never upload your ROM, photos or card images.**

## If something goes wrong

1. If the camera freezes or shows an error, **remove the battery**, then remove the card.
2. Put the battery back and power on **without the card**. On the reference camera this restored normal startup, but that isn't a guarantee for every situation.
3. Don't try another build until the camera works normally again. Then report what happened.
4. To stop using the mod, use a **normal card without boot markers** (for example a card freshly formatted in the camera). Don't leave a *bootable* card in the camera with its `autoexec.bin` deleted: with BootDisk on, a bootable card with no program on it can stop the camera starting. The BootDisk setting itself stays on.

## FAQ

**Can I just copy someone else's files to my card?** No. You need BootDisk enabled in your camera, a bootable card, and an `autoexec.bin` built from your own ROM.

**I formatted my card. Do I need to redo everything?** Only the card: the boot markers are usually gone, so the card needs to be made bootable again, and then you copy `autoexec.bin` back. BootDisk stays enabled in the camera.

**Which cards work?** The maintainer's camera worked with a 16 GB FAT32 SDHC card and a full-capacity 128 GB exFAT SDXC card. CFexpress can stay in the other slot, but the mod loads from the SD card.

**Does it change my photos?** XPan JPEGs are saved at 8192×3024. RAW files keep the full sensor data with an XPan crop tag. Full-frame shots are unchanged.

**Is the ding recorded into my movies?** Barely. It plays at the button press, before most of the recording starts. In tests only a faint tail (about −32 dBFS) was picked up by the camera's microphones.

**Can an AI assistant help me?** Yes. The [README](../README.md#let-your-coding-agent-guide-you) has a ready-made prompt, and [agent-setup.md](agent-setup.md) explains it. The assistant will follow the same safety rules as this guide.
