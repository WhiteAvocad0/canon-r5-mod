"""Extend the existing selected framing contract with actual playback C/ELF facts."""
from pathlib import Path
import json
import subprocess

import make_crop_runtime_contract as native_owner
import make_disable_runtime_contract as base_owner
import playback_model


def generate(build: Path, *, rom_path: Path | None = None, toolchain_dir: Path | None = None, write: bool = True) -> dict:
    build = Path(build).resolve()
    path = build / "framing-runtime-contract.json"
    contract = base_owner.generate(build, rom_path=rom_path, toolchain_dir=toolchain_dir, write=False)
    model = playback_model.export_model(contract["source_id"])
    sources = playback_model.model_digests()

    b = json.loads((build / "build.json").read_text())
    native_path = Path(b["output_image"]) if "output_image" in b else (build / "autoexec.bin")
    if not native_path.is_file():
        native_path = build / "autoexec.bin"
    native = native_path.read_bytes()
    symbols = json.loads((build / "symbols.json").read_text())

    profiles = model["profiles"]
    expected = {
        "playback_box_profile": profiles["box"]["bytes"],
        "playback_native_profile": profiles["native"]["bytes"],
        "playback_native_chains": profiles["chains"]["bytes"],
        "playback_mask_profile": profiles["mask"]["bytes"],
        "playback_mask_containers": profiles["containers"]["bytes"],
        "ifd_memory_profile": profiles["ifd_memory"]["bytes"],
        "playback_native_tags": model["layouts"]["tag_owner_bytes"],
        "playback_mask_view": model["layouts"]["view_owner_bytes"],
        "playback_reject_profile": model["rejection"]["profile_bytes"],
        "playback_reject_records": model["rejection"]["records_bytes"],
        "playback_first_view_profile": model["first_view"]["profile_bytes"],
        "playback_first_view_records": model["first_view"]["records_bytes"],
    }

    tool = native_owner.resolve_nm_tool(b, toolchain_dir=toolchain_dir)
    records = {}
    for line in subprocess.check_output([str(tool), "-S", "--defined-only", str(build / "handoff.elf")], text=True).splitlines():
        row = line.split()
        if len(row) == 4 and row[3] in expected:
            address, size = int(row[0], 16), int(row[1], 16)
            if row[3] in records or address != symbols[row[3]] or size != expected[row[3]]:
                raise ValueError("playback linked symbol mismatch: " + row[3])
            records[row[3]] = {"address": address, "bytes": size, "kind": row[2]}
    if records.keys() != expected.keys():
        raise ValueError("playback linked inventory incomplete")

    for symbol, profile_hex in [
        ("playback_box_profile", profiles["box"]["hex"]),
        ("playback_native_profile", profiles["native"]["hex"]),
        ("playback_native_chains", profiles["chains"]["hex"]),
        ("playback_mask_profile", profiles["mask"]["hex"]),
        ("playback_reject_profile", model["rejection"]["profile_hex"]),
        ("playback_first_view_profile", model["first_view"]["profile_hex"]),
        ("ifd_memory_profile", profiles["ifd_memory"]["hex"]),
        ("playback_mask_containers", profiles["containers"]["hex"]),
    ]:
        record = records[symbol]
        address = record["address"]
        size = record["bytes"]
        offset = symbols["_resident_load_offset"] + address - symbols["_resident_start"]
        if not symbols["_resident_start"] <= address < address + size <= symbols["_resident_data_end"] or not 0 <= offset < offset + size <= len(native):
            raise ValueError("playback profile outside initialized artifact: " + symbol)
        if native[offset : offset + size] != bytes.fromhex(profile_hex):
            raise ValueError("linked playback profile differs from actual C owner: " + symbol)

    for symbol, alignment in [
        ("playback_native_tags", model["layouts"]["tag_record_alignment"]),
        ("playback_mask_view", model["layouts"]["view_owner_alignment"]),
        ("playback_reject_records", model["rejection"]["profile"]["record_alignment"]),
        ("playback_first_view_records", model["first_view"]["profile"]["record_alignment"]),
    ]:
        record = records[symbol]
        address = record["address"]
        size = record["bytes"]
        if (
            record["kind"].upper() != "B"
            or address % alignment
            or not 0x100000 <= symbols["_bss_start"] <= address < address + size <= symbols["_bss_objects_end"] <= 0x300000
        ):
            raise ValueError("playback private owner outside admitted aligned BSS: " + symbol)

    if model["slots"]["count"] != len(model["slots"]["objects"]) or model["slots"]["cache_index"] != model["slots"]["metadata_count"] or model["slots"]["count"] != model["slots"]["metadata_count"] + 1:
        raise ValueError("playback C slot ownership inconsistent")
    if profiles["native"]["values"]["chain_count"] != profiles["chains"]["count"]:
        raise ValueError("playback C chain ownership inconsistent")
    if profiles["mask"]["values"]["container_count"] != profiles["containers"]["count"]:
        raise ValueError("playback C container ownership inconsistent")

    for name, fmt in model["formats"].items():
        if native.count(fmt.encode("ascii") + b"\0") != 1:
            raise ValueError("linked playback format/source mismatch: " + name)
    for name, fmt in model["rejection"]["formats"].items():
        if native.count(fmt.encode("ascii") + b"\0") != 1:
            raise ValueError("linked rejection format/source mismatch: " + name)
    for name, fmt in model["first_view"]["formats"].items():
        if native.count(fmt.encode("ascii") + b"\0") != 1:
            raise ValueError("linked first_view format/source mismatch: " + name)

    contract.update(playback_model=model, playback_sources=sources, playback_linked_records=records)
    native_owner.verify_native_inputs(contract["capture_contract"]["native_inputs"])
    playback_model.verify_model_sources(sources)
    if write:
        path.write_text(json.dumps(contract, indent=2) + "\n")
    return contract
