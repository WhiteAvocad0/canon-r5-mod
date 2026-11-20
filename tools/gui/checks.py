"""Input checks for R5 Mod Assistant, delegated to the builder's own admission code.

Nothing here weakens or re-implements a builder check: the ROM goes through
build.check_rom (the exact admission a build runs), WAVs through
audio_profile.admit_pcm and toolchains through toolchain.admit_toolchain. These
functions only read files and run the selected toolchain's version queries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import glob
import os
from pathlib import Path
import shutil
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

TOOLCHAIN_RELEASES_URL = "https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases"
TOOLCHAIN_FOLDER = "xpack-arm-none-eabi-gcc-15.2.1-1.1"
TOOLCHAIN_INSTRUCTIONS = (
    "Download the xPack GNU Arm Embedded GCC 15.2.1-1.1 archive for your computer from the "
    "releases page, unpack it anywhere (for example into a 'tools' folder in your home folder), "
    "then choose its 'bin' folder here. This prototype never downloads anything itself."
)


@dataclass
class CheckResult:
    ok: bool
    message: str
    facts: dict = field(default_factory=dict)


def _builder():
    import build  # deferred: importing the builder loads and cross-checks the feature manifest

    return build


def check_rom(path: Path, *, profile: str, options: dict[str, str | None]) -> CheckResult:
    """Strict ROM admission for this profile and its record cues, without toolchain or output.

    options maps manifest option ids (the builder's parameter names) to the selected value."""
    try:
        summary = _builder().check_rom(Path(path), profile=profile, **options)
    except (ValueError, OSError) as exc:
        return CheckResult(False, f"ROM refused: {exc}")
    regions = summary["verified_code_regions"] + summary.get("verified_xpan_regions", 0)
    return CheckResult(
        True,
        f"ROM accepted for {profile}: {summary['target']}, {summary['bytes']:,} bytes, "
        f"{regions} fingerprinted regions match.",
        summary,
    )


def check_bootinfo(
    path: Path,
    *,
    before_path: Path | None = None,
    profile: str = "xpan-crop",
    record_indicator: str | None = None,
    options: dict[str, str | None] | None = None,
) -> CheckResult:
    """Verify ROM admission, wizard BootInfo/script fingerprints, and BootInfo +0x04 state."""
    from cardprep import cbasic
    from gui import card

    opts: dict[str, str | None] = dict(options or {})
    if record_indicator is not None and "record_indicator" not in opts:
        opts["record_indicator"] = record_indicator
    rom = check_rom(path, profile=profile, options=opts)
    if not rom.ok:
        return rom
    try:
        data = card._read_file(Path(path), cbasic.ROM_BYTES)
        before = card._read_file(Path(before_path), cbasic.ROM_BYTES) if before_path is not None else None
        info = cbasic.inspect_bootinfo(data, before_bytes=before)
    except (cbasic.CBasicError, card.CardError, ValueError, OSError) as exc:
        return CheckResult(False, f"BootInfo check failed: {exc}")
    state_text = "ENABLED (0xFFFFFFFF)" if info.state == "enabled" else "DISABLED (0x00000000)"
    msg = f"BootInfo verified: BootDisk is {state_text} at offset 0x3FF8004."
    if info.before_compared:
        msg += " Only +0x04..+0x07 changed compared to the earlier ROM dump."
    return CheckResult(
        True,
        msg,
        {
            **rom.facts,
            "boot_disk_state": info.state,
            "boot_disk_raw": info.boot_disk_raw,
            "rom_sha256": info.rom_sha256,
            "before_compared": info.before_compared,
        },
    )


def check_wav(path: Path) -> CheckResult:
    """The builder's WAV admission: 48 kHz mono 16-bit PCM, one data chunk, at most 1 MiB."""
    from audio_profile import admit_pcm

    try:
        _, facts = admit_pcm(Path(path))
    except (ValueError, OSError) as exc:
        return CheckResult(False, f"WAV refused: {exc}")
    ms = facts["decoded_frames"] * 1000 // facts["format"]["sample_rate"]
    return CheckResult(
        True,
        f"WAV accepted: 48 kHz mono 16-bit, {ms:,} ms, {facts['embedded_pcm_bytes']:,} bytes embedded.",
        {"duration_ms": ms, **facts},
    )


def check_toolchain(path: Path) -> CheckResult:
    """The builder's toolchain admission (target triple and GCC 15.2.1)."""
    from toolchain import admit_toolchain

    try:
        _, admission = admit_toolchain(Path(path))
    except (ValueError, OSError) as exc:
        return CheckResult(False, f"Toolchain refused: {exc}")
    return CheckResult(
        True,
        f"Toolchain accepted: {admission['target']} GCC {admission['gcc_version']} in {admission['resolved_path']}",
        {"resolved_path": admission["resolved_path"], "gcc_version": admission["gcc_version"]},
    )


def toolchain_candidates(
    *, home: Path | None = None, environ: dict | None = None, platform: str | None = None
) -> list[Path]:
    """Existing folders that might hold arm-none-eabi-gcc, most likely first. Read-only."""
    home = Path.home() if home is None else Path(home)
    environ = dict(os.environ) if environ is None else environ
    platform = sys.platform if platform is None else platform
    patterns = [
        str(home / "tools" / "xpack-arm-none-eabi-gcc-15.2.1-*" / "bin"),
        str(home / "xpack-arm-none-eabi-gcc-15.2.1-*" / "bin"),
        str(home / "Downloads" / "xpack-arm-none-eabi-gcc-15.2.1-*" / "bin"),
        str(home / "opt" / "xpack-arm-none-eabi-gcc-15.2.1-*" / "bin"),
        str(home / ".local" / "xPacks" / "@xpack-dev-tools" / "arm-none-eabi-gcc" / "15.2.1-*" / ".content" / "bin"),
    ]
    if platform == "darwin":
        patterns.append("/Applications/ArmGNUToolchain/15.2*/arm-none-eabi/bin")
    elif platform.startswith("win"):
        for var in ("APPDATA", "LOCALAPPDATA"):
            if environ.get(var):
                patterns.append(str(Path(environ[var]) / "xPacks" / "@xpack-dev-tools" / "arm-none-eabi-gcc" / "15.2.1-*" / ".content" / "bin"))
        patterns.append("C:/xpack-arm-none-eabi-gcc-15.2.1-*/bin")
    else:
        patterns.append("/opt/xpack-arm-none-eabi-gcc-15.2.1-*/bin")
    found: list[Path] = []
    on_path = shutil.which("arm-none-eabi-gcc", path=environ.get("PATH"))
    if on_path:
        found.append(Path(on_path).resolve().parent)
    for pattern in patterns:
        for match in sorted(glob.glob(pattern), reverse=True):
            found.append(Path(match))
    unique: list[Path] = []
    for p in found:
        if p.is_dir() and p not in unique:
            unique.append(p)
    return unique


def detect_toolchain(candidates: list[Path] | None = None) -> tuple[Path | None, CheckResult | None, list[str]]:
    """The first candidate the builder admits, with notes on candidates it refused."""
    notes = []
    for candidate in toolchain_candidates() if candidates is None else candidates:
        result = check_toolchain(candidate)
        if result.ok:
            return candidate, result, notes
        notes.append(f"{candidate}: {result.message}")
    return None, None, notes
