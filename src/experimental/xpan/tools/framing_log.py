"""Source-bound private F1 observations; camera rendering is separate evidence."""
from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import re

from crop_log import framed, values
import framing_model
from ram_capture_log import decode_ram_records, record_partition

SCHEMA = "canon-r5-lab.framing-runtime-contract.v1"


def named_words(words: list[int], layout: dict) -> dict:
    result = {}
    for name, field in layout["fields"].items():
        start, count = field["word"], field["count"]
        result[name] = words[start] if count == 1 else words[start : start + count]
    return result


def interpret_snapshot(role: int, kind: int, state: int, words: list[int], meta: dict, policy) -> dict:
    state_names = {value: name for name, value in meta["snapshot_state"].items()}
    result = {"state": state_names[state], "state_raw": state, "facts": None, "native_case": None, "derived_projected_row": None}
    if state != meta["snapshot_state"]["ready"]:
        return result
    applied = kind == meta["snapshot_kind"]["applied"]
    array = (ctypes.c_uint32 * len(words))(*words)
    if role in (meta["role_ids"]["row"], meta["role_ids"]["offset"]):
        facts = named_words(words, meta["facts"])
        if facts["role"] != role:
            raise ValueError("snapshot role contradicts its complete native facts")
        case = int(policy.frame_native_admit(array))
        if applied and not case:
            raise ValueError("applied snapshot contradicts actual C framing admission")
        projected = None
        if case:
            output = (ctypes.c_uint32 * 12)(*facts["row"])
            status = int(policy.frame_native_project(array, output))
            if status not in (1, 2):
                raise ValueError("C framing admission/projection disagreement")
            if applied and status != 1:
                raise ValueError("first-applied framing snapshot is not a new C projection")
            projected = list(output)
        result.update(facts=facts, native_case=case, derived_projected_row=projected)
        if role == meta["role_ids"]["offset"] and projected is not None:
            result["derived_returned_y"] = projected[11]
    elif role == meta["role_ids"]["capture"]:
        facts = named_words(words, meta["capture"])
        admitted = int(policy.frame_native_capture_admit(array))
        if applied and not admitted:
            raise ValueError("applied capture contradicts actual C capture admission")
        result.update(facts=facts, native_case=admitted)
    else:
        facts = {name: words[index] for name, index in meta["transport"]["fields"].items()}
        if facts["role"] != role or facts["known"] not in (0, 1) or facts["enabled"] not in (0, 1):
            raise ValueError("invalid private transport observations")
        result["facts"] = facts
    return result


def interpret_group(rows: list[dict], contract: dict, meta: dict, policy) -> dict:
    partition = record_partition(rows)
    f = meta["formats"]
    sequence, version = values(rows[0], 2)
    if not 1 <= sequence <= meta["refresh"]["forced_max"] or version != meta["profile"]["schema"]:
        raise ValueError("framing report sequence/schema mismatch")
    if rows[-1]["text"] != f["end"] or values(rows[-1], 1) != [sequence]:
        raise ValueError("framing report end mismatch")
    if rows[1]["text"] != f["source"] or rows[1]["numeric"]["status"] != "no_numeric_arguments":
        raise ValueError("framing report source token mismatch")
    if rows[2]["text"] != f["disabled"]:
        raise ValueError("framing disabled observation missing")
    (disabled,) = values(rows[2], 1)
    if disabled not in (0, 1):
        raise ValueError("invalid framing disabled observation")
    roles = {}
    position = 3
    role_names = {value: name for name, value in meta["role_ids"].items()}
    kind_names = {value: name for name, value in meta["snapshot_kind"].items()}
    for _ in role_names:
        if position >= len(rows) - 1 or rows[position]["text"] != f["counts"]:
            raise ValueError("framing role count record missing")
        role, entered, identified, applied, rejected = values(rows[position], 5)
        position += 1
        if role not in role_names or role in roles:
            raise ValueError("duplicate/foreign framing role")
        snapshots = {}
        for _ in kind_names:
            if position >= len(rows) - 1 or rows[position]["text"] != f["ready"]:
                raise ValueError("framing snapshot header missing")
            owner_role, kind, state, count = values(rows[position], 4)
            position += 1
            if (
                owner_role != role
                or kind not in kind_names
                or kind in snapshots
                or state not in meta["snapshot_state"].values()
                or count != meta["role_words"][role]
            ):
                raise ValueError("framing snapshot identity/state/extent mismatch")
            words = []
            if state == meta["snapshot_state"]["ready"]:
                while len(words) < count:
                    remaining = count - len(words)
                    amount = min(4, remaining)
                    expected_format = f["words"] if amount == 4 else f["tail"]
                    if amount not in (3, 4) or position >= len(rows) - 1 or rows[position]["text"] != expected_format:
                        raise ValueError("missing/foreign framing snapshot chunk")
                    chunk = values(rows[position], amount + 1)
                    position += 1
                    if chunk[0] != len(words):
                        raise ValueError("framing snapshot chunk index mismatch")
                    words.extend(chunk[1:])
            snapshots[kind] = interpret_snapshot(role, kind, state, words, meta, policy)
        roles[role] = {
            "role_id": role,
            "counts_raw": {"entered": entered, "identified": identified, "applied": applied, "rejected": rejected},
            "snapshots": {kind_names[k]: v for k, v in snapshots.items()},
        }
    if position != len(rows) - 1:
        raise ValueError("unexpected framing rows after complete role blocks")
    return {
        "sequence": sequence,
        "source_id": contract["source_id"],
        "partition_index": partition,
        "framing_disabled_sample": bool(disabled),
        "roles": {role_names[k]: v for k, v in roles.items()},
        "record_offsets": [r["offset"] for r in rows],
    }


def decode_framing_records(parsed: dict, contract: dict, *, include_dir: Path | None = None) -> dict:
    if (
        not isinstance(contract, dict)
        or contract.get("schema") != SCHEMA
        or not isinstance(contract.get("source_id"), str)
        or not re.fullmatch(r"[0-9a-f]{16}", contract["source_id"])
    ):
        raise ValueError("invalid framing runtime contract")
    meta = framing_model.admit_model(contract, include_dir=include_dir)
    policy = framing_model.load_policy(contract["model_sources"], include_dir=include_dir)
    result = framed(
        parsed["records"],
        "R5F1",
        meta["formats"]["begin"],
        meta["formats"]["end"],
        lambda rows: interpret_group(rows, contract, meta, policy),
    )
    result.update(format=parsed["format"], hardware_framing_verified=False)
    return result


def decode_framing(data: bytes, contract: dict, *, include_dir: Path | None = None) -> dict:
    return decode_framing_records(decode_ram_records(data), contract, include_dir=include_dir)


def decode_runtime(data: bytes, contract: dict, *, include_dir: Path | None = None) -> dict:
    """Parse KUWA once; reuse the existing capture, framing, disable, and playback owners."""
    from export_refresh_log import decode_export_refresh_records

    parsed = decode_ram_records(data)
    refresh = decode_export_refresh_records(parsed, contract["capture_contract"])
    capture = refresh.pop("bundle_observations")
    framing = decode_framing_records(parsed, contract, include_dir=include_dir)
    disable = None
    if "disable_model" in contract:
        from disable_log import decode_disable_records

        disable = decode_disable_records(parsed, contract)
    for receipt in refresh["requests"]:
        matching = [
            g
            for g in framing["groups"]
            if g["source_id"] == receipt["source_id"]
            and g["sequence"] == receipt["report_id"]
            and g["partition_index"] == receipt["partition_index"]
        ]
        receipt["request_bound_capture_and_framing_bundle_observed"] = bool(
            receipt["request_bound_full_bundle_observed"] and len(matching) == 1
        )
        receipt["framing_record_offsets"] = matching[0]["record_offsets"] if len(matching) == 1 else None
    result = {
        "protocol": "M8",
        "source_id": contract["source_id"],
        "format": parsed["format"],
        "capture": capture,
        "framing": framing,
        "refresh": refresh,
        "first_disable": disable,
        "hardware_framing_verified": False,
    }
    if "playback_model" in contract:
        from playback_log import decode_playback_records

        result["playback"] = decode_playback_records(parsed, contract)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=None)
    args = parser.parse_args()
    inc = args.build_dir if args.build_dir is not None else args.contract.resolve().parent
    print(json.dumps(decode_runtime(args.dump.read_bytes(), json.loads(args.contract.read_text()), include_dir=inc), indent=2))
