"""Design tokens of R5 Mod Assistant, and a headless smoke test of every wizard step."""
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
        self.assertEqual(theme.font_px("heading", 13.0), 24)
        self.assertEqual(theme.font_px("body", 16.0), 16)   # a larger desktop font scales the whole scale
        for name in ("heading", "title", "subheading"):
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
        self.assertEqual(theme.MIN_WINDOW, (640, 480))
        self.assertGreaterEqual(theme.DEFAULT_WINDOW[0], theme.MIN_WINDOW[0])
        self.assertGreaterEqual(theme.DEFAULT_WINDOW[1], theme.MIN_WINDOW[1])
        self.assertTrue(60 <= theme.LINE_LENGTH <= 75)

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
                for status in (p.warning, p.danger):
                    self.assertGreaterEqual(c(status, p.bg), 4.5)
                # Disabled text must look disabled, yet stay visible.
                self.assertLess(c(p.text_disabled, p.bg), 4.5)
                self.assertGreater(c(p.text_disabled, p.bg), 1.8)
                self.assertLess(c(p.text_disabled, p.bg), c(p.text_muted, p.bg))
        self.assertLess(theme._luminance(theme.DARK.bg), theme._luminance(theme.LIGHT.bg))

    def test_status_panels_share_a_surface_and_focus_is_visible(self):
        for p in theme.PALETTES.values():
            for kind in theme.KINDS:
                self.assertEqual(theme.kind_colours(p, kind)[1:], (p.surface, p.border))
            for surface in (p.bg, p.surface, p.accent_subtle):
                self.assertGreaterEqual(theme.contrast(p.focus, surface), 3)
            self.assertNotEqual(p.warning, p.danger)

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
                app._open_utility("screenshots")  # the Tools section's utility, outside the steps
                self.root.update_idletasks()
                self.assertEqual(self.visible_step(app), [])
                self.assertTrue(app.utilities["screenshots"][0].winfo_manager())

    def test_language_switch_preserves_the_current_session_and_reopens_in_the_saved_language(self):
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
        self._destroy()
        self.assertNotIn(poll_timer, self.root.tk.call("after", "info"))
        self.root = make_root()
        reopened = self.build("light")
        self.assertEqual(reopened.language_menu.get(), "Español")
        self.assertEqual(reopened.wizard.current, self.wizard_mod.FEATURES)
        self.assertEqual(reopened.state.options["record_indicator"], "ding")
        self.assertEqual(reopened.next_button.cget("text"), "Siguiente")
        reopened.language_menu.current(0)
        reopened.language_menu.event_generate("<<ComboboxSelected>>")
        self.assertEqual(reopened.next_button.cget("text"), "Next")

    def test_compact_navigation_and_translated_controls_fit_after_resizing(self):
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

    def test_content_and_controls_reflow_in_both_directions(self):
        from tkinter import ttk
        from gui import shots
        app = self.build("light")
        self.root.deiconify()
        # Keep the long status in the model so a background refresh renders the same message.
        source = shots.Source(Path(self.tmp.name).joinpath(*(["Folder with screenshots"] * 30)), None, (), False)
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

    def test_flow_guidance(self):
        app = self.build("light")
        w = self.wizard_mod
        # A locked step says why in the footer instead of doing nothing.
        app._sidebar_clicked(w.BUILD)
        self.assertEqual(app.wizard.current, w.CAMERA)
        self.assertIn("Step 6 is locked", app.nav_note.cget("text"))
        self.assertEqual(str(app.nav_note.cget("style")), "Hint.Caption.TLabel")
        self.assertEqual(app.side_rows[w.BUILD]["parts"][4].cget("text"), "Locked")
        app.wizard.set_camera_confirmed(True)
        app._next()
        self.assertNotIn("locked", app.nav_note.cget("text"))
        # The short path is offered from the sidebar once steps 1 to 3 are done, and can be left again.
        self.assertFalse(app.mode_link.winfo_manager())
        app.wizard.set_bootdisk("confirmed")
        app.state.set_rom(Path(self.tmp.name) / "gang100.bin")
        app.state.finish("rom", app.state.rom_key(), True, "ROM accepted")
        app.refresh()
        self.assertTrue(app.mode_link.winfo_manager())
        app.mode_link.invoke()
        self.assertEqual(app.wizard.saved.mode, "update")
        self.assertEqual(app.wizard.current, w.FEATURES)
        self.assertEqual(app.mode_link.cget("text"), "Show all nine steps")
        app.mode_link.invoke()
        self.assertEqual(app.wizard.saved.mode, "full")
        self.assertEqual(len([r for r in app.side_rows.values() if r["parts"][0].winfo_manager()]), 9)
        # Features: XPan hides the WAV pickers, and Next confirms the choice.
        app.state.set_profile("xpan-crop")
        app.refresh()
        self.root.update_idletasks()
        self.assertFalse(any(box.winfo_manager() for *_x, box in app.input_widgets.values()))
        self.assertTrue(all(box.winfo_manager() for *_x, box in app.option_widgets.values()))
        app.state.set_profile("shutter-feedback")
        app.refresh()
        self.assertTrue(all(box.winfo_manager() for *_x, box in app.input_widgets.values()))
        self.assertFalse(any(box.winfo_manager() for *_x, box in app.option_widgets.values()))
        app.state.set_profile("xpan-crop")
        app.refresh()
        self.assertFalse(app.next_button.instate(["disabled"]))
        app._next()
        self.assertTrue(app.wizard.saved.features_confirmed)
        # The build log is folded away until asked for.
        self.assertFalse(app.log_box.winfo_manager())
        app.log_toggle.invoke()
        self.assertTrue(app.log_box.winfo_manager())
        # The copy step's warning checkbox appears only with warnings.
        self.assertFalse(app.card_confirm_box.winfo_manager())

    def test_bootdisk_camera_handoff_survives_reopening(self):
        app = self.build("light")
        w = self.wizard_mod
        app.wizard.goto(w.BOOTDISK)
        app._bootdisk_page("camera_disable")
        self._destroy()
        self.root = make_root()
        reopened = self.build("dark")
        self.assertTrue(reopened.bd_pages["camera"].winfo_manager())
        self.assertIn("disable", reopened.bd_camera_title.cget("text"))
        self.assertFalse(reopened.bd_pages["verify"].winfo_manager())
        self.assertTrue(reopened.next_button.instate(["disabled"]))
        reopened._bootdisk_page("verify")
        self.assertTrue(reopened.bd_pages["verify"].winfo_manager())
        self.assertFalse(reopened.bd_pages["setup"].winfo_manager())

    def test_returned_card_shortcut_and_resume(self):
        app = self.build("light")
        app.wizard.goto(self.wizard_mod.ROM)
        app.refresh()
        start = app.rom_pages["prepare"]
        self.assertTrue(start.winfo_manager())
        self.assertFalse(app.rom_pages["return"].winfo_manager())
        shortcut = next(button for button in self._descendants(start)
                        if button.winfo_class() == "TButton" and "returning" in button.cget("text"))
        shortcut.invoke()
        self.assertFalse(start.winfo_manager())
        self.assertTrue(app.rom_pages["return"].winfo_manager())
        self.assertTrue(app.next_button.instate(["disabled"]))
        self._destroy()
        self.root = make_root()
        reopened = self.build("light")
        self.assertTrue(reopened.rom_pages["return"].winfo_manager())
        self.assertTrue(reopened.next_button.instate(["disabled"]))

    def test_card_size_is_highlighted(self):
        app = self.build("light")
        w = self.wizard_mod
        caption = lambda step: app.side_rows[step]["parts"][4].cget("text")
        self.assertIn("SD card ≤ 32 GB", caption(w.ROM))
        self.assertIn("Any SD card", caption(w.BOOTCARD))
        self.assertEqual(caption(w.FEATURES), "To do")
        badges = {str(widget.cget("text")) for widget in self._descendants(app.panels[w.ROM][0])
                  if widget.winfo_class() == "TLabel" and "CardBadge" in str(widget.cget("style"))}
        self.assertEqual(badges, {"SD card ≤ 32 GB"})

    def test_finish_on_the_last_step(self):
        app = self.build("light")
        w = self.wizard_mod
        app.messagebox = mock.Mock()
        app.wizard.saved.current = w.TEST
        app.refresh()
        self.assertEqual(app.next_button.cget("text"), "Finish")
        self.assertTrue(app.next_button.instate(["disabled"]), "the test step is locked until a copy")
        with mock.patch.object(app.wizard, "status", return_value=w.StepStatus(w.DONE, "Checklist complete.")):
            app.refresh()
            self.assertFalse(app.next_button.instate(["disabled"]))
            app._next()
        app.messagebox.showinfo.assert_called_once()
        self.assertEqual(app.wizard.current, w.TEST)

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


class SelfTestModeTests(unittest.TestCase):
    """The packaged app's --self-test (gui.app.self_test), run here from the checkout."""

    def test_self_test_passes_and_leaves_saved_progress_alone(self):
        root = make_root()
        if root is None:
            self.skipTest("Tk is not available here (no tkinter or no display)")
        root.destroy()
        from gui import app as app_mod, checks, wizard
        before = (wizard.DEFAULT_STATE_FILE, checks.detect_toolchain)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(wizard, "DEFAULT_STATE_FILE", Path(tmp) / "own" / "wizard.json"), \
                mock.patch("sys.stdout") as out:
            self.assertEqual(app_mod.self_test(), 0)
            self.assertFalse((Path(tmp) / "own").exists())
            self.assertEqual(wizard.DEFAULT_STATE_FILE, Path(tmp) / "own" / "wizard.json")
        self.assertEqual((wizard.DEFAULT_STATE_FILE, checks.detect_toolchain), before)
        printed = "".join(call.args[0] for call in out.write.call_args_list)
        self.assertIn("self-test passed", printed)
        self.assertNotIn("FAIL", printed)


if __name__ == "__main__":
    unittest.main()
