"""Canonical host owner for the fixed R5.221 one-shot audio status format.

No camera IO. Raw numbers remain observations; settled state is not audible success.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Mapping

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "config" / "audio_log_format.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
FIELDS = tuple(SCHEMA["field_order"])
FLAGS = SCHEMA["observation_flags"]
OUTCOMES = {value: name for name, value in SCHEMA["outcomes"].items()}
_VALID_FOR = {
    "CONTEXT_BEFORE": "context_before_sampled", "CONTEXT_AFTER": "context_after_sampled",
    "STATE_BEFORE": "state_before_queried", "STATE_AFTER": "state_after_queried",
    "PHASE_BEFORE": "phase_before_sampled", "PHASE_AFTER": "phase_after_sampled",
    "TEST_OWNER_BEFORE": "test_owner_before_sampled", "SUBMIT_RETURN": "submit_return_observed",
    "ACTIVE_POLL": "own_pcm_active_observed", "ACTIVE_PCM_PTR": "own_pcm_active_observed",
    "ACTIVE_PCM_BYTES": "own_pcm_active_observed",
}

class AudioLogError(ValueError):
    """Rejected evidence with original bytes and successfully decoded partial facts."""
    def __init__(self, code: str, message: str, *, raw_bytes: bytes,
                 raw_fields: dict[str, int], identity: dict[str, str]):
        super().__init__(message)
        self.code = code
        self.raw_bytes = raw_bytes
        self.raw_fields = dict(raw_fields)
        self.identity = dict(identity)

def _identity(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("identity must be 64 lowercase hexadecimal characters")
    return value

def build_template(rom_sha256: str, source_sha256: str) -> tuple[bytes, dict[str, int]]:
    header = (SCHEMA["format"] + "\nROM_SHA256=" + _identity(rom_sha256)
              + "\nSOURCE_SHA256=" + _identity(source_sha256) + "\n").encode("ascii")
    body = bytearray(header)
    offsets = {}
    for name in FIELDS:
        body.extend((name + "=").encode("ascii"))
        offsets[name] = len(body)
        body.extend(b"00000000\n")
    return bytes(body), offsets

def render_audio_log(raw_fields: Mapping[str, int], *, rom_sha256: str,
                     source_sha256: str) -> bytes:
    if set(raw_fields) != set(FIELDS):
        raise ValueError("exact canonical runtime fields required")
    body, offsets = build_template(rom_sha256, source_sha256)
    result = bytearray(body)
    for key in FIELDS:
        value = raw_fields[key]
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            raise ValueError("raw field values must be unsigned 32-bit integers")
        off = offsets[key]
        result[off:off + 8] = f"{value:08x}".encode("ascii")
    return bytes(result)

def parse_audio_log(data: bytes, *, expected_rom_sha256: str,
                    expected_source_sha256: str, readiness_limit: int = 500,
                    observation_limit: int = 500, expected_pcm_pointer: int | None = None,
                    expected_pcm_bytes: int | None = None) -> dict:
    expected = {"rom_sha256": _identity(expected_rom_sha256),
                "source_sha256": _identity(expected_source_sha256)}
    for value in (readiness_limit, observation_limit):
        if type(value) is not int or not 1 <= value <= 0xffffffff:
            raise ValueError("poll limits must be positive uint32 integers")
    for value in (expected_pcm_pointer, expected_pcm_bytes):
        if value is not None and (type(value) is not int or not 1 <= value <= 0xffffffff):
            raise ValueError("expected PCM identity must be nonzero uint32 when supplied")
    if not isinstance(data, bytes):
        raise TypeError("log input must be bytes")
    raw = {}
    identity = {}
    def require(ok: bool, code: str, message: str) -> None:
        if not ok:
            raise AudioLogError(code, message, raw_bytes=data, raw_fields=raw, identity=identity)
    template, _ = build_template(expected_rom_sha256, expected_source_sha256)
    require(len(data) == len(template), "length", "unexpected fixed log length")
    rows = data.split(b"\n")
    require(len(rows) == 4 + len(FIELDS) and rows[-1] == b"", "structure", "exact LF lines required")
    require(rows[0] == SCHEMA["format"].encode("ascii"), "header", "unsupported diagnostic format")
    for row, key in zip(rows[1:3], ("ROM_SHA256", "SOURCE_SHA256")):
        prefix = (key + "=").encode("ascii")
        require(row.startswith(prefix) and re.fullmatch(rb"[0-9a-f]{64}", row[len(prefix):]) is not None,
                "identity_syntax", "malformed identity line")
        identity[key.lower()] = row[len(prefix):].decode("ascii")
    for key, row in zip(FIELDS, rows[3:-1]):
        prefix = (key + "=").encode("ascii")
        require(row.startswith(prefix), "field_order", "missing, duplicate, unknown or reordered field")
        encoded = row[len(prefix):]
        require(re.fullmatch(rb"[0-9a-f]{8}", encoded) is not None, "hex", "expected eight lowercase hex digits")
        raw[key] = int(encoded, 16)
    require(identity == expected, "identity_mismatch", "log identity differs from selected build")
    require(raw["OUTCOME"] in OUTCOMES, "outcome", "unrecognized outcome value")
    known_bits = 0
    for bit in FLAGS.values(): known_bits |= bit
    require(raw["OBS_FLAGS"] & ~known_bits == 0, "flags", "unknown observation flag")
    flags = {name: bool(raw["OBS_FLAGS"] & bit) for name, bit in FLAGS.items()}
    require(raw["READY_POLLS"] <= readiness_limit and raw["OBSERVE_POLLS"] <= observation_limit,
            "poll_limit", "poll count exceeds selected profile")
    require(raw["PHASE_SEEN_MASK"] & ~0x1f == 0, "phase_mask", "phase mask has unknown bits")
    observed = {}
    for key, flag in _VALID_FOR.items():
        require(flags[flag] or raw[key] == 0, "unflagged_value", "unobserved value must retain zero encoding")
        observed[key] = raw[key] if flags[flag] else None
    for suffix in ("BEFORE", "AFTER"):
        has_context = flags["context_" + suffix.lower() + "_sampled"] and raw["CONTEXT_" + suffix] != 0
        for kind in ("state", "phase"):
            flag = kind + "_" + suffix.lower() + ("_queried" if kind == "state" else "_sampled")
            require(not flags[flag] or has_context, "context_validity", "state/phase requires observed nonnull context")
    require(not flags["test_owner_before_sampled"] or
            (flags["context_before_sampled"] and raw["CONTEXT_BEFORE"] != 0),
            "context_validity", "test owner requires observed nonnull context")
    require(raw["PHASE_SEEN_MASK"] == 0 or raw["OBSERVE_POLLS"] >= 1,
            "phase_validity", "cumulative phase mask requires at least one observation attempt")
    if flags["own_pcm_active_observed"]:
        require(flags["submit_return_observed"], "active_evidence", "active evidence requires a submission")
        require(1 <= raw["ACTIVE_POLL"] <= raw["OBSERVE_POLLS"] and
                raw["ACTIVE_PCM_PTR"] != 0 and raw["ACTIVE_PCM_BYTES"] != 0,
                "active_evidence", "invalid active poll or PCM identity")
        require(raw["PHASE_SEEN_MASK"] & 2 != 0, "active_evidence", "active phase not present in mask")
        if expected_pcm_pointer is not None:
            require(raw["ACTIVE_PCM_PTR"] == expected_pcm_pointer, "pcm_identity", "PCM pointer mismatch")
        if expected_pcm_bytes is not None:
            require(raw["ACTIVE_PCM_BYTES"] == expected_pcm_bytes, "pcm_identity", "PCM byte count mismatch")
    if raw["OUTCOME"] == SCHEMA["outcomes"]["settled_observed"]:
        require(all(flags.values()) and raw["READY_POLLS"] >= 1 and
                raw["CONTEXT_BEFORE"] == raw["CONTEXT_AFTER"] != 0 and
                raw["STATE_BEFORE"] == raw["STATE_AFTER"] == 1 and
                raw["PHASE_BEFORE"] == raw["PHASE_AFTER"] == 3 and
                raw["SUBMIT_RETURN"] == 0 and raw["TEST_OWNER_BEFORE"] == 0 and
                raw["ACTIVE_POLL"] < raw["OBSERVE_POLLS"] and
                raw["PHASE_SEEN_MASK"] & 10 == 10,
                "settled_evidence", "settled outcome lacks ordered own-active/completion evidence")
    return {"identity": identity, "raw_fields": raw, "observation_flags": flags,
            "observed_fields": observed, "outcome": raw["OUTCOME"], "outcome_name": OUTCOMES[raw["OUTCOME"]]}
