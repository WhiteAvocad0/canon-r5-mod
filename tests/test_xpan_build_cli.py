"""Public CLI, portability, provenance, and mock end-to-end tests for --profile xpan-crop."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import build as build_mod
from test_xpan_contracts import make_synthetic_xpan_contract_and_rom


def write_xpan_mock_toolchain(bin_dir: Path, manifest: dict) -> None:
    """Create a portable mock arm-none-eabi toolchain supporting --profile xpan-crop."""
    driver = bin_dir / "_mock_xpan_driver.py"
    driver.write_text(
        f"""#!{sys.executable}
import json
from pathlib import Path
import struct
import sys

MANIFEST = json.loads({json.dumps(manifest)!r})
tool = Path(sys.argv[0]).name
args = sys.argv[1:]

if args == ["-dumpmachine"]:
    print("arm-none-eabi")
    sys.exit(0)
if args == ["-dumpfullversion"]:
    print("15.2.1")
    sys.exit(0)
if "--version" in args:
    print(f"{{tool}} (mock GNU Binutils/GCC) 15.2.1")
    sys.exit(0)

out_dir = Path.cwd()

def compute_symbols():
    res_start = MANIFEST["resident_base"]
    copied_startup = res_start + 0x100
    witness_body = copied_startup + MANIFEST["startup_bytes"] + 0x20
    properties = (witness_body + 512 + 15) & ~15
    obs = MANIFEST["observer"]
    observer_api = properties + 4 * len(obs["properties"])
    sound_worker_api = observer_api + 20
    sound_boot_api = sound_worker_api + 24

    code_base = sound_boot_api + 16
    syms = {{
        "_resident_start": res_start,
        "copied_startup": copied_startup,
        "witness_body": witness_body,
        "properties": properties,
        "observer_api": observer_api,
        "sound_worker_api": sound_worker_api,
        "sound_boot_api": sound_boot_api,
    }}
    code_names = [
        "camif_fixup", "canon_init_hook",
        "observer_read_counter", "sound_boot_report",
        "sound_boot_read_hw_context", "sound_worker_start", "sound_worker_entry",
        "sound_worker_poll_once", "sound_worker_set_enabled", "sound_worker_is_enabled",
        "sound_boot_record_boot_return", "sound_boot_record_startup_outcome",
        "sound_boot_context_is_ready", "sound_boot_start_worker",
        "root_primary_prepare", "root_secondary_take", "root_peer_check",
        "menu_prepare", "menu_install", "menu_image_table_getter",
        "frame_native_admit", "playback_mask_fit_begin", "q_label_entry",
        "xpan_review_authority_entry", "xpan_review_authority_observe", "xpan_review_outline_entry",
        "xpan_review_outline_decide", "xpan_review_gate_crop", "review_crop_entry", "review_crop_original",
        "review_crop_native_post", "review_crop_apply",
    ]
    for p in MANIFEST["patches"]:
        if p["symbol"] not in code_names:
            code_names.append(p["symbol"])
    for idx, name in enumerate(code_names):
        syms[name] = code_base + idx * 16
    syms["sound_boot_state"] = code_base + len(code_names) * 16

    root_addr = (syms["sound_boot_state"] + 16383) & ~16383
    boot_shadow = root_addr + 16384
    res_data_end = boot_shadow + 4096
    bss_start = (res_data_end + 15) & ~15
    sound_worker_state = bss_start
    bss_objects_end = (bss_start + 512 + 31) & ~31
    secondary_stack_top = bss_objects_end + 1024
    priv_stack_top = secondary_stack_top + MANIFEST["private_stack_bytes"]
    reserved_end = priv_stack_top
    load_offset = 0x40
    img_bytes = (load_offset + (res_data_end - res_start) + 8 + 31) & ~31
    syms.update({{
        "private_root": root_addr,
        "children": root_addr + 0x100,
        "boot_shadow_page": boot_shadow,
        "boot_shadow_page_end": boot_shadow + 4096,
        "_resident_data_end": res_data_end,
        "_bss_start": bss_start,
        "sound_worker_state": sound_worker_state,
        "_bss_objects_end": bss_objects_end,
        "_secondary_stack_top": secondary_stack_top,
        "_private_stack_top": priv_stack_top,
        "_reserved_end": reserved_end,
        "_resident_load_offset": load_offset,
        "_image_bytes": img_bytes,
    }})
    return syms

if tool == "arm-none-eabi-gcc":
    out_obj = Path(args[args.index("-o") + 1])
    out_obj.write_bytes(b"ELF_OBJ_MOCK")
elif tool == "arm-none-eabi-ld":
    out_elf = Path(args[args.index("-o") + 1])
    map_arg = next(a for a in args if a.startswith("-Map="))
    Path(map_arg.split("=", 1)[1]).write_text("mock memory map\\n")
    out_elf.write_bytes(b"ELF_MOCK")
elif tool == "arm-none-eabi-nm":
    if "-u" in args:
        sys.exit(0)
    for k, v in compute_symbols().items():
        print(f"{{v:08x}} T {{k}}")
elif tool == "arm-none-eabi-readelf":
    print("There are no relocations in this file.")
elif tool == "arm-none-eabi-objdump":
    if "-b" in args and "binary" in args:
        syms = compute_symbols()
        for p in MANIFEST["patches"]:
            addr = syms["copied_startup"] + p["offset"]
            target = syms[p["symbol"]] & ~1
            mnem = "bl" if p["link"] else "b.w"
            print(f" {{addr:x}}:\\tf000 d000 \\t{{mnem}}\\t0x{{target:x}}")
    else:
        print("Disassembly of section .text:")
elif tool == "arm-none-eabi-objcopy":
    syms = compute_symbols()
    out_bin = Path(args[-1])
    buf = bytearray(syms["_image_bytes"])
    buf[0:4] = bytes.fromhex("00bf01e0")
    buf[7] = 0xEA
    loc = lambda name: syms["_resident_load_offset"] + syms[name] - syms["_resident_start"]

    obs = MANIFEST["observer"]
    sw = MANIFEST["shutter_worker"]
    props = struct.pack(f"<{{len(obs['properties'])}}I", *obs["properties"])
    buf[loc("properties") : loc("properties") + len(props)] = props
    obs_api = struct.pack(
        "<5I",
        obs["register_slave"], obs["ack"], obs["debug"],
        syms["observer_read_counter"] | 1, syms["sound_boot_report"] | 1,
    )
    buf[loc("observer_api") : loc("observer_api") + len(obs_api)] = obs_api
    sw_api = struct.pack(
        "<6I",
        sw["create_task"], sw["start_task"], sw["delete_task"],
        syms["observer_read_counter"] | 1, 0, sw["sleep_ms"],
    )
    buf[loc("sound_worker_api") : loc("sound_worker_api") + len(sw_api)] = sw_api
    sb_api = struct.pack(
        "<2I",
        syms["sound_boot_read_hw_context"] | 1, syms["sound_worker_start"] | 1,
    )
    buf[loc("sound_boot_api") : loc("sound_boot_api") + len(sb_api)] = sb_api
    struct.pack_into("<II", buf, len(buf) - 8, 0xE12FFF13, 0xCCCCCCCC)
    out_bin.write_bytes(bytes(buf))
"""
    )
    driver.chmod(0o755)
    for exe in (
        "arm-none-eabi-gcc",
        "arm-none-eabi-ld",
        "arm-none-eabi-nm",
        "arm-none-eabi-readelf",
        "arm-none-eabi-objcopy",
        "arm-none-eabi-objdump",
    ):
        link = bin_dir / exe
        if not link.exists():
            link.symlink_to(driver.name)


class XPanBuildCLITests(unittest.TestCase):
    def test_xpan_crop_cli_forbids_wavs_and_default_requires_wavs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Default profile still requires --startup-wav and --shutter-wav
            proc_default = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "build.py"),
                    "--rom", "r.bin",
                    "--toolchain", "t",
                    "--output", str(base / "out_default"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_default.returncode, 2)
            self.assertIn("--startup-wav", proc_default.stderr)
            self.assertFalse((base / "out_default").exists())

            # --profile xpan-crop forbids --startup-wav or --shutter-wav before creating output
            for flag in ("--startup-wav", "--shutter-wav"):
                out = base / f"out_{flag.strip('-')}"
                proc_bad = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools" / "build.py"),
                        "--profile", "xpan-crop",
                        "--rom", "r.bin",
                        flag, "a.wav",
                        "--toolchain", "t",
                        "--output", str(out),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc_bad.returncode, 2)
                self.assertFalse(out.exists())

            # --force and --skip-target-check rejected
            for forbidden in ("--force", "--skip-target-check"):
                proc_forb = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools" / "build.py"),
                        "--profile", "xpan-crop",
                        "--rom", "r.bin",
                        "--toolchain", "t",
                        "--output", str(base / "out_forb"),
                        forbidden,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc_forb.returncode, 2)

    def test_xpan_crop_end_to_end_synthetic_build_no_wav_and_actual_rom_provenance(self):
        manifest, contract, xpan_contract, hooks, rom = make_synthetic_xpan_contract_and_rom()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            rom_path = root / "camera_b_rom.bin"
            # Introduce per-camera variance outside fingerprinted regions
            rom[0x10000:0x10020] = b"CAMERA_B_SYNTHETIC_NVRAM_BYTES__"
            rom_bytes = bytes(rom)
            rom_path.write_bytes(rom_bytes)

            tc_dir = root / "toolchain" / "bin"
            tc_dir.mkdir(parents=True)
            write_xpan_mock_toolchain(tc_dir, manifest)

            out_dir = root / "outside-xpan-out"
            prev_cwd = Path.cwd()
            try:
                os.chdir(root)
                record = build_mod.build_image(
                    rom_path=Path("camera_b_rom.bin"),
                    toolchain_dir=Path("toolchain/bin"),
                    output_dir=Path("outside-xpan-out"),
                    profile="xpan-crop",
                    manifest=manifest,
                    target_contract=contract,
                    xpan_contract=xpan_contract,
                    xpan_hooks=hooks,
                )
            finally:
                os.chdir(prev_cwd)

            self.assertEqual(record["profile"], "crop-only")
            self.assertTrue(record["host_build_ok"])
            self.assertFalse(record["runtime_executed"])
            self.assertFalse(record["camera_or_card_actions"])
            self.assertIsNone(record["audio"])
            self.assertIsNone(record["shutter_audio"])
            self.assertIsNone(record["configuration"]["packed_audio"])
            self.assertFalse(record["configuration"]["shutter_feedback"])
            self.assertEqual(record["shutter_feedback"]["sound_worker_api_pointers"]["play_shutter"], 0)

            for fname in (
                "autoexec.bin",
                "handoff.elf",
                "handoff.map",
                "symbols.json",
                "build.json",
                "source-manifest.json",
                "witness-template.bin",
                "expected-R5BOOT.TXT",
                "target-page.bin",
                "menu-vector.bin",
                "feature-targets.h",
                "feature-config.h",
                "frame-row/frame_row_constants.h",
            ):
                self.assertTrue((out_dir / fname).is_file(), fname)

            for forbidden in ("audio.pcm", "shutter.pcm", "audio.deflate", "shutter.deflate"):
                self.assertFalse((out_dir / forbidden).exists(), forbidden)

            actual_rom_sha = hashlib.sha256(rom_bytes).hexdigest()
            self.assertEqual(record["caller_input_hashes"]["rom"]["sha256"], actual_rom_sha)
            self.assertIn(actual_rom_sha.encode(), (out_dir / "witness-template.bin").read_bytes())
            self.assertEqual(rom_path.read_bytes(), rom_bytes)

            # Inputs must be repo-relative POSIX paths with zero private lab absolute paths
            manifest_obj = json.loads((out_dir / "source-manifest.json").read_text())
            for k in manifest_obj["inputs"]:
                self.assertFalse(Path(k).is_absolute(), k)
                self.assertNotIn("canon-r5-lab", k)


if __name__ == "__main__":
    unittest.main()
