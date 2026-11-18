"""Build assistant state, checks and runner logic, without a display or Tkinter."""
from datetime import datetime
import io
import contextlib
import itertools
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import build as build_mod
import features
from gui import checks, runner, state
from test_audio_profile import build_wav_bytes
from test_contracts import make_synthetic_contract_and_rom


def ready(s: state.AssistantState, tmp: Path) -> None:
    """Fill every input with a path and a passing result, as the window would after its checks."""
    s.set_rom(tmp / "rom.bin")
    for input_id in s.manifest.inputs:
        s.set_input(input_id, tmp / f"{input_id}.wav")
    s.set_toolchain(tmp / "tc")
    s.set_output_dir(tmp / "out")
    for what, input_id in (("rom", None), ("toolchain", None), *(("input", i) for i in s.manifest.inputs)):
        key = s.begin(what, input_id)
        s.finish(what, key, True, "ok", input_id=input_id)


class StateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_every_buildable_gui_selection_is_accepted_by_the_builder_cli(self):
        manifest = features.load()
        indicator = manifest.options["record_indicator"]
        combos = 0
        for profile, value in itertools.product(manifest.profile_ids, indicator.values):
            s = state.AssistantState(manifest)
            s.set_profile(profile)
            try:
                s.set_option("record_indicator", value)
            except ValueError:
                self.assertNotIn(profile, indicator.profiles)
                self.assertNotEqual(value, indicator.default)
                continue
            ready(s, self.tmp)
            self.assertEqual(s.blockers(), [])
            argv = s.build_argv()
            args = build_mod.build_parser().parse_args(argv)
            self.assertIsNone(build_mod.cli_selection_error(args), argv)
            self.assertEqual(args.profile, profile)
            if profile in indicator.profiles and value != indicator.default:
                self.assertEqual(args.record_indicator, value)
            else:
                self.assertIsNone(args.record_indicator)
            if dict(manifest.profiles[profile].inputs)["startup_wav"] == "forbidden":
                self.assertIsNone(args.startup_wav)
            combos += 1
        self.assertEqual(combos, 1 + len(indicator.values))  # shutter-feedback (off only) + xpan-crop x 4

    def test_incompatible_option_is_disabled_with_a_reason_and_reset_on_profile_change(self):
        s = state.AssistantState()
        s.set_profile("xpan-crop")
        s.set_option("record_indicator", "ding")
        (view,) = s.option_views()
        self.assertTrue(view.enabled)
        self.assertEqual(view.value, "ding")
        ding = next(c for c in view.choices if c.value == "ding")
        self.assertEqual(ding.status_label, "Verified on a private lab build")
        chirp = next(c for c in view.choices if c.value == "chirp2")
        self.assertEqual(chirp.status_label, "Experimental")
        s.set_profile("shutter-feedback")
        (view,) = s.option_views()
        self.assertFalse(view.enabled)
        self.assertIn("xpan-crop", view.reason)
        self.assertEqual(view.value, "off")
        with self.assertRaisesRegex(ValueError, "xpan-crop"):
            s.set_option("record_indicator", "ding")
        with self.assertRaises(ValueError):
            s.set_option("record_indicator", "loud")
        with self.assertRaises(ValueError):
            s.set_profile("xpan")

    def test_wav_pickers_follow_the_profile_policy(self):
        s = state.AssistantState()
        self.assertTrue(all(v.enabled for v in s.input_views()))
        s.set_profile("xpan-crop")
        for view in s.input_views():
            self.assertFalse(view.enabled)
            self.assertIn("xpan-crop", view.reason)
        # A WAV picked earlier is not passed to an xpan-crop build.
        ready(s, self.tmp)
        self.assertNotIn("--startup-wav", s.build_argv())

    def test_stale_check_results_are_ignored_and_rechecked(self):
        s = state.AssistantState()
        s.set_rom(self.tmp / "rom.bin")
        self.assertIn(("rom", None), s.needs_check())
        key = s.begin("rom")
        self.assertEqual(s.rom_check().status, state.CHECKING)
        self.assertNotIn(("rom", None), s.needs_check())
        s.set_profile("xpan-crop")  # ROM admission differs per profile
        self.assertFalse(s.finish("rom", key, True, "accepted for shutter-feedback"))
        self.assertEqual(s.rom_check().status, state.UNCHECKED)
        key = s.begin("rom")
        self.assertTrue(s.finish("rom", key, True, "accepted for xpan-crop"))
        s.set_option("record_indicator", "ding")  # the indicator adds fingerprinted pages
        self.assertEqual(s.rom_check().status, state.UNCHECKED)
        s.set_option("record_indicator", "off")
        self.assertEqual(s.rom_check().status, state.OK)

    def test_blockers_explain_what_is_missing(self):
        s = state.AssistantState()
        s.set_output_dir(None)
        text = " ".join(s.blockers())
        for needle in ("ROM", "Startup sound", "Shutter sound", "toolchain", "output folder"):
            self.assertIn(needle, text)
        with self.assertRaises(ValueError):
            s.build_argv()
        ready(s, self.tmp)
        key = s.begin("rom")
        s.finish("rom", key, False, "firmware signature mismatch")
        self.assertTrue(any("signature mismatch" in b for b in s.blockers()))
        s.set_output_dir(ROOT / "src" / "out")
        self.assertTrue(any("source directory" in b for b in s.blockers()))
        (self.tmp / "exists").mkdir()
        s.set_output_dir(self.tmp / "exists")
        self.assertTrue(any("already exists" in b for b in s.blockers()))
        s.building = True
        self.assertIn("A build is already running.", s.blockers())

    def test_default_output_dir_is_under_ignored_build(self):
        m = features.load()
        path = state.default_output_dir("xpan-crop", {"record_indicator": "ding"}, m, datetime(2026, 11, 18, 9, 5, 7))
        self.assertEqual(path, ROOT / "build" / "gui-xpan-crop-ding-20261118-090507")
        self.assertEqual(state.default_output_dir("shutter-feedback", {"record_indicator": "off"}, m,
                                                  datetime(2026, 1, 2, 3, 4, 5)).name,
                         "gui-shutter-feedback-20260102-030405")
        self.assertIn("build/", (ROOT / ".gitignore").read_text())


class CheckTests(unittest.TestCase):
    def test_rom_and_wav_checks_use_the_builder_admission(self):
        manifest, contract, rom = make_synthetic_contract_and_rom()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rom_path = tmp / "rom.bin"
            rom_path.write_bytes(bytes(rom))
            real = build_mod.check_rom
            with mock.patch.object(build_mod, "check_rom",
                                   lambda p, **k: real(p, manifest=manifest, target_contract=contract, **k)):
                good = checks.check_rom(rom_path, profile="shutter-feedback", record_indicator=None)
                self.assertTrue(good.ok, good.message)
                (tmp / "short.bin").write_bytes(b"\0" * 1024)
                bad = checks.check_rom(tmp / "short.bin", profile="shutter-feedback", record_indicator=None)
                self.assertFalse(bad.ok)
                self.assertIn("exactly", bad.message)
                missing = checks.check_rom(tmp / "nope.bin", profile="xpan-crop", record_indicator=None)
                self.assertFalse(missing.ok)

            wav = tmp / "a.wav"
            wav.write_bytes(build_wav_bytes(struct.pack("<480h", *range(480))))
            result = checks.check_wav(wav)
            self.assertTrue(result.ok, result.message)
            self.assertEqual(result.facts["duration_ms"], 10)
            stereo = bytearray(build_wav_bytes(struct.pack("<480h", *range(480))))
            struct.pack_into("<H", stereo, 22, 2)  # channels = 2
            wav.write_bytes(bytes(stereo))
            self.assertFalse(checks.check_wav(wav).ok)

    def test_toolchain_candidates_and_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            a = home / "tools" / "xpack-arm-none-eabi-gcc-15.2.1-1.1" / "bin"
            b = home / ".local" / "xPacks" / "@xpack-dev-tools" / "arm-none-eabi-gcc" / "15.2.1-1.1.1" / ".content" / "bin"
            for d in (a, b):
                d.mkdir(parents=True)
            (home / "xpack-arm-none-eabi-gcc-14.2.1-1.1" / "bin").mkdir(parents=True)  # wrong version: not listed
            found = checks.toolchain_candidates(home=home, environ={"PATH": ""}, platform="linux")
            self.assertEqual(found, [a, b])
            self.assertEqual(checks.detect_toolchain([home / "nope"])[0], None)
            refused = checks.check_toolchain(a)  # empty folder
            self.assertFalse(refused.ok)
            self.assertIn("missing", refused.message)


class RunnerTests(unittest.TestCase):
    def test_condense_log_lines(self):
        line = json.dumps(["/x/bin/arm-none-eabi-gcc", "-c", "a.c", "-o", "/out/a.o"])
        self.assertEqual(runner.condense_log_line(line), "gcc a.o")
        self.assertEqual(runner.condense_log_line(json.dumps(["/x/arm-none-eabi-nm", "-n", "/o/h.elf"])), "nm h.elf")
        self.assertIsNone(runner.condense_log_line("   \n"))
        self.assertEqual(runner.condense_log_line("warning: x"), "warning: x")

    def test_run_build_streams_and_reports_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = []
            outcome = runner.run_build(["--profile", "xpan-crop", "--rom", "r.bin", "--toolchain", "t",
                                        "--output", str(Path(tmp) / "out"), "--startup-wav", "a.wav"],
                                       Path(tmp) / "out", lines.append, poll_seconds=0.01)
            self.assertEqual(outcome.returncode, 2)
            self.assertIn("forbids --startup-wav", outcome.error_text())
            self.assertTrue(any("forbids" in l for l in lines))
            self.assertFalse((Path(tmp) / "out").exists())

    def test_summary_memory_and_contents_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            image = b"\x01" * 64
            import hashlib
            record = {
                "profile": "crop-only", "build_profile": "xpan-crop",
                "format": {"sha256": hashlib.sha256(image).hexdigest()},
                "record_indicator": {"cue": "ding"},
                "reservation": {"remaining_user_bytes": 907256},
                "witness": {"source_manifest_sha256": "ab" * 32},
                "caller_input_hashes": {"rom": {"sha256": "cd" * 32}, "toolchain": {"gcc_version": "15.2.1"}},
            }
            (out / "build.json").write_text(json.dumps(record))
            (out / "autoexec.bin").write_bytes(image)
            s = runner.summarize(out)
            self.assertTrue(s.sha256_matches_build_json)
            self.assertEqual(s.heap_floor, 524304)
            self.assertEqual(s.heap_margin, 907256 - 524304)
            self.assertEqual(s.options, {"record_indicator": "ding"})
            self.assertIn("382,952", runner.memory_line(s))
            path = runner.write_contents(s, features.load())
            text = path.read_text()
            for needle in ("hardware-untested", "xpan-crop", "Ding", "Verified on a private lab build", "ab" * 32,
                           "BootDisk"):
                self.assertIn(needle, text)
            with self.assertRaises(FileExistsError):
                runner.write_contents(s, features.load())
            (out / "autoexec.bin").write_bytes(b"\x02" * 64)
            self.assertFalse(runner.summarize(out).sha256_matches_build_json)


class NoTkinterTests(unittest.TestCase):
    def test_logic_modules_do_not_import_tkinter(self):
        for name in ("state", "checks", "runner", "card"):
            source = (ROOT / "tools" / "gui" / f"{name}.py").read_text()
            self.assertNotIn("tkinter", source, name)


if __name__ == "__main__":
    unittest.main()
