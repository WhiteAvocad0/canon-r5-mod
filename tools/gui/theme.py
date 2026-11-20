"""Design tokens and the ttk style of the setup assistant.

The tokens (fonts, type scale, spacing, palettes, window sizes) are plain data with no
Tkinter import, so they can be tested headless and reused by a later PySide6 port.
``apply()`` turns them into ttk styles; the caller passes in the ``ttk`` and
font modules of the standard GUI toolkit, so this module never imports Tk itself.

Design rules the window follows:

- one accent colour for actions, focus and the current step; the success, warning and
  danger colours are used only for statuses and warning callouts;
- an 8-px spacing grid (4 px only for a label and the line it belongs to);
- body text wraps at about ``LINE_LENGTH`` characters, everything left-aligned;
- the platform's own UI font (SF Pro on macOS, Segoe UI on Windows, Cantarell, Ubuntu
  or DejaVu Sans on Linux), resolved at runtime with fallbacks.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import os
import subprocess
import sys

# -- fonts ---------------------------------------------------------------------------

NATIVE = "<native>"  # the family Tk itself uses for TkDefaultFont / TkFixedFont

UI_FONTS = {
    # On macOS Tk's TkDefaultFont is the system font (.AppleSystemUIFont, SF Pro), which
    # font.families() does not list, so the native family comes first there.
    "darwin": (NATIVE, "SF Pro Text", "SF Pro", "Helvetica Neue", "Helvetica"),
    "win32": ("Segoe UI Variable Text", "Segoe UI", NATIVE, "Tahoma", "Arial"),
    "linux": ("Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans", "Liberation Sans", NATIVE),
}
MONO_FONTS = {
    "darwin": ("SF Mono", "Menlo", "Monaco", NATIVE),
    "win32": ("Cascadia Mono", "Consolas", "Courier New", NATIVE),
    "linux": ("DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono", "Noto Sans Mono", NATIVE),
}
LAST_RESORT = {"ui": "Helvetica", "mono": "Courier"}  # Tk maps these on every platform


@dataclass(frozen=True)
class TextStyle:
    size: int  # pixels at a 13-px body; scaled to the platform's own body size
    bold: bool = False
    mono: bool = False


BODY_PX = 13
TYPE_SCALE = {
    "heading": TextStyle(22, bold=True),     # the step's name
    "title": TextStyle(15, bold=True),       # the app's name in the sidebar
    "subheading": TextStyle(13, bold=True),  # "What you do", section names, callout headlines
    "body": TextStyle(13),
    "caption": TextStyle(11),                # statuses, hints, details
    "eyebrow": TextStyle(11, bold=True),     # "STEP 4", badges
    "mono": TextStyle(12, mono=True),        # paths, hashes, logs
}
MIN_FONT_PX = 9


def platform_key(platform: str | None = None) -> str:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return "darwin"
    if platform.startswith("win") or platform == "cygwin":
        return "win32"
    return "linux"


def resolve_family(candidates, available, native: str | None, last_resort: str) -> str:
    """The first candidate that is installed; ``NATIVE`` stands for Tk's own family."""
    installed = {name.lower(): name for name in available}
    for candidate in candidates:
        if candidate == NATIVE:
            if native:
                return native
            continue
        if candidate.lower() in installed:
            return installed[candidate.lower()]
    return native or last_resort


def native_px(size: int, scaling: float) -> float:
    """Tk font sizes are points when positive and pixels when negative."""
    if size < 0:
        return float(-size)
    if size == 0:
        return float(BODY_PX)
    return size * scaling


def font_px(token: str, base_px: float) -> int:
    """Pixel size of a type-scale token; never smaller than the platform's own body size."""
    scale = max(base_px, BODY_PX) / BODY_PX
    return max(MIN_FONT_PX, round(TYPE_SCALE[token].size * scale))


# -- spacing and layout ----------------------------------------------------------------

SPACE = {"xs": 4, "s": 8, "m": 16, "l": 24, "xl": 32, "xxl": 48}
LINE_LENGTH = 72          # characters per line of body text
CONTENT_MAX_WIDTH = 680   # the step column never grows wider than this (px)
SIDEBAR_WIDTH = 272
MIN_WINDOW = (900, 640)
DEFAULT_WINDOW = (1120, 780)
BUTTON_PADDING = (16, 8)
FIELD_PADDING = (8, 6)

# -- colour ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Palette:
    bg: str              # the step area
    surface: str         # sidebar, details, log and read-only fields
    hover: str           # hovered sidebar row and secondary button
    border: str
    border_strong: str   # field and button outlines
    text: str
    text_muted: str
    text_disabled: str
    accent: str          # primary buttons, current step, focus
    accent_hover: str
    accent_pressed: str
    accent_subtle: str   # current step background, info callouts
    accent_text: str     # links and the step eyebrow on bg
    on_accent: str       # text on accent
    success: str
    success_bg: str
    success_border: str
    warning: str
    warning_bg: str
    warning_border: str
    danger: str
    danger_bg: str
    danger_border: str
    field: str
    selection: str


LIGHT = Palette(
    bg="#FFFFFF", surface="#F5F6F8", hover="#EBEDF0", border="#E1E4E8", border_strong="#AEB4BD",
    text="#1C1E21", text_muted="#5B636D", text_disabled="#A0A6AE",
    accent="#2456D3", accent_hover="#1D49B6", accent_pressed="#183D99", accent_subtle="#EAF0FC",
    accent_text="#2456D3", on_accent="#FFFFFF",
    success="#1B7A3A", success_bg="#EAF5ED", success_border="#B6DCC2",
    warning="#855000", warning_bg="#FFF5DE", warning_border="#EDCB80",
    danger="#B3261E", danger_bg="#FCEEEC", danger_border="#F0BFBA",
    field="#FFFFFF", selection="#CCDBFA",
)
DARK = Palette(
    bg="#1C1D20", surface="#242529", hover="#2E3035", border="#36393F", border_strong="#4C5058",
    text="#E8EAED", text_muted="#A3AAB3", text_disabled="#61666F",
    accent="#3567D8", accent_hover="#4676E2", accent_pressed="#2B58BF", accent_subtle="#1F2C47",
    accent_text="#8BADF7", on_accent="#FFFFFF",
    success="#62C482", success_bg="#1B2D22", success_border="#2D5A3B",
    warning="#E9B54E", warning_bg="#322817", warning_border="#5E4A21",
    danger="#F2837A", danger_bg="#38201F", danger_border="#6B3532",
    field="#17181B", selection="#2E4877",
)
PALETTES = {"light": LIGHT, "dark": DARK}

# Kinds of status a line, a callout or a sidebar mark can show.
NEUTRAL, INFO, SUCCESS, WARNING, DANGER, BUSY = "neutral", "info", "success", "warning", "danger", "busy"
KINDS = (NEUTRAL, INFO, SUCCESS, WARNING, DANGER, BUSY)
GLYPHS = {NEUTRAL: "•", INFO: "i", SUCCESS: "✓", WARNING: "!", DANGER: "!", BUSY: "…"}


def kind_colours(p: Palette, kind: str) -> tuple[str, str, str]:
    """(foreground, background, border) of a status kind."""
    return {
        NEUTRAL: (p.text_muted, p.surface, p.border),
        INFO: (p.accent_text, p.accent_subtle, p.accent_subtle),
        SUCCESS: (p.success, p.success_bg, p.success_border),
        WARNING: (p.warning, p.warning_bg, p.warning_border),
        DANGER: (p.danger, p.danger_bg, p.danger_border),
        BUSY: (p.text_muted, p.surface, p.border),
    }[kind]


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #RRGGBB colours."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def palette_colours(p: Palette) -> dict[str, str]:
    return {f.name: getattr(p, f.name) for f in fields(p)}


# -- light or dark ----------------------------------------------------------------------

APPEARANCE_ENV = "R5_ASSISTANT_APPEARANCE"  # "light" or "dark" overrides the detection


def detect_appearance(platform: str | None = None, *, environ=None, run=subprocess.run) -> str:
    """'dark' when the desktop asks for dark mode and we can tell; otherwise 'light'."""
    environ = os.environ if environ is None else environ
    forced = environ.get(APPEARANCE_ENV, "").strip().lower()
    if forced in PALETTES:
        return forced
    key = platform_key(platform)
    try:
        if key == "darwin":
            out = run(["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"],
                      capture_output=True, text=True, timeout=2, check=False)
            return "dark" if out.stdout.strip().lower() == "dark" else "light"
        if key == "win32":
            import winreg  # noqa: PLC0415 - Windows only

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as handle:
                value, _ = winreg.QueryValueEx(handle, "AppsUseLightTheme")
            return "dark" if value == 0 else "light"
        if environ.get("GTK_THEME", "").lower().endswith(":dark"):
            return "dark"
        out = run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                  capture_output=True, text=True, timeout=2, check=False)
        return "dark" if "dark" in out.stdout.lower() else "light"
    except (OSError, ValueError, subprocess.SubprocessError, ImportError):
        return "light"


# -- ttk ------------------------------------------------------------------------------------


@dataclass
class Theme:
    appearance: str
    palette: Palette
    fonts: dict          # token -> Tk font object
    families: dict       # "ui"/"mono" -> resolved family
    text_wrap: int       # px for LINE_LENGTH characters of body text

    def colours(self, kind: str) -> tuple[str, str, str]:
        return kind_colours(self.palette, kind)


def _flat(style, name, colour, **extra):
    """Clam draws 3-D bevels from light/dark colours; set them flat."""
    style.configure(name, lightcolor=colour, darkcolor=colour, **extra)


def apply(root, ttk, tkfont, *, appearance: str | None = None, platform: str | None = None) -> Theme:
    """Configure fonts and ttk styles on ``root`` and return the resolved theme."""
    appearance = appearance or detect_appearance(platform)
    p = PALETTES[appearance]
    key = platform_key(platform)
    scaling = float(root.tk.call("tk", "scaling"))
    default, fixed = tkfont.nametofont("TkDefaultFont"), tkfont.nametofont("TkFixedFont")
    available = tkfont.families(root)
    families = {
        "ui": resolve_family(UI_FONTS[key], available, default.actual("family"), LAST_RESORT["ui"]),
        "mono": resolve_family(MONO_FONTS[key], available, fixed.actual("family"), LAST_RESORT["mono"]),
    }
    base = native_px(int(default.actual("size")), scaling)
    fonts = {}
    for token, ts in TYPE_SCALE.items():
        fonts[token] = tkfont.Font(root,
                                   family=families["mono" if ts.mono else "ui"],
                                   size=-font_px(token, base), weight="bold" if ts.bold else "normal")
    for name, token in (("TkDefaultFont", "body"), ("TkTextFont", "body"), ("TkMenuFont", "body"),
                        ("TkFixedFont", "mono")):
        tkfont.nametofont(name).configure(**{k: fonts[token].cget(k) for k in ("family", "size", "weight")})
    sample = "the quick brown fox jumps over a lazy dog "
    text_wrap = round(fonts["body"].measure(sample) / len(sample) * LINE_LENGTH)

    style = ttk.Style(root)
    style.theme_use("clam")  # the one built-in theme whose colours, borders and padding all follow the style
    root.configure(background=p.bg)
    style.configure(".", background=p.bg, foreground=p.text, font=fonts["body"], bordercolor=p.border,
                    lightcolor=p.bg, darkcolor=p.bg, troughcolor=p.border, focuscolor=p.accent,
                    selectbackground=p.selection, selectforeground=p.text, fieldbackground=p.field,
                    insertcolor=p.text, arrowcolor=p.text_muted)
    style.map(".", foreground=[("disabled", p.text_disabled)])

    # Frames
    style.configure("TFrame", background=p.bg)
    style.configure("Sidebar.TFrame", background=p.surface)
    style.configure("Card.TFrame", background=p.bg, bordercolor=p.border, relief="solid", borderwidth=1)
    _flat(style, "Card.TFrame", p.bg)
    style.configure("Details.TFrame", background=p.surface, bordercolor=p.border, relief="solid", borderwidth=1)
    _flat(style, "Details.TFrame", p.surface)
    style.configure("TSeparator", background=p.border)

    # Labels
    for name, font, fg in (("TLabel", "body", p.text), ("Body.TLabel", "body", p.text),
                           ("Muted.TLabel", "body", p.text_muted), ("Heading.TLabel", "heading", p.text),
                           ("Subheading.TLabel", "subheading", p.text), ("Caption.TLabel", "caption", p.text_muted),
                           ("Eyebrow.TLabel", "eyebrow", p.accent_text), ("Mono.TLabel", "mono", p.text)):
        style.configure(name, background=p.bg, foreground=fg, font=fonts[font])
    style.configure("Badge.TLabel", background=p.surface, foreground=p.text_muted, font=fonts["eyebrow"],
                    padding=(SPACE["s"], 2))
    for name, font, fg in (("Sidebar.TLabel", "body", p.text), ("Sidebar.Title.TLabel", "title", p.text),
                           ("Sidebar.Caption.TLabel", "caption", p.text_muted),
                           ("Sidebar.Eyebrow.TLabel", "eyebrow", p.text_muted)):
        style.configure(name, background=p.surface, foreground=fg, font=fonts[font])
    style.configure("SidebarBadge.TLabel", background=p.warning_bg, foreground=p.warning, font=fonts["eyebrow"],
                    padding=(SPACE["s"], 2))
    style.configure("Banner.TFrame", background=p.warning_bg)
    style.configure("Details.TLabel", background=p.surface, foreground=p.text_muted, font=fonts["body"])
    for kind in KINDS:
        fg, bg, border = kind_colours(p, kind)
        style.configure(f"Callout.{kind}.TFrame", background=bg, bordercolor=border, relief="solid", borderwidth=1)
        _flat(style, f"Callout.{kind}.TFrame", bg)
        style.configure(f"Callout.{kind}.TLabel", background=bg, foreground=p.text, font=fonts["body"])
        style.configure(f"Callout.{kind}.Headline.TLabel", background=bg,
                        foreground=p.text if kind in (NEUTRAL, BUSY) else fg, font=fonts["subheading"])
        style.configure(f"Callout.{kind}.Mono.TLabel", background=bg, foreground=p.text, font=fonts["mono"])
        style.configure(f"Status.{kind}.TLabel", background=p.bg, font=fonts["body"],
                        foreground=p.danger if kind == DANGER else (p.text_muted if kind in (NEUTRAL, BUSY) else p.text))
        style.configure(f"Status.{kind}.Icon.TLabel", background=p.bg, font=fonts["subheading"],
                        foreground=p.text_muted if kind in (NEUTRAL, BUSY) else fg)
    style.configure("Selected.Card.TFrame", background=p.bg, bordercolor=p.accent, relief="solid", borderwidth=1)
    _flat(style, "Selected.Card.TFrame", p.bg)

    # Buttons: secondary (default), primary, link and the Details toggle
    style.configure("TButton", background=p.bg, foreground=p.text, bordercolor=p.border_strong,
                    padding=BUTTON_PADDING, relief="flat", borderwidth=1, focusthickness=1, focuscolor=p.accent,
                    focussolid=1,
                    anchor="center", font=fonts["body"])
    _flat(style, "TButton", p.bg)
    style.map("TButton",
              background=[("disabled", p.bg), ("pressed", p.border), ("active", p.hover)],
              lightcolor=[("disabled", p.bg), ("pressed", p.border), ("active", p.hover)],
              darkcolor=[("disabled", p.bg), ("pressed", p.border), ("active", p.hover)],
              bordercolor=[("disabled", p.border), ("focus", p.accent)],
              foreground=[("disabled", p.text_disabled)])
    style.configure("Primary.TButton", background=p.accent, foreground=p.on_accent, bordercolor=p.accent,
                    focuscolor=p.on_accent, font=fonts["subheading"])
    _flat(style, "Primary.TButton", p.accent)
    primary_bg = [("disabled", p.hover), ("pressed", p.accent_pressed), ("active", p.accent_hover)]
    style.map("Primary.TButton", background=primary_bg, lightcolor=primary_bg, darkcolor=primary_bg,
              bordercolor=[("disabled", p.border), ("focus", p.accent_pressed)],
              foreground=[("disabled", p.text_disabled)])
    for name, bg, fg in (("Link.TButton", p.bg, p.accent_text), ("Banner.Link.TButton", p.warning_bg, p.accent_text),
                         ("Toggle.TButton", p.bg, p.text_muted)):
        style.configure(name, background=bg, foreground=fg, bordercolor=bg, padding=(0, 2), anchor="w",
                        focuscolor=p.accent, focussolid=1,
                        font=fonts["subheading" if name == "Toggle.TButton" else "body"])
        _flat(style, name, bg)
        style.map(name, background=[("active", bg)], lightcolor=[("active", bg)], darkcolor=[("active", bg)],
                  bordercolor=[("active", bg)],
                  foreground=[("disabled", p.text_disabled), ("pressed", p.accent_pressed),
                              ("active", p.accent_hover if name != "Toggle.TButton" else p.text)])

    # Choices
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=p.bg, foreground=p.text, indicatorbackground=p.field,
                        indicatorforeground=p.on_accent, upperbordercolor=p.border_strong,
                        lowerbordercolor=p.border_strong, indicatormargin=(0, 2, SPACE["s"], 0),
                        padding=(0, SPACE["xs"]), focuscolor=p.accent, focusthickness=1, focussolid=1,
                        wraplength=text_wrap)
        style.map(name, background=[("active", p.bg)],
                  indicatorbackground=[("disabled", p.surface), ("selected", p.accent), ("pressed", p.accent_subtle)],
                  upperbordercolor=[("disabled", p.border), ("selected", p.accent), ("focus", p.accent)],
                  lowerbordercolor=[("disabled", p.border), ("selected", p.accent), ("focus", p.accent)],
                  foreground=[("disabled", p.text_disabled)])
    style.configure("Strong.TRadiobutton", font=fonts["subheading"])

    # Fields
    for name in ("TEntry", "TCombobox"):
        style.configure(name, fieldbackground=p.field, foreground=p.text, bordercolor=p.border_strong,
                        lightcolor=p.field, darkcolor=p.field, padding=FIELD_PADDING, insertcolor=p.text,
                        background=p.field, arrowcolor=p.text_muted, arrowsize=12)
        style.map(name,
                  fieldbackground=[("disabled", p.surface), ("readonly", p.surface if name == "TEntry" else p.field)],
                  bordercolor=[("focus", p.accent), ("disabled", p.border)],
                  lightcolor=[("focus", p.accent)],
                  foreground=[("disabled", p.text_disabled)],
                  background=[("disabled", p.surface), ("active", p.hover)],
                  arrowcolor=[("disabled", p.text_disabled)],
                  selectbackground=[("readonly", "!focus", p.field), ("readonly", "focus", p.accent_subtle)],
                  selectforeground=[("readonly", p.text)])
    style.configure("Mono.TEntry", font=fonts["mono"])
    root.option_add("*TCombobox*Listbox.background", p.field)
    root.option_add("*TCombobox*Listbox.foreground", p.text)
    root.option_add("*TCombobox*Listbox.selectBackground", p.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", p.on_accent)
    root.option_add("*TCombobox*Listbox.font", fonts["body"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.relief", "flat")

    # Progress and scrolling
    # A thin, flat bar: clam's own bar has a fixed 18-px height, the default theme's takes a thickness.
    for element in ("R5.pbar", "R5.trough"):
        if element not in style.element_names():
            style.element_create(element, "from", "default")
    style.layout("Accent.Horizontal.TProgressbar", [("R5.trough", {"sticky": "nswe", "children": [
        ("R5.pbar", {"side": "left", "sticky": "ns"})]})])
    style.configure("Accent.Horizontal.TProgressbar", thickness=6, background=p.accent, troughcolor=p.border,
                    borderwidth=0, troughborderwidth=0, pbarrelief="flat", troughrelief="flat")
    style.configure("Thin.Vertical.TScrollbar", background=p.border_strong, troughcolor=p.bg, bordercolor=p.bg,
                    lightcolor=p.border_strong, darkcolor=p.border_strong, arrowsize=10, gripcount=0)
    style.map("Thin.Vertical.TScrollbar", background=[("active", p.text_disabled)])
    style.layout("Thin.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    return Theme(appearance, p, fonts, families, text_wrap)
