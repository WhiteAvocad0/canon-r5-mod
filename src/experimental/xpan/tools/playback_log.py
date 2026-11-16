"""Source-bound crop tuples/current-main dimensions/first-view diagnostics; no rendering claim."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from crop_log import framed, values
import playback_model
from ram_capture_log import record_partition


def _decode_cohort_groups(parsed, contract, model, meta, protocol, label, ready_state, none_reason, scope_note, cohort_builder):
    fmt = meta["formats"]
    profile = meta["profile"]

    def group(rows):
        partition = record_partition(rows)
        sequence, version, words = values(rows[0], 3)
        if not 1 <= sequence <= model["report"]["sequence_max"] or version != profile["schema"] or words != profile["words"]:
            raise ValueError(f"{label} sequence/schema/extent mismatch")
        if rows[1]["text"] != fmt["source"] or rows[1]["numeric"]["status"] != "no_numeric_arguments":
            raise ValueError(f"{label} source mismatch")
        position = 2
        cohorts = []

        def take(name, count):
            nonlocal position
            if position >= len(rows) - 1 or rows[position]["text"] != fmt[name]:
                raise ValueError(f"missing/out-of-order {label} row: " + name)
            result = values(rows[position], count)
            position += 1
            return result

        for role in range(profile["roles"]):
            observed, state, hits = take("role", 3)
            if observed != role or state not in meta["states"].values():
                raise ValueError(f"{label} cohort/state mismatch")
            facts = None
            reason = None
            reason_code = None
            if state == meta["states"][ready_state]:
                data = []
                for index in range(0, words, 4):
                    chunk = take("words", 6)
                    if chunk[:2] != [role, index]:
                        raise ValueError(f"{label} cohort/chunk index mismatch")
                    data.extend(chunk[2:])
                mask = data[meta["fields"]["known_mask"]["word"]]
                if mask & ~profile["known_mask"]:
                    raise ValueError(f"{label} known mask outside C owner")
                facts = {
                    name: (data[field["word"]] if name == "known_mask" or mask & field["known_bit"] else None)
                    for name, field in meta["fields"].items()
                }
                reason_code = facts["reason"]
                if reason_code is not None:
                    labels = [n for n, v in meta["reasons"].items() if v == reason_code]
                    if len(labels) != 1 or labels[0] == none_reason:
                        raise ValueError(f"observed {label} reason outside C enum")
                    reason = labels[0]
            role_name = next(n for n, v in meta["roles"].items() if v == role)
            cohorts.append(cohort_builder(role_name, role, state, hits, facts, reason_code, reason))
        if position != len(rows) - 1 or rows[-1]["text"] != fmt["end"] or values(rows[-1], 1) != [sequence]:
            raise ValueError(f"{label} end/row extent mismatch")
        return {
            "sequence": sequence,
            "source_id": contract["source_id"],
            "partition_index": partition,
            "cohorts": cohorts,
            "cohort_chronology_verified": False,
            "record_offsets": [r["offset"] for r in rows],
            "scope": scope_note,
        }

    result = framed(parsed["records"], protocol, fmt["begin"], fmt["end"], group)
    result.update(format=parsed["format"], hardware_playback_verified=False)
    return result


def _decode_rejection_records(parsed, contract, model):
    meta = model["rejection"]

    def build(role_name, role, state, hits, facts, reason_code, reason):
        candidate = (
            None
            if facts is None
            else {"chain_index": facts["chain"], "matched_prefix": facts["prefix"], "runtime_caller_proven": False}
        )
        return {
            "role": role_name,
            "role_index": role,
            "state_raw": state,
            "state": next(n for n, v in meta["states"].items() if v == state),
            "hits": hits,
            "facts": facts,
            "reason_code": reason_code,
            "reason": reason,
            "source_candidate": candidate,
        }

    result = _decode_cohort_groups(
        parsed,
        contract,
        model,
        meta,
        "R5Q1",
        "rejection",
        "PNR_STATE_READY",
        "PNR_NONE",
        "Independent first-failure cohorts, not later-fault chronology, guard repair or rendering proof.",
        build,
    )
    result["guard_repair_verified"] = False
    return result


def _decode_first_view_records(parsed, contract, model):
    meta = model["first_view"]

    def build(role_name, role, state, hits, facts, reason_code, reason):
        site = None
        if facts is not None and facts.get("site") is not None:
            labels = [n for n, v in meta["sites"].items() if v == facts["site"]]
            if len(labels) != 1:
                raise ValueError("observed first_view site outside C enum")
            site = labels[0]
        return {
            "role": role_name,
            "role_index": role,
            "state_raw": state,
            "state": next(n for n, v in meta["states"].items() if v == state),
            "hits": hits,
            "facts": facts,
            "reason_code": reason_code,
            "reason": reason,
            "site": site,
        }

    return _decode_cohort_groups(
        parsed,
        contract,
        model,
        meta,
        "R5V1",
        "first_view",
        "PFV_STATE_READY",
        "PFV_R_NONE",
        "Independent immutable first-view invocation/rejection cohorts; diagnostic only.",
        build,
    )


def decode_playback_records(parsed: dict, contract: dict) -> dict:
    meta = playback_model.admit_model(contract)
    fmt = meta["formats"]

    def group(rows):
        partition = record_partition(rows)
        sequence, version = values(rows[0], 2)
        if not 1 <= sequence <= meta["report"]["sequence_max"] or version != meta["report"]["schema"]:
            raise ValueError("playback sequence/schema mismatch")
        if rows[1]["text"] != fmt["source"] or rows[1]["numeric"]["status"] != "no_numeric_arguments":
            raise ValueError("playback source mismatch")
        position = 2

        def take(name, count):
            nonlocal position
            if position >= len(rows) - 1 or rows[position]["text"] != fmt[name]:
                raise ValueError("missing/out-of-order playback row: " + name)
            result = values(rows[position], count)
            position += 1
            return result

        native = take("native0", 4) + take("native4", 4)
        mask = take("mask0", 4) + take("mask4", 4) + take("mask8", 2)
        nf = meta["fields"]["native_observation"]["members"]
        mf = meta["fields"]["mask_observation"]["members"]
        ready = mask[mf["view_ready"]["word"]]
        if ready not in (0, 1):
            raise ValueError("invalid playback private-view state")
        view = {"ready": bool(ready)}
        for name in ("view_revision", "object_revision", "cache_revision"):
            raw = mask[mf[name]["word"]]
            if ready and not raw:
                raise ValueError("ready playback view has unobserved revision")
            view[name] = raw if ready else None
        slots = []
        kinds = meta["box_kinds"]
        fact_fields = meta["fields"]["box_facts"]["members"]
        for index, requested in enumerate(meta["slots"]["objects"]):
            observed, known, obj, revision = take("tag", 4)
            if observed != index or known not in (0, 1) or obj != requested:
                raise ValueError("playback slot knowledge/identity mismatch")
            words = None
            crop_tuple = None
            current = None
            kind = None
            kind_name = None
            ratio = None
            if known:
                if not revision:
                    raise ValueError("known playback slot has zero revision")
                first = take("words0", 5)
                last = take("words4", 4)
                if first[0] != index or last[0] != index:
                    raise ValueError("playback facts slot/chunk mismatch")
                words = first[1:] + last[1:]
                tuple_field = fact_fields["words"]
                crop_tuple = words[tuple_field["word"] : tuple_field["word"] + tuple_field["count"]]
                current = {
                    "width": words[fact_fields["current_width"]["word"]],
                    "height": words[fact_fields["current_height"]["word"]],
                }
                outcome = playback_model.classify(words, contract["playback_sources"])
                kind = outcome["kind_code"]
                ratio = outcome["ratio"]
                names = [n for n, v in kinds.items() if v == kind]
                if len(names) != 1:
                    raise ValueError("C playback kind outside compiled enum")
                kind_name = names[0]
            elif revision:
                raise ValueError("unknown playback slot invents a published revision")
            slots.append({
                "index": index,
                "known": bool(known),
                "object": obj,
                "revision": revision if known else None,
                "facts": words,
                "crop_tuple": crop_tuple,
                "current_main_dimensions": current,
                "kind_code": kind,
                "kind": kind_name,
                "crop_tuple_policy_eligible": None if kind is None else kind != kinds["PB_BOX_UNKNOWN"],
                "ratio": ratio,
            })
        if position != len(rows) - 1 or rows[-1]["text"] != fmt["end"] or values(rows[-1], 1) != [sequence]:
            raise ValueError("playback end/extent mismatch")
        return {
            "sequence": sequence,
            "source_id": contract["source_id"],
            "partition_index": partition,
            "native_counts": {name: native[f["word"]] for name, f in nf.items()},
            "mask_counts": {name: mask[f["word"]] for name, f in mf.items() if f["word"] < mf["view_ready"]["word"]},
            "mask_words_raw": mask,
            "mask_view": view,
            "slots": slots,
            "counts_coherence_verified": False,
            "whole_table_coherence_verified": False,
            "record_offsets": [r["offset"] for r in rows],
        }

    result = framed(parsed["records"], "R5P1", fmt["begin"], fmt["end"], group)
    result["rejections"] = _decode_rejection_records(parsed, contract, meta)
    result["first_view"] = _decode_first_view_records(parsed, contract, meta)
    result.update(format=parsed["format"], hardware_playback_verified=False)
    return result


if __name__ == "__main__":
    from framing_log import decode_runtime

    p = argparse.ArgumentParser()
    p.add_argument("dump", type=Path)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--build-dir", type=Path, default=None)
    a = p.parse_args()
    inc = a.build_dir if a.build_dir is not None else a.contract.resolve().parent
    print(json.dumps(decode_runtime(a.dump.read_bytes(), json.loads(a.contract.read_text()), include_dir=inc), indent=2))
