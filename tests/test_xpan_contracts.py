"""Public synthetic tests for XPan immutable code-fingerprint ROM admission and ROM-derived helpers."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src" / "experimental" / "xpan" / "tools"))

from contracts import (
    MANIFEST,
    TARGET_CONTRACT,
    XPAN_TARGET_CONTRACT,
    admit_xpan_rom_with_provenance,
    validate_xpan_target_contract_consistency,
)
from test_contracts import make_synthetic_contract_and_rom


def _synthetic_framing_table() -> bytes:
    rows = []
    for idx in range(70):
        # 12 words per row; keep admitted row indices distinct with valid even centered projection
        h = 682 if idx < 12 else (1200 if idx < 34 else (480 if idx < 56 else 1080))
        w = 1024 if idx < 12 else (1600 if idx < 34 else (720 if idx < 56 else 1920))
        sub_h = 576 if idx % 2 else 542
        if idx == 12:
            sub_h = 898
        elif idx == 18:
            sub_h = 846
        elif idx == 23:
            sub_h = 792
        elif idx == 29:
            sub_h = 748
        row = [w, h, w, h, w, h, w - (idx % 5) * 8, sub_h, idx, 53 + idx, idx, 53 + idx]
        rows.append(struct.pack("<12I", *row))
    return b"".join(rows)


def _synthetic_menu_vector_resource() -> bytes:
    # Minimal valid OpenVG resource container matching vector_resource/menu_vector.decode()
    paths = []
    offsets = []
    cursor = (6 + 2 * 4 + 3) & ~3
    for i in range(4):
        # Provide enough segment/coord capacity matching stock budgets
        n_quads = 45 if i in (1, 3) else 12
        segs = bytes([2] + [4] * n_quads + [0])
        coords = [0, 0] + [10, 0] * n_quads
        c1_payload = struct.pack("<H", len(segs)) + segs
        c1_size = (len(c1_payload) + 7) & ~3
        c1 = struct.pack("<HH", 0xAAA0, c1_size) + c1_payload + bytes(c1_size - 4 - len(c1_payload))
        c2_payload = struct.pack("<H" + "h" * len(coords), len(coords), *coords)
        c2_size = (len(c2_payload) + 7) & ~3
        c2 = struct.pack("<HH", 0xAAA1, c2_size) + c2_payload + bytes(c2_size - 4 - len(c2_payload))
        rec = c1 + c2
        offsets.append((cursor - 6) // 2)
        paths.append(rec)
        cursor += len(rec)
    header = struct.pack("<HHH4H", 0xCC10, cursor, 4, *offsets)
    group = header + bytes((-len(header)) & 3) + b"".join(paths)

    prefix = bytearray(72)
    struct.pack_into("<HHII", prefix, 0, 0x9999, 12, 0, 6)
    struct.pack_into("<HH", prefix, 12, 0x1112, 60)
    struct.pack_into("<4h", prefix, 36, 0, 68, 0, 40)
    struct.pack_into("<H", prefix, 52, 4)

    frame_offset = len(prefix) + len(group)
    frame_chunk = struct.pack("<HH", 0x2220, 4)
    end_chunk = struct.pack("<HH", 0xFFFF, 4) + bytes(64)  # padding ensures rounded native reads stay within buffer
    end_chunk = struct.pack("<HH", 0x2220, 68) + bytes(64)
    final_chunk = struct.pack("<HH", 0xFFFF, 4)
    res = bytearray(bytes(prefix) + group + end_chunk + final_chunk)
    total = len(res)
    struct.pack_into("<I", res, 4, total)
    struct.pack_into("<I", res, 16, total)
    struct.pack_into("<I", res, 20, frame_offset - 12)
    return bytes(res)


def make_synthetic_xpan_contract_and_rom() -> tuple[dict, dict, dict, list[dict], bytearray]:
    """Create a synthetic 64 MiB ROM + XPan contract + hook specs matching all admission rules."""
    manifest, contract, rom = make_synthetic_contract_and_rom()
    xpan_contract = copy.deepcopy(XPAN_TARGET_CONTRACT)
    rom_base = manifest["rom_base"]

    tools_dir = ROOT / "src" / "experimental" / "xpan" / "tools"
    base_fc = json.loads((tools_dir / "feature-contract.json").read_text())
    hooks = list(base_fc["hooks"])
    for ext_name in base_fc["hook_extensions"]:
        ext = json.loads((tools_dir / ext_name).read_text())
        for h in ext["hooks"]:
            hooks.append(
                {
                    "va": hex(h["va"]),
                    "length": h["length"],
                    "destination": h["destination_symbol"],
                    "original_hex": h["original_bytes"],
                }
            )

    # Populate synthetic bytes in any extra XPan pages not overlapping main_signature_window
    main_start = manifest["main_start"]
    main_end = main_start + 4 * manifest["signature_words"]
    for idx, region in enumerate(xpan_contract["extra_code_fingerprints"]):
        addr = region["address"]
        r_len = region["bytes"]
        if main_start <= addr < main_end:
            continue
        r_off = addr - rom_base
        pattern = hashlib.sha256(f"xpan-page:{region['name']}:{idx}".encode()).digest()
        for pos in range(0, r_len, len(pattern)):
            chunk = pattern[: min(len(pattern), r_len - pos)]
            rom[r_off + pos : r_off + pos + len(chunk)] = chunk

    # Write framing_table and menu_vector_resource BEFORE computing extra_code_fingerprints
    ft = _synthetic_framing_table()
    ft_addr = xpan_contract["framing_table"]["address"]
    ft_off = ft_addr - rom_base
    rom[ft_off : ft_off + len(ft)] = ft
    xpan_contract["framing_table"]["bytes"] = len(ft)
    xpan_contract["framing_table"]["sha256"] = hashlib.sha256(ft).hexdigest()

    mv = _synthetic_menu_vector_resource()
    mv_addr = xpan_contract["menu_vector_resource"]["address"]
    mv_off = mv_addr - rom_base
    rom[mv_off : mv_off + len(mv)] = mv
    xpan_contract["menu_vector_resource"]["bytes"] = len(mv)
    xpan_contract["menu_vector_resource"]["sha256"] = hashlib.sha256(mv).hexdigest()

    # Write hook original bytes; only hooks outside main_signature_window or keep base hooks intact
    for h in hooks:
        h_addr = int(h["va"], 16)
        orig = bytes.fromhex(h["original_hex"])
        h_off = h_addr - rom_base
        rom[h_off : h_off + len(orig)] = orig

    # Re-fix main_signature_window word sum in case any hook landed inside E0100000..E0140000
    main_off = main_start - rom_base
    sig_end = main_off + 4 * manifest["signature_words"]
    partial_sum = sum(x[0] for x in struct.iter_unpack("<I", rom[main_off : sig_end - 4])) & 0xFFFFFFFF
    fix_word = (manifest["signature"] - partial_sum) & 0xFFFFFFFF
    struct.pack_into("<I", rom, sig_end - 4, fix_word)

    # Refresh base code_fingerprints and xpan extra_code_fingerprints / boot_tables
    for region in contract["code_fingerprints"]:
        r_off = region["address"] - rom_base
        region["sha256"] = hashlib.sha256(rom[r_off : r_off + region["bytes"]]).hexdigest()
    for region in xpan_contract["boot_tables"]:
        r_off = region["address"] - rom_base
        region["sha256"] = hashlib.sha256(rom[r_off : r_off + region["bytes"]]).hexdigest()
    for region in xpan_contract["extra_code_fingerprints"]:
        r_off = region["address"] - rom_base
        region["sha256"] = hashlib.sha256(rom[r_off : r_off + region["bytes"]]).hexdigest()

    return manifest, contract, xpan_contract, hooks, rom


class XPanContractsTests(unittest.TestCase):
    def test_real_xpan_contract_has_no_dump_lock_and_covers_all_hooks_and_resources(self):
        tools_dir = ROOT / "src" / "experimental" / "xpan" / "tools"
        for name in (
            "feature-contract.json",
            "frame-native-hooks.json",
            "playback-native-hooks.json",
            "playback-mask-hooks.json",
            "q-label-hooks.json",
        ):
            obj = json.loads((tools_dir / name).read_text())
            self.assertNotIn("rom_sha256", obj, name)
            self.assertNotIn("dump_sha256", obj, name)

        validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT)
        self.assertNotIn("dump_sha256", XPAN_TARGET_CONTRACT)
        self.assertNotIn("rom_sha256", XPAN_TARGET_CONTRACT)
        self.assertEqual(XPAN_TARGET_CONTRACT["admission_mode"], "immutable_code_fingerprints")
        self.assertEqual(len(XPAN_TARGET_CONTRACT["extra_code_fingerprints"]), 128)

        bad = copy.deepcopy(XPAN_TARGET_CONTRACT)
        bad["dump_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "dump_sha256"):
            validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, bad)

    def test_xpan_admission_accepts_per_camera_variance_and_rejects_code_or_table_mutation(self):
        manifest, contract, xpan_contract, hooks, rom = make_synthetic_xpan_contract_and_rom()
        startup_a, prov_a = admit_xpan_rom_with_provenance(
            bytes(rom), manifest=manifest, contract=contract, xpan_contract=xpan_contract, hooks=hooks
        )
        self.assertEqual(len(startup_a), manifest["startup_bytes"])
        self.assertEqual(prov_a["sha256"], hashlib.sha256(rom).hexdigest())

        # Per-camera NVRAM/settings difference outside fingerprinted pages (0xE0010000) must pass
        rom_b = bytearray(rom)
        rom_b[0x10000:0x10040] = b"CAMERA_B_CALIBRATION_OR_NVRAM_VARIANCE_" + bytes(25)
        startup_b, prov_b = admit_xpan_rom_with_provenance(
            bytes(rom_b), manifest=manifest, contract=contract, xpan_contract=xpan_contract, hooks=hooks
        )
        self.assertEqual(startup_b, startup_a)
        self.assertNotEqual(prov_b["sha256"], prov_a["sha256"])
        self.assertEqual(prov_b["sha256"], hashlib.sha256(rom_b).hexdigest())

        # Mutate framing table -> must fail
        bad_ft = bytearray(rom)
        bad_ft[xpan_contract["framing_table"]["address"] - manifest["rom_base"]] ^= 0x01
        with self.assertRaisesRegex(ValueError, "framing_table"):
            admit_xpan_rom_with_provenance(
                bytes(bad_ft), manifest=manifest, contract=contract, xpan_contract=xpan_contract, hooks=hooks
            )

        # Mutate menu vector resource -> must fail
        bad_mv = bytearray(rom)
        bad_mv[xpan_contract["menu_vector_resource"]["address"] - manifest["rom_base"]] ^= 0x01
        with self.assertRaisesRegex(ValueError, "menu_vector_resource"):
            admit_xpan_rom_with_provenance(
                bytes(bad_mv), manifest=manifest, contract=contract, xpan_contract=xpan_contract, hooks=hooks
            )

        # Mutate a hook window inside an extra page -> must fail
        hook_outside = next(h for h in hooks if int(h["va"], 16) >= 0xE0140000)
        bad_hook = bytearray(rom)
        bad_hook[int(hook_outside["va"], 16) - manifest["rom_base"]] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "xpan_page_|hook"):
            admit_xpan_rom_with_provenance(
                bytes(bad_hook), manifest=manifest, contract=contract, xpan_contract=xpan_contract, hooks=hooks
            )

    def test_rom_derived_frame_row_and_menu_vector_reject_unverified_and_generate_into_out(self):
        import frame_row_profile
        import menu_vector

        manifest, contract, xpan_contract, hooks, rom = make_synthetic_xpan_contract_and_rom()
        _, prov = admit_xpan_rom_with_provenance(
            bytes(rom), manifest=manifest, contract=contract, xpan_contract=xpan_contract, hooks=hooks
        )
        profile = frame_row_profile.build_profile_from_rom(
            bytes(rom),
            rom_sha256=prov["sha256"],
            expected_table_sha256=xpan_contract["framing_table"]["sha256"],
            caller_path="synthetic.bin",
        )
        self.assertEqual(len(profile["admitted"]), 12)
        self.assertEqual(profile["source"]["sha256"], prov["sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "frame-row"
            frame_row_profile.write_generated(out, profile)
            hdr = (out / "frame_row_constants.h").read_text()
            self.assertIn(f'XPAN_FRAME_ROW_SOURCE_SHA "{prov["sha256"]}"', hdr)
            self.assertIn("#define XPAN_FRAME_ROW_CASE_COUNT 12u", hdr)

        with self.assertRaisesRegex(ValueError, "framing table"):
            frame_row_profile.build_profile_from_rom(
                bytes(rom),
                rom_sha256=prov["sha256"],
                expected_table_sha256="0" * 64,
                caller_path="synthetic.bin",
            )

        res_bytes, _, proof = menu_vector.build_from_rom(
            bytes(rom),
            expected_resource_sha256=xpan_contract["menu_vector_resource"]["sha256"],
            resource_bytes=xpan_contract["menu_vector_resource"]["bytes"],
            linearize=True,
        )
        self.assertGreater(len(res_bytes), 32)
        self.assertEqual(proof["source_sha256"], xpan_contract["menu_vector_resource"]["sha256"])
        self.assertEqual(proof["variant"], "linear")


if __name__ == "__main__":
    unittest.main()
