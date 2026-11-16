"""Source-bound layout for R5 2.2.1 KUWA RAM exports and 84-byte DebugMsg records."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import struct

RAM_BYTES = 262144
HEADER_BYTES = 128
RECORD_BYTES = 84
_COLLECTIONS = ("records", "unrecognized_slots", "untyped_text_candidates")
_TOKEN = re.compile(r"%[# +0]*[0-9]{0,2}(?:ll|l)?[duxXoc]")


def numeric_args(slot: bytes, text: str) -> dict:
    """Decode backward-packed 32-bit scalar args per producer 0xe0124138."""
    matches = list(_TOKEN.finditer(text))
    if any(c == "%" and not any(m.start() <= i < m.end() for m in matches) for i, c in enumerate(text)):
        return {"status": "unhandled_format", "arguments": []}
    result = []
    cursor = RECORD_BYTES
    for m in matches:
        if "ll" in m.group():
            return {"status": "unhandled_64bit_alignment", "arguments": result}
        cursor -= 4
        if cursor < 11 + len(text.encode("ascii")) + 1:
            return {"status": "text_argument_overlap", "arguments": result}
        value = struct.unpack_from("<I", slot, cursor)[0]
        entry = {"format": m.group(), "record_argument_offset": cursor, "raw_u32": value, "raw_hex": hex(value)}
        if m.group().endswith("d"):
            entry["signed_i32"] = value - (1 << 32) if value & (1 << 31) else value
        result.append(entry)
    return {"status": "decoded_scalar32" if matches else "no_numeric_arguments", "arguments": result}


def decode_debug_ring(data: bytes, baseline: bytes) -> dict:
    """Decode 84-byte DebugMsg records while preserving BEL and untyped strings."""
    records, unrecognized, untyped_text = [], [], []
    erased = 0
    for off in range(0, len(data) - RECORD_BYTES + 1, RECORD_BYTES):
        slot = data[off : off + RECORD_BYTES]
        if slot == bytes([255]) * RECORD_BYTES:
            erased += 1
            continue
        if slot[8] != 255:
            payload = slot[8:RECORD_BYTES]
            nul = payload.find(b"\0")
            if nul >= 0 and all(c in (7, 9, 10, 13) or 32 <= c < 127 for c in payload[:nul]):
                untyped_text.append({
                    "offset": hex(off),
                    "counter_candidate": struct.unpack_from("<I", slot)[0],
                    "timer_candidate": struct.unpack_from("<I", slot, 4)[0],
                    "payload_text_candidate": payload[:nul].decode("ascii"),
                    "limitation": "payload producer/tag not bound; do not assign DebugMsg class/severity or packed arguments",
                })
            unrecognized.append({"offset": hex(off), "reason": "non_DebugMsg_tag"})
            continue
        textbytes = slot[11:RECORD_BYTES]
        end = textbytes.find(b"\0")
        if end < 0:
            unrecognized.append({"offset": hex(off), "reason": "no_bounded_NUL"})
            continue
        textbytes = textbytes[:end]
        if any(c not in (7, 9, 10, 13) and not 32 <= c < 127 for c in textbytes):
            unrecognized.append({"offset": hex(off), "reason": "unsupported_text_byte"})
            continue
        text = textbytes.decode("ascii")
        records.append({
            "offset": hex(off),
            "counter_raw": struct.unpack_from("<I", slot)[0],
            "timer_raw": struct.unpack_from("<I", slot, 4)[0],
            "class_raw": slot[9],
            "level_flags_raw": slot[10],
            "text": text,
            "text_hex": textbytes.hex(),
            "numeric": numeric_args(slot, text),
            "changed_vs_baseline_slot": slot != baseline[off : off + RECORD_BYTES],
        })
    return {
        "records": records,
        "unrecognized_slots": unrecognized,
        "untyped_text_candidates": untyped_text,
        "erased_full_slots": erased,
    }


def _partitions(data: bytes, role: str) -> list[dict]:
    if len(data) != RAM_BYTES or data[:4] != b"KUWA":
        raise ValueError(f"{role} must be a {RAM_BYTES}-byte R5 2.2.1 KUWA RAM export")
    partitions = []
    next_offset = HEADER_BYTES
    for index in range(2):
        offset, extent, capacity, start, sequence = struct.unpack_from("<5I", data, 8 + index * 60)
        if offset != next_offset or extent < RECORD_BYTES or extent > RAM_BYTES - offset:
            raise ValueError(f"{role} partition {index} has an invalid or overlapping extent")
        if capacity != extent // RECORD_BYTES:
            raise ValueError(f"{role} partition {index} slot capacity contradicts its extent")
        slot_bytes = capacity * RECORD_BYTES
        padding_offset = offset + slot_bytes
        partitions.append({
            "index": index,
            "offset": offset,
            "byte_extent": extent,
            "slot_capacity": capacity,
            "start_sequence_raw": start,
            "next_sequence_raw": sequence,
            "slot_bytes": slot_bytes,
            "padding_offset": padding_offset,
            "padding_bytes": extent - slot_bytes,
            "padding_hex": data[padding_offset : offset + extent].hex(),
        })
        next_offset = offset + extent
    if next_offset != RAM_BYTES:
        raise ValueError(f"{role} partition extents do not cover the complete RAM export")
    return partitions


def _decode(data: bytes, baseline: bytes | None) -> dict:
    partitions = _partitions(data, "Capture")
    if baseline is not None:
        previous_partitions = _partitions(baseline, "Baseline")
        if any(
            any(current[key] != previous[key] for key in ("offset", "byte_extent", "slot_capacity"))
            for current, previous in zip(partitions, previous_partitions)
        ):
            raise ValueError("Capture and baseline partition layouts differ; physical-slot comparison is unavailable")
    result = {name: [] for name in _COLLECTIONS}
    result["erased_full_slots"] = 0
    for partition in partitions:
        start = partition["offset"]
        end = start + partition["slot_bytes"]
        previous = baseline[start:end] if baseline is not None else b""
        decoded = decode_debug_ring(data[start:end], previous)
        for name in _COLLECTIONS:
            for record in decoded[name]:
                relative = int(record["offset"], 16)
                record["offset"] = hex(start + relative)
                record["partition_index"] = partition["index"]
                record["partition_slot"] = relative // RECORD_BYTES
                if name == "unrecognized_slots":
                    record["slot_hex"] = data[start + relative : start + relative + RECORD_BYTES].hex()
                if name == "records" and baseline is None:
                    record.pop("changed_vs_baseline_slot", None)
            result[name].extend(decoded[name])
        result["erased_full_slots"] += decoded["erased_full_slots"]
        partition["record_counts"] = {name: len(decoded[name]) for name in _COLLECTIONS}
        partition["record_counts"]["erased_full_slots"] = decoded["erased_full_slots"]
    result["format"] = {
        "kind": "R5_221_KUWA_RAM",
        "bytes": len(data),
        "header_bytes": HEADER_BYTES,
        "record_bytes": RECORD_BYTES,
        "header_hex": data[:HEADER_BYTES].hex(),
        "next_counter_raw": struct.unpack_from("<I", data, 4)[0],
        "partitions": partitions,
        "sha256": hashlib.sha256(data).hexdigest(),
        "boundary_owner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "coverage": "Source-bound KUWA partition extents; complete records in physical slot order within each partition.",
    }
    if baseline is not None:
        result["format"].update(
            baseline_header_hex=baseline[:HEADER_BYTES].hex(),
            baseline_sha256=hashlib.sha256(baseline).hexdigest(),
        )
    return result


def record_partition(records: list[dict]) -> int:
    """Require one parsed storage partition for a protocol group."""
    if not records:
        raise ValueError("protocol group has no partition provenance")
    partition = records[0].get("partition_index")
    if type(partition) is not int or partition not in (0, 1) or any(
        type(r.get("partition_index")) is not int or r["partition_index"] != partition for r in records
    ):
        raise ValueError("protocol group spans partitions or lacks partition provenance")
    return partition


def decode_ram_capture(data: bytes, baseline: bytes) -> dict:
    return _decode(data, baseline)


def decode_ram_records(data: bytes) -> dict:
    return _decode(data, None)
