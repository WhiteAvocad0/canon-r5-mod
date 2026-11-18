"""The "Prepare card" step's confirm flow, without a display or a device."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cardprep_images as ci
from cardprep import devices, layout, workflow
from gui.prep_state import PrepState

BOOT = {"disk0"}
CARD = devices.classify_macos(ci.mac_whole(), [ci.mac_part()], BOOT)
OTHER = devices.classify_macos(ci.mac_whole("disk5"), [ci.mac_part("disk5", name="SPARE")], BOOT)
USB = devices.classify_macos(ci.mac_whole("disk6", BusProtocol="USB"), [ci.mac_part("disk6", BusProtocol="USB")], BOOT)


def read_result(cand=CARD, area=None):
    mbr, default, disk = ci.fat32_card()
    area = default if area is None else area
    snap = workflow.Snapshot(cand, mbr, area, layout.inspect(mbr, area, disk_bytes=disk))
    plan = layout.plan_markers(mbr, area, disk_bytes=disk)
    return snap, plan, workflow.Backup(Path("/tmp/backup"), mbr, area, {})


def ready(state: PrepState) -> PrepState:
    state.set_acknowledged(True)
    state.set_candidates([CARD, OTHER, USB])
    state.select("disk4")
    state.set_typed_name("EOS_DIGITAL")
    return state


class ConfirmFlow(unittest.TestCase):
    def test_initial_blockers_follow_the_steps(self):
        s = PrepState("darwin")
        text = " ".join(s.read_blockers())
        self.assertIn("formatted in the camera", text)
        self.assertIn("Search for SD cards", text)
        self.assertFalse(any(step.done for step in s.steps()))

    def test_only_accepted_cards_can_be_chosen(self):
        s = PrepState("darwin")
        s.set_candidates([CARD, OTHER, USB])
        self.assertEqual([c.whole_id for c in s.accepted], ["disk4", "disk5"])
        self.assertEqual([c.whole_id for c in s.refused], ["disk6"])
        with self.assertRaises(ValueError):
            s.select("disk6")
        with self.assertRaises(ValueError):
            s.select("disk0")

    def test_typed_volume_name_must_match_exactly(self):
        s = PrepState("darwin")
        s.set_acknowledged(True)
        s.set_candidates([CARD])
        s.select("disk4")
        for typed in ("", "eos_digital", "EOS_DIGITAL ", " EOS_DIGITAL", "EOS_DIGITA"):
            s.set_typed_name(typed)
            self.assertFalse(s.name_confirmed, typed)
            self.assertIn("Type the card's volume name", " ".join(s.read_blockers()))
        s.set_typed_name("EOS_DIGITAL")
        self.assertTrue(s.name_confirmed)
        self.assertEqual(s.read_blockers(), [])

    def test_no_card_found(self):
        s = PrepState("darwin")
        s.set_acknowledged(True)
        s.set_candidates([USB])
        self.assertIn("No usable SD card", " ".join(s.read_blockers()))

    def test_other_platforms_cannot_prepare(self):
        for platform in ("linux", "win32"):
            s = ready(PrepState(platform))
            self.assertIn("macOS only", " ".join(s.read_blockers()))

    def test_write_needs_a_current_read_and_backup(self):
        s = ready(PrepState("darwin"))
        self.assertIn("Read the card first", " ".join(s.write_blockers()))
        snap, plan, backup = read_result()
        self.assertTrue(s.read_done(snap, plan, backup))
        self.assertEqual(s.write_blockers(), [])
        self.assertIn("Backup saved and verified", "\n".join(s.plan_lines()))
        s.busy = "Writing"
        self.assertIn("Wait", " ".join(s.write_blockers()))

    def test_stale_reads_are_ignored(self):
        s = ready(PrepState("darwin"))
        snap, plan, backup = read_result()
        s.select("disk5")  # another card chosen while the read was running
        self.assertFalse(s.read_done(snap, plan, backup))
        self.assertIsNone(s.plan)
        s = ready(PrepState("darwin"))
        s.set_typed_name("EOS_DIGITA")
        self.assertFalse(s.read_done(snap, plan, backup))
        s = ready(PrepState("darwin"))
        mismatched = workflow.Backup(backup.folder, backup.mbr, bytes(12288), {})
        self.assertFalse(s.read_done(snap, plan, mismatched))

    def test_changing_anything_forgets_the_plan(self):
        for change in (lambda s: s.select("disk5"), lambda s: s.set_typed_name("x"),
                       lambda s: s.set_candidates([CARD]), lambda s: s.set_restore_from(None)):
            s = ready(PrepState("darwin"))
            s.read_done(*read_result())
            change(s)
            self.assertIsNone(s.plan)
            self.assertIsNone(s.backup)
            self.assertTrue(s.write_blockers())

    def test_nothing_to_write_when_markers_present(self):
        s = ready(PrepState("darwin"))
        _, plan, _ = read_result()
        marked = read_result(area=plan.after_area)
        self.assertTrue(s.read_done(*marked))
        self.assertIn("Nothing to write", " ".join(s.write_blockers()))
        self.assertIn("already present", "\n".join(s.plan_lines()))

    def test_after_any_write_attempt_read_again(self):
        s = ready(PrepState("darwin"))
        s.read_done(*read_result())
        s.write_done(workflow.Report(False, "Boot markers: cancelled. Nothing was written."))
        self.assertIn("Read the card first", " ".join(s.write_blockers()))
        s.read_done(*read_result())
        s.write_done(workflow.Report(True, "Boot markers: done and verified.", written_sectors=[0]))
        self.assertTrue(s.steps()[-1].done)
        self.assertIsNone(s.plan)

    def test_restore_read_must_match_the_backup_shown(self):
        s = ready(PrepState("darwin"))
        snap, mark, backup = read_result()
        _, _, area_backup = read_result()
        source = workflow.Backup(Path("/tmp/a"), snap.mbr, snap.area, {})
        restore = layout.plan_restore(snap.mbr, snap.area, snap.mbr, snap.area, disk_bytes=snap.candidate.disk_bytes)
        s.set_restore_from(source)
        s.set_typed_name("EOS_DIGITAL")
        s.set_restore_from(workflow.Backup(Path("/tmp/b"), snap.mbr, snap.area, {}))  # changed during the read
        self.assertFalse(s.read_done(snap, restore, backup, source=source))
        s.set_restore_from(source)
        self.assertTrue(s.read_done(snap, restore, backup, source=source))

    def test_restore_needs_a_backup_folder(self):
        s = ready(PrepState("darwin"))
        self.assertIn("Choose a backup folder", " ".join(s.read_blockers(restoring=True)))
        s.set_restore_from(workflow.Backup(Path("/tmp/b"), b"", b"", {}))
        self.assertEqual(s.read_blockers(restoring=True), [])
        s.set_acknowledged(False)  # restoring does not need the "formatted in the camera" confirmation
        self.assertEqual(s.read_blockers(restoring=True), [])
        self.assertTrue(s.read_blockers())


if __name__ == "__main__":
    unittest.main()
