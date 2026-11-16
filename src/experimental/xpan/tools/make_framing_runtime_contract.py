"""Bind the actual linked M8 profile/layout and real C decoder expectations."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

import make_crop_runtime_contract as capture_owner
import framing_model

SCHEMA = "canon-r5-lab.framing-runtime-contract.v1"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate(build: Path, *, rom_path: Path | None = None, toolchain_dir: Path | None = None, write: bool = True) -> dict:
    build = Path(build).resolve()
    capture = capture_owner.generate(build, rom_path=rom_path, toolchain_dir=toolchain_dir, write=write)
    b = json.loads((build / "build.json").read_text())
    symbols = json.loads((build / "symbols.json").read_text())
    native_path = Path(b["output_image"]) if "output_image" in b else (build / "autoexec.bin")
    if not native_path.is_file():
        native_path = build / "autoexec.bin"
    native = native_path.read_bytes()

    model = framing_model.export_model(capture["source_id"], include_dir=build)
    expected = {
        "frame_native_profile": model["layout"]["profile_bytes"],
        "frame_memory_profile": model["memory"]["profile_bytes"],
        "frame_native_tags": model["layout"]["tag_owner_bytes"],
        "export_refresh_control": model["refresh"]["control_bytes"],
    }
    tool = capture_owner.resolve_nm_tool(b, toolchain_dir=toolchain_dir)
    records = {}
    for line in subprocess.check_output([str(tool), "-S", "--defined-only", str(build / "handoff.elf")], text=True).splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[3] in expected:
            address, count = int(fields[0], 16), int(fields[1], 16)
            if fields[3] in records or address != symbols[fields[3]] or count != expected[fields[3]]:
                raise ValueError("actual framing symbol extent/identity differs: " + fields[3])
            records[fields[3]] = {"address": address, "bytes": count, "kind": fields[2]}
    if records.keys() != expected.keys():
        raise ValueError("framing linked record inventory incomplete")

    for name, profile_hex in [("frame_native_profile", model["profile_hex"]), ("frame_memory_profile", model["memory"]["profile_hex"])]:
        profile = records[name]
        offset = symbols["_resident_load_offset"] + profile["address"] - symbols["_resident_start"]
        if native[offset : offset + profile["bytes"]] != bytes.fromhex(profile_hex):
            raise ValueError("linked framing profile differs from actual C model owner: " + name)

    for name, alignment in [("frame_native_tags", 8), ("export_refresh_control", 4)]:
        record = records[name]
        if (
            record["address"] % alignment
            or not 0x100000 <= symbols["_bss_start"] <= record["address"] < record["address"] + record["bytes"] <= symbols["_bss_objects_end"] <= 0x300000
        ):
            raise ValueError("framing state is outside admitted aligned low BSS: " + name)

    for name, fmt in model["formats"].items():
        if native.count(fmt.encode("ascii") + b"\0") != 1:
            raise ValueError("linked F1 format differs from C owner: " + name)

    prefix = ("R5R1 S=" + capture["source_id"]).encode("ascii")
    if native.count(prefix) != 1:
        raise ValueError("linked refresh source marker is not unique")
    start = native.index(prefix)
    end = native.find(b"\0", start, start + 76)
    if end < 0:
        raise ValueError("linked refresh format exceeds record budget")

    sources = framing_model.model_digests(include_dir=build)
    contract = {
        "schema": SCHEMA,
        "source_id": capture["source_id"],
        "native_sha256": capture["native_sha256"],
        "native_bytes": len(native),
        "capture_contract": capture,
        "framing_model": model,
        "model_sources": sources,
        "linked_records": records,
        "refresh_format": native[start:end].decode("ascii"),
        "memory_admission": {
            "owner": "root_ram_supported/current_ram_supported/root_layout_supported",
            "ttbr0_section_words": [0x00114406, 0x00214406],
            "both_core_words_checked_before_mod": True,
            "frame_memory_profile": model["memory"],
            "irq_stack_source_page": 0xE0301000,
            "stack_authority": "Actual ASM-saved SP, one configured IRQ half or existing low-stack range, bounded source-frame slots and unchanged caller/token/tag guards; borrowed pointer follows retain DATA policy.",
            "scope": "Source-derived existing native guard plus actual ELF placement; target execution unverified.",
        },
        "hardware_framing_verified": False,
        "scope": "Host C/ELF binding for XPan source preview. Hardware verification requires explicit camera test.",
    }
    capture_owner.verify_native_inputs(capture["native_inputs"])
    framing_model.verify_model_sources(contract["model_sources"], include_dir=build)
    if write:
        (build / "framing-runtime-contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    return contract
