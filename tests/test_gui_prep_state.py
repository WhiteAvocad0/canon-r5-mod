"""The "Prepare card" step's flow and invariants, without a display or a device."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cardprep_images as ci
from cardprep import devices, workflow
from gui.app import App
from gui.i18n import Translator
from gui.prep_state import FORMAT_WARNING, PrepState
from test_cardprep_workflow import CHECKER_START_REFUSAL, ImageOps

BOOT = {"disk0"}
CARD = devices.classify_macos(ci.mac_whole(), [ci.mac_part()], BOOT)
OTHER = devices.classify_macos(ci.mac_whole("disk5"), [ci.mac_part("disk5", name="SPARE")], BOOT)
USB = devices.classify_macos(ci.mac_whole("disk6", BusProtocol="USB"), [ci.mac_part("disk6", BusProtocol="USB")], BOOT)


@unittest.skipUnless(ci.FD_PASSING, "needs UNIX sockets with SCM_RIGHTS")
class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardprep-state-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        mbr, area, _ = ci.fat32_card()
        ci.write_disk_image(self.tmp / "disk.img", mbr, area, ci.LAB_FAT32["start_lba"])
        self.ops = ImageOps(CARD, self.tmp / "disk.img")

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


class DialogRecovery(Images):
    """Run the wizard callbacks headless against the disk image, including any retry."""

    def setUp(self):
        super().setUp()
        app = self.app = App.__new__(App)
        app.prep = self.state()
        app.refresh = mock.Mock()
        app.cbasic_busy, app.copying = None, False
        app.bootdisk_ack = mock.Mock()
        app.bootdisk_ack.get.return_value = True
        app.words = Translator("en")
        app.filedialog, app.messagebox, app.wizard = mock.Mock(), mock.Mock(), mock.Mock()
        app.messagebox.askokcancel.return_value = True
        app.messagebox.askretrycancel.return_value = False
        app._stage_cbasic_folder_impl = mock.Mock()
        self.status = mock.Mock()

        def background(work, done):
            try:
                result = work()
            except Exception as exc:
                result = exc
            done(result)

        app._background = background
        self.enterContext(mock.patch.object(devices, "MacOps", return_value=self.ops))
        self.start = self.enterContext(mock.patch.object(workflow, "start", wraps=workflow.start))
        self.ops.unmounted_verify = self.ops.mounted_verify = (False, CHECKER_START_REFUSAL)

    def test_script_write_retry_preserves_task_and_uses_fresh_mount(self):
        mounted = self.tmp / "new-mount"

        def retry(*_args, **_kwargs):
            self.ops.unmounted_verify = (True, "passed")
            self.ops.chosen = replace(CARD, mount_point=str(mounted))
            return True

        self.app.messagebox.askretrycancel.side_effect = retry
        self.app._prepare_cbasic_card("disable_bootdisk", self.status)
        self.assertEqual([call.kwargs["verify_only"] for call in self.start.call_args_list], [False, True])
        self.assertEqual(self.ops.calls.count("write"), 1)
        self.assertEqual(self.ops.calls.count("read"), 2)
        self.assertEqual(self.app.messagebox.askokcancel.call_count, 2)  # disable warning, then marker write
        stage = self.app._stage_cbasic_folder_impl
        stage.assert_called_once()
        self.assertEqual(stage.call_args.args, (mounted, "disable_bootdisk", self.status))
        self.assertTrue(any("Volume check passed" in line for line in stage.call_args.kwargs["prefix_lines"]))

    def test_already_marked_script_keeps_failed_report_and_never_stages(self):
        self.app._prepare_cbasic_card("dump", self.status)
        self.assertEqual(self.ops.calls.count("write"), 1)
        self.ops.calls.clear()
        self.app.messagebox.reset_mock()
        self.app._prepare_cbasic_card("dump", self.status)
        self.assertNotIn("write", self.ops.calls)
        self.app.messagebox.askokcancel.assert_not_called()
        self.app.messagebox.askretrycancel.assert_called_once()
        prompt = self.app.messagebox.askretrycancel.call_args.args[1]
        self.assertIn("-69846", prompt)
        self.assertIn("Backup:", prompt)
        self.app._stage_cbasic_folder_impl.assert_not_called()
        self.assertFalse(self.app._card_busy())

    def test_script_retry_refuses_to_reapply_a_missing_marker(self):
        def erase_marker_before_retry(*_args, **_kwargs):
            self.ops.poke(496, bytes(6))
            return True

        self.app.messagebox.askretrycancel.side_effect = erase_marker_before_retry
        self.app._prepare_cbasic_card("dump", self.status)
        self.assertEqual(self.ops.calls.count("write"), 1)
        self.assertEqual(self.ops.calls.count("read"), 2)
        self.app.messagebox.askokcancel.assert_called_once()
        self.app.messagebox.askretrycancel.assert_called_once()
        self.app.messagebox.showerror.assert_called_once()
        self.app._stage_cbasic_folder_impl.assert_not_called()
        self.assertFalse(self.app._card_busy())

    def test_mount_lookup_cannot_fall_back_to_an_old_path(self):
        self.ops.unmounted_verify = (True, "passed")
        for lost_card in (True, False):
            with self.subTest(lost_card=lost_card):
                def change_mount(cand):
                    if lost_card:
                        self.ops.gone = True
                    else:
                        self.ops.mounted = False
                    return True, "mounted"

                self.ops.gone = False
                self.ops.mounted = True
                with mock.patch.object(self.ops, "mount", side_effect=change_mount):
                    self.app._prepare_cbasic_card("enable_bootdisk", self.status)
                self.app._stage_cbasic_folder_impl.assert_not_called()
                self.assertIn("No script files were staged", self.app.messagebox.showerror.call_args.args[1])
                self.assertFalse(self.app._card_busy())

    def test_failed_no_write_mark_clears_an_earlier_prepared_result(self):
        self.ops.unmounted_verify = (True, "passed")
        self.app._prep_run("mark")
        self.app.wizard.set_card_prepared.assert_called_with("prepared")
        self.ops.calls.clear()
        self.ops.unmounted_verify = (False, CHECKER_START_REFUSAL)
        self.app._prep_run("mark")
        self.assertFalse(self.app.prep.report.ok)
        self.app.wizard.set_card_prepared.assert_called_with(None)
        self.app.messagebox.askretrycancel.assert_called_once()
        self.assertNotIn("write", self.ops.calls)

    def test_undo_and_chosen_restore_retry_the_original_source(self):
        for action in ("undo", "restore"):
            with self.subTest(action=action):
                self.ops.unmounted_verify = (True, "passed")
                marked = workflow.start(CARD, self.ops, self.app.prep.backup_root)
                self.assertTrue(workflow.write(marked, self.ops).ok)
                self.app.filedialog.askdirectory.return_value = str(marked.backup.folder)
                self.ops.unmounted_verify = (False, CHECKER_START_REFUSAL)
                self.ops.calls.clear()
                self.start.reset_mock()

                def retry(*_args, **_kwargs):
                    self.ops.unmounted_verify = (True, "passed")
                    return True

                self.app.messagebox.askretrycancel.side_effect = retry
                self.app._prep_run(action)
                self.assertTrue(self.app.prep.report.ok)
                self.assertEqual(self.ops.calls.count("write"), 1)
                self.assertEqual(self.start.call_count, 2)
                retry_call = self.start.call_args
                self.assertTrue(retry_call.kwargs["verify_only"])
                self.assertEqual(retry_call.kwargs["restore_from"].folder, marked.backup.folder)
                self.assertEqual(retry_call.args[0], CARD)
                self.app.wizard.set_card_prepared.assert_called_with(None)
        self.app.filedialog.askdirectory.assert_called_once()


if __name__ == "__main__":
    unittest.main()
