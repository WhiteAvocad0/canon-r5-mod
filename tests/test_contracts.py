"""Public synthetic tests for Canon EOS R5 2.2.1 immutable code-fingerprint ROM admission."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from contracts import (
    MANIFEST,
    TARGET_CONTRACT,
    admit_rom,
    admit_rom_with_provenance,
    deployment,
    patch_startup,
    reservation,
    thumb_branch,
    validate_layout,
    validate_target_contract_consistency,
    validate_write_buffer,
)


def make_synthetic_contract_and_rom() -> tuple[dict, dict, bytearray]:
    """Create a synthetic 64 MiB ROM matching a synthetic test contract derived from MANIFEST/TARGET_CONTRACT."""
    manifest = copy.deepcopy(MANIFEST)
    contract = copy.deepcopy(TARGET_CONTRACT)

    rom = bytearray(manifest["dump_bytes"])
    rom_base = manifest["rom_base"]
    main_off = manifest["main_start"] - rom_base

    # Populate distinct synthetic bytes in all fingerprinted code windows first.
    for idx, region in enumerate(contract["code_fingerprints"]):
        r_off = region["address"] - rom_base
        r_len = region["bytes"]
        pattern = hashlib.sha256(f"region:{region['name']}:{idx}".encode()).digest()
        for pos in range(0, r_len, len(pattern)):
            chunk = pattern[: min(len(pattern), r_len - pos)]
            rom[r_off + pos : r_off + pos + len(chunk)] = chunk

    # Install exact expected startup patch instructions inside main_signature_window (startup_4k).
    for patch in manifest["patches"]:
        p_off = main_off + patch["offset"]
        rom[p_off : p_off + 4] = bytes.fromhex(patch["expected"])

    # Adjust the final 4-byte word of main_signature_window so its 32-bit word sum equals manifest["signature"].
    sig_end = main_off + 4 * manifest["signature_words"]
    partial_sum = sum(x[0] for x in struct.iter_unpack("<I", rom[main_off : sig_end - 4])) & 0xFFFFFFFF
    fix_word = (manifest["signature"] - partial_sum) & 0xFFFFFFFF
    struct.pack_into("<I", rom, sig_end - 4, fix_word)

    # Recompute SHA-256 digests for the synthetic test contract (main_signature_window now includes patches + fix_word).
    for region in contract["code_fingerprints"]:
        r_off = region["address"] - rom_base
        r_len = region["bytes"]
        region["sha256"] = hashlib.sha256(rom[r_off : r_off + r_len]).hexdigest()

    return manifest, contract, rom


class ContractsTests(unittest.TestCase):
    def test_real_public_contract_consistency_and_no_dump_lock(self):
        validate_target_contract_consistency(MANIFEST, TARGET_CONTRACT)
        self.assertNotIn("dump_sha256", MANIFEST)
        self.assertNotIn("dump_sha256", TARGET_CONTRACT)
        self.assertEqual(TARGET_CONTRACT["admission_mode"], "immutable_code_fingerprints")
        self.assertFalse(deployment()["ready"])

        # Reject reintroducing single-camera full-ROM sha lock
        bad_manifest = copy.deepcopy(MANIFEST)
        bad_manifest["dump_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "dump_sha256"):
            validate_target_contract_consistency(bad_manifest, TARGET_CONTRACT)

        # Reject uncovering any bound API
        bad_contract = copy.deepcopy(TARGET_CONTRACT)
        bad_contract["code_fingerprints"] = [
            entry for entry in bad_contract["code_fingerprints"] if entry["name"] != "audio_beep_start"
        ]
        with self.assertRaisesRegex(ValueError, "audio.beep_start"):
            validate_target_contract_consistency(MANIFEST, bad_contract)

        # Reject binding disagreement between firmware.json and target_contract.json
        mismatch = copy.deepcopy(MANIFEST)
        mismatch["shutter_worker"]["create_task"] ^= 2
        with self.assertRaisesRegex(ValueError, "shutter_worker.create_task"):
            validate_target_contract_consistency(mismatch, TARGET_CONTRACT)

    def test_real_contract_rejects_bad_zero_wrong_firmware_and_r6_sized_inputs(self):
        with self.assertRaises(TypeError):
            admit_rom("not-bytes")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "wrong/truncated"):
            admit_rom(b"\x00" * 1024)

        zero_rom = bytes(MANIFEST["dump_bytes"])
        with self.assertRaisesRegex(ValueError, "signature mismatch"):
            admit_rom(zero_rom)

        # Synthesize wrong R5 firmware / R6 dump that matches signature sum at E0100000..E0140000
        # but fails code fingerprint verification.
        wrong_fw = bytearray(MANIFEST["dump_bytes"])
        main_off = MANIFEST["main_start"] - MANIFEST["rom_base"]
        sig_end = main_off + 4 * MANIFEST["signature_words"]
        struct.pack_into("<I", wrong_fw, sig_end - 4, MANIFEST["signature"])
        with self.assertRaisesRegex(ValueError, "code fingerprint mismatch"):
            admit_rom(bytes(wrong_fw))

    def test_non_fingerprinted_camera_bytes_may_differ_and_target_code_mutation_rejected(self):
        manifest, contract, rom = make_synthetic_contract_and_rom()
        startup_a, prov_a = admit_rom_with_provenance(bytes(rom), manifest=manifest, contract=contract)
        expected_sha_a = hashlib.sha256(rom).hexdigest()
        self.assertEqual(len(startup_a), manifest["startup_bytes"])
        self.assertEqual(prov_a["sha256"], expected_sha_a)
        self.assertEqual(prov_a["verified_code_regions"], len(contract["code_fingerprints"]))

        # Simulate per-camera NVRAM/settings/BootDisk differences outside fingerprinted code regions
        # (e.g. 0xE0010000..0xE0020000 in early flash before main_start=0xE0100000).
        rom_camera_b = bytearray(rom)
        rom_camera_b[0x10000:0x10040] = b"CAMERA_B_NVRAM_SETTINGS_DIFFERENCE_" + bytes(29)
        startup_b, prov_b = admit_rom_with_provenance(bytes(rom_camera_b), manifest=manifest, contract=contract)
        self.assertEqual(startup_b, startup_a)
        self.assertNotEqual(prov_b["sha256"], prov_a["sha256"])
        self.assertEqual(prov_b["sha256"], hashlib.sha256(rom_camera_b).hexdigest())

        # Mutate one byte inside a bound API window (witness_sleep) -> must be rejected
        sleep_entry = next(e for e in contract["code_fingerprints"] if e["name"] == "witness_sleep")
        bad_api = bytearray(rom)
        bad_api[sleep_entry["address"] - manifest["rom_base"]] ^= 0x01
        with self.assertRaisesRegex(ValueError, "witness_sleep"):
            admit_rom_with_provenance(bytes(bad_api), manifest=manifest, contract=contract)

        # Mutate main_signature_window -> word-sum or fingerprint rejects
        bad_main = bytearray(rom)
        bad_main[manifest["main_start"] - manifest["rom_base"]] ^= 0x01
        with self.assertRaisesRegex(ValueError, "signature mismatch|code fingerprint mismatch"):
            admit_rom_with_provenance(bytes(bad_main), manifest=manifest, contract=contract)

    def test_thumb_branch_patch_startup_reservation_and_layout_bounds(self):
        # Known positive and negative Thumb-2 BL/B encodings
        bl = thumb_branch(0x01090100, 0x01090201, link=True)
        b = thumb_branch(0x01090200, 0x01090101, link=False)
        self.assertEqual(len(bl), 4)
        self.assertEqual(len(b), 4)
        with self.assertRaises(ValueError):
            thumb_branch(0x01090101, 0x01090201, link=True)
        with self.assertRaises(ValueError):
            thumb_branch(0x01090100, 0x01090200, link=True)

        manifest, contract, rom = make_synthetic_contract_and_rom()
        startup = admit_rom(bytes(rom), manifest=manifest, contract=contract)
        syms = {p["symbol"]: 0x01092000 + i * 0x40 for i, p in enumerate(manifest["patches"])}
        patched = patch_startup(startup, 0x01090100, syms, manifest=manifest)
        self.assertEqual(len(patched), manifest["startup_bytes"])
        self.assertNotEqual(patched, startup)

        # Minimum user memory left check (524304 bytes), 16-byte aligned end <= limit - minimum_user_left
        max_valid_end = (manifest["user_start"] + manifest["user_length"] - manifest["minimum_user_left"]) & ~15
        res_ok = reservation(max_valid_end, manifest=manifest)
        self.assertGreaterEqual(res_ok["remaining_user_bytes"], manifest["minimum_user_left"])
        with self.assertRaisesRegex(ValueError, "insufficient Canon user memory"):
            reservation(max_valid_end + 16, manifest=manifest)

        # Layout & FIO write buffer contract
        base = manifest["resident_base"]
        good_syms = {
            "_resident_start": base,
            "copied_startup": base + 0x100,
            "_resident_data_end": base + 0x2000,
            "_bss_start": base + 0x2000,
            "_private_stack_top": base + 0x4000,
            "_reserved_end": base + 0x4000,
            "_resident_load_offset": 0x40,
            "_image_bytes": 0x2080,
        }
        self.assertEqual(validate_layout(good_syms, manifest=manifest)["end"], base + 0x4000)
        wb = validate_write_buffer(base + 0x1200, 256, base, base + 0x2000)
        self.assertEqual(wb["uncached_address"], (base + 0x1200) | 0x40000000)
        with self.assertRaises(ValueError):
            validate_write_buffer(base + 0x1F80, 256, base, base + 0x2000)


if __name__ == "__main__":
    unittest.main()
