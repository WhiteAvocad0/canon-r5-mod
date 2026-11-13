"""Public CLI, CWD portability, provenance, mutation guard, and mock end-to-end builder tests."""
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
from test_audio_profile import build_wav_bytes
from test_contracts import make_synthetic_contract_and_rom


def write_e2e_mock_toolchain(bin_dir: Path, manifest: dict) -> None:
    """Create a portable Python-based mock arm-none-eabi toolchain supporting end-to-end build_image."""
    driver = bin_dir / "_mock_driver.py"
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
startup_pcm = (out_dir / "audio.pcm").read_bytes() if (out_dir / "audio.pcm").exists() else b""
shutter_pcm = (out_dir / "shutter.pcm").read_bytes() if (out_dir / "shutter.pcm").exists() else b""

def compute_symbols():
    res_start = MANIFEST["resident_base"]
    copied_startup = res_start + 0x100
    witness_body = copied_startup + MANIFEST["startup_bytes"] + 0x20
    audio_descriptor = (witness_body + 512 + 31) & ~31
    audio_pcm = audio_descriptor + 64
    audio_pcm_end = audio_pcm + len(startup_pcm)
    shutter_descriptor = (audio_pcm_end + 31) & ~31
    shutter_pcm_addr = shutter_descriptor + 64
    shutter_pcm_end = shutter_pcm_addr + len(shutter_pcm)
    properties = (shutter_pcm_end + 15) & ~15
    obs = MANIFEST["observer"]
    observer_api = properties + 4 * len(obs["properties"])
    sound_worker_api = observer_api + 20
    sound_boot_api = sound_worker_api + 24

    code_base = sound_boot_api + 16
    syms = {{
        "_resident_start": res_start,
        "copied_startup": copied_startup,
        "witness_body": witness_body,
        "audio_descriptor": audio_descriptor,
        "audio_pcm": audio_pcm,
        "audio_pcm_end": audio_pcm_end,
        "shutter_descriptor": shutter_descriptor,
        "shutter_pcm": shutter_pcm_addr,
        "shutter_pcm_end": shutter_pcm_end,
        "properties": properties,
        "observer_api": observer_api,
        "sound_worker_api": sound_worker_api,
        "sound_boot_api": sound_boot_api,
    }}
    code_names = [
        "camif_fixup", "canon_init_hook", "audio_boot_once",
        "observer_read_counter", "sound_boot_report", "sound_worker_play_shutter",
        "sound_boot_read_hw_context", "sound_worker_start", "sound_worker_entry",
        "sound_worker_poll_once", "sound_worker_set_enabled", "sound_worker_is_enabled",
        "sound_boot_record_boot_return", "sound_boot_record_startup_outcome",
        "sound_boot_context_is_ready", "sound_boot_start_worker",
    ]
    for p in MANIFEST["patches"]:
        if p["symbol"] not in code_names:
            code_names.append(p["symbol"])
    for idx, name in enumerate(code_names):
        syms[name] = code_base + idx * 16
    syms["sound_boot_state"] = code_base + len(code_names) * 16

    res_data_end = syms["sound_boot_state"] + 64
    bss_start = (res_data_end + 15) & ~15
    sound_worker_state = bss_start
    priv_stack_top = (bss_start + MANIFEST["private_stack_bytes"] + 128 + 15) & ~15
    reserved_end = priv_stack_top
    load_offset = 0x40
    img_bytes = (load_offset + (res_data_end - res_start) + 8 + 31) & ~31
    syms.update({{
        "_resident_data_end": res_data_end,
        "_bss_start": bss_start,
        "sound_worker_state": sound_worker_state,
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
    if (out_dir / "audio-descriptor.bin").exists():
        ad = (out_dir / "audio-descriptor.bin").read_bytes()
        buf[loc("audio_descriptor") : loc("audio_descriptor") + len(ad)] = ad
    buf[loc("audio_pcm") : loc("audio_pcm") + len(startup_pcm)] = startup_pcm
    if (out_dir / "shutter-descriptor.bin").exists():
        sd = (out_dir / "shutter-descriptor.bin").read_bytes()
        buf[loc("shutter_descriptor") : loc("shutter_descriptor") + len(sd)] = sd
    buf[loc("shutter_pcm") : loc("shutter_pcm") + len(shutter_pcm)] = shutter_pcm

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
        syms["observer_read_counter"] | 1, syms["sound_worker_play_shutter"] | 1, sw["sleep_ms"],
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


class BuildCLITests(unittest.TestCase):
    def test_cli_requires_explicit_flags_and_rejects_force_or_skip_flags(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build.py")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--rom", proc.stderr)
        self.assertIn("--startup-wav", proc.stderr)
        self.assertIn("--shutter-wav", proc.stderr)
        self.assertIn("--toolchain", proc.stderr)
        self.assertIn("--output", proc.stderr)

        for forbidden_flag in ("--force", "--skip-target-check"):
            bad = subprocess.run(
                [
                    sys.executable, str(ROOT / "tools" / "build.py"),
                    "--rom", "r.bin", "--startup-wav", "a.wav", "--shutter-wav", "b.wav",
                    "--toolchain", "t", "--output", "o", forbidden_flag,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(bad.returncode, 2)
            self.assertIn("unrecognized arguments", bad.stderr)

    def test_rejects_output_inside_source_tree_or_existing_directory(self):
        with self.assertRaisesRegex(ValueError, "repository root"):
            build_mod._validate_output_dir(ROOT)
        with self.assertRaisesRegex(ValueError, "source directory"):
            build_mod._validate_output_dir(ROOT / "src" / "accidental")

        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "already_exists"
            existing.mkdir()
            with self.assertRaises(FileExistsError):
                build_mod._validate_output_dir(existing)

    def test_end_to_end_synthetic_build_provenance_and_cwd_portability(self):
        manifest, contract, rom = make_synthetic_contract_and_rom()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            rom_path = root / "camera_dump.bin"
            rom_bytes = bytes(rom)
            rom_path.write_bytes(rom_bytes)

            startup_dir = root / "startup_assets"
            startup_dir.mkdir()
            startup_wav = startup_dir / "startup.wav"
            startup_wav.write_bytes(build_wav_bytes(struct.pack("<17h", *range(17))))
            (startup_dir / "conversion.json").write_text('{"tool":"synthetic"}\n')
            (startup_dir / "pcm-facts.json").write_text('{"role":"startup"}\n')

            shutter_dir = root / "shutter_assets"
            shutter_dir.mkdir()
            shutter_wav = shutter_dir / "shutter.wav"
            shutter_wav.write_bytes(build_wav_bytes(struct.pack("<16h", *range(16))))

            tc_dir = root / "toolchain" / "bin"
            tc_dir.mkdir(parents=True)
            write_e2e_mock_toolchain(tc_dir, manifest)

            out_dir = root / "outside-build-out"
            prev_cwd = Path.cwd()
            try:
                os.chdir(root)
                record = build_mod.build_image(
                    rom_path=Path("camera_dump.bin"),
                    startup_wav=Path("startup_assets/startup.wav"),
                    shutter_wav=Path("shutter_assets/shutter.wav"),
                    toolchain_dir=Path("toolchain/bin"),
                    output_dir=Path("outside-build-out"),
                    manifest=manifest,
                    target_contract=contract,
                )
            finally:
                os.chdir(prev_cwd)

            self.assertTrue(record["host_build_ok"])
            self.assertFalse(record["runtime_executed"])
            self.assertFalse(record["camera_or_card_actions"])
            self.assertTrue((out_dir / "autoexec.bin").is_file())
            self.assertTrue((out_dir / "handoff.elf").is_file())
            self.assertTrue((out_dir / "handoff.map").is_file())
            self.assertTrue((out_dir / "symbols.json").is_file())
            self.assertTrue((out_dir / "disassembly.txt").is_file())
            self.assertTrue((out_dir / "startup-disassembly.txt").is_file())
            self.assertTrue((out_dir / "build.json").is_file())
            self.assertTrue((out_dir / "source-manifest.json").is_file())

            actual_rom_sha = hashlib.sha256(rom_bytes).hexdigest()
            self.assertEqual(record["caller_input_hashes"]["rom"]["sha256"], actual_rom_sha)
            self.assertEqual(record["caller_input_hashes"]["rom"]["caller_input_path"], "camera_dump.bin")
            self.assertEqual(record["caller_input_hashes"]["startup_wav"]["padding_bytes"], 30)
            self.assertEqual(record["caller_input_hashes"]["shutter_wav"]["padding_bytes"], 0)
            self.assertIn(actual_rom_sha.encode(), (out_dir / "witness-template.bin").read_bytes())

            self.assertEqual(sorted(record["audio"]["asset_provenance"].keys()), ["conversion.json", "pcm-facts.json"])
            self.assertEqual(record["shutter_audio"]["asset_provenance"], {})
            manifest_inputs = json.loads((out_dir / "source-manifest.json").read_text())["inputs"]
            self.assertIn(str((startup_dir / "conversion.json").resolve()), manifest_inputs)
            self.assertNotIn("conversion.json", record["shutter_audio"]["asset_provenance"])

            self.assertEqual(rom_path.read_bytes(), rom_bytes)


if __name__ == "__main__":
    unittest.main()
