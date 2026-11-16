"""Actual C-owned failure reason/layout/protocol facts."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import tempfile

from host_cc import find_host_cc

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent
SOURCES_SRC = ("frame_disable_trace.c", "frame_disable_trace.h")
SOURCES_TOOLS = ("frame-disable-model-export.c", "disable_model.py")


def digests() -> dict[str, str]:
    d = {name: hashlib.sha256((XPAN_SRC / name).read_bytes()).hexdigest() for name in SOURCES_SRC}
    for name in SOURCES_TOOLS:
        d[name] = hashlib.sha256((TOOLS_DIR / name).read_bytes()).hexdigest()
    return d


def export_model(source_id: str) -> dict:
    if not isinstance(source_id, str) or not re.fullmatch("[0-9a-f]{16}", source_id):
        raise ValueError("Invalid compiled failure source ID")
    before = digests()
    cc = find_host_cc()
    with tempfile.TemporaryDirectory(prefix="r5-disable-model-") as directory:
        exe = Path(directory) / "export"
        subprocess.run(
            [
                cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-DFRAME_NATIVE_HOST",
                '-DOBSERVER_SOURCE_ID="' + source_id + '"',
                "-I" + str(XPAN_SRC),
                str(TOOLS_DIR / "frame-disable-model-export.c"),
                str(XPAN_SRC / "frame_disable_trace.c"),
                "-o", str(exe),
            ],
            check=True,
            capture_output=True,
        )
        result = json.loads(subprocess.check_output([str(exe)]))
    if before != digests():
        raise ValueError("Disable model source changed during export")
    return result
