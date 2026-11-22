"""Run tools/build.py for the assistant and summarise the result.

The build runs as a separate `python tools/build.py ...` process (the documented
CLI, with all of its checks), never in-process; the packaged app runs itself in
build mode instead (bundle.builder_command). The summary only reads what the
builder wrote; the one file this module adds to the output folder is the
human-readable BUILD-CONTENTS.txt.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Callable

TOOLS_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parent
BUILD_PY = TOOLS_DIR / "build.py"
FIRMWARE_JSON = ROOT / "config" / "firmware.json"
CONTENTS_NAME = "BUILD-CONTENTS.txt"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import features
from gui import bundle


@dataclass
class BuildOutcome:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def error_text(self) -> str:
        lines = [l for l in self.stderr.strip().splitlines() if l.strip()]
        return lines[-1] if lines else f"build.py exited with status {self.returncode}"


def condense_log_line(line: str) -> str | None:
    """Turn one build.log line into a short progress line (command lines are JSON argv arrays)."""
    line = line.rstrip("\n")
    if not line.strip():
        return None
    if line.startswith("["):
        try:
            argv = json.loads(line)
        except json.JSONDecodeError:
            return line
        if isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv):
            tool = Path(argv[0]).name.removeprefix("arm-none-eabi-")
            target = argv[argv.index("-o") + 1] if "-o" in argv[:-1] else argv[-1]
            return f"{tool} {Path(target).name}"
    return line


def run_build(argv: list[str], output_dir: Path, on_line: Callable[[str], None],
              command: list[str] | None = None, poll_seconds: float = 0.2) -> BuildOutcome:
    """Run the builder, reporting stderr lines and condensed build.log progress as they appear."""
    log = Path(output_dir) / "build.log"
    offset = 0
    pending = ""
    stderr_lines: list[str] = []

    def tail():
        nonlocal offset, pending
        try:
            with open(log, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
        except FileNotFoundError:
            return
        pending += chunk
        *complete, pending = pending.split("\n")
        for line in complete:
            short = condense_log_line(line)
            if short:
                on_line(short)

    command = bundle.builder_command(BUILD_PY) if command is None else command
    with subprocess.Popen([*command, *argv], cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as proc:
        def pump_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)
                on_line(line.rstrip("\n"))

        reader = threading.Thread(target=pump_stderr, daemon=True)
        reader.start()
        while proc.poll() is None:
            tail()
            time.sleep(poll_seconds)
        stdout = proc.stdout.read()
        reader.join()
    tail()
    return BuildOutcome(proc.returncode, stdout, "".join(stderr_lines))


@dataclass
class BuildSummary:
    output_dir: Path
    autoexec: Path
    sha256: str
    bytes: int
    sha256_matches_build_json: bool
    profile: str
    options: dict[str, str]
    remaining_heap: int
    heap_floor: int
    heap_pool: int
    source_manifest_sha256: str
    rom_sha256: str
    gcc_version: str
    wavs: dict[str, dict]

    @property
    def heap_margin(self) -> int:
        return self.remaining_heap - self.heap_floor


def summarize(output_dir: Path) -> BuildSummary:
    """Read build.json, re-hash autoexec.bin and work out the memory margin."""
    out = Path(output_dir)
    record = json.loads((out / "build.json").read_text())
    firmware = json.loads(FIRMWARE_JSON.read_text())
    autoexec = out / "autoexec.bin"
    data = autoexec.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    profile = record.get("build_profile", record["profile"])
    # Builds without either cue record no indicator; builds from before the stop cue record no stop_cue;
    # builds without --screenshot (or from before it) record no screenshot.
    indicator = record.get("record_indicator") or {}
    options = {
        "record_indicator": indicator.get("cue", "off"),
        "record_stop_indicator": indicator.get("stop_cue", "off"),
        "screenshot": (record.get("screenshot") or {}).get("trigger", "off"),
    } if profile == "xpan-crop" else {}
    wavs = {}
    for key in ("startup_wav", "shutter_wav"):
        facts = record["caller_input_hashes"].get(key)
        if facts:
            wavs[key] = facts
    return BuildSummary(
        output_dir=out,
        autoexec=autoexec,
        sha256=sha,
        bytes=len(data),
        sha256_matches_build_json=sha == record["format"]["sha256"],
        profile=profile,
        options=options,
        remaining_heap=record["reservation"]["remaining_user_bytes"],
        heap_floor=firmware["minimum_user_left"],
        heap_pool=firmware["user_length"],
        source_manifest_sha256=record["witness"]["source_manifest_sha256"],
        rom_sha256=record["caller_input_hashes"]["rom"]["sha256"],
        gcc_version=record["caller_input_hashes"]["toolchain"]["gcc_version"],
        wavs=wavs,
    )


def memory_line(s: BuildSummary) -> str:
    return (f"Canon memory left after this build: {s.remaining_heap:,} bytes "
            f"(safety floor {s.heap_floor:,}; margin {s.heap_margin:,} bytes).")


def contents_text(s: BuildSummary, manifest: features.FeatureManifest, now: datetime | None = None) -> str:
    """The human-readable 'what's in this build' description."""
    now = datetime.now() if now is None else now
    profile = manifest.profiles[s.profile]
    level = manifest.status_levels[profile.status]
    lines = [
        "WHAT'S IN THIS BUILD",
        "====================",
        f"Made by R5 Mod Assistant (prototype) on {now:%Y-%m-%d %H:%M}.",
        f"Camera: {manifest.target} only.",
        "",
        "IMPORTANT: " + manifest.public_build_note,
        "",
        f"Profile: {s.profile} - {profile.label}",
        f"  {profile.description}",
        f"  Status: {level.label}. {level.text} {profile.status_detail}",
    ]
    for oid, value in s.options.items():
        option = manifest.options[oid]
        choice = option.choice(value)
        lines.append(f"Option: {option.label} = {value} ({choice.label})")
        if choice.status:
            clevel = manifest.status_levels[choice.status]
            lines.append(f"  Status: {clevel.label}. {clevel.text} {choice.status_detail or ''}".rstrip())
    for key, facts in s.wavs.items():
        lines.append(f"{manifest.inputs[key].label}: {facts['caller_input_path']}")
        lines.append(f"  SHA-256 {facts['wav_sha256']}, {facts['embedded_pcm_bytes']:,} bytes embedded")
    lines += [
        "",
        f"autoexec.bin: {s.bytes:,} bytes",
        f"  SHA-256 {s.sha256}" + ("" if s.sha256_matches_build_json else "  (DOES NOT MATCH build.json)"),
        memory_line(s),
        f"Source manifest SHA-256 (quote this in test reports): {s.source_manifest_sha256}",
        f"Built from your ROM with SHA-256 {s.rom_sha256} (keep the ROM private).",
        f"Toolchain: GNU Arm GCC {s.gcc_version}",
        "",
        "Not in this build and not done by the build: enabling BootDisk in the camera, writing card",
        "boot markers, or dumping the ROM. The card must be prepared (the assistant's Prepare card",
        "tab can write the boot markers on macOS) before you copy autoexec.bin to it.",
        "Full provenance: build.json and source-manifest.json in this folder.",
    ]
    return "\n".join(lines) + "\n"


def write_contents(s: BuildSummary, manifest: features.FeatureManifest) -> Path:
    path = s.output_dir / CONTENTS_NAME
    with open(path, "x", encoding="utf-8") as f:
        f.write(contents_text(s, manifest))
    return path
