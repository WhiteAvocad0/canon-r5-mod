"""Shared Tk fixture and isolated visible-window scenarios launched by test_gui_theme.py."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from gui import theme


def make_root():
    """A hidden Tk root, or None when this Python has no Tk or there is no display."""
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


class WindowFixture(unittest.TestCase):
    """Builds the real window hidden, with saved progress in a temporary folder and no toolchain search."""

    def setUp(self):
        self.root = make_root()
        if self.root is None:
            self.skipTest("Tk is not available here (no tkinter or no display)")
        import tkinter as tk
        from tkinter import filedialog, font as tkfont, messagebox, ttk
        from gui import app as app_mod, checks, wizard
        self.tk, self.app_mod, self.wizard_mod = tk, app_mod, wizard
        if not hasattr(self, "work_dir"):
            self.work_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        patches = [mock.patch.object(wizard, "DEFAULT_STATE_FILE", self.work_dir / "wizard.json"),
                   mock.patch.object(checks, "detect_toolchain", lambda: (None, None, []))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.modules = (tk, ttk, filedialog, messagebox, tkfont)
        self.addCleanup(self._destroy)

    def _destroy(self):
        root, self.root = self.root, None
        if root is not None:
            root.destroy()

    def build(self, appearance):
        app = self.app_mod.App(*self.modules, root=self.root, appearance=appearance)
        self.root.update_idletasks()
        return app

    def _descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self._descendants(child)


class VisibleWindowScenario(WindowFixture):
    def __init__(self, method, directory):
        super().__init__(method)
        self.work_dir = directory

    def switch_language(self):
        from gui.i18n import LANGUAGES
        from messages import message
        app = self.build("light")
        self.assertEqual(app.language_menu.get(), "English")
        app.wizard.goto(self.wizard_mod.FEATURES)
        app.profile_var.set("xpan-crop")
        app._profile_changed()
        app.bootdisk_ack.set(True)
        app._append_log("raw build output {untouched}")
        app.result.set(theme.SUCCESS, "Built and verified", message("Folder: {root}", root="/card/{Next}"))
        app._open_utility("screenshots")
        self.root.deiconify()
        self.root.geometry("%dx%d" % theme.MIN_WINDOW)
        with mock.patch.object(app, "_background", side_effect=AssertionError("unexpected new operation")):
            for language, label, action in (("zh_CN", "语言", "转换为 PNG"), ("es", "Idioma", "Convertir a PNG")):
                app.language_menu.current(list(LANGUAGES).index(language))
                app.language_menu.event_generate("<<ComboboxSelected>>")
                self.root.update_idletasks()
                self.assertEqual(app.language_label.cget("text"), label)
                self.assertEqual(app.shots_button.cget("text"), action)
                self.assertTrue(app.language_menu.winfo_ismapped())
                right = self.root.winfo_rootx() + self.root.winfo_width()
                self.assertLess(right - app.language_menu.winfo_rootx() - app.language_menu.winfo_width(), 50)
                self.assertLess(app.language_menu.winfo_rooty() - self.root.winfo_rooty(), 30)
                self.assertEqual(app.utility, "screenshots")
                self.assertEqual(app.wizard.current, self.wizard_mod.FEATURES)
                self.assertTrue(app.bootdisk_ack.get())
                self.assertIn("raw build output {untouched}", app.log.get("1.0", "end"))
                self.assertIn("/card/{Next}", app.result.body.cget("text"))
                self.assertEqual(app.result.headline.cget("text"), app.words.render("Built and verified"))
                with mock.patch.object(app.messagebox, "showinfo") as dialog:
                    app._finish()
                    title, content = dialog.call_args.args
                    self.assertEqual(title, app.words.render("All done"))
                    self.assertNotIn("Setup is complete.", content)
        app._back()
        combo = app.option_widgets["record_indicator"][0]
        combo.current(1)
        combo.event_generate("<<ComboboxSelected>>")
        self.assertEqual(app.state.options["record_indicator"], "ding")
        self.assertEqual(combo.get(), app.words.render("Ding: struck-bar chime, 250 ms"))
        poll_timer = app._poll_timer
        root = self.root
        self._destroy()
        self.assertNotIn(poll_timer, root.tk.call("after", "info"))

    def reopen_session(self):
        reopened = self.build("light")
        self.assertEqual(reopened.language_menu.get(), "Español")
        self.assertEqual(reopened.wizard.current, self.wizard_mod.FEATURES)
        self.assertEqual(reopened.state.options["record_indicator"], "ding")
        self.assertEqual(reopened.next_button.cget("text"), "Siguiente")
        self.root.deiconify()
        self.root.update()
        self.assertTrue(reopened.language_menu.winfo_ismapped())
        reopened.language_menu.current(0)
        reopened.language_menu.event_generate("<<ComboboxSelected>>")
        self.root.update()
        self.assertEqual(reopened.words.language, "en")
        self.assertEqual(reopened.next_button.cget("text"), "Next")

    def compact_navigation(self):
        from tkinter import ttk
        app = self.build("light")
        self.root.deiconify()
        for language in ("Español", "简体中文"):
            app.language_menu.set(language)
            app.language_menu.event_generate("<<ComboboxSelected>>")
            for size in ((640, 480), (800, 600), theme.DEFAULT_WINDOW):
                self.root.geometry("%dx%d" % size)
                app.wizard.saved.current = self.wizard_mod.BOOTCARD
                app.utility = None
                app.refresh()
                undo = app.prep_undo.master.master
                if not undo.winfo_manager():
                    toggle = next(w for w in undo.master.winfo_children()
                                  if isinstance(w, ttk.Button) and w.cget("text").endswith(
                                      app.words.render("Restore a card backup")))
                    toggle.invoke()
                self.root.update_idletasks()
                # The window manager may limit the requested size to its usable desktop.
                shell = app.sidebar.master
                viewport = shell.winfo_width(), shell.winfo_height()
                min_width, min_height = theme.SIDEBAR_MIN_WINDOW
                compact = viewport[0] < min_width or viewport[1] < min_height
                with self.subTest(language=language, requested=size, viewport=viewport):
                    self.assertEqual(bool(app.step_menu_button.winfo_ismapped()), compact)
                    self.assertEqual(bool(app.sidebar.winfo_ismapped()), not compact)
                    for button in (app.prep_restore, app.prep_undo, app.language_menu):
                        self.assertTrue(button.winfo_ismapped())
                        self.assertGreaterEqual(button.winfo_width() + 1, button.winfo_reqwidth())
                        self.assertGreaterEqual(button.winfo_rootx(), shell.winfo_rootx())
                        self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                             shell.winfo_rootx() + viewport[0])
                    if compact:
                        app._populate_step_menu()
                        app.step_menu.invoke("end")
                        self.assertEqual(app.utility, "screenshots")
                        self.assertTrue(app.language_menu.winfo_ismapped())

    def resize_content(self):
        from tkinter import ttk
        from gui import shots
        app = self.build("light")
        self.root.deiconify()
        # Keep the long status in the model so a background refresh renders the same message.
        source = shots.Source(self.work_dir.joinpath(*(["Folder with screenshots"] * 30)), None, (), False)
        app.shots.set_source(source)
        app._open_utility("screenshots")
        frame = app.utilities["screenshots"][0]
        folder_field = next(w for w in self._descendants(frame) if isinstance(w, ttk.Entry))
        status = app.shots_src_status
        self.assertEqual(status.text.cget("text"), source.describe())

        def geometry(width, height):
            self.root.geometry(f"{width}x{height}")
            self.root.update_idletasks()
            return (app._inners["screenshots"].winfo_width(), folder_field.winfo_width(),
                    int(str(status.text.cget("wraplength"))), status.text.winfo_reqheight())

        def assert_grew(before, after):
            self.assertTrue(all(a > b for b, a in zip(before[:3], after[:3])), (before, after))
            self.assertLessEqual(after[3], before[3])

        # The window manager's usable width can be smaller than the physical display.
        geometry(1440, 780)
        wide_width = self.root.winfo_width()
        compact = geometry(640, 480)
        larger_compact = geometry(min(800, wide_width), 600)
        if wide_width > 640:
            assert_grew(compact, larger_compact)
            if wide_width >= 800:
                self.assertLess(larger_compact[3], compact[3])
        desktop_width = max(theme.SIDEBAR_MIN_WINDOW[0], min(1120, wide_width - 220))
        if wide_width > desktop_width:
            desktop = geometry(desktop_width, 780)
            wide = geometry(wide_width, 780)
            assert_grew(desktop, wide)
            if wide_width >= theme.DEFAULT_WINDOW[0]:
                self.assertLess(wide[3], compact[3])
            self.assertEqual(geometry(desktop_width, 780), desktop)
        self.assertEqual(geometry(640, 480), compact)


if __name__ == "__main__":
    scenario, directory = sys.argv[1:]
    case = VisibleWindowScenario(scenario, Path(directory))
    result = unittest.TestResult()
    case.run(result)
    for test, traceback in result.errors + result.failures:
        print(f"{test}\n{traceback}", file=sys.stderr)
    if not result.wasSuccessful():
        sys.exit(1)
    if result.skipped:
        print(result.skipped[0][1], file=sys.stderr)
        sys.exit(77)
