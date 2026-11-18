"""The "Prepare card" step's flow and invariants, without a display or a device."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cardprep_images as ci
from cardprep import devices, workflow
from gui.prep_state import FORMAT_WARNING, PrepState
from test_cardprep_workflow import ImageOps

BOOT = {"disk0"}
CARD = devices.classify_macos(ci.mac_whole(), [ci.mac_part()], BOOT)
OTHER = devices.classify_macos(ci.mac_whole("disk5"), [ci.mac_part("disk5", name="SPARE")], BOOT)
USB = devices.classify_macos(ci.mac_whole("disk6", BusProtocol="USB"), [ci.mac_part("disk6", BusProtocol="USB")], BOOT)


class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardprep-state-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        mbr, area, _ = ci.fat32_card()
        (self.tmp / "disk.img").write_bytes(mbr + bytes(4096))
        (self.tmp / "part.img").write_bytes(area + bytes(4096))
        self.ops = ImageOps(CARD, self.tmp / "disk.img", self.tmp / "part.img")

    def state(self) -> PrepState:
        s = PrepState("darwin", backup_root=self.tmp / "backups")
        s.set_candidates([CARD, OTHER, USB])
        s.select("disk4")
        return s

    def read(self, s: PrepState, **kwargs):
        token = s.begin("Reading...")
        return token, workflow.start(s.selected, self.ops, s.backup_root, **kwargs)


class Choosing(Images):
    def test_steps_before_prepare(self):
        s = PrepState("darwin")
        self.assertIn("Find SD cards", " ".join(s.blockers()))
        s.set_candidates([CARD, OTHER, USB])
        self.assertIn("Choose your card", " ".join(s.blockers()))
        s.select("disk4")
        self.assertEqual(s.blockers(), [])

    def test_only_accepted_cards_can_be_chosen(self):
        s = PrepState("darwin")
        s.set_candidates([CARD, OTHER, USB])
        self.assertEqual([c.whole_id for c in s.accepted], ["disk4", "disk5"])
        self.assertEqual([c.whole_id for c in s.refused], ["disk6"])
        for whole_id in ("disk6", "disk0"):
            with self.assertRaises(ValueError):
                s.select(whole_id)

    def test_no_typed_name_is_needed(self):
        s = self.state()
        self.assertEqual(s.blockers(), [])
        self.assertFalse(hasattr(s, "typed_name"))

    def test_no_card_found(self):
        s = PrepState("darwin")
        s.set_candidates([USB])
        self.assertIn("No usable SD card", " ".join(s.blockers()))

    def test_other_platforms_cannot_prepare(self):
        for platform in ("linux", "win32"):
            s = PrepState(platform)
            s.set_candidates([CARD])
            s.select("disk4")
            self.assertIn("macOS only", " ".join(s.blockers()))

    def test_warning_text(self):
        self.assertIn("IN THE CAMERA", FORMAT_WARNING)
        self.assertIn("copy your photos and videos", FORMAT_WARNING)


class Prepare(Images):
    def test_read_then_one_confirmation_naming_the_card(self):
        s = self.state()
        token, session = self.read(s)
        self.assertEqual(s.read_finished(token, session), "confirm")
        title, text = s.confirmation()
        self.assertEqual(title, "Prepare card")
        self.assertTrue(text.startswith("Prepare EOS_DIGITAL (15.9 GB FAT32 SD card)?"))
        self.assertIn("sector(s) 0", text)
        self.assertIn(str(session.backup.folder), text)
        self.assertTrue(s.may_write(token, session))
        self.assertTrue(s.blocks_copy, "tab 6 is blocked while a confirmation is pending")
        s.begin_write(session)
        self.assertTrue(s.blocks_copy)
        report = workflow.write(session, self.ops)
        s.finished(report)
        self.assertTrue(s.report.ok, s.report.lines)
        self.assertIsNone(s.session)
        self.assertFalse(s.blocks_copy)
        self.assertIn(f"Backup: {session.backup.folder}", s.report.lines)

    def test_already_prepared_needs_no_confirmation(self):
        s = self.state()
        token, session = self.read(s)
        s.read_finished(token, session)
        s.begin_write(session)
        s.finished(workflow.write(session, self.ops))
        token, again = self.read(s)
        self.assertEqual(s.read_finished(token, again), "done")
        self.assertIsNone(s.session)
        self.assertIn("Already prepared", s.report.title)
        self.assertTrue(any(line.startswith("Backup: ") for line in s.report.lines))

    def test_late_reads_are_thrown_away(self):
        for change in (lambda s: s.select("disk5"), lambda s: s.set_candidates([CARD, OTHER]),
                       lambda s: s.card_gone("removed"), lambda s: s.select(None)):
            s = self.state()
            token, session = self.read(s)
            change(s)
            self.assertEqual(s.read_finished(token, session), "stale")
            self.assertIsNone(s.session)
            workflow.cancel(session, self.ops)

    def test_after_ok_only_the_same_session_is_written(self):
        s = self.state()
        token, session = self.read(s)
        s.read_finished(token, session)
        s.select("disk5")  # the choice changed while the dialog was open
        self.assertFalse(s.may_write(token, session))
        s = self.state()
        token, session = self.read(s)
        s.read_finished(token, session)
        other_token, other = token, workflow.Session(session.snapshot, session.plan, session.backup)
        self.assertFalse(s.may_write(other_token, other))
        s.copying = True
        self.assertFalse(s.may_write(token, session))
        workflow.cancel(session, self.ops)

    def test_one_operation_at_a_time(self):
        s = self.state()
        s.begin("Reading...")
        self.assertIn("Wait", " ".join(s.blockers()))
        with self.assertRaises(RuntimeError):
            s.begin("again")
        self.assertTrue(s.blocks_copy)
        s = self.state()
        s.copying = True
        self.assertIn("copied to a card", " ".join(s.blockers()))

    def test_any_write_attempt_discards_the_session(self):
        for report in (workflow.Report(False, "cancelled", nothing_written=True),
                       workflow.Report(False, "failed", written_sectors=[0]), workflow.Report(True, "done")):
            s = self.state()
            token, session = self.read(s)
            s.read_finished(token, session)
            s.begin_write(session)
            s.finished(report)
            self.assertIsNone(s.session)
            self.assertFalse(s.may_write(token, session))
            workflow.cancel(session, self.ops)

    def test_failures_are_shown_and_discard(self):
        s = self.state()
        token = s.begin("Reading...")
        s.failed("Not prepared: the card is NTFS. Nothing was written.")
        self.assertIsNone(s.busy)
        self.assertNotEqual(token, s.generation)
        self.assertIn("NTFS", s.error)
        self.assertEqual(s.blockers(), [])


class Restore(Images):
    def test_undo_confirmation_names_the_backup(self):
        s = self.state()
        token, session = self.read(s)
        s.read_finished(token, session)
        s.begin_write(session)
        s.finished(workflow.write(session, self.ops))
        latest = workflow.latest_backup(CARD, s.backup_root)
        self.assertEqual(latest.folder, session.backup.folder)
        token, undo = self.read(s, restore_from=latest)
        self.assertEqual(s.read_finished(token, undo), "confirm")
        title, text = s.confirmation()
        self.assertEqual(title, "Undo card preparation")
        self.assertIn(str(latest.folder), text)
        self.assertIn(str(undo.backup.folder), text)
        workflow.cancel(undo, self.ops)


if __name__ == "__main__":
    unittest.main()
