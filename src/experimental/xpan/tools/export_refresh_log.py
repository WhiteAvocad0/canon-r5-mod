"""Scoped R5R1 completion receipts; no exporter-consumption or atomic-copy claim."""
from __future__ import annotations

from collections import Counter
import re

from crop_log import decode_crop_records, decode_ram_records, values
from ram_capture_log import record_partition

MARKER = re.compile(r"R5R1 S=([0-9a-f]{16}) Q=%u I=%u R=%u")
RESULTS = {1: "bundle_returned", 2: "exhausted", 3: "unsupported"}


def decode_export_refresh_records(parsed: dict | bytes, contract: dict) -> dict:
    if isinstance(parsed, (bytes, bytearray, memoryview)):
        parsed = decode_ram_records(bytes(parsed))
    crop = decode_crop_records(parsed, contract)
    audio_available = bool(crop["audio"].get("available", True))
    groups = {
        "M2": crop["menu"]["boot_observations"]["groups"],
        "G1": crop["menu"]["groups"],
        "J1": crop["jpeg_trace"]["groups"],
    }
    if audio_available:
        groups["A1"] = crop["audio"]["groups"]
    receipts, rejected = [], []
    for row in parsed["records"]:
        if not row["text"].startswith("R5R1 "):
            continue
        try:
            token = MARKER.fullmatch(row["text"])
            if token is None or token[1] != contract["source_id"]:
                raise ValueError("unknown marker format or source")
            partition = record_partition([row])
            ticket, report, result = values(row, 3)
            if not 1 <= ticket <= 0xFFFFFFFF or result not in RESULTS:
                raise ValueError("invalid ticket or completion result")
            if (result == 1 and not 1 <= report <= 8) or (result != 1 and report != 0):
                raise ValueError("completion result/report association mismatch")
            selected = {
                name: [
                    g for g in candidates
                    if g["source_id"] == token[1] and g["sequence"] == report and g.get("partition_index") == partition
                ]
                for name, candidates in groups.items()
            }
            complete = result == 1 and all(len(g) == 1 for g in selected.values())
            cpu0 = complete and selected["M2"][0]["phases"][1]["cpu_id"] == 0
            offsets = {name: (g[0].get("record_offsets", g[0].get("offsets")) if len(g) == 1 else None) for name, g in selected.items()}
            if not audio_available:
                offsets["A1"] = None
            receipts.append({
                "source_id": token[1],
                "ticket": ticket,
                "partition_index": partition,
                "report_id": report if report else None,
                "result": RESULTS[result],
                "worker_bundle_return_marker_observed": result == 1,
                "request_bound_full_bundle_observed": bool(complete and cpu0),
                "late_mpidr_cpu0_observed": bool(cpu0),
                "exporter_consumed_ack_before_copy": None,
                "record_offset": row["offset"],
                "bundle_record_offsets": offsets,
            })
        except (ValueError, KeyError, TypeError) as error:
            rejected.append({"offset": row["offset"], "reason": str(error)})
    return {
        "protocol": "R5R1",
        "source_id": contract["source_id"],
        "format": parsed["format"],
        "requests": receipts,
        "rejected_markers": rejected,
        "ticket_counts": dict(Counter(item["ticket"] for item in receipts)),
        "bundle_observations": crop,
    }


def decode_export_refresh(data: bytes, contract: dict) -> dict:
    return decode_export_refresh_records(decode_ram_records(data), contract)
