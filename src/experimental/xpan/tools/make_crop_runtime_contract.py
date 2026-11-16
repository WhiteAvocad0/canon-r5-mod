"""Export M7 expectations from a selected XPan native build and the actual C owners."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent
XPAN_RESIDENT = XPAN_SRC / "resident"
REPO_ROOT = XPAN_SRC.parents[2]
PUBLIC_TOOLS = REPO_ROOT / "tools"

if str(PUBLIC_TOOLS) not in sys.path:
    sys.path.insert(0, str(PUBLIC_TOOLS))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from contracts import admit_xpan_rom_with_provenance
from host_cc import find_host_cc
from toolchain import _resolve_tool

SCHEMA = "canon-r5-lab.crop-runtime-contract.v1"
PROFILE_NAMES = (
    "midr_mask", "midr_value", "sctlr", "actlr", "ttbr0", "ttbr1",
    "ttbcr", "dacr", "core_mask", "ttbr0_stride", "boot_cpsr_mask", "boot_cpsr_value",
)
MODEL_SOURCES = (
    "root_profile.c", "root_plan.h", "multi_plan.c", "multi_plan.h",
    "code_patch.c", "code_patch.h", "heap_span.c", "heap_span.h",
    "boot_policy.c", "boot_policy.h", "heap_native.h", "menu_table.h",
    "menu_native.h", "feature_pages.h", "jpeg_crop_trace.h", "jpeg_crop.h",
    "jpeg_crop.c", "geometry.h", "rect_crop.c", "rect_crop.h",
    "resident/boot_mmu.h", "tools/crop-model-export.c",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_nm_tool(build_json: dict, toolchain_dir: Path | None = None) -> Path:
    if toolchain_dir is not None:
        return _resolve_tool(Path(toolchain_dir), "arm-none-eabi-nm")
    exes = build_json.get("toolchain_admission", {}).get("executables", {})
    nm_path = exes.get("arm-none-eabi-nm", {}).get("path")
    if nm_path and Path(nm_path).is_file():
        return Path(nm_path)
    tc_dir = build_json.get("toolchain_admission", {}).get("resolved_path")
    if tc_dir:
        return _resolve_tool(Path(tc_dir), "arm-none-eabi-nm")
    raise FileNotFoundError("Cannot resolve arm-none-eabi-nm from build.json or --toolchain")


def verify_native_inputs(inputs: dict[str, str], *, repo_root: Path = REPO_ROOT) -> None:
    for name, digest in inputs.items():
        p = Path(name) if Path(name).is_absolute() else (repo_root / name)
        if sha(p.read_bytes()) != digest:
            raise ValueError("native input drift: " + name)


def generate(
    build: Path,
    *,
    rom_path: Path | None = None,
    toolchain_dir: Path | None = None,
    write: bool = True,
) -> dict:
    build = Path(build).resolve()
    b = json.loads((build / "build.json").read_text())
    sy = json.loads((build / "symbols.json").read_text())
    verify_native_inputs(b["inputs"])

    manifest_bytes = (build / "source-manifest.json").read_bytes()
    digest = sha(manifest_bytes)
    source_id = b["observer"]["source_id"]
    if digest != b["witness"]["source_manifest_sha256"] or digest[:16] != source_id:
        raise ValueError("source manifest/token mismatch")
    manifest = json.loads(manifest_bytes)
    if manifest != {"inputs": b["inputs"], "configuration": b["configuration"]}:
        raise ValueError("source manifest differs from native input/configuration owner")

    native_path = Path(b["output_image"]) if "output_image" in b else (build / "autoexec.bin")
    if not native_path.is_file():
        native_path = build / "autoexec.bin"
    native = native_path.read_bytes()
    if (len(native), sha(native)) != (b["format"]["bytes"], b["format"]["sha256"]):
        raise ValueError("native artifact drift")

    is_crop_only = b["configuration"]["profile"] == "crop-only"
    if not is_crop_only:
        raise ValueError("XPan crop contract exporter expects profile == 'crop-only'")
    for protocol in ("R5G1", "R5J1"):
        if native.count(f"{protocol} S={source_id}\0".encode()) != 1:
            raise ValueError("compiled source token mismatch: " + protocol)
    if b"R5A1" in native:
        raise ValueError("unexpected R5A1 audio token in crop-only native artifact")

    resolved_rom = Path(rom_path) if rom_path is not None else Path(b["caller_input_hashes"]["rom"]["resolved_path"])
    rom = resolved_rom.read_bytes()
    actual_rom_sha = sha(rom)
    if actual_rom_sha != b["caller_input_hashes"]["rom"]["sha256"]:
        raise ValueError("ROM digest disagrees with build.json provenance")
    admit_xpan_rom_with_provenance(rom)

    def offset(symbol: str) -> int:
        return sy["_resident_load_offset"] + sy[symbol] - sy["_resident_start"]

    def linked(symbol: str, count: int) -> bytes:
        start = offset(symbol)
        if start < 0 or count < 0 or start + count > len(native):
            raise ValueError("linked initialized symbol outside artifact: " + symbol)
        return native[start : start + count]

    cc = find_host_cc()
    with tempfile.TemporaryDirectory(prefix="r5-crop-model-") as tmp:
        exe = Path(tmp) / "export"
        subprocess.run(
            [
                cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-I" + str(XPAN_SRC),
                "-I" + str(XPAN_RESIDENT),
                "-I" + str(build),
                "-DXPAN_RECT_HOST=1",
                "-DR5_CROP_ONLY=1",
                *[str(XPAN_SRC / n) for n in ("root_profile.c", "multi_plan.c", "code_patch.c", "heap_span.c", "boot_policy.c", "rect_crop.c")],
                str(TOOLS_DIR / "crop-model-export.c"),
                "-o", str(exe),
            ],
            check=True,
            capture_output=True,
        )
        args = [
            str(resolved_rom),
            str(native_path),
            *[hex(offset(n)) for n in ("feature_targets", "feature_patches", "feature_source_checksums")],
            "0x0",
            *[hex(sy[n]) for n in ("private_root", "children", "_resident_start", "_resident_data_end", "_bss_objects_end")],
            hex(offset("feature_witnesses")),
        ]
        result = subprocess.run([str(exe), *args], capture_output=True)
        if result.returncode:
            raise ValueError(f"shared C crop model rejected linked/source data (stage {result.returncode})")
        c = json.loads(result.stdout)

    nm_tool = resolve_nm_tool(b, toolchain_dir=toolchain_dir)
    linked_records = {}
    expected_sizes = {
        "feature_targets": len(c["pages"]) * 8,
        "feature_patches": c["feature_patch_count"] * 28,
        "feature_source_checksums": len(c["pages"]) * 4,
        "feature_witnesses": len(c["witnesses"]) * 8,
        "root_target_profile": 48,
    }
    if "pcm_packed_roles" in sy:
        raise ValueError("unexpected pcm_packed_roles symbol in crop-only build")
    for line in subprocess.check_output([str(nm_tool), "-S", "--defined-only", str(build / "handoff.elf")], text=True).splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[3] in expected_sizes:
            address, size = int(fields[0], 16), int(fields[1], 16)
            if address != sy[fields[3]] or size != expected_sizes[fields[3]]:
                raise ValueError("generated count/symbol differs from actual linked record: " + fields[3])
            linked_records[fields[3]] = {"address": address, "bytes": size}
    if linked_records.keys() != expected_sizes.keys():
        raise ValueError("actual linked record inventory incomplete")

    profile = c.pop("profile_words")
    if linked("root_target_profile", 48) != struct.pack("<12I", *profile):
        raise ValueError("linked profile differs from actual C owner")
    boot_original = rom[0x100000:0x101000]
    if linked("boot_shadow_page", 4096) != boot_original:
        raise ValueError("linked boot page is not its source-virgin template")

    for page in c["pages"]:
        patched = bytes.fromhex(page.pop("patched_hex"))
        original = rom[page["va"] - 0xE0000000 : page["va"] - 0xE0000000 + 4096]
        if len(patched) != 4096:
            raise ValueError("C patched page extent differs")
        page.update(source_sha256=sha(original), patched_sha256=sha(patched))

    count = len(c["pages"])
    patch_count = c["feature_patch_count"]
    patches = []
    data = linked("feature_patches", patch_count * 28)
    for i in range(patch_count):
        va, destination, length = struct.unpack_from("<III", data, i * 28)
        patches.append({"va": va, "destination": destination, "length": length, "before_hex": data[i * 28 + 12 : i * 28 + 28].hex()})

    if c["roles"] != []:
        raise ValueError("expected zero roles in crop-only C model export")
    c.pop("roles")
    menu = c["menu"]
    vector = linked("menu_vector", sy["menu_vector_end"] - sy["menu_vector"])
    if len(vector) != menu["vector_bytes"]:
        raise ValueError("linked menu vector length differs")
    menu.update(getter=sy["menu_image_table_getter"] | 1, original_getter=0xE026AF47, aggregate_root=0xE3500000, vector_sha256=sha(vector))
    boot = next(p for p in c["pages"] if p["va"] == 0xE0100000)

    contract = {
        "schema": SCHEMA,
        "source_id": source_id,
        "native_sha256": sha(native),
        "native_bytes": len(native),
        "source_manifest_sha256": digest,
        "rom_sha256": actual_rom_sha,
        "admission_profile": dict(zip(PROFILE_NAMES, profile)),
        "target_va": boot["va"],
        "root_physical": sy["private_root"],
        "table_physical": sy["children"],
        "page_physical": boot["pa"],
        "expected_page_checksum": boot["checksum"],
        "page_guard_admitted": True,
        "virgin_boot_page_sha256": sha(boot_original),
        "feature_patches": patches,
        "feature_page_count": count,
        **c,
        "packed_roles": [],
        "linked_records": linked_records,
        "native_inputs": b["inputs"],
        "model_sources": {n: sha((XPAN_SRC / n).read_bytes()) for n in MODEL_SOURCES},
        "scope": "Host expectations from actual linked M7 records and shared C owners. Boot pages are patched by the runtime before first root publication. No native execution, rendering, or camera verification asserted.",
    }
    verify_native_inputs(b["inputs"])
    if write:
        (build / "crop-runtime-contract.json").write_text(json.dumps(contract, indent=2) + "\n")
    return contract


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("--rom", type=Path, default=None)
    parser.add_argument("--toolchain", type=Path, default=None)
    args = parser.parse_args()
    c = generate(args.build, rom_path=args.rom, toolchain_dir=args.toolchain)
    print(json.dumps({"source_id": c["source_id"], "native_sha256": c["native_sha256"], "pages": c["feature_page_count"], "contract": str(args.build / "crop-runtime-contract.json")}, indent=2))
