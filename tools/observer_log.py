"""Decode source-selected R5O1 and R5O2 packed messages, independent of ring layout.

Scans candidate 84-byte DebugMsg records, not arbitrary KUWA header fields.
Preserves protocol version and raw payload facts without mixing versions or
assigning capture semantics to events or counter samples.
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

VERSION_CAPS = {
    "R5O1": 32,
    "R5O2": 64,
}
V2_MAX_DUMPS = 8
WORKER_KINDS = (
    "worker_boot",
    "worker_task",
    "worker_playback",
    "worker_baseline",
    "worker_counters",
)


def _to_i32(value: int) -> int:
    return value - 0x100000000 if (value & 0x80000000) else value


def decode_observer_log(data: bytes, expected_source: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}(?:[0-9a-f]{48})?", expected_source):
        raise ValueError("expected source must be a 16-character source ID or full SHA-256")
    expected_source = expected_source[:16]
    records, rejected = [], []
    for hit in re.finditer(b"R5O[12]", data):
        version = hit.group(0).decode("ascii")
        offset = hit.start() - 11
        item = {"offset": offset, "version": version}
        if offset < 0 or offset + 84 > len(data):
            rejected.append({**item, "reason": "truncated_record"})
            continue
        slot = data[offset : offset + 84]
        item.update(
            counter_raw=struct.unpack_from("<I", slot)[0],
            timer_raw=struct.unpack_from("<I", slot, 4)[0],
        )
        if slot[8:11] != b"\xff\x84\x03":
            rejected.append({**item, "reason": "record_tag_class_or_level"})
            continue
        end = slot.find(b"\0", 11)
        if end < 0:
            rejected.append({**item, "reason": "missing_bounded_NUL"})
            continue
        text = slot[11:end].decode("ascii", errors="backslashreplace")
        item["format"] = text
        formats = {
            f"{version} N=%u P=%08X L=%u V=%08X F=%u": (
                "event",
                ("sequence", "property", "length", "value", "flag_raw"),
            ),
            f"{version} D=%u C=%u O=%u": ("begin", ("dump", "count", "overflow")),
            f"{version} E=%u": ("end", ("dump",)),
            f"{version} OVERFLOW": ("overflow", ()),
        }
        if version == "R5O2":
            formats.update(
                {
                    "R5O2 G=%u N=%u F=%u P=%08X V=%08X": (
                        "counter_sample",
                        ("sample_index", "callback_sequence", "flag_raw", "context_pointer", "value"),
                    ),
                    "R5O2 I=%u R=%d A=%u O=%u C=%u": (
                        "worker_boot",
                        (
                            "boot_return_observed",
                            "boot_rc_raw",
                            "startup_outcome_observed",
                            "startup_outcome",
                            "context_flags",
                        ),
                    ),
                    "R5O2 T=%d C=%d S=%d O=%u E=%u": (
                        "worker_task",
                        (
                            "raw_task_id_raw",
                            "create_returned_raw",
                            "start_returned_raw",
                            "task_owned",
                            "worker_flags",
                        ),
                    ),
                    "R5O2 W=%u P=%u S=%u D=%u F=%u": (
                        "worker_playback",
                        (
                            "enabled",
                            "playback_attempts",
                            "playback_successes",
                            "preflight_drops",
                            "fault_latched",
                        ),
                    ),
                    "R5O2 B=%u C=%08X V=%u L=%u R=%u": (
                        "worker_baseline",
                        (
                            "has_baseline",
                            "baseline_ctx",
                            "baseline_val",
                            "last_play_outcome",
                            "fault_reason",
                        ),
                    ),
                    "R5O2 Q=%u C=%u D=%u X=%u R=%u": (
                        "worker_counters",
                        (
                            "shots_observed",
                            "coalesced_drops",
                            "dropped_during_playback",
                            "dropped_disabled",
                            "counter_resets",
                        ),
                    ),
                }
            )
        token = re.fullmatch(rf"{version} S=([0-9a-f]{{16}}) T=%08X", text)
        if token:
            kind, fields = "token", ("token",)
            item["source_id"] = token[1]
        elif text in formats:
            kind, fields = formats[text]
        else:
            rejected.append({**item, "reason": "unknown_observer_format"})
            continue
        if end + 1 > 84 - 4 * len(fields):
            rejected.append({**item, "reason": "text_argument_overlap"})
            continue
        item["kind"] = kind
        for index, name in enumerate(fields):
            item[name] = struct.unpack_from("<I", slot, 80 - index * 4)[0]
        if kind == "event":
            flag = item.pop("flag_raw")
            observed = bool(flag) if flag in (0, 1) else None
            if version == "R5O1":
                item["u32_observed_raw"] = flag
                item["u32_observed"] = observed
                # Unsupported length remains a fact; zero is not interpreted as data.
                item["scalar_u32"] = item["value"] if observed else None
            else:
                item["first_u32_observed_raw"] = flag
                item["first_u32_observed"] = observed
                # R5O2 admits only the first u32 prefix when len >= 4 and buffer != NULL.
                item["first_u32"] = item["value"] if observed else None
                item["observation_scope"] = (
                    "FIRST_U32" if observed else ("UNOBSERVED" if observed is False else "UNKNOWN")
                )
        elif kind == "counter_sample":
            flag = item.pop("flag_raw")
            observed = bool(flag) if flag in (0, 1) else None
            item["observed_flag"] = flag
            item["counter_observed"] = observed
            item["counter_value"] = item["value"] if observed else None
        elif kind == "worker_boot":
            # boot_rc_raw / boot_rc_i32 hold the opaque FW_CANON_INIT post-return r0 register view.
            item["boot_rc_i32"] = _to_i32(item["boot_rc_raw"])
            ctx_flags = item["context_flags"]
            if ctx_flags <= 3:
                item["context_checked"] = bool(ctx_flags & 1)
                item["context_ready"] = bool(ctx_flags & 2) if item["context_checked"] else None
            else:
                item["context_checked"] = None
                item["context_ready"] = None
        elif kind == "worker_task":
            item["raw_task_id_i32"] = _to_i32(item["raw_task_id_raw"])
            item["create_returned_i32"] = _to_i32(item["create_returned_raw"])
            item["start_returned_i32"] = _to_i32(item["start_returned_raw"])
            wflags = item["worker_flags"]
            if wflags <= 0xF:
                item["worker_entered"] = bool(wflags & 1)
                item["create_return_observed"] = bool(wflags & 2)
                item["start_return_observed"] = bool(wflags & 4)
                item["delete_return_observed"] = bool(wflags & 8)
                item["create_returned"] = (
                    item["create_returned_i32"] if item["create_return_observed"] else None
                )
                item["start_returned"] = (
                    item["start_returned_i32"] if item["start_return_observed"] else None
                )
            else:
                item["worker_entered"] = None
                item["create_return_observed"] = None
                item["start_return_observed"] = None
                item["delete_return_observed"] = None
                item["create_returned"] = None
                item["start_returned"] = None
        records.append(item)

    groups = []
    for begin in [r for r in records if r["kind"] == "begin"]:
        version = begin["version"]
        max_logged = VERSION_CAPS[version]
        problems = []
        if sum(r["kind"] == "begin" and r["version"] == version and r["dump"] == begin["dump"] for r in records) != 1:
            problems.append("ambiguous_begin")
        delta = lambda r: (r["counter_raw"] - begin["counter_raw"]) & 0xFFFFFFFF
        ends = [
            r
            for r in records
            if r["kind"] == "end"
            and r["version"] == version
            and r["dump"] == begin["dump"]
            and 0 < delta(r) < 0x80000000
        ]
        if len(ends) != 1:
            problems.append("missing_or_ambiguous_end")
            interval_records = []
            members = []
        else:
            end_delta = delta(ends[0])
            interval_records = sorted(
                [r for r in records if 0 < delta(r) < end_delta],
                key=delta,
            )
            if any(r["version"] != version for r in interval_records):
                problems.append("mixed_protocol_versions")
            if any(0 < delta(r) < end_delta for r in rejected if "counter_raw" in r):
                problems.append("rejected_message_in_group")
            members = [r for r in interval_records if r["version"] == version]
        tokens = [r for r in members if r["kind"] == "token"]
        events = [r for r in members if r["kind"] == "event"]
        samples = [r for r in members if r["kind"] == "counter_sample"]
        worker_rows = [r for r in members if r["kind"] in WORKER_KINDS]
        if len({r["counter_raw"] for r in interval_records}) != len(interval_records):
            problems.append("ambiguous_record_order")
        if version == "R5O1":
            if any(
                e["u32_observed"] is None
                or (e["u32_observed"] and e["length"] != 4)
                or (e["u32_observed"] is False and e["value"] != 0)
                for e in events
            ):
                problems.append("unknown_or_inconsistent_scalar_observation")
        else:
            if any(
                e["first_u32_observed"] is None
                or (e["first_u32_observed"] and e["length"] < 4)
                or (e["first_u32_observed"] is False and e["value"] != 0)
                for e in events
            ):
                problems.append("unknown_or_inconsistent_first_u32_observation")
            if any(s["counter_observed"] is None for s in samples):
                problems.append("unknown_counter_sample_flag")
            if any(
                (s["counter_observed"] is False and (s["context_pointer"] != 0 or s["value"] != 0))
                or (s["counter_observed"] is True and s["context_pointer"] == 0)
                for s in samples
            ):
                problems.append("inconsistent_counter_sample")
            if (
                begin["dump"] < 1
                or begin["dump"] > V2_MAX_DUMPS
                or [s["sample_index"] for s in samples] != list(range(1, begin["dump"] + 1))
            ):
                problems.append("missing_duplicate_or_unordered_counter_samples")
            if events and samples:
                event_pos = [idx for idx, m in enumerate(members) if m["kind"] == "event"]
                sample_pos = [idx for idx, m in enumerate(members) if m["kind"] == "counter_sample"]
                if max(event_pos) > min(sample_pos):
                    problems.append("counter_samples_interleaved_with_events")
            if worker_rows:
                if [r["kind"] for r in worker_rows] != list(WORKER_KINDS):
                    problems.append("missing_duplicate_or_unordered_worker_supplement")
                prior_pos = [
                    idx for idx, m in enumerate(members) if m["kind"] in ("token", "event", "counter_sample")
                ]
                worker_pos = [idx for idx, m in enumerate(members) if m["kind"] in WORKER_KINDS]
                if prior_pos and worker_pos and min(worker_pos) < max(prior_pos):
                    problems.append("worker_supplement_before_prior_rows")
                invalid_flag = False
                inconsistent_context = False
                inconsistent_baseline = False
                for w in worker_rows:
                    if w["kind"] == "worker_boot":
                        if any(
                            w[f] not in (0, 1)
                            for f in ("boot_return_observed", "startup_outcome_observed")
                        ):
                            invalid_flag = True
                        if w["context_flags"] == 2:
                            inconsistent_context = True
                        elif w["context_flags"] not in (0, 1, 3):
                            invalid_flag = True
                    elif w["kind"] == "worker_task":
                        if w["task_owned"] not in (0, 1) or w["worker_flags"] > 0xF:
                            invalid_flag = True
                    elif w["kind"] == "worker_playback":
                        if any(w[f] not in (0, 1) for f in ("enabled", "fault_latched")):
                            invalid_flag = True
                    elif w["kind"] == "worker_baseline":
                        if w["has_baseline"] not in (0, 1):
                            invalid_flag = True
                        elif w["has_baseline"] == 1 and w["baseline_ctx"] == 0:
                            inconsistent_baseline = True
                if invalid_flag:
                    problems.append("invalid_worker_flag")
                if inconsistent_context:
                    problems.append("inconsistent_worker_context")
                if inconsistent_baseline:
                    problems.append("inconsistent_worker_baseline")
        if len(tokens) != 1:
            problems.append("missing_or_ambiguous_token")
        else:
            if tokens[0]["source_id"] != expected_source:
                problems.append("source_mismatch")
            if tokens[0]["token"] == 0:
                problems.append("missing_listener_identity")
        if begin["count"] > max_logged or [e["sequence"] for e in events] != list(range(1, begin["count"] + 1)):
            problems.append("missing_duplicate_or_unordered_events")
        if any(r["kind"] in ("begin", "end") for r in interval_records):
            problems.append("nested_group")
        if begin["overflow"] not in (0, 1):
            problems.append("unknown_overflow_flag")
        group_entry = {
            "version": version,
            "max_notifications_logged": max_logged,
            "dump": begin["dump"],
            "count": begin["count"],
            "overflow": begin["overflow"],
            "events": events,
            "source_id": tokens[0]["source_id"] if len(tokens) == 1 else None,
            "begin_counter_raw": begin["counter_raw"],
            "retrieval_complete": not problems,
            "stored_history_complete": not problems and not begin["overflow"],
            "problems": problems,
        }
        if version == "R5O2":
            group_entry["counter_samples"] = samples
            group_entry["worker_rows"] = worker_rows
            group_entry["worker_supplement"] = (
                {r["kind"]: r for r in worker_rows}
                if [r["kind"] for r in worker_rows] == list(WORKER_KINDS)
                else None
            )
        groups.append(group_entry)
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "expected_source_id": expected_source,
        "records": records,
        "rejected_candidates": rejected,
        "groups": groups,
        "capture_count": None,
        "coverage": "R5O1/R5O2 signature candidates only; no whole-ring chronology or capture semantics established",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path)
    parser.add_argument("--expected-source", required=True)
    args = parser.parse_args()
    print(json.dumps(decode_observer_log(args.dump.read_bytes(), args.expected_source), indent=2))
