"""Top Q extension of the selected XPan framing/runtime contract."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import tempfile

import make_crop_runtime_contract as native_owner
import make_playback_runtime_contract as playback_owner
import q_label_model


def generate(build: Path, *, rom_path: Path | None = None, toolchain_dir: Path | None = None, write: bool = True) -> dict:
    build = Path(build).resolve()
    path = build / "framing-runtime-contract.json"
    contract = playback_owner.generate(build, rom_path=rom_path, toolchain_dir=toolchain_dir, write=False)
    sources = q_label_model.model_digests()
    model = q_label_model.export_model()
    q_label_model.verify_model_sources(sources)

    b = json.loads((build / "build.json").read_text())
    native_path = Path(b["output_image"]) if "output_image" in b else (build / "autoexec.bin")
    if not native_path.is_file():
        native_path = build / "autoexec.bin"
    native = native_path.read_bytes()
    symbols = json.loads((build / "symbols.json").read_text())

    expected = {"q_label_profile": model["profile"]["bytes"], "q_label_text": model["text"]["bytes"]}
    tool = native_owner.resolve_nm_tool(b, toolchain_dir=toolchain_dir)
    records = {}
    for line in subprocess.check_output([str(tool), "-S", "--defined-only", str(build / "handoff.elf")], text=True).splitlines():
        row = line.split()
        if len(row) == 4 and row[3] in expected:
            address, size = int(row[0], 16), int(row[1], 16)
            if row[3] in records or address != symbols[row[3]] or size != expected[row[3]]:
                raise ValueError("Q linked symbol mismatch: " + row[3])
            records[row[3]] = {"address": address, "bytes": size, "kind": row[2]}
    if records.keys() != expected.keys():
        raise ValueError("Q linked inventory incomplete")

    for symbol, body in (("q_label_profile", model["profile"]["hex"]), ("q_label_text", model["text"]["hex"])):
        record = records[symbol]
        address = record["address"]
        size = record["bytes"]
        offset = symbols["_resident_load_offset"] + address - symbols["_resident_start"]
        if not symbols["_resident_start"] <= address < address + size <= symbols["_resident_data_end"] or not 0 <= offset < offset + size <= len(native):
            raise ValueError("Q record outside initialized artifact: " + symbol)
        if native[offset : offset + size] != bytes.fromhex(body):
            raise ValueError("linked Q record differs from actual C owner: " + symbol)
    if model["profile"]["values"]["text_bytes"] != model["text"]["bytes"]:
        raise ValueError("Q C text ownership inconsistent")

    contract.update(q_label_model=model, q_label_sources=sources, q_label_linked_records=records)
    native_owner.verify_native_inputs(contract["capture_contract"]["native_inputs"])
    q_label_model.verify_model_sources(sources)

    if write:
        with tempfile.NamedTemporaryFile(mode="w", dir=build, prefix=".framing-runtime-contract-", suffix=".new", delete=False) as out:
            temporary = Path(out.name)
            out.write(json.dumps(contract, indent=2) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    return contract


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("build", type=Path)
    p.add_argument("--rom", type=Path, default=None)
    p.add_argument("--toolchain", type=Path, default=None)
    a = p.parse_args()
    c = generate(a.build, rom_path=a.rom, toolchain_dir=a.toolchain)
    print(json.dumps({"native_sha256": c["native_sha256"], "source_id": c["source_id"], "q_label_records": c["q_label_linked_records"], "contract": str(a.build / "framing-runtime-contract.json"), "hardware_q_label_verified": False}, indent=2))
