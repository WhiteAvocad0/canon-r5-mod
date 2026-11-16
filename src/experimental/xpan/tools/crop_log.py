"""M7 source-bound menu/root and local JPEG/ROI observations."""
from __future__ import annotations

import copy
import ctypes
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

from boot_mmu_log import _values as values
from host_cc import find_host_cc
import menu_log as MENU
from ram_capture_log import decode_ram_records, record_partition

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent
SCHEMA = "canon-r5-lab.crop-runtime-contract.v1"

J_BEGIN, J_END = "R5J1 B=%u V=%u", "R5J1 E=%u"
J_COUNT = "R5J1 C=%u N=%u I=%u A=%u"
J_READY = "R5J1 R=%u K=%u S=%u N=%u"
J_PAIR = "R5J1 F=%u K=%u W=%u A=%08X B=%08X"
J_SINGLE = "R5J1 F=%u K=%u W=%u A=%08X"
_POLICIES = {}


def policy_owner(c: dict):
    """Reuse the pinned real C policy; historical contracts may lack its source."""
    names = ("jpeg_crop.c", "jpeg_crop.h", "jpeg_crop_trace.h", "geometry.h")
    key = tuple(c.get("model_sources", {}).get(n) for n in names)
    try:
        matching = all(key) and all(hashlib.sha256((XPAN_SRC / n).read_bytes()).hexdigest() == h for n, h in zip(names, key))
    except OSError:
        matching = False
    if not matching:
        return None, "matching policy source unavailable; immutable facts preserved without reevaluation"
    if key not in _POLICIES:
        tmp = tempfile.TemporaryDirectory(prefix="r5-crop-trace-policy-")
        path = Path(tmp.name) / "policy.so"
        try:
            cc = find_host_cc()
            subprocess.run(
                [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-DXPAN_JPEG_HOST=1", "-shared", "-fPIC", str(XPAN_SRC / "jpeg_crop.c"), "-o", str(path)],
                check=True,
                capture_output=True,
            )
            library = ctypes.CDLL(str(path))
            for name in ("xpan_jpeg_crop_admit", "xpan_roi_crop_admit"):
                fn = getattr(library, name)
                fn.argtypes = [ctypes.c_void_p]
                fn.restype = ctypes.c_uint32
        except (OSError, subprocess.CalledProcessError) as exc:
            tmp.cleanup()
            return None, "host policy compilation unavailable: " + type(exc).__name__
        _POLICIES[key] = (tmp, library)
    library = _POLICIES[key][1]

    def check(role, words):
        data = (ctypes.c_uint32 * len(words))(*words)
        fn = library.xpan_roi_crop_admit if role == c["jpeg_trace"]["roi"] else library.xpan_jpeg_crop_admit
        return bool(fn(data))

    return check, None


def header(rows: list[dict], source: str, protocol: str, end: str, version: int = 1) -> tuple[int, int]:
    partition = record_partition(rows)
    seq, v = values(rows[0], 2)
    if not 1 <= seq <= 8 or v != version or rows[-1]["text"] != end or values(rows[-1], 1) != [seq]:
        raise ValueError("sequence/version/end mismatch")
    if rows[1]["text"] != protocol + " S=" + source or rows[1]["numeric"]["status"] != "no_numeric_arguments":
        raise ValueError("source token mismatch")
    return seq, partition


def jpeg_group(rows: list[dict], c: dict, policy, limitation: str | None) -> dict:
    config = c["jpeg_trace"]
    seq, partition = header(rows, c["source_id"], "R5J1", J_END, config["schema"])
    position, roles = 2, []
    for role in range(config["roles"]):
        if position >= len(rows) - 1 or rows[position]["text"] != J_COUNT:
            raise ValueError("missing or unordered local-trace counters")
        found, entered, identified, applied = values(rows[position], 4)
        position += 1
        if found != role:
            raise ValueError("local-trace counter role mismatch")
        snapshots = []
        for kind in range(config["kinds"]):
            if position >= len(rows) - 1 or rows[position]["text"] != J_READY:
                raise ValueError("missing or unordered local-trace snapshot state")
            r, k, state, count = values(rows[position], 4)
            position += 1
            wanted = config["roi_words"] if role == config["roi"] else config["jpeg_words"]
            if (r, k, count) != (role, kind, wanted) or state not in (config["empty"], config["writing"], config["ready"]):
                raise ValueError("snapshot identity/state/extent mismatch")
            words = None
            if state == config["ready"]:
                words = []
                for index in range(0, count, 2):
                    fmt, n = (J_PAIR, 5) if index + 1 < count else (J_SINGLE, 4)
                    if position >= len(rows) - 1 or rows[position]["text"] != fmt:
                        raise ValueError("published snapshot words missing or unordered")
                    fields = values(rows[position], n)
                    position += 1
                    if fields[:3] != [role, kind, index]:
                        raise ValueError("snapshot word index/owner mismatch")
                    words.extend(fields[3:])
            indices = config["roi_word_indices"] if role == config["roi"] else config["jpeg_word_indices"]
            if words is not None and role != config["roi"] and words[indices["JT_CONSUMER"]] != role:
                raise ValueError("snapshot consumer differs from its source trace role")
            consistent = policy(role, words) if words is not None and policy else None
            if kind == config["applied"] and words is not None and consistent is False:
                raise ValueError("first-applied facts contradict the actual pinned C policy")
            snapshots.append({
                "kind": "first_applied" if kind == config["applied"] else "first_identified",
                "state_raw": state,
                "published": words is not None,
                "words": words,
                "facts": {name: words[index] for name, index in indices.items()} if words is not None else None,
                "source_policy_admitted": consistent,
            })
        roles.append({
            "role": role,
            "counters": {"entered": entered, "identified": identified, "applied": applied},
            "counters_coherent": False,
            "snapshots": snapshots,
            "local_application_observed": snapshots[config["applied"]]["published"],
        })
    if position != len(rows) - 1:
        raise ValueError("extra local-trace words or records")
    return {
        "sequence": seq,
        "source_id": c["source_id"],
        "partition_index": partition,
        "roles": roles,
        "local_applied_roles": [r["role"] for r in roles if r["local_application_observed"]],
        "policy_reevaluation_limitation": limitation,
        "same_event_pairing_verified": False,
        "capture_queue_ownership_verified": False,
        "actual_pixels_verified": False,
        "record_offsets": [r["offset"] for r in rows],
    }


def framed(records: list[dict], prefix: str, begin: str, end: str, interpret) -> dict:
    groups, rejected, pending, orphans = [], [], [], []
    for r in records:
        if not r["text"].startswith(prefix + " "):
            continue
        if r["text"] == begin:
            if pending:
                rejected.append({"offset": pending[0]["offset"], "reason": "incomplete previous report"})
            pending = [r]
        elif pending:
            pending.append(r)
            if r["text"] == end:
                try:
                    groups.append(interpret(pending))
                except ValueError as exc:
                    rejected.append({"offset": pending[0]["offset"], "reason": str(exc)})
                pending = []
        else:
            orphans.append(r["offset"])
    if pending:
        rejected.append({"offset": pending[0]["offset"], "reason": "incomplete retained report"})
    return {
        "protocol": prefix,
        "complete_groups": len(groups),
        "groups": groups,
        "rejected_groups": rejected,
        "orphan_record_offsets": orphans,
    }


def strict_menu(parsed: dict, c: dict) -> dict:
    menu_contract = copy.deepcopy(c)
    menu_contract["schema"] = MENU.SCHEMA
    result = MENU.decode_menu_records(parsed, menu_contract)
    accepted = []
    root_labels = {name: number for name, number in c["root_codes"].items()}
    policy = {row[0]: row[1:] for row in c["boot_policy"]["primary"]}
    secondary = {row[0]: row[1:] for row in c["boot_policy"]["secondary"]}
    for g in result["groups"]:
        r0, r1 = (root_labels[name] for name in g["core_results"])
        after0, after1 = g["startup_roots_after_raw"]
        if g["root_ready"]:
            valid = (
                after0 == c["root_physical"]
                and policy[r0][1] == c["boot_policy"]["mod"]
                and secondary[r1][1] == c["boot_policy"]["entry_mod"]
                and after1 == c["root_physical"]
            )
        else:
            valid = (
                after0 == c["admission_profile"]["ttbr1"]
                and policy[r0][0] == c["boot_policy"]["stock"]
                and r1 == c["root_codes"]["ROOT_NONE"]
            )
        if not valid:
            result["rejected_groups"].append({"offset": g["record_offsets"][0], "reason": "M7 terminal boot policy cannot release this worker state"})
        else:
            g["feature_page_count"] = c["feature_page_count"]
            accepted.append(g)
    result["groups"] = accepted
    result["complete_groups"] = len(accepted)
    return result


def decode_crop_records(parsed: dict | bytes, c: dict) -> dict:
    if isinstance(parsed, (bytes, bytearray, memoryview)):
        parsed = decode_ram_records(bytes(parsed))
    if not isinstance(c, dict) or c.get("schema") != SCHEMA or not isinstance(c.get("source_id"), str) or not re.fullmatch(r"[0-9a-f]{16}", c["source_id"]):
        raise ValueError("invalid crop runtime contract")
    menu = strict_menu(parsed, c)
    if c.get("packed_roles"):
        raise ValueError("packed_roles unexpected in silent public xpan-crop contract")
    a1_rows = [r for r in parsed["records"] if r["text"].startswith("R5A1 ")]
    audio = {
        "available": False,
        "complete_groups": 0,
        "groups": [],
        "rejected_groups": [{"offset": r["offset"], "reason": "R5A1 unexpected in crop-only contract"} for r in a1_rows],
        "orphan_record_offsets": [r["offset"] for r in a1_rows],
    }
    policy, limitation = policy_owner(c)
    jpeg = framed(parsed["records"], "R5J1", J_BEGIN, J_END, lambda rows: jpeg_group(rows, c, policy, limitation))
    return {
        "protocol": "M7",
        "source_id": c["source_id"],
        "format": parsed["format"],
        "menu": menu,
        "audio": audio,
        "jpeg_trace": jpeg,
        "full_crop_feature_verified": False,
    }


def decode_crop(data: bytes, contract: dict) -> dict:
    return decode_crop_records(decode_ram_records(data), contract)
