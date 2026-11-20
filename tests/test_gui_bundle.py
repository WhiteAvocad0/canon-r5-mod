"""The packaged macOS app's modes and frozen paths (tools/gui/bundle.py), without PyInstaller or Tkinter."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build as build_mod
import features
from gui import app as app_mod, bundle, runner, state

LAUNCHER = ROOT / "packaging" / "macos" / "launcher.py"


class FrozenPathTests(unittest.TestCase):
    def test_a_checkout_is_not_frozen(self):
        self.assertFalse(bundle.is_frozen())
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertTrue(bundle.is_frozen())

    def test_builder_command(self):
        build_py = ROOT / "tools" / "build.py"
        self.assertEqual(bundle.builder_command(build_py, frozen=False, executable="/py"), ["/py", str(build_py)])
        app = "/Applications/R5 Mod Assistant.app/Contents/MacOS/R5 Mod Assistant"
        self.assertEqual(bundle.builder_command(build_py, frozen=True, executable=app), [app, "--run-build"])
        self.assertEqual(bundle.builder_command(build_py), [sys.executable, str(build_py)])

    def test_builds_root_is_writable_outside_the_app(self):
        self.assertEqual(bundle.builds_root(ROOT, frozen=False), ROOT / "build")
        self.assertEqual(bundle.builds_root(ROOT, frozen=True, home=Path("/h")), Path("/h/R5-builds"))
        frozen_root = bundle.builds_root(ROOT, frozen=True)
        self.assertEqual(frozen_root, Path.home() / "R5-builds")
        self.assertEqual(build_mod.check_output_dir(frozen_root / "gui-x"), (frozen_root / "gui-x").resolve())

    def test_default_output_dir_follows_the_mode(self):
        m = features.load()
        when = datetime(2026, 11, 18, 9, 5, 7)
        self.assertEqual(state.default_output_dir("xpan-crop", {}, m, when),
                         ROOT / "build" / "gui-xpan-crop-20261118-090507")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(Path, "home", lambda: Path("/Users/someone")):
            self.assertEqual(state.default_output_dir("xpan-crop", {}, m, when),
                             Path("/Users/someone/R5-builds/gui-xpan-crop-20261118-090507"))

    def test_linked_documents_are_in_the_tree(self):
        for path in app_mod.DOCS.values():
            self.assertTrue(path.is_file(), path)
            self.assertEqual(path.parent, ROOT / "docs")


class DispatchTests(unittest.TestCase):
    def test_run_build_calls_the_builder_main_with_the_rest(self):
        fake = types.SimpleNamespace(main=mock.Mock(return_value=7))
        with mock.patch.dict(sys.modules, {"build": fake}), \
                mock.patch.object(app_mod, "main", side_effect=AssertionError("window started")):
            self.assertEqual(bundle.main(["--run-build", "--profile", "xpan-crop"]), 7)
        fake.main.assert_called_once_with(["--profile", "xpan-crop"])

    def test_self_test_and_window(self):
        with mock.patch.object(app_mod, "self_test", return_value=0) as self_test, \
                mock.patch.object(app_mod, "main", return_value=0) as window:
            self.assertEqual(bundle.main(["--self-test"]), 0)
            self_test.assert_called_once_with()
            window.assert_not_called()
            for argv in ([], ["-psn_0_12345"], ["--run-build-later"]):  # anything else opens the window
                self.assertEqual(bundle.main(argv), 0)
            self.assertEqual(window.call_count, 3)

    def test_launcher_runs_the_builder_cli_from_a_checkout(self):
        proc = subprocess.run([sys.executable, str(LAUNCHER), "--run-build", "--help"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--toolchain", proc.stdout)
        self.assertIn("--record-indicator", proc.stdout)

    def test_runner_starts_the_app_in_build_mode(self):
        """The frozen command line, with the launcher standing in for the app's binary."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            lines = []
            outcome = runner.run_build(["--profile", "xpan-crop", "--rom", "r.bin", "--toolchain", "t",
                                        "--output", str(out), "--startup-wav", "a.wav"], out, lines.append,
                                       command=[sys.executable, str(LAUNCHER), bundle.BUILD_FLAG], poll_seconds=0.01)
            self.assertEqual(outcome.returncode, 2)
            self.assertIn("forbids --startup-wav", outcome.error_text())
            self.assertFalse(out.exists())


class PackagingTests(unittest.TestCase):
    def test_version_is_one_semver_string(self):
        self.assertRegex(bundle.VERSION, r"^\d+\.\d+\.\d+$")
        proc = subprocess.run([sys.executable, str(ROOT / "packaging" / "macos" / "build_app.py"), "--print-version"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.stdout.strip(), bundle.VERSION, proc.stderr)

    def test_bundle_module_does_not_import_tkinter(self):
        self.assertNotIn("tkinter", (ROOT / "tools" / "gui" / "bundle.py").read_text())

    def test_workflow_checks_the_tag_against_the_version(self):
        text = (ROOT / ".github" / "workflows" / "macos-app.yml").read_text()
        self.assertIn("app-v*", text)
        self.assertIn("--print-version", text)
        self.assertIn("--self-test", text)
        pinned = (ROOT / "packaging" / "macos" / "requirements.txt").read_text().split()
        self.assertEqual(len(pinned), 1)
        self.assertRegex(pinned[0], r"^pyinstaller==\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
