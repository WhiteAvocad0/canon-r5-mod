"""Build assistant copy-to-card logic against temporary folders (no device, no display)."""
from datetime import datetime
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from gui import card
from image_format import finalize_image


def make_image(fill: int = 0x11, size: int = 256) -> bytes:
    buf = bytearray([fill]) * size
    struct.pack_into("<II", buf, size - 8, 0xE12FFF13, 0xCCCCCCCC)
    return finalize_image(bytes(buf))


def snapshot(folder: Path) -> dict[str, bytes | None]:
    return {
        str(p.relative_to(folder)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(folder.rglob("*"))
    }


class CardCopyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.build = self.tmp / "build"
        self.build.mkdir()
        self.image = make_image()
        self.sha = hashlib.sha256(self.image).hexdigest()
        (self.build / "autoexec.bin").write_bytes(self.image)
        self.card = self.tmp / "card"
        (self.card / "DCIM" / "100CANON").mkdir(parents=True)
        (self.card / "DCIM" / "100CANON" / "IMG_0001.CR3").write_bytes(b"photo")
        (self.card / "MISC").mkdir()
        self.backups = self.build / "card-backup"
        self.now = datetime(2026, 11, 18, 12, 0, 0)

    def copy(self, **kwargs):
        return card.copy_to_card(self.build / "autoexec.bin", self.sha, self.card, self.backups,
                                 confirmed=kwargs.pop("confirmed", True), now=self.now, **kwargs)

    def test_fresh_card_gets_verified_uppercase_copy_and_nothing_else_changes(self):
        before = snapshot(self.card)
        result = self.copy()
        self.assertEqual(result.target.name, "AUTOEXEC.BIN")
        self.assertIsNone(result.backup)
        self.assertFalse(result.replaced_existing)
        self.assertEqual((self.card / "AUTOEXEC.BIN").read_bytes(), self.image)
        after = snapshot(self.card)
        self.assertEqual({k: v for k, v in after.items() if k != "AUTOEXEC.BIN"}, before)
        self.assertFalse(self.backups.exists())
        self.assertTrue(any("eject" in m.lower() for m in result.messages))

    def test_existing_autoexec_is_found_case_insensitively_backed_up_then_replaced(self):
        old = b"older build"
        (self.card / "autoexec.bin").write_bytes(old)
        result = self.copy()
        self.assertEqual(result.target.name, "autoexec.bin")  # keeps the card's existing spelling
        self.assertTrue(result.replaced_existing)
        self.assertEqual(result.backup.read_bytes(), old)
        self.assertEqual(result.backup.parent, self.backups)
        self.assertIn(hashlib.sha256(old).hexdigest()[:12], result.backup.name)
        self.assertEqual((self.card / "autoexec.bin").read_bytes(), self.image)
        self.assertEqual(sorted(p.name for p in self.card.iterdir()), ["DCIM", "MISC", "autoexec.bin"])

    def test_identical_card_copy_writes_nothing(self):
        (self.card / "AUTOEXEC.BIN").write_bytes(self.image)
        before = snapshot(self.card)
        result = self.copy()
        self.assertTrue(result.already_identical)
        self.assertEqual(snapshot(self.card), before)

    def test_temp_readback_mismatch_removes_only_its_own_temp_file(self):
        old = b"older build"
        (self.card / "AUTOEXEC.BIN").write_bytes(old)
        (self.card / "R5GUI-DEADBEEF.TMP").write_bytes(b"left by an earlier run")
        before = snapshot(self.card)
        real = card._read_file

        def corrupt_temp(path, limit=card.MAX_IMAGE_BYTES):
            data = real(path, limit)
            return data[:-1] + b"\x00" if Path(path).name.startswith("R5GUI-") and Path(path).name != "R5GUI-DEADBEEF.TMP" else data

        with mock.patch.object(card, "_read_file", corrupt_temp), self.assertRaisesRegex(card.CardError, "temporary copy"):
            self.copy()
        self.assertEqual(snapshot(self.card), before)  # earlier leftover kept, our temp removed, card file intact
        self.assertEqual(len(list(self.backups.iterdir())), 1)  # backup on the computer is kept

    def test_final_readback_mismatch_reports_backup_and_deletes_nothing(self):
        old = b"older build"
        (self.card / "AUTOEXEC.BIN").write_bytes(old)
        real = card._read_file
        target = (self.card / "AUTOEXEC.BIN").resolve()
        calls = {"target": 0}

        def corrupt_final(path, limit=card.MAX_IMAGE_BYTES):
            data = real(path, limit)
            if Path(path).resolve() == target:
                calls["target"] += 1
                if calls["target"] >= 3:  # 1: backup source, 2: pre-replace re-check, 3: final readback
                    return data[::-1]
            return data

        with mock.patch.object(card, "_read_file", corrupt_final), \
                self.assertRaisesRegex(card.CardError, "backed up at"):
            self.copy()
        self.assertEqual(sorted(p.name for p in self.card.iterdir()), ["AUTOEXEC.BIN", "DCIM", "MISC"])
        (backup,) = self.backups.iterdir()
        self.assertEqual(backup.read_bytes(), old)

    def test_card_that_changes_during_copy_is_not_replaced(self):
        real_replace = os.replace
        real_matches = card._matches

        def sneaky_matches(root):
            (Path(root) / "AUTOEXEC.BIN").write_bytes(b"appeared meanwhile")
            return real_matches(root)

        with mock.patch.object(card, "_matches", sneaky_matches), \
                mock.patch.object(os, "replace", side_effect=AssertionError("must not replace")), \
                self.assertRaisesRegex(card.CardError, "appeared"):
            self.copy()
        self.assertEqual((self.card / "AUTOEXEC.BIN").read_bytes(), b"appeared meanwhile")
        self.assertFalse(any(p.name.startswith("R5GUI-") for p in self.card.iterdir()))
        self.assertIs(os.replace, real_replace)

    def test_build_image_must_match_build_json_and_footer(self):
        with self.assertRaisesRegex(card.CardError, "does not match build.json"):
            card.copy_to_card(self.build / "autoexec.bin", "0" * 64, self.card, self.backups, confirmed=True)
        bad = bytearray(self.image)
        bad[0] ^= 1
        (self.build / "autoexec.bin").write_bytes(bytes(bad))
        with self.assertRaisesRegex(card.CardError, "format check"):
            card.copy_to_card(self.build / "autoexec.bin", hashlib.sha256(bad).hexdigest(), self.card,
                              self.backups, confirmed=True)
        self.assertFalse(any(p.name.casefold() == "autoexec.bin" for p in self.card.iterdir()))

    def test_unusual_folders_need_confirmation_and_refusals_cannot_be_confirmed(self):
        plain = self.tmp / "not-a-card"
        plain.mkdir()
        info = card.inspect_card(plain)
        self.assertFalse(info.has_dcim)
        self.assertTrue(any("DCIM" in w for w in info.warnings))
        self.assertTrue(any("root" in w for w in info.warnings))  # a temp folder is not a mount point
        with self.assertRaises(card.NeedsConfirmation):
            card.copy_to_card(self.build / "autoexec.bin", self.sha, plain, self.backups, confirmed=False)
        self.assertEqual(list(plain.iterdir()), [])

        (self.card / "AUTOEXEC.BIN").mkdir()
        with self.assertRaisesRegex(card.CardError, "not a regular file"):
            self.copy()
        with self.assertRaisesRegex(card.CardError, "home folder"):
            card.copy_to_card(self.build / "autoexec.bin", self.sha, Path.home(), self.backups, confirmed=True)
        with self.assertRaisesRegex(card.CardError, "not a folder"):
            card.copy_to_card(self.build / "autoexec.bin", self.sha, self.tmp / "missing", self.backups, confirmed=True)

    def test_card_size_warning_and_leftovers_are_reported(self):
        (self.card / "R5GUI-0000.TMP").write_bytes(b"x")
        usage = mock.Mock(total=512 * 1000**2)
        with mock.patch.object(card.shutil, "disk_usage", return_value=usage), \
                mock.patch.object(card.os.path, "ismount", return_value=True):
            info = card.inspect_card(self.card)
        self.assertEqual(info.leftover_temps, ["R5GUI-0000.TMP"])
        self.assertTrue(any("unusual" in w for w in info.warnings))
        self.assertTrue(any("R5GUI-0000.TMP" in w for w in info.warnings))
        usage.total = 64 * 1000**3
        (self.card / "R5GUI-0000.TMP").unlink()
        with mock.patch.object(card.shutil, "disk_usage", return_value=usage), \
                mock.patch.object(card.os.path, "ismount", return_value=True):
            self.assertEqual(card.inspect_card(self.card).warnings, [])

    def test_symlinked_autoexec_is_refused(self):
        outside = self.tmp / "elsewhere.bin"
        outside.write_bytes(b"not on the card")
        try:
            (self.card / "AUTOEXEC.BIN").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(card.CardError, "not a regular file"):
            self.copy()
        self.assertEqual(outside.read_bytes(), b"not on the card")

    def test_eject_hints_name_the_os_tool(self):
        self.assertIn("Finder", card.eject_hint("darwin"))
        self.assertIn("Safely Remove", card.eject_hint("win32"))
        self.assertIn("Unmount", card.eject_hint("linux"))


if __name__ == "__main__":
    unittest.main()
