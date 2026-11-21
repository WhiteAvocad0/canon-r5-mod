"""The packaged desktop app's modes, frozen paths and packaging helpers, without PyInstaller or Tkinter."""
from datetime import datetime
import importlib.util
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

LAUNCHER = ROOT / "packaging" / "launcher.py"
BUILD_APP = ROOT / "packaging" / "build_app.py"


def load_build_app():
    spec = importlib.util.spec_from_file_location("build_app", BUILD_APP)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"build_app": module}):  # dataclasses look their module up there
        spec.loader.exec_module(module)
    return module


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
        fake = types.SimpleNamespace(main=mock.Mock(return_value=7), __file__="/r/tools/build.py")
        with mock.patch.dict(sys.modules, {"build": fake}), mock.patch.object(sys, "argv", ["app"]), \
                mock.patch.object(app_mod, "main", side_effect=AssertionError("window started")):
            self.assertEqual(bundle.main(["--run-build", "--profile", "xpan-crop"]), 7)
            self.assertEqual(sys.argv, ["/r/tools/build.py", "--profile", "xpan-crop"])
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
        self.assertTrue(proc.stdout.startswith("usage: build.py "), proc.stdout[:40])

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
        proc = subprocess.run([sys.executable, str(BUILD_APP), "--print-version"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.stdout.strip(), bundle.VERSION, proc.stderr)

    def test_bundle_module_does_not_import_tkinter(self):
        self.assertNotIn("tkinter", (ROOT / "tools" / "gui" / "bundle.py").read_text())

    def test_workflow_checks_the_tag_against_the_version(self):
        text = (ROOT / ".github" / "workflows" / "desktop-app.yml").read_text()
        self.assertIn("app-v*", text)
        self.assertIn("--print-version", text)
        self.assertIn("--self-test", text)
        for needle in ("windows-latest", "macos-latest", "macos-15-intel", "*.zip", "*.dmg", "SHA256SUMS.txt"):
            self.assertIn(needle, text)
        pinned = (ROOT / "packaging" / "requirements.txt").read_text().split()
        self.assertEqual(len(pinned), 1)
        self.assertRegex(pinned[0], r"^pyinstaller==\d+\.\d+\.\d+$")

    def test_package_names_and_bundled_tree_locations(self):
        build_app = load_build_app()
        dist = Path("/d")
        mac = build_app.target_for(dist, "R5 Mod Assistant", "darwin", "arm64")
        self.assertEqual((mac.os, mac.arch), ("macos", "arm64"))
        self.assertEqual(mac.exe, dist / "R5 Mod Assistant.app" / "Contents" / "MacOS" / "R5 Mod Assistant")
        self.assertEqual(mac.tree, dist / "R5 Mod Assistant.app" / "Contents" / "Resources" / "r5-mod")
        win = build_app.target_for(dist, "R5 Mod Assistant", "win32", "AMD64")
        self.assertEqual((win.os, win.arch), ("windows", "x64"))
        self.assertEqual(win.app, dist / "R5 Mod Assistant")
        self.assertEqual(win.exe, dist / "R5 Mod Assistant" / "R5 Mod Assistant.exe")
        self.assertEqual(win.tree, dist / "R5 Mod Assistant" / "_internal" / "r5-mod")
        self.assertEqual(build_app.target_for(dist, "X", "win32", "ARM64").arch, "arm64")
        with self.assertRaises(SystemExit):
            build_app.target_for(dist, "X", "linux", "x86_64")

    def test_staged_files_are_checked_against_git_blob_ids(self):
        build_app = load_build_app()
        data = b"line one\r\nline two\n"
        expected = subprocess.run(["git", "hash-object", "--stdin"], input=data, capture_output=True,
                                  check=True).stdout.decode().strip()
        self.assertEqual(build_app.git_blob_id(data, "sha1"), expected)

    def test_read_me_covers_the_unsigned_app_and_its_limits(self):
        build_app = load_build_app()
        windows = build_app.README["windows"] + build_app.README_COMMON
        for needle in ("More info", "Run anyway", "GNU Arm toolchain", "macOS only", "step 8", "untested prototype",
                       "Licenses"):
            self.assertIn(needle, windows)
        mac = build_app.README["macos"] + build_app.README_COMMON
        for needle in ("Open Anyway", "Privacy & Security", "GNU Arm toolchain", "untested prototype"):
            self.assertIn(needle, mac)


class WindowsTests(unittest.TestCase):
    def test_frozen_windows_app_hides_console_windows(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(bundle, "hide_console_windows") as hide, \
                mock.patch.object(app_mod, "main", return_value=0):
            bundle.main([], platform="win32")
            hide.assert_called_once_with()
            bundle.main([], platform="darwin")
            hide.assert_called_once_with()
        with mock.patch.object(bundle, "hide_console_windows") as hide, mock.patch.object(app_mod, "main", return_value=0):
            bundle.main([], platform="win32")  # from a checkout: python.exe has its own console
            hide.assert_not_called()

    def test_hide_console_windows_adds_create_no_window_once(self):
        calls = []

        class FakePopen:
            def __init__(self, *args, **kwargs):
                calls.append((args, kwargs))

        bundle.hide_console_windows(FakePopen, flag=0x08000000)
        bundle.hide_console_windows(FakePopen, flag=0x08000000)  # a second call changes nothing
        FakePopen(["gcc", "--version"], stdout=-1)
        FakePopen(["powershell"], creationflags=0x200)
        self.assertEqual(calls[0], ((["gcc", "--version"],), {"stdout": -1, "creationflags": 0x08000000}))
        self.assertEqual(calls[1][1]["creationflags"], 0x08000200)

    def test_open_document_on_windows_falls_back_to_notepad(self):
        opened, started = [], []
        doc = ROOT / "docs" / "gui.md"
        app_mod.open_document(doc, platform="win32", startfile=opened.append, popen=started.append)
        self.assertEqual((opened, started), ([str(doc)], []))

        def no_app(_path):
            raise OSError("No application is associated with the specified file")
        app_mod.open_document(doc, platform="win32", startfile=no_app, popen=started.append)
        self.assertEqual(started, [["notepad.exe", str(doc)]])
        urls = []
        app_mod.open_document(doc, platform="darwin", browser=urls.append)
        self.assertEqual(urls, [doc.as_uri()])

    def test_windows_toolchain_candidates_include_arm_layouts(self):
        from gui import checks
        with tempfile.TemporaryDirectory() as tmp:
            home, program_files = Path(tmp) / "home", Path(tmp) / "Program Files (x86)"
            installed = program_files / "Arm GNU Toolchain arm-none-eabi" / "15.2 rel1" / "bin"
            unpacked = home / "Downloads" / "arm-gnu-toolchain-15.2.rel1-mingw-w64-x86_64-arm-none-eabi" / "bin"
            old = program_files / "Arm GNU Toolchain arm-none-eabi" / "14.2 rel1" / "bin"
            for d in (installed, unpacked, old):
                d.mkdir(parents=True)
            found = checks.toolchain_candidates(home=home, environ={"PATH": "", "ProgramFiles(x86)": str(program_files)},
                                                platform="win32")
            self.assertEqual(found, [unpacked, installed])

    def test_windows_toolchain_admission_finds_exe_names_without_pinned_hashes(self):
        import json
        import toolchain
        policy = json.loads(toolchain.POLICY_PATH.read_text())
        self.assertNotIn("sha256", json.dumps({k: v for k, v in policy.items() if k != "notes"}))
        self.assertEqual(policy["required_gcc_version"], "15.2.1")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "arm-none-eabi-gcc.exe").write_bytes(b"")
            with mock.patch.object(toolchain.sys, "platform", "win32"):
                self.assertEqual(toolchain._resolve_tool(Path(tmp), "arm-none-eabi-gcc"),
                                 Path(tmp) / "arm-none-eabi-gcc.exe")

    def test_card_preparation_says_macos_only_elsewhere(self):
        from gui import wizard
        with tempfile.TemporaryDirectory() as tmp:
            w = wizard.Wizard(state.AssistantState(), state_file=Path(tmp) / "w.json", prep_supported=False)
            note = w.status(wizard.BOOTCARD).message
        self.assertIn("macOS only", note)
        self.assertIn("on a Mac", note)


if __name__ == "__main__":
    unittest.main()
