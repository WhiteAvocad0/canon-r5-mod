# Agent-assisted setup and testing

Use the copyable prompt in the README with the release you actually checked out. It is a guide for an agent working with you; it does not supply missing camera compatibility, enable BootDisk, or make an untested build verified.

## Establish the target and goal

The supported research target is the original Canon EOS R5 on firmware 2.2.1. Other firmware and bodies need independent ports. An agent should inspect available context before asking for facts, then confirm model/firmware on the camera when necessary. Choose silent XPan cropping or the separate audio profile. A working startup or shutter sound does not prove that crop metadata, framing and playback all work.

Read AGENTS.md, README.md, usage.md and xpan.md before following camera instructions. Use the current checked-out revision's build command, prerequisites and validation matrix. The public source package has its own build/source identity; private reference test results do not establish that a newly repackaged binary ran on your camera.

## Build and preserve evidence

Run the documented portable host tests first. Use the existing compatible toolchain and user-supplied ROM. The silent XPan profile should not require audio files. The audio profile needs separate startup and shutter WAV inputs with the documented format. Do not silently normalize, trim, replace roles, disable guards or install unrelated tools.

Keep the builder's actual input digests, source manifest, symbols, selected profile and diagnostic contracts. Build success is software evidence. ROMs, output payloads, private photos/audio and real debug dumps stay local; a public report can start with the smallest relevant text summary and hashes.

## Prepare the correct card

A volume label or a disk number is not sufficient identity. Use current OS facts including reader/device path, capacity, partition geometry and filesystem. Preserve files and follow the owner's backup instructions. Do not reformat or repartition a working full-capacity SDXC card to address an assumed SDHC limit.

Native exFAT boot and Canon Basic debug export are separate paths. The full-capacity 128 GB native boot trial worked; the established debug-export workflow uses the 16 GB FAT32 lab card. Do not infer exFAT SCRIPT support from native boot, or rewrite markers merely because a later host verification step failed.

Our earlier host verification confused a read-only mounted volume with physically unwritable media. An agent should distinguish physical-media writability from volume/mount state. If normal unmount/verification is blocked by the macOS login screen, ask for unlock and wait; do not force the unmount or repeat the payload write. A password belongs in the local OS prompt, never in chat.

The public builder writes offline build outputs only. It does not activate the camera's persistent BootDisk flag or install card boot markers. The build assistant's opt-in [Prepare card](gui.md#5-prepare-card) tab (macOS; exFAT verified on the owner's Mac and camera, FAT32 untested) is the one supported way to write the markers, and the owner runs it. An unknown activation prerequisite remains unknown. Any separately proposed persistent or destructive operation needs the owner's specific authorization.

## Test one coherent session

Read back the intended payload and complete normal ejection before removing media. For initial isolation use the documented single-SD setup with USB/HDMI disconnected and CFexpress removed. This controlled setup does not contradict the earlier user report that CFexpress coexistence worked.

Follow the current XPan test matrix and quality/mode limits. Include a Full-frame control, saved JPEG dimensions and RAW crop facts, menu/Q labels, live LCD/EVF framing, and playback first-open behavior. Record portrait orientation and auto-rotate setting explicitly. A returned portrait file is not proof of a portrait display fix. The owner reported private `4838eb81` normal ▶ LCD/EVF portrait/landscape testing working on 17 November 2026; the public repackaged image remains hardware-untested. Test normal ▶ playback separately from automatic post-shot review. The current profile includes the review fix (immediate XPan bars and no 16:9 outline for XPan shots; stock review for Full frame and bursts), verified on a private lab build on 18 November 2026; builds without it show the 16:9 outline first.

Diagnostic exports are optional unless a documented test specifically requires them. Do not enable Canon Basic or write SCRIPT markers merely to satisfy the agent prompt. Missing diagnostics should remain an explicit coverage limit.

Keep one short checklist rather than asking for a new small test after every finding. Wait for card activity to stop before power-off/removal. Do not repeatedly trigger a failing probe. If an error appears, stop the trial, power off, remove the test card and follow the recovery guidance; establish normal startup without it before another candidate. Removing a card cannot be claimed to undo any persistent camera setting changes.

## Read the return before preparing again

Acquire new files and compare filenames/sizes/hashes with the prepared-card record before writing another payload. Use the decoder and runtime contract produced for that exact source/build. Retain complete groups, coverage and ambiguity. A ring-buffer's last physical group may be an older empty startup report; never select it as newest simply by list position. Source IDs identify builds, not camera boot sessions.

Separate observed files/counters, the user's visual/audio report, source-derived explanations and unknowns. A projected/hidden-guide counter supports pipeline execution; it does not replace seeing the black bars on the actual display. Use the repository's test-report form with concise reproduction steps and explicit limitations; do not request public uploads of ROMs or private media.
