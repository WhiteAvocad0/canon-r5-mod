"""Design tokens of the setup assistant, and a headless smoke test of every wizard step."""
import ast
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from gui import theme  # noqa: E402


class FontResolutionTests(unittest.TestCase):
    def test_first_installed_candidate_wins_case_insensitively(self):
        family = theme.resolve_family(("Cantarell", "Ubuntu", "DejaVu Sans", theme.NATIVE),
                                      ["dejavu sans", "Ubuntu", "Liberation Sans"], "sans-serif", "Helvetica")
        self.assertEqual(family, "Ubuntu")

    def test_native_family_stands_for_tks_own_font(self):
        # macOS: the system font (SF Pro) is not listed by font.families(), so it comes first as NATIVE.
        family = theme.resolve_family(theme.UI_FONTS["darwin"], ["Helvetica Neue", "Helvetica"],
                                      ".AppleSystemUIFont", "Helvetica")
        self.assertEqual(family, ".AppleSystemUIFont")

    def test_missing_native_family_falls_through_to_the_next_candidate(self):
        family = theme.resolve_family(theme.UI_FONTS["darwin"], ["Helvetica Neue"], "", "Helvetica")
        self.assertEqual(family, "Helvetica Neue")

    def test_nothing_installed_falls_back_to_native_then_last_resort(self):
        self.assertEqual(theme.resolve_family(("Segoe UI",), [], "Tahoma", "Helvetica"), "Tahoma")
        self.assertEqual(theme.resolve_family(("Segoe UI",), [], None, "Helvetica"), "Helvetica")

    def test_each_platform_prefers_its_own_ui_font(self):
        self.assertEqual(theme.UI_FONTS["darwin"][0], theme.NATIVE)
        self.assertIn("Segoe UI", theme.UI_FONTS["win32"][:2])
        for family in ("Cantarell", "Ubuntu", "DejaVu Sans"):
            self.assertIn(family, theme.UI_FONTS["linux"])
        for fonts in (*theme.UI_FONTS.values(), *theme.MONO_FONTS.values()):
            self.assertIn(theme.NATIVE, fonts)  # Tk's own font is always a fallback
        self.assertEqual(theme.resolve_family(theme.UI_FONTS["win32"], ["Arial", "Segoe UI", "Tahoma"],
                                              "Segoe UI", "Helvetica"), "Segoe UI")
        self.assertEqual(theme.resolve_family(theme.MONO_FONTS["linux"], ["Liberation Mono", "DejaVu Sans Mono"],
                                              "Courier", "Courier"), "DejaVu Sans Mono")

    def test_platform_key(self):
        self.assertEqual(theme.platform_key("darwin"), "darwin")
        self.assertEqual(theme.platform_key("win32"), "win32")
        self.assertEqual(theme.platform_key("cygwin"), "win32")
        self.assertEqual(theme.platform_key("linux"), "linux")
        self.assertEqual(theme.platform_key("freebsd14"), "linux")


class TokenTests(unittest.TestCase):
    def test_type_scale_has_a_clear_hierarchy(self):
        for base in (12.0, 13.0, 13.3, 16.0, 20.0):
            px = {token: theme.font_px(token, base) for token in theme.TYPE_SCALE}
            self.assertGreater(px["heading"], px["title"])
            self.assertGreater(px["title"], px["body"])
            self.assertEqual(px["subheading"], px["body"])
            self.assertLess(px["caption"], px["body"])
            self.assertLess(px["mono"], px["body"])
            self.assertTrue(all(v >= theme.MIN_FONT_PX for v in px.values()))
        self.assertEqual(theme.font_px("body", 13.3), 13)   # macOS
        self.assertEqual(theme.font_px("body", 12.0), 13)   # Windows 9 pt and Linux Tk: never smaller than 13 px
        self.assertEqual(theme.font_px("heading", 13.0), 22)
        self.assertEqual(theme.font_px("body", 16.0), 16)   # a larger desktop font scales the whole scale
        for name in ("heading", "title", "subheading", "eyebrow"):
            self.assertTrue(theme.TYPE_SCALE[name].bold, name)
        self.assertTrue(theme.TYPE_SCALE["mono"].mono)

    def test_native_px_reads_tk_sizes(self):
        self.assertEqual(theme.native_px(-12, 1.33), 12.0)       # negative sizes are pixels
        self.assertAlmostEqual(theme.native_px(10, 1.3335), 13.335)  # positive sizes are points
        self.assertEqual(theme.native_px(0, 1.0), theme.BODY_PX)

    def test_spacing_follows_the_8_px_grid(self):
        self.assertEqual(theme.SPACE["xs"], 4)  # the half step, only between a label and its line
        for name, value in theme.SPACE.items():
            if name != "xs":
                self.assertEqual(value % 8, 0, name)
        self.assertEqual(list(theme.SPACE.values()), sorted(theme.SPACE.values()))
        self.assertEqual(theme.BUTTON_PADDING[0] % 8, 0)

    def test_layout_tokens(self):
        self.assertEqual(theme.MIN_WINDOW, (900, 640))
        self.assertGreaterEqual(theme.DEFAULT_WINDOW[0], theme.MIN_WINDOW[0])
        self.assertGreaterEqual(theme.DEFAULT_WINDOW[1], theme.MIN_WINDOW[1])
        self.assertTrue(60 <= theme.LINE_LENGTH <= 75)
        self.assertLess(theme.SIDEBAR_WIDTH + theme.CONTENT_MAX_WIDTH, theme.DEFAULT_WINDOW[0] + 1)

    def test_palettes_are_valid_and_readable(self):
        hex_colour = re.compile(r"#[0-9A-Fa-f]{6}")
        for name, p in theme.PALETTES.items():
            with self.subTest(palette=name):
                for field, value in theme.palette_colours(p).items():
                    self.assertRegex(value, hex_colour, field)
                c = theme.contrast
                self.assertGreaterEqual(c(p.text, p.bg), 7)
                self.assertGreaterEqual(c(p.text, p.surface), 7)
                self.assertGreaterEqual(c(p.text_muted, p.bg), 4.5)
                self.assertGreaterEqual(c(p.text_muted, p.surface), 4.5)
                self.assertGreaterEqual(c(p.on_accent, p.accent), 4.5)
                self.assertGreaterEqual(c(p.accent_text, p.bg), 4.5)
                self.assertGreaterEqual(c(p.accent_text, p.accent_subtle), 4.5)
                for kind in theme.KINDS:
                    fg, bg, _ = theme.kind_colours(p, kind)
                    self.assertGreaterEqual(c(fg, bg), 4.5, kind)
                    self.assertGreaterEqual(c(p.text, bg), 7, kind)
                for status in (p.success, p.danger):
                    self.assertGreaterEqual(c(status, p.bg), 4.5)
                # Disabled text must look disabled, yet stay visible.
                self.assertLess(c(p.text_disabled, p.bg), 4.5)
                self.assertGreater(c(p.text_disabled, p.bg), 1.8)
                self.assertLess(c(p.text_disabled, p.bg), c(p.text_muted, p.bg))
        self.assertLess(theme._luminance(theme.DARK.bg), theme._luminance(theme.LIGHT.bg))

    def test_one_accent_and_status_colours_stay_distinct(self):
        for p in theme.PALETTES.values():
            statuses = {p.success, p.warning, p.danger}
            self.assertEqual(len(statuses), 3)
            self.assertNotIn(p.accent, statuses)

    def test_theme_module_does_not_import_tk(self):
        tree = ast.parse((ROOT / "tools" / "gui" / "theme.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("tkinter", imported)


class AppearanceTests(unittest.TestCase):
    @staticmethod
    def runner(stdout="", exc=None):
        def run(*_args, **_kwargs):
            if exc:
                raise exc
            return subprocess.CompletedProcess([], 0, stdout, "")
        return run

    def test_environment_override(self):
        for value in ("dark", "light", " Dark "):
            self.assertEqual(theme.detect_appearance("darwin", environ={theme.APPEARANCE_ENV: value},
                                                     run=self.runner(exc=AssertionError("not called"))),
                             value.strip().lower())

    def test_macos_reads_apple_interface_style(self):
        self.assertEqual(theme.detect_appearance("darwin", environ={}, run=self.runner("Dark\n")), "dark")
        # In light mode the key does not exist: empty output and a non-zero exit.
        self.assertEqual(theme.detect_appearance("darwin", environ={}, run=self.runner("")), "light")
        self.assertEqual(theme.detect_appearance("darwin", environ={}, run=self.runner(exc=OSError("no defaults"))),
                         "light")
        self.assertEqual(theme.detect_appearance(
            "darwin", environ={}, run=self.runner(exc=subprocess.TimeoutExpired("defaults", 2))), "light")

    def test_linux_reads_gtk_theme_or_gsettings(self):
        self.assertEqual(theme.detect_appearance("linux", environ={"GTK_THEME": "Adwaita:dark"},
                                                 run=self.runner(exc=AssertionError("not called"))), "dark")
        self.assertEqual(theme.detect_appearance("linux", environ={}, run=self.runner("'prefer-dark'\n")), "dark")
        self.assertEqual(theme.detect_appearance("linux", environ={}, run=self.runner("'default'\n")), "light")
        self.assertEqual(theme.detect_appearance("linux", environ={}, run=self.runner(exc=FileNotFoundError())),
                         "light")

    @unittest.skipIf(sys.platform.startswith("win"), "winreg exists on Windows")
    def test_windows_without_registry_falls_back_to_light(self):
        self.assertEqual(theme.detect_appearance("win32", environ={}), "light")


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


class WindowSmokeTests(unittest.TestCase):
    """Builds the real window hidden, with saved progress in a temporary folder and no toolchain search."""

    def setUp(self):
        self.root = make_root()
        if self.root is None:
            self.skipTest("Tk is not available here (no tkinter or no display)")
        import tkinter as tk
        from tkinter import filedialog, font as tkfont, messagebox, ttk
        from gui import app as app_mod, checks, wizard
        self.tk, self.app_mod, self.wizard_mod = tk, app_mod, wizard
        self.tmp = tempfile.TemporaryDirectory()
        patches = [mock.patch.object(wizard, "DEFAULT_STATE_FILE", Path(self.tmp.name) / "wizard.json"),
                   mock.patch.object(checks, "detect_toolchain", lambda: (None, None, []))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.modules = (tk, ttk, filedialog, messagebox, tkfont)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._destroy)

    def _destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def build(self, appearance):
        app = self.app_mod.App(*self.modules, root=self.root, appearance=appearance)
        self.root.update_idletasks()
        return app

    def visible_step(self, app):
        return [step for step, (frame, _check, _canvas) in app.panels.items() if frame.winfo_manager()]

    def test_every_step_builds_in_light_and_dark(self):
        for appearance in ("light", "dark"):
            with self.subTest(appearance=appearance):
                if appearance == "dark":
                    self._destroy()
                    self.root = make_root()
                app = self.build(appearance)
                self.assertEqual(app.theme.appearance, appearance)
                self.assertEqual(self.root.cget("background"), theme.PALETTES[appearance].bg)
                self.assertTrue(app.theme.families["ui"] and app.theme.families["mono"])
                sizes = {k: abs(int(f.actual("size"))) for k, f in app.theme.fonts.items()}
                self.assertGreater(sizes["heading"], sizes["body"])
                self.assertEqual(set(app.panels), {s.id for s in self.wizard_mod.STEPS})
                for step in self.wizard_mod.STEPS:
                    app.wizard.saved.current = step.id
                    app.refresh()
                    self.root.update_idletasks()
                    self.assertEqual(self.visible_step(app), [step.id])
                    packed = [s for s, row in app.side_rows.items() if row["parts"][0].winfo_manager()]
                    self.assertEqual(packed, app.wizard.path())

    def test_gating_and_controls_are_unchanged(self):
        app = self.build("light")
        w = self.wizard_mod
        self.assertEqual(app.wizard.current, w.CAMERA)
        self.assertTrue(app.next_button.instate(["disabled"]))
        self.assertTrue(app.back_button.instate(["disabled"]))
        app._sidebar_clicked(w.BUILD)  # blocked: the sidebar does not jump there
        self.assertEqual(app.wizard.current, w.CAMERA)
        app.camera_var.set(True)
        app.wizard.set_camera_confirmed(True)
        app.refresh()
        self.assertFalse(app.next_button.instate(["disabled"]))
        app._next()
        self.assertEqual(app.wizard.current, w.ROM)
        self.assertEqual(self.visible_step(app), [w.ROM])
        self.assertEqual(app._side_focus(w.ROM, 1), "break")
        self.assertTrue(app.update_button.instate(["disabled"]))  # one-time steps are not done
        self.assertTrue(app.bootdisk_box.instate(["disabled"]))   # needs the acknowledgement first
        app.bootdisk_ack.set(True)
        app.refresh()
        self.assertFalse(app.bootdisk_box.instate(["disabled"]))
        self.assertTrue(app.build_button.instate(["disabled"]))
        self.assertTrue(app.copy_button.instate(["disabled"]))
        self.assertTrue(app.card_confirm_box.instate(["disabled"]))
        for button in (app.prep_button, app.prep_undo, app.prep_restore):
            self.assertTrue(button.instate(["disabled"]))
        self.assertEqual(app.blockers.headline.cget("text"), "Before you can build:")

    def test_results_busy_states_and_details_render(self):
        from cardprep import workflow
        app = self.build("light")
        self.assertFalse(app.prep_title.frame.winfo_manager())  # no result yet
        app.prep.finished(workflow.Report(True, "Done and verified.", ["Backup: /tmp/backup", "Sectors 12, 23, 0, 11."]))
        app.refresh()
        self.assertEqual(app.prep_title.kind, theme.SUCCESS)
        self.assertIn("Backup: /tmp/backup", app.prep_log.get("1.0", "end"))
        self.assertTrue(app.prep_log_box.winfo_manager())
        app.prep.failed("Not prepared: the card was removed.")
        app.refresh()
        self.assertEqual(app.prep_title.headline.cget("text"), "Not prepared: the card was removed.")
        self.assertEqual(app.prep_title.kind, theme.DANGER)
        self.assertFalse(app.prep_log_box.winfo_manager())
        app.prep.busy = "Looking for SD cards..."
        app.refresh()
        self.assertTrue(app.prep_busy.running)
        self.assertIn("Wait: Looking for SD cards...", app.prep_blockers.cget("text"))
        app.prep.busy = None
        app.refresh()
        self.assertFalse(app.prep_busy.running)
        app.state.building = True
        app.refresh()
        self.assertTrue(app.build_busy.running)
        app.state.building = False
        app.refresh()
        self.assertFalse(app.build_busy.running)
        app.result.set(theme.DANGER, "Build refused or failed", "firmware signature mismatch")
        app.copy_result.set(theme.SUCCESS, "Done", "Copied and verified AUTOEXEC.BIN.")
        app.card_report.set(theme.WARNING, "Check the warnings before you copy", "Warning: no DCIM folder.")
        app.copy_result.clear()
        self.assertFalse(app.copy_result.frame.winfo_manager())
        frame = app.panels[self.wizard_mod.CAMERA][0]
        toggles = [w for w in self._descendants(frame) if w.winfo_class() == "TButton"
                   and "Details" in str(w.cget("text"))]
        self.assertEqual(len(toggles), 1)
        toggles[0].invoke()
        self.assertIn("▾", toggles[0].cget("text"))
        toggles[0].invoke()
        self.assertIn("▸", toggles[0].cget("text"))
        app._set_column(500)  # a narrow window re-wraps the text
        self.assertTrue(all(int(str(label.cget("wraplength"))) <= 500 for label, _i, _f in app._wrapped))

    def _descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self._descendants(child)


if __name__ == "__main__":
    unittest.main()
