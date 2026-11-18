"""The setup wizard's step logic, without a display: gating, resume, the short path, blocked steps."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from gui import wizard as wz
from gui.state import AssistantState


class FakeSummary:
    def __init__(self, out: Path, profile="xpan-crop", options=None, sha="ab" * 32, ok=True):
        self.output_dir, self.profile, self.sha256 = out, profile, sha
        self.options = {"record_indicator": "off"} if options is None else options
        self.sha256_matches_build_json = ok


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wizard-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.file = self.tmp / "wizard.json"
        self.a = AssistantState()
        self.a.set_profile("xpan-crop")
        self.w = wz.Wizard(self.a, state_file=self.file)

    def rom_ok(self, ok=True, message="ROM accepted"):
        self.a.set_rom(self.tmp / "gang100.bin")
        self.a.finish("rom", self.a.rom_key(), ok, message)

    def toolchain_ok(self, ok=True):
        self.a.set_toolchain(self.tmp / "bin")
        self.a.finish("toolchain", self.a.toolchain_key(), ok, "GCC 15.2.1" if ok else "wrong GCC version")

    def one_time(self):
        self.w.set_camera_confirmed(True)
        self.rom_ok()
        self.w.set_bootdisk("confirmed")

    def walk(self, *steps):
        for step in steps:
            self.assertEqual(self.w.current, step)
            self.assertTrue(self.w.next(), (step, self.w.next_blockers()))


class Steps(Base):
    def test_nine_steps_in_order_with_placeholders_marked(self):
        self.assertEqual([s.id for s in wz.STEPS], [wz.CAMERA, wz.ROM, wz.BOOTDISK, wz.FEATURES, wz.TOOLCHAIN,
                                                    wz.BUILD, wz.BOOTCARD, wz.COPY, wz.TEST])
        self.assertEqual([s.number for s in wz.STEPS], list(range(1, 10)))
        self.assertEqual([s.id for s in wz.STEPS if s.placeholder], [wz.ROM, wz.BOOTDISK])
        for step in (wz.ROM, wz.BOOTDISK):
            self.assertIn(wz.COMING_SOON, wz.GUIDE[step].details)
        self.assertEqual(set(wz.GUIDE), set(wz.STEP_BY_ID))

    def test_initial_statuses(self):
        st = {k: v.status for k, v in self.w.statuses().items()}
        self.assertEqual(st[wz.CAMERA], wz.NEEDS_ACTION)
        self.assertEqual(st[wz.BUILD], wz.BLOCKED)
        self.assertEqual(st[wz.COPY], wz.BLOCKED)
        self.assertEqual(st[wz.TEST], wz.BLOCKED)
        self.assertEqual(self.w.current, wz.CAMERA)


class Gating(Base):
    def test_next_only_when_the_step_passes(self):
        self.assertFalse(self.w.next())
        self.assertIn("confirm", self.w.next_blockers()[0])
        self.w.set_camera_confirmed(True)
        self.walk(wz.CAMERA)
        self.assertFalse(self.w.next())  # no ROM yet
        self.rom_ok(False, "firmware signature mismatch")
        self.assertIn("refused", self.w.next_blockers()[0])
        self.rom_ok()
        self.walk(wz.ROM)
        self.assertFalse(self.w.next())
        self.w.set_bootdisk("confirmed")
        self.assertIn("not verified", self.w.status(wz.BOOTDISK).message)
        self.walk(wz.BOOTDISK)
        self.assertFalse(self.w.next())
        self.w.confirm_features()
        self.walk(wz.FEATURES)
        self.toolchain_ok()
        self.walk(wz.TOOLCHAIN)
        self.assertEqual(self.w.status(wz.BUILD).status, wz.NEEDS_ACTION)
        self.w.set_build(FakeSummary(self.tmp / "out"))
        self.walk(wz.BUILD)
        self.w.set_card_prepared("elsewhere")
        self.assertEqual(self.w.status(wz.BOOTCARD).status, wz.SKIPPED)
        self.walk(wz.BOOTCARD)
        with self.assertRaises(ValueError):
            self.w.set_copied("cd" * 32)  # not the current build
        self.w.set_copied("ab" * 32)
        self.walk(wz.COPY)
        for i in range(len(wz.TEST_CHECKLIST)):
            self.w.set_test_item(i, True)
        self.assertEqual(self.w.status(wz.TEST).status, wz.DONE)
        self.assertFalse(self.w.next(), "the last step has no next")
        self.assertTrue(self.w.back())
        self.assertEqual(self.w.current, wz.COPY)

    def test_blocked_step_when_a_check_fails(self):
        self.one_time()
        self.w.confirm_features()
        self.toolchain_ok(False)
        build = self.w.status(wz.BUILD)
        self.assertEqual(build.status, wz.BLOCKED)
        self.assertIn("Build tools", build.message)
        self.assertFalse(self.w.can_goto(wz.BUILD))
        self.assertFalse(self.w.goto(wz.COPY))
        self.rom_ok(False, "Input size must be exactly 67108864 bytes")
        self.assertIn("ROM dump", self.w.status(wz.BUILD).message)
        self.toolchain_ok()
        self.rom_ok()
        self.assertTrue(self.w.goto(wz.BUILD))

    def test_changing_features_invalidates_build_copy_and_test(self):
        self.one_time()
        self.w.confirm_features()
        self.toolchain_ok()
        self.w.set_build(FakeSummary(self.tmp / "out"))
        self.w.set_copied("ab" * 32)
        self.w.set_test_item(0, True)
        self.a.set_option("record_indicator", "ding")
        self.w.features_changed()
        self.assertEqual(self.w.status(wz.FEATURES).status, wz.NEEDS_ACTION)
        self.assertEqual(self.w.status(wz.BUILD).status, wz.BLOCKED, "features must be confirmed again")
        self.assertIsNone(self.w.build_summary, "the old build is for other features")
        self.assertEqual(self.w.status(wz.COPY).status, wz.BLOCKED)
        self.assertEqual(self.w.saved.test_checked, [])

    def test_wav_inputs_gate_features_for_custom_sounds(self):
        self.a.set_profile("shutter-feedback")
        self.w.confirm_features()
        self.assertEqual(self.w.status(wz.FEATURES).status, wz.NEEDS_ACTION)
        self.assertIn("Pick the", self.w.status(wz.FEATURES).message)
        for input_id in self.a.input_paths:
            self.a.set_input(input_id, self.tmp / f"{input_id}.wav")
            self.a.finish("input", self.a.input_key(input_id), True, "WAV accepted", input_id=input_id)
        self.assertEqual(self.w.status(wz.FEATURES).status, wz.DONE)

    def test_bootdisk_cannot_be_marked_verified_yet(self):
        with self.assertRaises(ValueError):
            self.w.set_bootdisk("verified")


class Resume(Base):
    def test_progress_survives_and_is_rechecked(self):
        self.one_time()
        self.w.confirm_features()
        self.toolchain_ok()
        out = self.tmp / "out"
        self.w.set_build(FakeSummary(out))
        self.w.set_copied("ab" * 32)
        self.w.goto(wz.COPY)
        saved = json.loads(self.file.read_text())
        self.assertEqual(saved["current"], wz.COPY)
        self.assertEqual(saved["rom_path"], str(self.tmp / "gang100.bin"))
        # Reopen: ROM and toolchain are re-checked by the window's check loop, the build by verify_build.
        a2 = AssistantState()
        w2 = wz.Wizard(a2, state_file=self.file)
        w2.load(verify_build=lambda p: (FakeSummary(p), None))
        self.assertTrue(w2.saved.camera_confirmed)
        self.assertEqual(a2.rom_path, self.tmp / "gang100.bin")
        self.assertEqual(w2.status(wz.ROM).message, "Checking the ROM...")  # stored path, not yet re-checked
        self.assertIn(("rom", None), a2.needs_check())
        self.assertIn(("toolchain", None), a2.needs_check())
        self.assertEqual(w2.saved.copied_sha256, "ab" * 32, "the same build was copied")
        a2.finish("rom", a2.rom_key(), True, "ROM accepted")
        a2.finish("toolchain", a2.toolchain_key(), True, "GCC 15.2.1")
        self.assertEqual(w2.status(wz.COPY).status, wz.DONE)
        self.assertEqual(w2.current, wz.COPY)

    def test_a_changed_build_is_not_trusted(self):
        self.one_time()
        self.w.set_build(FakeSummary(self.tmp / "out"))
        self.w.set_copied("ab" * 32)
        w2 = wz.Wizard(AssistantState(), state_file=self.file)
        w2.load(verify_build=lambda p: (None, "The previous build no longer matches its build.json; build again."))
        self.assertIsNone(w2.build_summary)
        self.assertIsNone(w2.saved.copied_sha256)
        self.assertIn("no longer matches", w2.status(wz.BUILD).message + (w2.build_note or ""))

    def test_missing_or_damaged_state_file_starts_fresh(self):
        w2 = wz.Wizard(AssistantState(), state_file=self.tmp / "none.json")
        w2.load()
        self.assertEqual(w2.current, wz.CAMERA)
        self.file.write_text("{not json")
        w3 = wz.Wizard(AssistantState(), state_file=self.file)
        w3.load()
        self.assertFalse(w3.saved.camera_confirmed)
        self.file.write_text(json.dumps({"version": 99, "camera_confirmed": True}))
        w3.load()
        self.assertFalse(w3.saved.camera_confirmed)
        self.file.write_text(json.dumps({"version": 1, "current": "nonsense", "bootdisk": "hacked"}))
        w3.load()
        self.assertEqual((w3.current, w3.saved.bootdisk), (wz.CAMERA, "unknown"))


class ReturningUser(Base):
    def test_update_path_needs_the_one_time_steps(self):
        reasons = self.w.start_update()
        self.assertTrue(reasons)
        self.assertEqual(self.w.saved.mode, "full")
        self.one_time()
        self.assertEqual(self.w.start_update(), [])
        self.assertEqual(self.w.path(), [wz.FEATURES, wz.TOOLCHAIN, wz.BUILD, wz.COPY])
        self.assertEqual(self.w.current, wz.FEATURES)

    def test_update_path_skips_a_working_toolchain_and_walks_to_copy(self):
        self.one_time()
        self.toolchain_ok()
        self.w.start_update()
        self.assertEqual(self.w.path(), [wz.FEATURES, wz.BUILD, wz.COPY])
        self.assertFalse(self.w.next(), "features must be confirmed again")
        self.w.confirm_features()
        self.walk(wz.FEATURES)
        self.w.set_build(FakeSummary(self.tmp / "out"))
        self.walk(wz.BUILD)
        self.assertEqual(self.w.current, wz.COPY)
        self.w.set_copied("ab" * 32)
        self.assertEqual(self.w.next_blockers(), ["This is the last step."])
        self.assertFalse(self.w.goto(wz.CAMERA), "one-time steps are not on the short path")
        self.w.start_full()
        self.assertEqual(len(self.w.path()), 9)


if __name__ == "__main__":
    unittest.main()
