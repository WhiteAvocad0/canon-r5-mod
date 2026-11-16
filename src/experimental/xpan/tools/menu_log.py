"""Source-bound R5G1 registration observations over the shared KUWA record owner."""
from __future__ import annotations

import re
from boot_mmu_log import _values, decode_boot_mmu_records
from ram_capture_log import record_partition

SCHEMA = "canon-r5-lab.menu-runtime-contract.v1"
FORMATS = [
    "R5G1 B=%u V=%u", None, "R5G1 P=%u H=%08X R=%08X T=%08X",
    "R5G1 A=%u P=%08X V=%u R=%u", "R5G1 H=%u A=%08X B=%08X",
    "R5G1 I=%u N=%u R=%u D=%08X", "R5G1 C=%08X R=%08X",
    "R5G1 U=%u R=%u A=%u B=%u", "R5G1 T=%08X U=%08X R=%08X C=%08X",
    "R5G1 L=%u P=%u D=%u C=%08X", "R5G1 E=%u",
]
SOURCE = re.compile(r"R5G1 S=([0-9a-f]{16})")


def _profile(phase: dict, c: dict, core: int, *, boot: bool, root: int) -> bool:
    p = c["admission_profile"]
    r = phase["registers"]
    if phase["cpu_id"] != core or r is None or not phase["flags"] & 1:
        return False
    for name in ("sctlr", "actlr", "ttbr0", "ttbr1", "ttbcr", "dacr"):
        expected = root if name == "ttbr1" else p[name] + (core * p["ttbr0_stride"] if name == "ttbr0" else 0)
        if r[name] != expected:
            return False
    if boot:
        tables = phase["tables"]
        return bool(
            phase["flags"] == 3
            and phase["cpsr_raw"] & p["boot_cpsr_mask"] == p["boot_cpsr_value"]
            and tables is not None
            and all(t["first_raw"] == 0xE005D406 + n * 0x1000000 and t["mismatch_mask"] == 0 for n, t in enumerate(tables))
        )
    return bool(phase["cpsr_raw"] & 31 in (0x13, 0x1F))


def _contract(c: dict) -> None:
    if not isinstance(c, dict) or c.get("schema") != SCHEMA or not isinstance(c.get("source_id"), str) or not re.fullmatch(r"[0-9a-f]{16}", c["source_id"]):
        raise ValueError("invalid menu runtime contract")
    required = ("target_va", "root_physical", "table_physical", "page_physical", "expected_page_checksum")
    menu_keys = (
        "count", "target_id", "source_table", "source_end", "source_target_prefix",
        "source_target_bytes", "table_bytes", "table_reserved", "prefix_reserved",
        "allocation_bytes", "heap_base", "heap_end", "heap_min_raw", "heap_max_raw",
        "vector_bytes", "getter", "original_getter", "aggregate_root",
    )
    m = c.get("menu", {})
    if not isinstance(m, dict):
        raise ValueError("invalid menu constants owner")
    for key, source in [(x, c) for x in required] + [(x, m) for x in menu_keys]:
        if type(source.get(key)) is not int or not 0 <= source[key] <= 0xFFFFFFFF:
            raise ValueError("missing or invalid contract value: " + key)
    for name in ("prepare_codes", "install_codes", "root_codes"):
        codes = c.get(name)
        if not isinstance(codes, dict) or not codes or any(type(x) is not int or x < 0 for x in codes.values()) or len(set(codes.values())) != len(codes):
            raise ValueError("invalid contract status owner: " + name)
    p = c.get("admission_profile", {})
    if not isinstance(p, dict) or any(
        type(p.get(key)) is not int or not 0 <= p[key] <= 0xFFFFFFFF
        for key in ("sctlr", "actlr", "ttbr0", "ttbr1", "ttbcr", "dacr", "ttbr0_stride", "boot_cpsr_mask", "boot_cpsr_value")
    ):
        raise ValueError("invalid admission profile owner")
    if not {"ROOT_PREPARED", "ROOT_SWITCHED"} <= c["root_codes"].keys():
        raise ValueError("incomplete root result owner")
    if not c.get("page_guard_admitted") or not isinstance(m.get("heap_supersections"), dict):
        raise ValueError("contract lacks shared C admission")


def _status(c: dict, name: str, value: int) -> str:
    values = {number: label for label, number in c[name].items()}
    if value not in values:
        raise ValueError("invalid " + name + " status")
    return values[value]


def _group(records: list[dict], c: dict, boot_groups: list[dict]) -> dict:
    partition = record_partition(records)
    if len(records) != len(FORMATS) or any(records[i]["text"] != text for i, text in enumerate(FORMATS) if text):
        raise ValueError("incomplete or unordered menu report")
    sequence, version = _values(records[0], 2)
    if not 1 <= sequence <= 8 or version != 1 or _values(records[-1], 1) != [sequence]:
        raise ValueError("sequence/version mismatch")
    token = SOURCE.fullmatch(records[1]["text"])
    source_numeric = records[1]["numeric"]
    if token is None or token[1] != c["source_id"] or source_numeric["status"] != "no_numeric_arguments" or source_numeric["arguments"]:
        raise ValueError("source token or literal argument mismatch")
    prep, heap, raw, table = _values(records[2], 4)
    request, prefix, vector_bytes, ready = _values(records[3], 4)
    map_count, map_a, map_b = _values(records[4], 3)
    generations, installs, install, delegate = _values(records[5], 4)
    current_getter, aggregate = _values(records[6], 2)
    root_prep, root_ready, result0, result1 = _values(records[7], 4)
    after0, after1, expected_root, reference = _values(records[8], 4)
    view, plan, drift, checksum = _values(records[9], 4)
    prep_name = _status(c, "prepare_codes", prep)
    install_name = _status(c, "install_codes", install)
    root_names = [_status(c, "root_codes", x) for x in (root_prep, result0, result1)]
    if ready not in (0, 1) or root_ready not in (0, 1) or plan not in (0, 1) or view > 2:
        raise ValueError("invalid publication/view flags")
    m, roots = c["menu"], c["root_codes"]
    if (request, vector_bytes) != (m["allocation_bytes"], m["vector_bytes"]):
        raise ValueError("allocation request or vector size differs from compiled owner")
    if expected_root != c["root_physical"]:
        raise ValueError("linked private root differs")
    root_prepared = root_prep == roots["ROOT_PREPARED"]
    if reference != (c["expected_page_checksum"] if root_prepared else 0):
        raise ValueError("root reference checksum contradicts preparation")
    if root_ready and not (root_prepared and result0 == roots["ROOT_SWITCHED"]):
        raise ValueError("root publication contradicts preparation")
    switched = []
    for result, after in ((result0, after0), (result1, after1)):
        success = result == roots["ROOT_SWITCHED"]
        if success and not (root_ready and after == expected_root):
            raise ValueError("switched root contradicts publication or readback")
        switched.append(success)
    if plan and not (view and root_ready):
        raise ValueError("private plan without a published admitted view")
    if checksum and not (view == 2 and plan):
        raise ValueError("page checksum without private-view admission")
    if drift != 0xFFFFFFFF and (not view or not root_ready or drift > 4096):
        raise ValueError("root drift without an admitted source-root observation")

    raw_valid = raw % 4 == 0 and m["heap_min_raw"] <= raw <= m["heap_max_raw"]
    early = prep_name in ("PREP_NONE", "PREP_BAD_CORE", "PREP_BAD_HEAP", "PREP_ALLOC_FAILED", "PREP_BAD_RANGE")
    if prep_name in ("PREP_NONE", "PREP_BAD_CORE"):
        if any((heap, raw)):
            raise ValueError("early preparation status contradicts untouched allocator state")
    elif prep_name == "PREP_BAD_HEAP":
        if heap == m["heap_base"] or raw:
            raise ValueError("heap rejection contradicts allocator admission")
    else:
        if heap != m["heap_base"]:
            raise ValueError("allocation without admitted heap identity")
        if prep_name == "PREP_ALLOC_FAILED":
            if raw:
                raise ValueError("allocation failure reports a nonnull return")
        elif not raw:
            raise ValueError("post-allocation status lacks retained raw return")
        elif prep_name == "PREP_BAD_RANGE":
            if raw_valid:
                raise ValueError("range rejection reports an admitted range")
        elif not raw_valid:
            raise ValueError("post-range status reports an unsupported allocation")
    first = raw >> 24
    last = (raw + request - 1) >> 24
    expected_count = (last - first + 1) * 16
    mapping_admitted = prep_name in ("PREP_READY", "PREP_BAD_TABLE")
    if map_count == 0:
        if map_a or map_b or mapping_admitted:
            raise ValueError("unread mapping state contradicts reported admission")
        mapping_words = None
    else:
        if early or not raw_valid or map_count != expected_count or map_count not in (16, 32) or not root_ready:
            raise ValueError("mapping observation does not cover the complete allocation groups")
        if map_count == 16 and map_b:
            raise ValueError("one-group observation reports an unwritten second group")
        mapping_words = [map_a] + ([map_b] if map_count == 32 else [])
        if mapping_admitted:
            wanted = [m["heap_supersections"].get(str(group)) for group in range(first, last + 1)]
            if None in wanted or mapping_words != wanted:
                raise ValueError("admitted mapping first words differ from source-exact identity supersections")
    if early and (table or prefix or ready or map_count):
        raise ValueError("early failure contradicts later preparation state")
    if prep_name == "PREP_READY":
        wanted_table = (raw + 31) & ~31
        if not (
            ready
            and table == wanted_table
            and prefix == table + m["table_reserved"]
            and prefix + m["prefix_reserved"] <= raw + request
            and vector_bytes + 4 <= m["prefix_reserved"]
            and root_ready
        ):
            raise ValueError("ready preparation has inconsistent retained layout or publication")
    elif table or prefix or ready:
        raise ValueError("unpublished preparation exposes retained table/prefix")
    if not early and prep_name != "PREP_BAD_MAPPING" and not mapping_admitted:
        raise ValueError("unknown preparation lifecycle")

    if installs > generations or (install_name != "INSTALL_OK" and installs >= generations and generations):
        raise ValueError("installation history contradicts generation count")
    if install_name == "INSTALL_NONE":
        if generations or installs or delegate:
            raise ValueError("unattempted installation has historical state")
    elif not generations:
        raise ValueError("installation result lacks a generation")
    if install_name == "INSTALL_UNPREPARED":
        if ready or installs or delegate:
            raise ValueError("unprepared installation contradicts preparation/history")
    elif install_name != "INSTALL_NONE" and not ready:
        raise ValueError("installation advanced before table publication")
    if install_name == "INSTALL_BAD_GETTER" and delegate == m["original_getter"]:
        raise ValueError("getter rejection reports the admitted default getter")
    if install_name in ("INSTALL_BAD_CALLBACK", "INSTALL_BAD_READBACK", "INSTALL_OK") and delegate != m["original_getter"]:
        raise ValueError("callback registration lacks the admitted original delegate")
    last_succeeded = install_name == "INSTALL_OK"
    if last_succeeded and not (ready and generations >= installs >= 1 and delegate == m["original_getter"]):
        raise ValueError("successful installation contradicts preparation or history")
    current_matches = current_getter == m["getter"] and aggregate == m["aggregate_root"]
    allocator_return_observed = prep_name in ("PREP_ALLOC_FAILED", "PREP_BAD_RANGE", "PREP_BAD_MAPPING", "PREP_BAD_TABLE", "PREP_READY")

    matching = [g for g in boot_groups if g["sequence"] == sequence and g.get("partition_index") == partition]
    boot = matching[0] if len(matching) == 1 else None
    problems = []
    startup = False
    late = False
    if boot is None:
        problems.append("missing or ambiguous same-source, same-sequence, same-partition R5M2 owner report")
    else:
        phases = boot["phases"]
        startup_owners = [
            _profile(phases[0], c, 0, boot=True, root=c["admission_profile"]["ttbr1"]),
            _profile(phases[2], c, 1, boot=True, root=c["admission_profile"]["ttbr1"]) and boot["secondary_slot_restore_issued"],
        ]
        startup = bool(all(startup_owners) and root_ready and all(switched))
        if not startup:
            problems.append("startup controls, secondary continuation or compact root results not admitted")
        late = bool(
            startup_owners[0]
            and switched[0]
            and root_ready
            and _profile(phases[1], c, 0, boot=False, root=expected_root)
            and view == 2
            and plan
            and drift == 0
            and checksum == reference == c["expected_page_checksum"]
        )
        if not late:
            problems.append("late CPU0 owner controls/view/plan/drift/checksum not admitted")
    return {
        "sequence": sequence,
        "partition_index": partition,
        "source_id": c["source_id"],
        "target_va": c["target_va"],
        "preparation": prep_name,
        "ready": bool(ready),
        "heap_raw": heap,
        "raw_allocation": raw,
        "raw_allocation_return_observed": allocator_return_observed,
        "retained_nonnull_allocation_return": bool(raw),
        "allocation_range_admitted": not early,
        "allocation_request_bytes": request,
        "allocation_layout": {"table": table, "prefix": prefix, "vector_bytes": vector_bytes} if ready else None,
        "mapping_entry_count": map_count,
        "mapping_first_words_raw": mapping_words,
        "heap_identity_mapping_admitted": mapping_admitted,
        "installation_result": install_name,
        "initialization_generations": generations,
        "successful_installations": installs,
        "historical_installation_reported": bool(installs),
        "delegate_raw": delegate,
        "current_getter_raw": current_getter,
        "current_getter_matches_mod": current_getter == m["getter"],
        "aggregate_root_raw": aggregate,
        "last_installation_succeeded": last_succeeded,
        "current_registration_matches_mod": current_matches,
        "current_menu_registration_observed": bool(ready and installs and current_matches),
        "native_vector_render_observed": False,
        "full_xpan_implemented": False,
        "root_preparation": root_names[0],
        "root_ready": bool(root_ready),
        "core_results": root_names[1:],
        "startup_roots_after_raw": [after0, after1],
        "root_reference_checksum": reference,
        "both_cpus_startup_mapping_observed": startup,
        "late_cpu0_mapping_observed": late,
        "late_root_kind": ("unknown", "stock", "private")[view],
        "private_plan_matches": True if plan else None,
        "private_plan_match_raw": plan,
        "original_root_changes": drift if drift != 0xFFFFFFFF else None,
        "late_page_checksum": checksum if view == 2 and plan else None,
        "boot_context_problems": problems,
        "boot_context": boot,
        "record_offsets": [r["offset"] for r in records],
    }


def decode_menu_records(parsed: dict, contract: dict) -> dict:
    _contract(contract)
    boot = decode_boot_mmu_records(parsed, contract["source_id"], protocol="R5M2")
    records = [r for r in parsed["records"] if r["text"].startswith("R5G1 ")]
    groups, rejected, pending, orphans = [], [], [], []
    for record in records:
        if record["text"] == FORMATS[0]:
            if pending:
                rejected.append({"offset": pending[0]["offset"], "reason": "incomplete report before next begin"})
            pending = [record]
        elif pending:
            pending.append(record)
            if record["text"] == FORMATS[-1]:
                try:
                    groups.append(_group(pending, contract, boot["groups"]))
                except ValueError as exc:
                    rejected.append({"offset": pending[0]["offset"], "reason": str(exc)})
                pending = []
        else:
            orphans.append(record["offset"])
    if pending:
        rejected.append({"offset": pending[0]["offset"], "reason": "incomplete report at retained capture end"})
    return {
        "protocol": "R5G1",
        "format": parsed["format"],
        "source_id": contract["source_id"],
        "complete_groups": len(groups),
        "groups": groups,
        "rejected_groups": rejected,
        "orphan_record_offsets": orphans,
        "boot_observations": boot,
    }
