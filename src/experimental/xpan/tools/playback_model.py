"""Actual typed C playback metadata/policies; no Canon calls or Python classifier."""
from pathlib import Path
import ctypes
import hashlib
import json
import re
import subprocess
import tempfile

from host_cc import find_host_cc

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent
MODEL_SOURCES_SRC = (
    "playback_report.c", "playback_report.h",
    "playback_box.c", "playback_box.h",
    "playback_tags.c", "playback_tags.h",
    "playback_reject.c", "playback_reject.h",
    "playback_first_view.c", "playback_first_view.h",
    "playback_native.c", "playback_native.h", "playback_native_profile.h",
    "playback_mask_native.c", "playback_mask_native.h", "playback_mask_profile.h",
    "frame_memory.c", "frame_memory.h",
    "ifd_memory.c", "ifd_memory.h",
    "export_refresh.h",
)
MODEL_SOURCES_TOOLS = ("playback_model.py", "playback-model-export.c")
FLAGS = ("-DPLAYBACK_NATIVE_HOST", "-DPLAYBACK_NATIVE_POLICY_ONLY", "-DPLAYBACK_MASK_HOST", "-DPLAYBACK_MASK_POLICY_ONLY")
POLICY_SOURCES = (
    "playback_box.c", "playback_native.c", "playback_mask_native.c",
    "frame_memory.c", "ifd_memory.c", "playback_reject.c", "playback_first_view.c",
)
_METADATA = None
_POLICY = None


def model_digests() -> dict[str, str]:
    digests = {name: hashlib.sha256((XPAN_SRC / name).read_bytes()).hexdigest() for name in MODEL_SOURCES_SRC}
    for name in MODEL_SOURCES_TOOLS:
        digests[name] = hashlib.sha256((TOOLS_DIR / name).read_bytes()).hexdigest()
    return digests


def verify_model_sources(expected: dict) -> None:
    if not isinstance(expected, dict) or expected != model_digests():
        raise ValueError("playback model source drift")


def export_model(source_id: str = "0000000000000000") -> dict:
    if not isinstance(source_id, str) or not re.fullmatch("[0-9a-f]{16}", source_id):
        raise ValueError("invalid playback source token")
    before = model_digests()
    cc = find_host_cc()
    with tempfile.TemporaryDirectory(prefix="r5-playback-model-") as tmp:
        exe = Path(tmp) / "export"
        subprocess.run(
            [
                cc, "-std=c11", "-Wall", "-Wextra", "-Werror", *FLAGS,
                '-DOBSERVER_SOURCE_ID="' + source_id + '"',
                "-I" + str(XPAN_SRC),
                str(TOOLS_DIR / "playback-model-export.c"),
                *[str(XPAN_SRC / n) for n in POLICY_SOURCES],
                "-o", str(exe),
            ],
            check=True,
            capture_output=True,
        )
        result = json.loads(subprocess.check_output([str(exe)]))
    verify_model_sources(before)
    return result


def admit_model(contract: dict) -> dict:
    global _METADATA
    sources = contract.get("playback_sources")
    verify_model_sources(sources)
    key = contract.get("source_id"), tuple(sorted(sources.items()))
    if _METADATA is None or _METADATA[0] != key:
        _METADATA = (key, json.dumps(export_model(key[0]), sort_keys=True))
    actual = json.loads(_METADATA[1])
    if actual != contract.get("playback_model"):
        raise ValueError("playback metadata differs from actual C owner")
    return actual


def _policy(expected: dict):
    global _POLICY
    verify_model_sources(expected)
    key = tuple(sorted(expected.items()))
    if _POLICY is None or _POLICY[0] != key:
        cc = find_host_cc()
        with tempfile.TemporaryDirectory(prefix="r5-playback-policy-") as tmp:
            out = Path(tmp) / "policy.so"
            subprocess.run(
                [
                    cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", *FLAGS,
                    "-I" + str(XPAN_SRC),
                    *[str(XPAN_SRC / n) for n in POLICY_SOURCES],
                    "-o", str(out),
                ],
                check=True,
                capture_output=True,
            )
            lib = ctypes.CDLL(str(out))
        lib.playback_box_kind.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        lib.playback_box_kind.restype = ctypes.c_uint32
        lib.playback_box_ratio.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        lib.playback_box_ratio.restype = ctypes.c_uint32
        verify_model_sources(expected)
        _POLICY = (key, lib)
    return _POLICY[1]


def classify(words, expected_sources: dict) -> dict:
    """Classify the five-word crop tuple; current-main words do not prove file size."""
    if not isinstance(words, (list, tuple)) or len(words) != 7 or any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in words):
        raise ValueError("playback facts must be seven u32 words")
    lib = _policy(expected_sources)
    facts = (ctypes.c_uint32 * 7)(*words)
    ratio = (ctypes.c_uint32 * 2)()
    kind = int(lib.playback_box_kind(facts))
    observed = int(lib.playback_box_ratio(facts, ratio))
    return {"kind_code": kind, "ratio": list(ratio) if observed else None}
