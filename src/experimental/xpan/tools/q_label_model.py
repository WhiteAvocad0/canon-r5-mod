"""Actual C Q label metadata and pure policy; no string getter or target calls."""
from pathlib import Path
import ctypes
import hashlib
import json
import subprocess
import tempfile

from host_cc import find_host_cc

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent
MODEL_SOURCES_SRC = ("q_label.c", "q_label.h", "q_label_profile.h", "q_label_hooks.S", "frame_memory.c", "frame_memory.h")
MODEL_SOURCES_TOOLS = ("q_label_model.py", "q-label-model-export.c", "q_label_profile.py")
POLICY_SOURCES = ("q_label.c", "frame_memory.c")
FLAGS = ("-DQ_LABEL_POLICY_ONLY",)
_METADATA = None
_POLICY = None


def model_digests() -> dict[str, str]:
    digests = {name: hashlib.sha256((XPAN_SRC / name).read_bytes()).hexdigest() for name in MODEL_SOURCES_SRC}
    for name in MODEL_SOURCES_TOOLS:
        digests[name] = hashlib.sha256((TOOLS_DIR / name).read_bytes()).hexdigest()
    return digests


def verify_model_sources(expected: dict) -> None:
    if not isinstance(expected, dict) or expected != model_digests():
        raise ValueError("Q label model source drift")


def export_model() -> dict:
    before = model_digests()
    cc = find_host_cc()
    with tempfile.TemporaryDirectory(prefix="r5-q-label-export-") as tmp:
        exe = Path(tmp) / "export"
        subprocess.run(
            [
                cc, "-std=c11", "-Wall", "-Wextra", "-Werror", *FLAGS,
                "-I" + str(XPAN_SRC),
                str(TOOLS_DIR / "q-label-model-export.c"),
                *[str(XPAN_SRC / n) for n in POLICY_SOURCES],
                "-o", str(exe),
            ],
            check=True,
            capture_output=True,
        )
        result = json.loads(subprocess.check_output([str(exe)]))
    verify_model_sources(before)
    return result


def _metadata(expected: dict) -> dict:
    global _METADATA
    verify_model_sources(expected)
    key = tuple(sorted(expected.items()))
    if _METADATA is None or _METADATA[0] != key:
        _METADATA = (key, json.dumps(export_model(), sort_keys=True))
    return json.loads(_METADATA[1])


def admit_model(contract: dict) -> dict:
    actual = _metadata(contract.get("q_label_sources"))
    if actual != contract.get("q_label_model"):
        raise ValueError("Q label metadata differs from actual C owner")
    return actual


def _policy(expected: dict):
    global _POLICY
    verify_model_sources(expected)
    key = tuple(sorted(expected.items()))
    if _POLICY is None or _POLICY[0] != key:
        cc = find_host_cc()
        with tempfile.TemporaryDirectory(prefix="r5-q-label-policy-") as tmp:
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
        lib.q_label_admit.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        lib.q_label_admit.restype = ctypes.c_uint32
        verify_model_sources(expected)
        _POLICY = (key, lib)
    return _POLICY[1]


def admit_facts(words, expected_sources: dict) -> bool:
    meta = _metadata(expected_sources)
    count = meta["layouts"]["facts_bytes"] // ctypes.sizeof(ctypes.c_uint32)
    if not isinstance(words, (list, tuple)) or len(words) != count or any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in words):
        raise ValueError("Q label facts must match the actual C u32 extent")
    facts = (ctypes.c_uint32 * count)(*words)
    return bool(_policy(expected_sources).q_label_admit(facts))
