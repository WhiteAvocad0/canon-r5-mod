"""Decode private first-failure facts with C-owned meanings and unknowns."""
from __future__ import annotations

from crop_log import framed, values
import disable_model
from ram_capture_log import record_partition


def decode_disable_records(parsed: dict, contract: dict) -> dict:
    sources = contract["disable_sources"]
    if sources != disable_model.digests():
        raise ValueError("First-disable model source drift")
    meta = disable_model.export_model(contract["source_id"])
    if meta != contract["disable_model"]:
        raise ValueError("First-disable metadata differs from C owner")
    fmt = meta["formats"]
    profile = meta["profile"]

    def group(rows: list[dict]) -> dict:
        partition = record_partition(rows)
        sequence, version, count = values(rows[0], 3)
        if not 1 <= sequence <= 8 or version != profile["schema"] or count != profile["words"]:
            raise ValueError("Failure report header mismatch")
        if rows[1]["text"] != fmt["source"] or rows[1]["numeric"]["status"] != "no_numeric_arguments":
            raise ValueError("Failure source mismatch")
        if rows[2]["text"] != fmt["state"]:
            raise ValueError("Failure publication state missing")
        (state,) = values(rows[2], 1)
        if state not in meta["states"].values():
            raise ValueError("Unknown failure publication state")
        if rows[-1]["text"] != fmt["end"] or values(rows[-1], 1) != [sequence]:
            raise ValueError("Failure report end mismatch")
        words = []
        position = 3
        facts = None
        reason = None
        site = None
        if state == meta["states"]["ready"]:
            while len(words) < count:
                if position >= len(rows) - 1 or rows[position]["text"] != fmt["words"]:
                    raise ValueError("Failure facts chunk missing")
                chunk = values(rows[position], 5)
                position += 1
                if chunk[0] != len(words):
                    raise ValueError("Failure facts chunk index mismatch")
                words.extend(chunk[1:])
            fields = meta["fields"]
            known = words[fields["known_mask"]]
            if known & ~profile["known_all"] or not known & (1 << fields["reason"]) or not known & (1 << fields["site"]):
                raise ValueError("Invalid failure known mask")
            reasons = {v: k for k, v in meta["reasons"].items()}
            sites = {v: k for k, v in meta["sites"].items()}
            reason = reasons.get(words[fields["reason"]])
            site = sites.get(words[fields["site"]])
            if reason is None or reason == "FD_NONE" or site is None:
                raise ValueError("Invalid first-failure reason/site")
            facts = {name: (words[index] if name == "known_mask" or known & (1 << index) else None) for name, index in fields.items()}
        if position != len(rows) - 1:
            raise ValueError("Unexpected failure report rows")
        return {
            "sequence": sequence,
            "source_id": contract["source_id"],
            "partition_index": partition,
            "state": next(k for k, v in meta["states"].items() if v == state),
            "facts": facts,
            "reason": reason,
            "site": site,
            "record_offsets": [r["offset"] for r in rows],
        }

    result = framed(parsed["records"], "R5D1", fmt["begin"], fmt["end"], group)
    result["format"] = parsed["format"]
    return result
