"""Extend actual linked framing expectations with first-disable observations."""
from pathlib import Path
import json
import subprocess

import make_crop_runtime_contract as capture_owner
import make_framing_runtime_contract as base_owner
import disable_model


def generate(build: Path, *, rom_path: Path | None = None, toolchain_dir: Path | None = None, write: bool = True) -> dict:
    build = Path(build).resolve()
    contract = base_owner.generate(build, rom_path=rom_path, toolchain_dir=toolchain_dir, write=False)
    meta = disable_model.export_model(contract["source_id"])
    b = json.loads((build / "build.json").read_text())
    symbols = json.loads((build / "symbols.json").read_text())
    native_path = Path(b["output_image"]) if "output_image" in b else (build / "autoexec.bin")
    if not native_path.is_file():
        native_path = build / "autoexec.bin"
    native = native_path.read_bytes()

    expected = {
        "frame_disable_profile": len(bytes.fromhex(meta["profile_hex"])),
        "frame_disable_trace": meta["profile"]["record_bytes"],
    }
    records = {}
    tool = capture_owner.resolve_nm_tool(b, toolchain_dir=toolchain_dir)
    for line in subprocess.check_output([str(tool), "-S", "--defined-only", str(build / "handoff.elf")], text=True).splitlines():
        f = line.split()
        if len(f) == 4 and f[3] in expected:
            address, size = int(f[0], 16), int(f[1], 16)
            if address != symbols[f[3]] or size != expected[f[3]]:
                raise ValueError("First-disable linked record mismatch")
            records[f[3]] = {"address": address, "bytes": size, "kind": f[2]}
    if records.keys() != expected.keys():
        raise ValueError("First-disable linked record missing")

    profile = records["frame_disable_profile"]
    offset = symbols["_resident_load_offset"] + profile["address"] - symbols["_resident_start"]
    if native[offset : offset + profile["bytes"]] != bytes.fromhex(meta["profile_hex"]):
        raise ValueError("Linked first-disable profile differs from actual C owner")

    state = records["frame_disable_trace"]
    if (
        state["address"] % meta["profile"]["record_alignment"]
        or not symbols["_bss_start"] <= state["address"] < state["address"] + state["bytes"] <= symbols["_bss_objects_end"] <= 0x300000
    ):
        raise ValueError("First-disable record outside admitted BSS")

    for name, fmt in meta["formats"].items():
        if native.count(fmt.encode() + b"\0") != 1:
            raise ValueError("First-disable literal mismatch: " + name)

    contract.update(disable_model=meta, disable_sources=disable_model.digests(), disable_linked_records=records)
    if write:
        (build / "framing-runtime-contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    return contract
