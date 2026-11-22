"""R5 Mod Assistant copy-to-card logic against temporary folders (no device, no display)."""
from datetime import datetime
import hashlib
import os
import shutil
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


class _All:
    """A confirmation set that contains every warning (for tests that fail before warnings matter)."""

    def __contains__(self, item):
        return True


ALL = _All()


def snapshot(folder: Path) -> dict[str, bytes | None]:
    return {
        str(p.relative_to(folder)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(folder.rglob("*"))
    }


class ProtectedLocationTests(unittest.TestCase):
    """Pure path rules: removable drive roots are allowed, system and home locations refused."""

    WIN_ENV = {"SystemDrive": "C:", "SystemRoot": "C:\\Windows", "windir": "C:\\WINDOWS"}

    def win(self, path, home="C:\\Users\\me", env=None):
        return card.protected_location(path, windows=True, home=home, environ=self.WIN_ENV if env is None else env)

    def posix(self, path, home="/Users/me"):
        return card.protected_location(path, windows=False, home=home, environ={})

    def test_windows_removable_drive_roots_are_allowed(self):
        for path in ("E:\\", "E:/", "e:\\", "F:\\", "E:\\DCIM", "\\\\server\\share\\", "C:\\cards\\sd"):
            with self.subTest(path):
                self.assertIsNone(self.win(path))

    def test_windows_system_drive_windows_folder_and_home_are_refused(self):
        self.assertIn("system drive", self.win("C:\\"))
        self.assertIn("system drive", self.win("c:/"))
        self.assertIn("Windows folder", self.win("C:\\Windows\\Temp"))
        self.assertIn("Windows folder", self.win("c:\\windows"))
        self.assertIn("home folder", self.win("C:\\Users"))
        self.assertIn("home folder", self.win("C:\\Users\\ME"))  # Windows paths are case-insensitive
        # A system drive other than C: comes from %SystemDrive% / %SystemRoot%, not from a letter guess.
        env = {"SystemDrive": "D:", "SystemRoot": "D:\\Windows"}
        self.assertIn("system drive", self.win("D:\\", home="D:\\Users\\me", env=env))
        self.assertIsNone(self.win("C:\\", home="D:\\Users\\me", env=env))
        # Without any environment the home folder's drive still counts as the system drive.
        self.assertIn("system drive", self.win("C:\\", env={}))
        self.assertIsNone(self.win("E:\\", env={}))

    def test_posix_mounts_allowed_system_and_home_refused(self):
        for path in ("/Volumes/EOS_DIGITAL", "/media/me/CARD", "/run/media/me/CARD", "/mnt/sd", "/private/tmp/card"):
            with self.subTest(path):
                self.assertIsNone(self.posix(path, home="/home/me" if path.startswith(("/media", "/run")) else "/Users/me"))
        for path in ("/", "/Volumes", "/media", "/run/media", "/usr/local", "/System/Volumes/Data", "/etc"):
            with self.subTest(path):
                self.assertIn("system folder", self.posix(path))
        self.assertIn("home folder", self.posix("/Users/me"))
        self.assertIsNotNone(self.posix("/home", home="/home/me"))
        self.assertIsNone(self.posix("/Users/me/Desktop/card"))  # inside home: allowed, with card warnings


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
                                 confirmed_warnings=card.inspect_card(self.card).warnings, now=self.now, **kwargs)

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
            card.copy_to_card(self.build / "autoexec.bin", "0" * 64, self.card, self.backups, confirmed_warnings=ALL)
        bad = bytearray(self.image)
        bad[0] ^= 1
        (self.build / "autoexec.bin").write_bytes(bytes(bad))
        with self.assertRaisesRegex(card.CardError, "format check"):
            card.copy_to_card(self.build / "autoexec.bin", hashlib.sha256(bad).hexdigest(), self.card,
                              self.backups, confirmed_warnings=ALL)
        self.assertFalse(any(p.name.casefold() == "autoexec.bin" for p in self.card.iterdir()))

    def test_unusual_folders_need_confirmation_and_refusals_cannot_be_confirmed(self):
        plain = self.tmp / "not-a-card"
        plain.mkdir()
        info = card.inspect_card(plain)
        self.assertFalse(info.has_dcim)
        self.assertTrue(any("DCIM" in w for w in info.warnings))
        self.assertTrue(any("root" in w for w in info.warnings))  # a temp folder is not a mount point
        with self.assertRaises(card.NeedsConfirmation):
            card.copy_to_card(self.build / "autoexec.bin", self.sha, plain, self.backups)
        self.assertEqual(list(plain.iterdir()), [])

        (self.card / "AUTOEXEC.BIN").mkdir()
        with self.assertRaisesRegex(card.CardError, "not a regular file"):
            self.copy()
        with self.assertRaisesRegex(card.CardError, "home folder"):
            card.copy_to_card(self.build / "autoexec.bin", self.sha, Path.home(), self.backups, confirmed_warnings=ALL)
        with self.assertRaisesRegex(card.CardError, "not a folder"):
            card.copy_to_card(self.build / "autoexec.bin", self.sha, self.tmp / "missing", self.backups, confirmed_warnings=ALL)

    def test_confirmation_covers_only_the_warnings_that_were_shown(self):
        plain = self.tmp / "not-a-card"
        plain.mkdir()
        shown = card.inspect_card(plain).warnings
        (plain / "R5GUI-0000.TMP").write_bytes(b"x")  # a new warning appears after the person confirmed
        with self.assertRaisesRegex(card.NeedsConfirmation, "Leftover"):
            card.copy_to_card(self.build / "autoexec.bin", self.sha, plain, self.backups, confirmed_warnings=shown)
        self.assertEqual(sorted(p.name for p in plain.iterdir()), ["R5GUI-0000.TMP"])
        result = card.copy_to_card(self.build / "autoexec.bin", self.sha, plain, self.backups,
                                   confirmed_warnings=card.inspect_card(plain).warnings)
        self.assertEqual(result.target.read_bytes(), self.image)

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


class ScreenshotFolderTests(unittest.TestCase):
    """A --screenshot build's copy also makes the empty DCIM/R5SHOTS the camera writes into."""

    setUp = CardCopyTests.setUp
    copy = CardCopyTests.copy

    def test_copy_creates_only_the_empty_folder(self):
        before = snapshot(self.card)
        result = self.copy(screenshot_folder=True)
        self.assertTrue((self.card / "DCIM" / "R5SHOTS").is_dir())
        self.assertEqual(list((self.card / "DCIM" / "R5SHOTS").iterdir()), [])
        self.assertIn("Created the empty screenshot folder DCIM/R5SHOTS on the card.", result.messages)
        after = snapshot(self.card)
        self.assertEqual({k: v for k, v in after.items() if k.casefold() not in ("autoexec.bin", "dcim/r5shots")}, before)

    def test_without_the_option_no_folder_is_made(self):
        self.copy()
        self.assertFalse((self.card / "DCIM" / "R5SHOTS").exists())

    def test_existing_folder_and_its_files_are_left_alone(self):
        shots = self.card / "DCIM" / "r5shots"
        shots.mkdir()
        (shots / "SS_00001.YCA").write_bytes(b"x")
        result = self.copy(screenshot_folder=True)
        self.assertEqual([p.name for p in (self.card / "DCIM").iterdir() if p.name.casefold() == "r5shots"], ["r5shots"])
        self.assertEqual((shots / "SS_00001.YCA").read_bytes(), b"x")
        self.assertIn("The screenshot folder DCIM/r5shots is already on the card.", result.messages)

    def test_a_file_in_the_way_is_reported_not_replaced(self):
        (self.card / "DCIM" / "R5SHOTS").write_bytes(b"not a folder")
        result = self.copy(screenshot_folder=True)
        self.assertEqual((self.card / "DCIM" / "R5SHOTS").read_bytes(), b"not a folder")
        self.assertTrue(any("is not a plain folder" in m for m in result.messages))

    def test_no_dcim_means_no_folder(self):
        shutil.rmtree(self.card / "DCIM")
        result = self.copy(screenshot_folder=True)
        self.assertFalse(any(p.name.casefold() == "dcim" for p in self.card.iterdir()))
        self.assertTrue(any("no DCIM folder" in m for m in result.messages))

    def test_identical_image_still_gets_the_folder(self):
        self.copy()
        result = self.copy(screenshot_folder=True)
        self.assertTrue(result.already_identical)
        self.assertTrue((self.card / "DCIM" / "R5SHOTS").is_dir())
