"""Canon Basic card staging, BootInfo verification, and returned ROM dump collection tests."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import build as build_mod
from cardprep import cbasic
from gui import card
from test_contracts import make_synthetic_contract_and_rom


def make_synthetic_cbasic_rom(boot_disk_val: int = 0x00000000):
    manifest, contract, rom = make_synthetic_contract_and_rom()
    fps = {}
    for name, (start_va, end_va, _) in cbasic.WIZARD_FINGERPRINTS.items():
        s_off = start_va - cbasic.ROM_BASE
        e_off = end_va - cbasic.ROM_BASE
        pattern = hashlib.sha256(f"wizard:{name}".encode()).digest()
        for pos in range(s_off, e_off, len(pattern)):
            chunk = pattern[: min(len(pattern), e_off - pos)]
            rom[pos : pos + len(chunk)] = chunk
        fps[name] = (start_va, end_va, hashlib.sha256(rom[s_off:e_off]).hexdigest())

    blk_off = cbasic.BOOTINFO_OFFSET
    rom[blk_off : blk_off + cbasic.BOOTINFO_BYTES] = b"\xff" * cbasic.BOOTINFO_BYTES
    struct.pack_into("<II", rom, blk_off, 0x00000000, boot_disk_val)
    return manifest, contract, fps, rom


class BootInfoTests(unittest.TestCase):
    def test_inspect_bootinfo_enabled_disabled_and_before_after_diff(self):
        _, _, fps, before = make_synthetic_cbasic_rom(0x00000000)
        _, _, _, after = make_synthetic_cbasic_rom(0xFFFFFFFF)

        b_facts = cbasic.inspect_bootinfo(bytes(before), fingerprints=fps)
        self.assertEqual((b_facts.state, b_facts.boot_disk_raw), ("disabled", 0x00000000))

        a_facts = cbasic.inspect_bootinfo(bytes(after), before_bytes=bytes(before), fingerprints=fps)
        self.assertEqual((a_facts.state, a_facts.boot_disk_raw), ("enabled", 0xFFFFFFFF))
        self.assertTrue(a_facts.before_compared)
        self.assertEqual(a_facts.before_sha256, b_facts.rom_sha256)

    def test_inspect_bootinfo_refusals(self):
        _, _, fps, rom = make_synthetic_cbasic_rom(0xFFFFFFFF)

        with self.assertRaises(cbasic.CBasicError) as ctx:
            cbasic.inspect_bootinfo(b"\x00" * 1024, fingerprints=fps)
        self.assertEqual(ctx.exception.code, "rom_length")

        bad_fp = bytearray(rom)
        bad_fp[0xE00068C0 - cbasic.ROM_BASE] ^= 1
        with self.assertRaises(cbasic.CBasicError) as ctx:
            cbasic.inspect_bootinfo(bytes(bad_fp), fingerprints=fps)
        self.assertEqual(ctx.exception.code, "wizard_fingerprint_mismatch")

        bad_val = bytearray(rom)
        struct.pack_into("<I", bad_val, cbasic.BOOTINFO_OFFSET + 4, 0x12345678)
        with self.assertRaises(cbasic.CBasicError) as ctx:
            cbasic.inspect_bootinfo(bytes(bad_val), fingerprints=fps)
        self.assertEqual(ctx.exception.code, "unexpected_bootdisk_value")

        for off in (0x00, 0x0C, 0x10, 0x24):
            bad_inv = bytearray(rom)
            bad_inv[cbasic.BOOTINFO_OFFSET + off] ^= 0x01
            with self.assertRaises(cbasic.CBasicError) as ctx:
                cbasic.inspect_bootinfo(bytes(bad_inv), fingerprints=fps)
            self.assertEqual(ctx.exception.code, "bootinfo_invariant")

        _, _, _, before = make_synthetic_cbasic_rom(0x00000000)
        drifted = bytearray(rom)
        drifted[cbasic.BOOTINFO_OFFSET + 0x20] ^= 0x01  # inside first 48 bytes, outside +0x04..+0x07
        with self.assertRaises(cbasic.CBasicError) as ctx:
            cbasic.inspect_bootinfo(bytes(drifted), before_bytes=bytes(before), fingerprints=fps)
        self.assertEqual(ctx.exception.code, "bootinfo_unexpected_diff")


class CardStagingAndCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbasic-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.card_dir = self.tmp / "card"
        (self.card_dir / "DCIM").mkdir(parents=True)
        self.backup_dir = self.tmp / "backup"
        self.dump_dir = self.tmp / "dumps"
        self.disk_usage_ok = mock.Mock(total=16 * 1000**3, free=2 * 1024**3)

    def _stage(self, task="dump", disk_usage=None):
        usage = self.disk_usage_ok if disk_usage is None else disk_usage
        with mock.patch.object(card.shutil, "disk_usage", return_value=usage), \
                mock.patch.object(cbasic.shutil, "disk_usage", return_value=usage), \
                mock.patch.object(card.os.path, "ismount", return_value=True):
            return cbasic.stage_script_files(self.card_dir, task, self.backup_dir)

    def test_exact_script_bytes_and_hashes_for_all_tasks(self):
        staged = self._stage("enable_bootdisk")
        self.assertEqual((self.card_dir / "script.req").read_bytes(), b"for DC_scriptdisk")
        self.assertEqual(staged.script_req_sha256,
                         "60e6a567e9c0731d95415e0fb2c687efd2d6309f766edee17d127d77597d1767")
        self.assertEqual(staged.extend_m_sha256,
                         "f2f78fd8c92621cb63396888d6f5c8329c0b01a6ee6b1c47b1be6878d662d5f3")

        staged_dis = self._stage("disable_bootdisk")
        self.assertEqual(staged_dis.extend_m_sha256,
                         "604b10173439ee2d8cc4c94ea733e9a44e18c79cd5cb10144016ff975c33f12c")
        # Previous extend.m was backed up into backup_dir before replacement.
        backups = list(self.backup_dir.glob("extend.m-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), cbasic.ENABLE_BOOTDISK_SCRIPT)

        staged_dump = self._stage("dump")
        self.assertIn(b"SaveAllRomImageToFile()", (self.card_dir / "extend.m").read_bytes())
        self.assertNotIn(b"BootDisk", (self.card_dir / "extend.m").read_bytes())
        record = json.loads((self.backup_dir / "cbasic-staged.json").read_text())
        self.assertEqual(record["task"], "dump")
        self.assertEqual(record["extend_m_sha256"], staged_dump.extend_m_sha256)

    def test_stage_refusals(self):
        with self.assertRaises(card.NeedsConfirmation):
            cbasic.stage_script_files(self.card_dir, "dump", self.backup_dir)

        low_space = mock.Mock(total=16 * 1000**3, free=100 * 1024 * 1024)
        with self.assertRaises(cbasic.CBasicError) as ctx:
            self._stage("dump", disk_usage=low_space)
        self.assertEqual(ctx.exception.code, "insufficient_free_space")

        (self.card_dir / "AUTOEXEC.BIN").write_bytes(b"mod")
        with self.assertRaises(cbasic.CBasicError) as ctx:
            self._stage("dump")
        self.assertEqual(ctx.exception.code, "autoexec_present")

    def test_collect_returned_dump_discovers_new_verifies_and_cleans_scripts(self):
        manifest, contract, fps, rom_before = make_synthetic_cbasic_rom(0x00000000)
        _, _, _, rom_after = make_synthetic_cbasic_rom(0xFFFFFFFF)
        (self.card_dir / "gang100.bin").write_bytes(bytes(rom_before))
        staged = self._stage("enable_bootdisk")
        self.assertIn("gang100.bin", staged.baseline_gang1)

        real_check = build_mod.check_rom
        with mock.patch.object(build_mod, "check_rom",
                               lambda p, **k: real_check(p, manifest=manifest, target_contract=contract, **k)), \
                mock.patch.object(cbasic, "WIZARD_FINGERPRINTS", fps):
            # Before camera runs the script, require_new=True refuses cleanly and leaves scripts untouched.
            with self.assertRaises(cbasic.CBasicError) as ctx:
                cbasic.collect_returned_dump(
                    self.card_dir, self.dump_dir,
                    baseline_gang1=staged.baseline_gang1, require_new=True,
                    profile="shutter-feedback",
                )
            self.assertEqual(ctx.exception.code, "no_new_dump")
            self.assertTrue((self.card_dir / "extend.m").exists())

            # Camera runs script and writes gang101.bin.
            (self.card_dir / "GANG101.BIN").write_bytes(bytes(rom_after))
            before_local = self.tmp / "before.bin"
            before_local.write_bytes(bytes(rom_before))
            ret = cbasic.collect_returned_dump(
                self.card_dir, self.dump_dir,
                baseline_gang1=staged.baseline_gang1, require_new=True,
                before_rom=before_local, profile="shutter-feedback",
                backup_dir=self.backup_dir,
            )

        self.assertEqual(ret.card_file.name.lower(), "gang101.bin")
        self.assertEqual(ret.bootinfo.state, "enabled")
        self.assertTrue(ret.bootinfo.before_compared)
        self.assertEqual(ret.sha256, hashlib.sha256(rom_after).hexdigest())
        self.assertEqual(ret.local_path.read_bytes(), bytes(rom_after))
        self.assertEqual(sorted(f.lower() for f in ret.cleaned_files), ["extend.m", "script.req"])
        self.assertFalse((self.card_dir / "extend.m").exists())
        self.assertFalse((self.card_dir / "script.req").exists())

    def test_scan_gang1_files_orders_numerically_and_refuses_symlinks(self):
        for name in ("gang110.bin", "gang101.bin", "gang100.bin"):
            p = self.card_dir / name
            with open(p, "wb") as f:
                f.truncate(cbasic.ROM_BYTES)
        self.assertEqual([p.name for p in cbasic.scan_gang1_files(self.card_dir)],
                         ["gang100.bin", "gang101.bin", "gang110.bin"])
        try:
            (self.card_dir / "gang102.bin").symlink_to(self.card_dir / "gang100.bin")
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(cbasic.CBasicError) as ctx:
            cbasic.scan_gang1_files(self.card_dir)
        self.assertEqual(ctx.exception.code, "symlink_refused")


if __name__ == "__main__":
    unittest.main()
