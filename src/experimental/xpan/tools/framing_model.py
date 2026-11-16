"""Compile the actual scalar C framing owners; never invoke Canon or touch a card."""
from pathlib import Path
import ctypes
import hashlib
import json
import re
import subprocess
import tempfile

from host_cc import find_host_cc
import frame_row_profile

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent
MODEL_SOURCES_SRC = (
    "frame_native.c",
    "frame_native.h",
    "frame_native_profile.h",
    "frame_memory.c",
    "frame_memory.h",
    "frame_native_trace.c",
    "frame_native_trace.h",
    "frame_tags.h",
    "export_refresh.h",
    "frame-row/frame_row.c",
    "frame-row/frame_row.h",
)
MODEL_SOURCES_TOOLS = (
    "framing_model.py",
    "frame-model-export.c",
    "frame_row_profile.py",
)
_POLICY = None
_METADATA = None


def _resolve_include_dir(include_dir: Path | None, tmp_dir: Path) -> Path:
    if include_dir is not None:
        hdr = Path(include_dir) / "frame-row" / "frame_row_constants.h"
        if not hdr.is_file():
            raise FileNotFoundError(f"Missing generated frame_row_constants.h in {include_dir}")
        return Path(include_dir)
    synth_dir = tmp_dir / "synth_inc"
    (synth_dir / "frame-row").mkdir(parents=True, exist_ok=True)
    (synth_dir / "frame-row" / "frame_row_constants.h").write_text(
        frame_row_profile.render_synthetic_constants_header()
    )
    return synth_dir


def model_digests(include_dir: Path | None = None) -> dict[str, str]:
    digests = {name: hashlib.sha256((XPAN_SRC / name).read_bytes()).hexdigest() for name in MODEL_SOURCES_SRC}
    for name in MODEL_SOURCES_TOOLS:
        digests[name] = hashlib.sha256((TOOLS_DIR / name).read_bytes()).hexdigest()
    if include_dir is not None:
        hdr = Path(include_dir) / "frame-row" / "frame_row_constants.h"
        digests["frame-row/frame_row_constants.h"] = hashlib.sha256(hdr.read_bytes()).hexdigest()
    return digests


def verify_model_sources(expected: dict, include_dir: Path | None = None) -> None:
    if not isinstance(expected, dict) or expected != model_digests(include_dir=include_dir):
        raise ValueError("framing C model source drift")


def export_model(source_id: str = "0000000000000000", *, include_dir: Path | None = None) -> dict:
    if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{16}", source_id):
        raise ValueError("framing source ID must be 16 lowercase hexadecimal digits")
    before = model_digests(include_dir=include_dir)
    cc = find_host_cc()
    with tempfile.TemporaryDirectory(prefix="r5-framing-model-") as name:
        tmp = Path(name)
        inc = _resolve_include_dir(include_dir, tmp)
        exe = tmp / "export"
        subprocess.run(
            [
                cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-DFRAME_NATIVE_HOST", "-DFRAME_NATIVE_POLICY_ONLY",
                '-DOBSERVER_SOURCE_ID="' + source_id + '"',
                "-I" + str(inc),
                "-I" + str(inc / "frame-row"),
                "-I" + str(XPAN_SRC),
                str(TOOLS_DIR / "frame-model-export.c"),
                *(str(XPAN_SRC / n) for n in ("frame_native.c", "frame_memory.c", "frame_native_trace.c", "frame-row/frame_row.c")),
                "-o", str(exe),
            ],
            check=True,
            capture_output=True,
        )
        result = json.loads(subprocess.check_output([str(exe)]))
    verify_model_sources(before, include_dir=include_dir)
    return result


def admit_model(contract: dict, *, include_dir: Path | None = None) -> dict:
    """Admit retained metadata against the current exact C owners once."""
    global _METADATA
    sources = contract.get("model_sources")
    verify_model_sources(sources, include_dir=include_dir)
    source_id = contract.get("source_id")
    key = source_id, str(include_dir) if include_dir else None, tuple(sorted(sources.items()))
    if _METADATA is None or _METADATA[0] != key:
        _METADATA = key, json.dumps(export_model(source_id, include_dir=include_dir), sort_keys=True)
    actual = json.loads(_METADATA[1])
    if actual != contract.get("framing_model"):
        raise ValueError("retained framing metadata differs from actual C owners")
    return actual


def load_policy(expected_sources: dict, *, include_dir: Path | None = None):
    """Return the real C predicate/projection, keyed by admitted source contents."""
    global _POLICY
    verify_model_sources(expected_sources, include_dir=include_dir)
    key = str(include_dir) if include_dir else None, tuple(sorted(expected_sources.items()))
    if _POLICY is not None and _POLICY[0] == key:
        return _POLICY[1]
    cc = find_host_cc()
    with tempfile.TemporaryDirectory(prefix="r5-framing-policy-") as name:
        tmp = Path(name)
        inc = _resolve_include_dir(include_dir, tmp)
        output = tmp / "policy.so"
        subprocess.run(
            [
                cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                "-DFRAME_NATIVE_HOST", "-DFRAME_NATIVE_POLICY_ONLY",
                "-I" + str(inc),
                "-I" + str(inc / "frame-row"),
                "-I" + str(XPAN_SRC),
                str(XPAN_SRC / "frame_native.c"),
                str(XPAN_SRC / "frame_memory.c"),
                str(XPAN_SRC / "frame-row/frame_row.c"),
                "-o", str(output),
            ],
            check=True,
            capture_output=True,
        )
        library = ctypes.CDLL(str(output))
    for function in (library.frame_native_admit, library.frame_native_capture_admit):
        function.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        function.restype = ctypes.c_uint32
    library.frame_native_project.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    library.frame_native_project.restype = ctypes.c_uint32
    verify_model_sources(expected_sources, include_dir=include_dir)
    _POLICY = key, library
    return library
