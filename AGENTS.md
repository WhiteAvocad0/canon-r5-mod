# Canon R5 Sound Mod

Public source-preview project for original Canon EOS R5 firmware2.2.1 only. The immutable reference is the user-confirmed originalR5 2.2.1 build36; preserve its target behavior and clearly identify repackaged-source validation separately.

- Shell commands use rtk (rtk proxy for raw commands).
- Distinguish source facts, host tests and user-reported camera evidence. Public repackaged source has not itself been hardware-tested; private baseline36 was user-confirmed for XP startup, Mambo shutter and normal shooting on 16GB SDHC/FAT32 and full-capacity128GB SDXC/exFAT. SDXC returned files, repeated boots and longer shooting are unverified.
- Do not commit or upload Canon ROM/FIR/dumps, built images/objects/toolchains, real diagnostic dumps/photos, user audio (XP/Mambo), credentials, build outputs, private lab history or local plans. Users supply their own ROM dump and two WAV clips. Initial publication is source+docs only, no binary release.
- Preserve native baseline C/ASM/linker behavior. All addresses are originalR5 2.2.1-specific. Other R5 firmware/R6/R6II/R6III are unsupported and need independent ports; no cross-model binary testing.
- No automatic camera-side bootflag activation, firmware/NVRAM writes, raw-device writes or formatting. Card instructions require backups and correct model/firmware; supplied tools build offline only.
- Firmware admission must accept per-camera settings differences outside immutable target code while retaining strict exact-code/patch checks. Preserve actual caller ROM digest and paths as provenance, not a private camera full-dump hash as compatibility identity. No force/skip-target-check option.
- Audio roles: startup WAV and shutter WAV separate, full clips, 48kHz mono16PCM,32-byte zero alignment only, no normalization/trimming. Busy effects drop; no delayed replay. No immediate user mute or fullburst/movie/latency/shutdown certification claimed.
- Maintain GPL-2.0-or-later notices from Magic Lantern-derived boot/footer work; no Canon affiliation or endorsement. Do not attest human review or broader device support.
- Portable documented CLI: python3 tools/build.py --rom PATH --startup-wav PATH --shutter-wav PATH --toolchain DIR --output DIR. Outputs ignored under build/ by convention. CLI never contacts camera/media.
- Host tests must run without private lab paths/assets. Real-ROM cross-build verification is separately opt-in and reported distinctly. Do not install unrelated software or dependencies automatically.
