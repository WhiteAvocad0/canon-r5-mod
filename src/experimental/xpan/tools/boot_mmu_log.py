"""Source-bound R5M1/R5M2 interpretation over the shared KUWA/DebugMsg record owner."""
from __future__ import annotations

import re
from ram_capture_log import decode_ram_records, record_partition

BEGIN = "R5M1 B=%u N=%u"
PHASE = "R5M1 P=%u F=%u C=%08X M=%08X"
REGS = "R5M1 A=%08X X=%08X T=%08X U=%08X"
CONTROL = "R5M1 R=%08X D=%08X"
TABLE = "R5M1 L=%u F=%08X E=%08X M=%08X"
SLOTS = "R5M1 H=%08X J=%08X W=%08X Z=%08X"
END = "R5M1 E=%u"
SOURCE = re.compile(r"R5M1 S=([0-9a-f]{16}) V=%u")


def _values(record: dict, count: int) -> list[int]:
    numeric = record["numeric"]
    if numeric["status"] != "decoded_scalar32" or len(numeric["arguments"]) != count:
        raise ValueError("unsupported or malformed packed scalar arguments")
    return [item["raw_u32"] for item in numeric["arguments"]]


def validate_snapshot_observation(flags: int, cpsr: int, sctlr: int, ttbr1: int, ttbcr: int) -> None:
    if flags not in (0, 1, 3):
        raise ValueError("invalid observation flags")
    if flags & 1 and cpsr & 31 not in (0x13, 0x1F):
        raise ValueError("observed CP15 registers contradict the producer privilege-mode guard")
    if flags & 2 and not (sctlr & 1 and ttbcr == 7 and ttbr1 & 0xFFFFC000 == 0xDFFC0000):
        raise ValueError("table observation contradicts its address guards")


def _group(records: list[dict], expected_source: str, protocol: str) -> dict:
    partition = record_partition(records)
    phases_required = 2 if protocol == "R5M1" else 3
    record_count = 4 + 6 * phases_required
    label = lambda text: text.replace("R5M1", protocol, 1)
    if len(records) != record_count:
        raise ValueError(f"expected {record_count} {protocol} records, got {len(records)}")
    sequence, phase_count = _values(records[0], 2)
    if not 1 <= sequence <= 8 or phase_count != phases_required:
        raise ValueError("invalid sequence or phase count")
    source_pattern = SOURCE if protocol == "R5M1" else re.compile(r"R5M2 S=([0-9a-f]{16}) V=%u C=%u R=%u")
    token = source_pattern.fullmatch(records[1]["text"])
    source_values = _values(records[1], 1 if protocol == "R5M1" else 3)
    if token is None or token[1] != expected_source or source_values[0] != (1 if protocol == "R5M1" else 2):
        raise ValueError("source or version mismatch")
    secondary_seen, restore_code = source_values[1:] if protocol == "R5M2" else (None, None)
    if protocol == "R5M2" and (secondary_seen not in (0, 1) or restore_code not in ((1, 2) if secondary_seen else (0,))):
        raise ValueError("invalid secondary publication or restore code")
    if records[-1]["text"] != label(END) or _values(records[-1], 1) != [sequence]:
        raise ValueError("missing or mismatched end record")

    phases = []
    for phase_index in range(phases_required):
        start = 2 + 6 * phase_index
        part = records[start : start + 6]
        if [r["text"] for r in part] != [label(t) for t in (PHASE, REGS, CONTROL, TABLE, TABLE, TABLE)]:
            raise ValueError("phase records missing or out of order")
        phase, flags, cpsr, mpidr = _values(part[0], 4)
        if phase != phase_index:
            raise ValueError("invalid phase index")
        sctlr, actlr, ttbr0, ttbr1 = _values(part[1], 4)
        ttbcr, dacr = _values(part[2], 2)
        validate_snapshot_observation(flags, cpsr, sctlr, ttbr1, ttbcr)
        if phase_index == 2:
            if not secondary_seen and any((flags, cpsr, mpidr, sctlr, actlr, ttbr0, ttbr1, ttbcr, dacr)):
                raise ValueError("unpublished secondary data was reported")
            if secondary_seen and (not flags & 1 or mpidr & 15 != 1):
                raise ValueError("published secondary snapshot is not admitted CPU1 data")
        tables = []
        for block in range(3):
            index, first, expected, mask = _values(part[3 + block], 4)
            if index != block or expected != 0xE005D406 + block * 0x1000000:
                raise ValueError("table index or pinned ROM descriptor mismatch")
            if flags & 2 and mask > 0xFFFF:
                raise ValueError("invalid descriptor mismatch mask")
            tables.append({"block": block, "first_raw": first, "expected": expected, "mismatch_mask": mask})
        phases.append({
            "phase": phase,
            "flags": flags,
            "cpsr_raw": cpsr,
            "mpidr_raw": mpidr,
            "cpu_id": mpidr & 15 if flags & 1 else None,
            "registers": {
                "sctlr": sctlr, "actlr": actlr, "ttbr0": ttbr0,
                "ttbr1": ttbr1, "ttbcr": ttbcr, "dacr": dacr,
            } if flags & 1 else None,
            "tables": tables if flags & 2 else None,
            "table_rows_raw": tables,
        })
    slots_index = 2 + 6 * phases_required
    if records[slots_index]["text"] != label(SLOTS):
        raise ValueError("fixed-slot record missing")
    cache_owner, jpeg_entry, encoder_secondary, encoder_window = _values(records[slots_index], 4)

    def tables_match(phase: dict) -> bool:
        return bool(phase["tables"] and all(row["mismatch_mask"] == 0 for row in phase["tables"]))

    early_cpu0_ok = bool(phases[0]["flags"] == 3 and phases[0]["cpu_id"] == 0 and tables_match(phases[0]))
    late_cpu0_ok = bool(phases[1]["flags"] == 3 and phases[1]["cpu_id"] == 0 and tables_match(phases[1]))
    secondary_cpu1_ok = bool(
        protocol == "R5M2" and secondary_seen and phases[2]["flags"] == 3 and phases[2]["cpu_id"] == 1 and tables_match(phases[2])
    )
    return {
        "sequence": sequence,
        "partition_index": partition,
        "source_id": expected_source,
        "secondary_published": bool(secondary_seen) if protocol == "R5M2" else None,
        "secondary_slot_restore_issued": restore_code == 1 if protocol == "R5M2" else None,
        "secondary_restore_code": restore_code,
        "phases": phases,
        "fixed_slots_raw": {
            "0x171dc": cache_owner, "0x158d0": jpeg_entry,
            "0x30830": encoder_secondary, "0x30840": encoder_window,
        },
        "early_cpu0_baseline_matched": early_cpu0_ok,
        "late_cpu0_baseline_matched": late_cpu0_ok,
        "secondary_cpu1_baseline_matched": secondary_cpu1_ok if protocol == "R5M2" else None,
        "offsets": [r["offset"] for r in records],
    }


def decode_boot_mmu_records(parsed: dict, expected_source: str, *, protocol: str = "R5M1") -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", expected_source):
        raise ValueError("expected_source must be 16 lowercase hex characters")
    if protocol not in ("R5M1", "R5M2"):
        raise ValueError("unsupported boot MMU protocol")
    begin_text = BEGIN.replace("R5M1", protocol, 1)
    end_text = END.replace("R5M1", protocol, 1)
    records = [r for r in parsed["records"] if r["text"].startswith(protocol + " ")]
    groups, rejected, pending, orphans = [], [], [], []
    for record in records:
        if record["text"] == begin_text:
            if pending:
                rejected.append({"offset": pending[0]["offset"], "reason": "incomplete group before next begin"})
            pending = [record]
        elif pending:
            pending.append(record)
            if record["text"] == end_text:
                try:
                    groups.append(_group(pending, expected_source, protocol))
                except ValueError as exc:
                    rejected.append({"offset": pending[0]["offset"], "reason": str(exc)})
                pending = []
        else:
            orphans.append(record["offset"])
    if pending:
        rejected.append({"offset": pending[0]["offset"], "reason": "incomplete group at end of capture"})
    return {
        "protocol": protocol,
        "format": parsed["format"],
        "source_id": expected_source,
        "complete_groups": len(groups),
        "groups": groups,
        "rejected_groups": rejected,
        "orphan_record_offsets": orphans,
    }


def decode_boot_mmu(data: bytes, expected_source: str, *, protocol: str = "R5M1") -> dict:
    return decode_boot_mmu_records(decode_ram_records(data), expected_source, protocol=protocol)
