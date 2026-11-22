"""The Screenshots utility: its widget-free state, and the real window (skipped without Tk)."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui import shots, theme
import screenshots
from test_screenshots import shot, tree


class Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.card = self.home / "EOS_DIGITAL"
        self.r5shots = self.card / "DCIM" / "R5SHOTS"
        self.r5shots.mkdir(parents=True)
        (self.r5shots / "SS_00001.YCA").write_bytes(shot())
        (self.r5shots / "SS_00002.YCA").write_bytes(b"R5SS-broken")


class StateTests(Folders):
    def test_default_output_is_dated_in_pictures(self):
        st = shots.ShotsState(today=date(2026, 11, 19), home=self.home)
        self.assertEqual(st.output_dir, self.home / "Pictures" / "R5 screenshots" / "2026-11-19")
        self.assertEqual(shots.default_output_dir(date(2026, 1, 2), Path("/h")), Path("/h/Pictures/R5 screenshots/2026-01-02"))

    def test_blockers_and_sources(self):
        st = shots.ShotsState(home=self.home)
        self.assertEqual(st.blockers(), ["Choose the card or a folder with screenshots."])
        st.set_source(shots.find_source(self.home / "Pictures"))
        self.assertIn("No R5SHOTS folder", st.blockers()[0])
        empty = self.home / "copy" / "R5SHOTS"
        empty.mkdir(parents=True)
        st.set_source(shots.find_source(self.home / "copy"))
        self.assertIn("has no screenshots", st.blockers()[0])
        source = shots.find_source(self.card)
        self.assertEqual((source.shots, len(source.files), source.on_card), (self.r5shots, 2, False))
        self.assertEqual(source.describe(), f"2 screenshots in {self.r5shots}.")
        st.set_source(source)
        self.assertEqual(st.blockers(), [])
        for bad in (self.r5shots / "png", self.card, self.home, Path("/"), Path("/usr/share")):
            st.set_output(bad)
            self.assertTrue(st.blockers(), bad)
        st.set_output(self.home / "Pictures" / "R5")
        self.assertEqual(st.blockers(), [])

    def test_convert_report_and_skip(self):
        st = shots.ShotsState(home=self.home)
        st.set_source(shots.find_source(self.card))
        before = tree(self.card)
        source, out = st.begin()
        self.assertEqual(st.blockers(), ["Wait: Converting..."])
        st.progress(0, 2, "SS_00001.YCA")
        self.assertEqual(st.busy, "Converting 1 of 2: SS_00001.YCA")
        st.finished(screenshots.convert_folder(source.shots, out, picked=source.picked, home=self.home))
        kind, headline, lines = st.report()
        self.assertEqual((kind, headline), (theme.WARNING, "1 converted, 0 already converted, 1 not converted."))
        self.assertIn("SS_00001.YCA: 1024x682 LCD, R5SS v3 -> SS_00001.png", lines)
        self.assertTrue(any(line.startswith("SS_00002.YCA: not converted") for line in lines))
        self.assertTrue(st.can_open_output())
        self.assertEqual(tree(self.card), before)
        (self.r5shots / "SS_00002.YCA").unlink()
        st.finished(screenshots.convert_folder(source.shots, out, picked=source.picked, home=self.home))
        self.assertEqual(st.report()[:2], (theme.SUCCESS, "0 converted, 1 already converted."))
        st.finished(RuntimeError("disk full"))
        self.assertEqual(st.report(), (theme.DANGER, "Not converted: disk full", []))


def make_root():
    try:
        import tkinter as tk
    except ImportError:
        return None
    try:
        root = tk.Tk()
    except tk.TclError:
        return None
    root.withdraw()
    return root


class WindowTests(Folders):
    def setUp(self):
        super().setUp()
        self.root = make_root()
        if self.root is None:
            self.skipTest("Tk is not available here (no tkinter or no display)")
        import tkinter as tk
        from tkinter import filedialog, font as tkfont, messagebox, ttk
        from gui import app as app_mod, checks, wizard
        self.app_mod, self.wizard_mod = app_mod, wizard
        for p in (mock.patch.object(wizard, "DEFAULT_STATE_FILE", self.home / "wizard.json"),
                  mock.patch.object(checks, "detect_toolchain", lambda: (None, None, []))):
            p.start()
            self.addCleanup(p.stop)
        self.app = app_mod.App(tk, ttk, filedialog, messagebox, tkfont, root=self.root, appearance="light")
        self.app.shots = shots.ShotsState(home=self.home)
        self.app.filedialog = mock.Mock()
        self.addCleanup(self._destroy)

    def _destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def wait(self, until, seconds=30):
        end = time.monotonic() + seconds
        while not until():
            self.assertLess(time.monotonic(), end, "timed out")
            self.root.update()
            time.sleep(0.01)

    def shown(self):
        steps = [s for s, (frame, _c, _v) in self.app.panels.items() if frame.winfo_manager()]
        tools = [k for k, (frame, _c, _v) in self.app.utilities.items() if frame.winfo_manager()]
        return steps, tools

    def test_screenshots_is_a_tool_outside_the_setup_steps(self):
        app, w = self.app, self.wizard_mod
        self.assertNotIn(shots.SCREENSHOTS, app.panels)
        self.assertNotIn(shots.SCREENSHOTS, app.wizard.path())
        self.assertEqual(self.shown(), ([w.CAMERA], []))
        app._open_utility(shots.SCREENSHOTS)  # the Tools sidebar action
        self.root.update_idletasks()
        self.assertEqual(self.shown(), ([], [shots.SCREENSHOTS]))
        self.assertEqual(app.back_button.cget("text"), "Back to setup")
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertTrue(app.shots_button.instate(["disabled"]))
        app._back()
        self.assertEqual(self.shown(), ([w.CAMERA], []))
        self.assertEqual(app.wizard.current, w.CAMERA)
        row = app.tool_rows[shots.SCREENSHOTS]["parts"]  # the sidebar's Tools row (hidden windows get no clicks)
        self.assertEqual(row[3].cget("text"), shots.TITLE)
        self.assertTrue(all(part.bind("<Button-1>") for part in row))
        self.assertNotIn(shots.SCREENSHOTS, app.side_rows)
        app._open_utility(shots.SCREENSHOTS)
        self.assertEqual(self.shown(), ([], [shots.SCREENSHOTS]))
        app._sidebar_clicked(w.CAMERA)
        self.assertEqual(self.shown(), ([w.CAMERA], []))

    def test_pick_convert_and_open(self):
        app = self.app
        out = self.home / "out"
        app.filedialog.askdirectory.side_effect = [str(self.card), str(out)]
        app._open_utility(shots.SCREENSHOTS)
        before = tree(self.card)
        app._pick_shots_source()
        self.wait(lambda: app.shots.busy is None and app.shots.source is not None)
        self.assertIn("2 screenshots in", app.shots_src_status.text.cget("text"))
        app._pick_shots_output()
        self.assertEqual(app.shots_out_var.get(), str(out))
        self.assertFalse(app.shots_button.instate(["disabled"]))
        self.assertTrue(app.shots_open.instate(["disabled"]))  # no folder yet
        app.shots_button.invoke()
        self.wait(lambda: app.shots.busy is None)
        self.assertEqual(app.shots_result.kind, theme.WARNING)
        self.assertIn("1 converted", app.shots_result.headline.cget("text"))
        self.assertIn("SS_00002.YCA: not converted", app.shots_log.get("1.0", "end"))
        self.assertTrue(app.shots_log_box.winfo_manager())
        self.assertTrue((out / "SS_00001.png").is_file())
        self.assertEqual(tree(self.card), before, "the card is only read")
        self.assertFalse(app.shots_open.instate(["disabled"]))
        with mock.patch.object(self.app_mod, "open_path") as opened:
            app.shots_open.invoke()
        opened.assert_called_once_with(out)
        app.shots.set_output(self.r5shots)
        app.refresh()
        self.assertTrue(app.shots_button.instate(["disabled"]))
        self.assertEqual(app.shots_out_status.text.cget("style"), f"Status.{theme.DANGER}.TLabel")


if __name__ == "__main__":
    unittest.main()
