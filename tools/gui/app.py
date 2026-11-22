"""R5 Mod Assistant (prototype, issue #16 Phase 1).

Run:  python3 tools/gui/app.py

Standard-library Python 3.11+ with Tkinter only. This window is a thin view: the
decisions live in state.py, prep_state.py, checks.py, runner.py, card.py and
tools/cardprep, and the build itself is tools/build.py with all of its checks. It
never downloads anything, never formats or partitions a card and never contacts a
camera. Boot sectors are written only by the "Prepare card" tab (issue #16 Phase 2),
after the person confirms the card, through the operating system's password prompt.
The Screenshots utility (shots.py, outside the setup steps) only reads the card.
Its look (fonts, colours, spacing and ttk styles) comes from theme.py.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser

if sys.version_info < (3, 11):
    sys.exit("R5 Mod Assistant needs Python 3.11 or newer (https://www.python.org/downloads/).")

TOOLS_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from gui.i18n import LANGUAGES, Translator, lines_of
from messages import error_text, joined, message, phrase

from cardprep import cbasic, devices as card_devices, layout, workflow as card_flow
from gui import bundle, card, checks, runner, shots, theme
import screenshots as screenshots_mod
from gui.prep_state import EXPLAIN, FORMAT_WARNING, PrepState
from gui.state import CHECKING, FAILED, OK, AssistantState, default_output_dir
from gui.theme import BUSY, DANGER, INFO, NEUTRAL, SPACE, SUCCESS, WARNING
from gui.wizard import (BLOCKED, BOOTCARD, BOOTDISK, BUILD, CAMERA, CARD_BADGES, COPY, DONE, FEATURES, GUIDE, CAMERA_CARD_CHECKLIST, TEST_RECOVERY,
                        NEEDS_ACTION, ROM, SKIPPED, STEP_BY_ID, STEPS, TEST, TEST_CHECKLIST, TOOLCHAIN, Wizard)


def verify_build(out: Path):
    """On resume: the last build counts only if autoexec.bin still matches its build.json."""
    try:
        summary = runner.summarize(out)
    except (OSError, ValueError, KeyError):
        return None, message("The previous build in {out} can't be read any more; build again.", out=out)
    if not summary.sha256_matches_build_json:
        return None, message('The previous build in {out} no longer matches its build.json; build again.', out=out)
    return summary, None


def verify_bootdisk(rom_path: Path, expected_sha256: str) -> bool:
    """On resume: verified BootDisk counts only if the readback ROM dump still matches and shows enabled."""
    try:
        data = card._read_file(Path(rom_path), cbasic.ROM_BYTES)
    except (card.CardError, OSError):
        return False
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        return False
    try:
        return cbasic.inspect_bootinfo(data).state == "enabled"
    except (cbasic.CBasicError, ValueError):
        return False


DOCS = {
    "Command-line getting started guide": ROOT / "docs" / "getting-started.md",
    "Prerequisites": ROOT / "docs" / "usage.md",
    "About this assistant": ROOT / "docs" / "gui.md",
}
CANNOT_DO = """\
What this assistant does: it guides you through dumping the camera's ROM and enabling \
BootDisk with a Canon Basic SDHC card you run yourself in the camera, verifies the \
returned ROM dump, builds autoexec.bin on your computer, can write the two boot \
markers onto a camera-formatted card (step 7, macOS only, with a backup first), and \
copies autoexec.bin to the card. What it never does:

  •  change the camera automatically: the ROM dump and BootDisk steps only prepare an \
SDHC card and check the dump you return; you run them yourself in the camera;
  •  write the camera's firmware or NVRAM directly from this computer;
  •  format, partition or erase a card, or write its partition table (MBR);
  •  write anything to a card except the boot-marker sectors in step 7, the Canon \
Basic SCRIPT marker on FAT32 SDHC cards in steps 2-3, and the card-root files;
  •  download the toolchain (you install it yourself; see step 5);
  •  test anything on the camera, or tell you whether your camera will work.

Every build made here is a public repackaged build and is hardware-untested. \
Original Canon EOS R5 on firmware 2.2.1 only. If the camera freezes or shows an \
error, remove the battery, then the card; see the Getting started guide.
"""
CHECK_KINDS = {OK: SUCCESS, FAILED: DANGER, CHECKING: BUSY}
STEP_KINDS = {DONE: SUCCESS, SKIPPED: NEUTRAL, BLOCKED: WARNING, NEEDS_ACTION: INFO}
STEP_HEADLINES = {DONE: "Check passed", SKIPPED: "Skipped", BLOCKED: "Locked", NEEDS_ACTION: "To do"}
SIDEBAR_STATUS = {DONE: "Done", SKIPPED: "Skipped", BLOCKED: "Locked", NEEDS_ACTION: "To do"}
XS, S, M, L, XL = (SPACE[k] for k in ("xs", "s", "m", "l", "xl"))
CARD_INSET = 2 * M + 2  # a card's padding and border, for the width of the text inside it


def open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - opening a local folder/file the user selected
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def open_document(path: Path, *, platform: str | None = None, startfile=None, popen=subprocess.Popen,
                  browser=webbrowser.open) -> None:
    """Open one of the guides (a Markdown file in docs/).

    Windows has no app for .md files by default, so if it can't open one, Notepad shows it."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("win"):
        try:
            (os.startfile if startfile is None else startfile)(str(path))  # noqa: S606 - a bundled guide
        except OSError:
            popen(["notepad.exe", str(path)])
    else:
        browser(Path(path).as_uri())


def bullets(lines) -> str:
    return joined("\n", (message('•  {line}', line=phrase(line)) for line in lines))


class Slot:
    """A place in a layout whose content can be shown or hidden without losing its position."""

    def __init__(self, ttk, parent, fill="x", **pack):
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill=fill, anchor="w")
        self.pack = pack

    def show(self, widget):
        if not widget.winfo_manager():
            widget.pack(in_=self.frame, fill="x", **self.pack)

    def hide(self, widget):
        if widget.winfo_manager():
            widget.pack_forget()
            self.frame.configure(height=1)  # Tk keeps an emptied frame's old size otherwise


class Callout:
    """A neutral panel with a status icon, a short headline and optional details."""

    def __init__(self, app, parent, *, pady=(M, 0), fill="x"):
        tk, ttk = app.tk, app.ttk
        self.app, self.kind = app, NEUTRAL
        self.slot = Slot(ttk, parent, fill=fill, pady=pady)
        self.frame = ttk.Frame(self.slot.frame, padding=(M, 12), style=f"Callout.{NEUTRAL}.TFrame")
        self.icon = tk.Canvas(self.frame, width=20, height=20, highlightthickness=0, borderwidth=0)
        self.icon.grid(row=0, column=0, sticky="nw", padx=(0, 12), pady=(1, 0))
        self.headline = ttk.Label(self.frame, justify="left")
        self.headline.grid(row=0, column=1, sticky="w")
        self.body = ttk.Label(self.frame, justify="left")
        self.frame.columnconfigure(1, weight=1)
        app.wrap(self.headline, CARD_INSET + 32, full=True)
        app.wrap(self.body, CARD_INSET + 32, full=True)

    def set(self, kind, headline, body="", mono=False):
        self.kind = kind
        self.frame.configure(style=f"Callout.{kind}.TFrame")
        self.app.words.configure(self.headline, text=headline, style=f"Callout.{kind}.Headline.TLabel")
        self.app.words.configure(self.body, text=body, style=f"Callout.{kind}.Mono.TLabel" if mono else f"Callout.{kind}.TLabel")
        if body:
            self.body.grid(row=1, column=1, sticky="w", pady=(XS, 0))
        else:
            self.body.grid_remove()
        if not headline:  # body-only callouts put the text level with the icon
            self.headline.grid_remove()
            self.body.grid(row=0, column=1, sticky="w", pady=0)
        else:
            self.headline.grid()
        self.app.draw_mark(self.icon, kind, self.app.theme.colours(kind)[1])
        self.slot.show(self.frame)

    def clear(self):
        self.slot.hide(self.frame)


class StatusLine:
    """One status: an icon in the status colour and the message beside it."""

    def __init__(self, app, parent, pady=(S, 0)):
        ttk = app.ttk
        self.app = app
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="x", pady=pady)
        self.icon = ttk.Label(self.frame, width=2, style=f"Status.{NEUTRAL}.Icon.TLabel")
        self.icon.pack(side="left", anchor="n")
        self.text = ttk.Label(self.frame, justify="left", style=f"Status.{NEUTRAL}.TLabel")
        self.text.pack(side="left", anchor="n", fill="x")
        app.wrap(self.text, 24, full=True)

    def set(self, kind, text):
        self.app.words.configure(self.icon, text=theme.GLYPHS[kind], style=f"Status.{kind}.Icon.TLabel")
        self.app.words.configure(self.text, text=text, style=f"Status.{kind}.TLabel")

    def show_check(self, check):
        self.set(CHECK_KINDS.get(check.status, NEUTRAL), check.message or "Not checked yet.")


class Busy:
    """An indeterminate progress bar with a caption, shown only while something runs."""

    def __init__(self, app, parent, pady=(12, 0)):
        ttk = app.ttk
        self.app = app
        self.slot = Slot(ttk, parent, pady=pady)
        self.frame = ttk.Frame(self.slot.frame)
        self.bar = ttk.Progressbar(self.frame, mode="indeterminate", length=160,
                                   style="Accent.Horizontal.TProgressbar")
        self.bar.pack(side="left")
        self.label = ttk.Label(self.frame, style="Caption.TLabel", justify="left")
        self.label.pack(side="left", padx=(12, 0))
        app.wrap(self.label, 200, full=True)
        self.running = False

    def set(self, text):
        if text:
            self.app.words.configure(self.label, text=text)
            self.slot.show(self.frame)
            if not self.running:
                self.bar.start(12)
                self.running = True
        else:
            if self.running:
                self.bar.stop()
                self.running = False
            self.slot.hide(self.frame)


class App:
    def __init__(self, tk, ttk, filedialog, messagebox, tkfont, *, root=None, appearance=None):
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.state = AssistantState()
        self.events: queue.Queue = queue.Queue()
        self.summary: runner.BuildSummary | None = None
        self.card_info: card.CardInspection | None = None
        self.copying = False
        self.cbasic_busy: str | None = None
        self.cbasic_baselines: dict[str, tuple[Path, dict[str, str]]] = {}
        self.prep = PrepState()
        self.shots = shots.ShotsState()
        self.utility: str | None = None  # a utility shown instead of the current setup step
        self._hint: str | None = None  # why the step clicked in the sidebar is locked; cleared on the next move
        self.wizard = Wizard(self.state, prep_supported=card_devices.prepare_supported())
        self.state.set_output_dir(default_output_dir(self.state.profile, self.state.options, self.state.manifest))
        self.wizard.load(verify_build=verify_build, verify_bootdisk=verify_bootdisk)
        self.summary = self.wizard.build_summary
        self.words = Translator(self.wizard.saved.language)
        self._wrapped: list = []
        self._need_rows = []
        self._inners = {}  # step -> the scrolled frame holding its content
        self._detecting = False  # the toolchain search is running
        self._column = theme.DEFAULT_WINDOW[0] - theme.SIDEBAR_WIDTH - 2 * XL

        self.root = tk.Tk() if root is None else root
        self.theme = theme.apply(self.root, ttk, tkfont, appearance=appearance)
        self.root.title(self.words.render("R5 Mod Assistant (prototype)"))
        self.root.minsize(*theme.MIN_WINDOW)
        width = max(theme.MIN_WINDOW[0], min(theme.DEFAULT_WINDOW[0], self.root.winfo_screenwidth() - 80))
        height = max(theme.MIN_WINDOW[1], min(theme.DEFAULT_WINDOW[1], self.root.winfo_screenheight() - 120))
        self.root.geometry(f"{width}x{height}")

        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)
        self.sidebar = self._sidebar(shell)
        self.sidebar_rule = ttk.Separator(shell, orient="vertical")
        self.sidebar_rule.pack(side="left", fill="y")
        main = ttk.Frame(shell)
        main.pack(side="left", fill="both", expand=True)
        self._help_menu()
        self._language_header(main)
        footer = self._footer(main)
        self.content = ttk.Frame(main)
        self.content.pack(fill="both", expand=True)
        footer.lift(self.content)  # Tab reaches the step's controls before Back and Next

        self.panels = {}
        self.utilities = {}  # utility id -> (frame, None, canvas), like panels but outside the setup steps
        self._camera_panel()
        self._rom_tab()
        self._bootdisk_panel()
        self._features_tab()
        self._toolchain_tab()
        self._build_tab()
        self._prep_tab()
        self._card_tab()
        self._test_panel()
        self._screenshots_panel()
        self._bind_scrolling()
        self.content.bind("<Configure>", lambda event: self._set_column(event.width - self.scrollbar_width))
        self._compact = None
        shell.bind("<Configure>", lambda event: self._size_navigation(main, event.width, event.height))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.root.after(100, self._poll)
        self._detect_toolchain()
        self.refresh()

    # -- layout helpers ---------------------------------------------------------

    def wrap(self, label, inset=0, full=False):
        """Keep a label's wrap width in step with the column: body text at about 72 characters,
        ``full`` text (statuses, callouts, paths) at the column's width less ``inset``."""
        self._wrapped.append((label, inset, full))
        label.configure(wraplength=self._wrap_width(inset, full))
        return label

    def _wrap_width(self, inset, full):
        width = self._column - inset
        return max(160, width if full else min(self.theme.text_wrap, width))

    def _set_column(self, width):
        column = max(160, width - 2 * XL)
        if column == self._column:
            return
        self._column = column
        for label, inset, full in self._wrapped:
            label.configure(wraplength=self._wrap_width(inset, full))
        self._wrap_needs()
        choice_wrap = self._wrap_width(28, False)
        for name in ("TCheckbutton", "TRadiobutton"):
            self.ttk.Style(self.root).configure(name, wraplength=choice_wrap)

    def _text(self, parent, text="", style="Body.TLabel", pady=(S, 0), inset=0, full=False):
        label = self.words.widget(self.ttk.Label, parent, text=text, style=style, justify="left")
        label.pack(anchor="w", fill="x", pady=pady)
        return self.wrap(label, inset, full)

    def _list(self, parent, lines, inset=0, numbered=False):
        """A bulleted or numbered list whose wrapped lines stay indented under their text."""
        grid = self.ttk.Frame(parent)
        grid.pack(fill="x", anchor="w")
        grid.columnconfigure(1, weight=1)
        for row, line in enumerate(lines):
            self.words.widget(self.ttk.Label, grid, text=f"{row + 1}." if numbered else "•",
                           style="Strong.TLabel" if numbered else "Body.TLabel").grid(
                row=row, column=0, sticky="nw", padx=(0, S), pady=(XS, 0))
            label = self.words.widget(self.ttk.Label, grid, text=line, style="Body.TLabel", justify="left")
            label.grid(row=row, column=1, sticky="w", pady=(XS, 0))
            self.wrap(label, inset + 3 * S)
        return grid

    def _badge(self, parent, card):
        """The card size a step needs, as a small highlighted label."""
        return self.words.widget(self.ttk.Label, parent, text=CARD_BADGES[card], style=f"CardBadge.{card}.TLabel")

    def _needs_box(self, parent, needs, title):
        """What a step needs (SD card, computer, camera...), with the card size highlighted."""
        box = self.ttk.Frame(parent, style="Needs.TFrame", padding=M)
        box.pack(fill="x", pady=(L, 0))
        self.wrap(self.words.widget(self.ttk.Label, box, text=title, style="Needs.Title.TLabel", justify="left"),
                  CARD_INSET, full=True).pack(anchor="w", fill="x")
        grid = self.ttk.Frame(box, style="Needs.Inner.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        details = []
        titles = []
        for row, need in enumerate(needs):
            title_label = self.words.widget(self.ttk.Label, grid, text=need.what, style="Needs.Strong.TLabel")
            title_label.grid(row=row, column=0, sticky="nw", padx=(0, M), pady=(S, 0))
            titles.append(title_label)
            cell = self.ttk.Frame(grid, style="Needs.Inner.TFrame")
            cell.grid(row=row, column=1, sticky="we", pady=(S, 0))
            if need.card:
                self._badge(cell, need.card).pack(anchor="w", pady=(0, XS))
            detail = self.words.widget(self.ttk.Label, cell, text=need.detail, style="Needs.TLabel",
                                       justify="left", wraplength=self.theme.text_wrap)
            detail.pack(anchor="w", fill="x")
            details.append(detail)

        self._need_rows.append((titles, details))
        return box

    def _wrap_needs(self):
        for titles, details in self._need_rows:
            inset = CARD_INSET + M + max(title.winfo_reqwidth() for title in titles)
            for detail in details:
                detail.configure(wraplength=self._wrap_width(inset, False))

    def _card(self, parent, title=None, pady=(M, 0), style="Card.TFrame"):
        box = self.ttk.Frame(parent, style=style, padding=M)
        box.pack(fill="x", pady=pady)
        if title:
            self._text(box, title, "Subheading.TLabel", pady=(0, XS), inset=CARD_INSET)
        return box

    def _field(self, parent, label, variable, buttons, pady=(M, 0), inset=0):
        """A caption, a read-only path box (monospace) and its buttons."""
        if label:
            self._text(parent, label, "Caption.TLabel", pady=(pady[0], XS), inset=inset)
            pady = (0, pady[1])
        row = self.ttk.Frame(parent)
        row.pack(fill="x", pady=pady)
        self.ttk.Entry(row, textvariable=variable, state="readonly", style="Mono.TEntry", width=12,
                       font=self.theme.fonts["mono"]).pack(side="left", fill="x", expand=True)
        made = []
        for text, command in buttons:
            button = self.words.widget(self.ttk.Button, row, text=text, command=command)
            button.pack(side="left", padx=(S, 0))
            made.append(button)
        return made

    def _buttons(self, parent, pady=(M, 0)):
        row = self.ttk.Frame(parent)
        row.pack(fill="x", pady=pady)
        return row

    @staticmethod
    def _fit_button_row(row):
        buttons = row.pack_slaves()
        if len(buttons) < 2 or not row.winfo_ismapped():
            return
        stacked = sum(b.winfo_reqwidth() for b in buttons) + S * (len(buttons) - 1) > row.winfo_width()
        side = "top" if stacked else "left"
        for index, button in enumerate(buttons):
            if str(button.pack_info()["side"]) != side:
                gap = S if index else 0
                button.pack_configure(side=side, anchor="w", padx=(0 if stacked else gap, 0),
                                      pady=(gap if stacked else 0, 0))

    def _button(self, row, text, command=None, primary=False, **kw):
        """A button at the end of a row; primary for the step's main action."""
        first = not row.winfo_children()
        button = self.words.widget(self.ttk.Button, row, text=text, command=command, style="Primary.TButton" if primary else "TButton",
                                 **kw)
        button.pack(side="left", padx=(0 if first else S, 0))
        if not first:
            row.bind("<Configure>", lambda _e: self._fit_button_row(row))
            row.bind("<Map>", lambda _e: self._fit_button_row(row))
            for child in row.pack_slaves():
                child.bind("<Configure>", lambda _e, frame=row: self._fit_button_row(frame))
        return button

    def _textbox(self, parent, height, pady=(S, 0)):
        """A read-only, selectable monospace box for logs and reports."""
        tk, p = self.tk, self.theme.palette
        box = self.ttk.Frame(parent, style="Details.TFrame", padding=1)
        box.pack(fill="both", expand=True, pady=pady)
        text = tk.Text(box, height=height, wrap="word", state="disabled", takefocus=0, relief="flat", borderwidth=0,
                       highlightthickness=0, padx=12, pady=S, font=self.theme.fonts["mono"], background=p.surface,
                       foreground=p.text, insertbackground=p.text, selectbackground=p.selection,
                       selectforeground=p.text, inactiveselectbackground=p.selection)
        bar = self.ttk.Scrollbar(box, orient="vertical", command=text.yview, style="Thin.Vertical.TScrollbar")
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        return text, box

    def _set_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", self.words.render(text))
        widget.configure(state="disabled")

    def draw_mark(self, canvas, kind, background, text=None, ring=False, colour=None):
        """A round status mark: a filled circle with a glyph, or an outlined ring."""
        p = self.theme.palette
        fg = colour or (self.theme.colours(kind)[0] if kind not in (NEUTRAL, BUSY) else p.text_muted)
        size = int(canvas.cget("width"))
        canvas.configure(background=background)
        canvas.delete("all")
        if ring:
            canvas.create_oval(1, 1, size - 2, size - 2, outline=fg, width=1)
            glyph_colour = fg
        else:
            canvas.create_oval(1, 1, size - 2, size - 2, fill=fg, outline=fg)
            glyph_colour = p.bg
        canvas.create_text(size / 2, size / 2, text=text or theme.GLYPHS[kind], fill=glyph_colour,
                           font=self.theme.fonts["eyebrow"])

    # -- frame: sidebar, menu, footer --------------------------------------------

    def _sidebar(self, parent):
        ttk, tk, p, f = self.ttk, self.tk, self.theme.palette, self.theme.fonts
        side = ttk.Frame(parent, style="Sidebar.TFrame", padding=(M, L, M, M), width=theme.SIDEBAR_WIDTH)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = ttk.Frame(side, style="Sidebar.TFrame")
        brand.pack(fill="x", padx=S)
        self.words.widget(ttk.Label, brand, text="Canon R5 mod", style="Sidebar.Title.TLabel").pack(side="left")
        self.words.widget(ttk.Label, brand, text="Prototype", style="SidebarBadge.TLabel").pack(side="left", padx=(S, 0))
        self.words.widget(ttk.Label, side, text="R5 Mod Assistant", style="Sidebar.Caption.TLabel").pack(anchor="w", padx=S, pady=(2, 0))
        self.words.widget(ttk.Label, side, text="Steps", style="Sidebar.Eyebrow.TLabel").pack(anchor="w", padx=S, pady=(M + S, S))
        rows = ttk.Frame(side, style="Sidebar.TFrame")
        rows.pack(fill="x")
        self.side_rows = {}
        for info in STEPS:
            parts = self._side_row(rows, info.id, info.title, lambda s=info.id: self._sidebar_clicked(s))
            parts[0].bind("<Up>", lambda _e, s=info.id: self._side_focus(s, -1))
            parts[0].bind("<Down>", lambda _e, s=info.id: self._side_focus(s, 1))
            self.side_rows[info.id] = {"parts": parts, "hover": False}
        self._side_path = None
        self.mode_label = self.words.widget(ttk.Label, side, text="", style="Sidebar.Caption.TLabel", justify="left",
                                    wraplength=theme.SIDEBAR_WIDTH - 3 * M - 2 * S)
        self.mode_label.pack(anchor="w", padx=S, pady=(M, 0))
        # Switches between the full setup and the short "Update my card" path, from any step.
        self.mode_link = self.words.widget(ttk.Button, side, text="", style="Sidebar.Link.TButton", cursor="hand2",
                                    command=self._switch_mode)
        self.mode_link.pack(anchor="w", padx=S, pady=(XS, 0))
        # Compact windows expose this utility in the Steps menu.
        self.words.widget(ttk.Label, side, text="Tools", style="Sidebar.Eyebrow.TLabel").pack(anchor="w", padx=S, pady=(M, XS))
        tools = ttk.Frame(side, style="Sidebar.TFrame")
        tools.pack(fill="x")
        self.tool_rows = {}
        parts = self._side_row(tools, shots.SCREENSHOTS, shots.TITLE, lambda: self._open_utility(shots.SCREENSHOTS))
        parts[0].pack(fill="x")
        self.words.configure(parts[4], text="Convert card screenshots")
        self.tool_rows[shots.SCREENSHOTS] = {"parts": parts, "hover": False}
        return side

    def _side_row(self, parent, key, text, on_click):
        """A sidebar row: a focus bar, a round mark, a title and a status caption."""
        tk, p, f = self.tk, self.theme.palette, self.theme.fonts
        row = tk.Frame(parent, background=p.surface, highlightthickness=1, highlightbackground=p.surface,
                       highlightcolor=p.focus, takefocus=1, cursor="hand2")
        bar = tk.Frame(row, width=1, background=p.surface)
        bar.grid(row=0, column=0, rowspan=2, sticky="ns")
        mark = tk.Canvas(row, width=22, height=22, highlightthickness=0, borderwidth=0, background=p.surface)
        mark.grid(row=0, column=1, rowspan=2, padx=(S, 10), pady=S)
        title = self.words.widget(tk.Label, row, text=text, anchor="w", justify="left", background=p.surface,
                         foreground=p.text, font=f["body"], borderwidth=0, padx=0, pady=0)
        title.grid(row=0, column=2, sticky="sw", pady=(S, 0))
        status = self.words.widget(tk.Label, row, text="", anchor="w", justify="left", wraplength=theme.SIDEBAR_WIDTH - 5 * M,
                          background=p.surface, foreground=p.text_muted,
                          font=f["caption"], borderwidth=0, padx=0, pady=0)
        status.grid(row=1, column=2, sticky="nw", pady=(0, S))
        row.columnconfigure(2, weight=1)
        parts = (row, bar, mark, title, status)
        for widget in parts:
            widget.bind("<Button-1>", lambda _e: on_click())
            widget.bind("<Enter>", lambda _e: self._side_hover(key, True))
            widget.bind("<Leave>", lambda _e: self._side_hover(key, False))
        for binding in ("<Return>", "<KP_Enter>", "<space>"):
            row.bind(binding, lambda _e: on_click())
        return parts

    def _help_menu(self):
        bar = self.tk.Menu(self.root)
        help_menu = self.tk.Menu(bar, name="help", tearoff=False)
        for label, path in DOCS.items():
            help_menu.add_command(label=label, command=lambda p=path: open_document(p))
        bar.add_cascade(label="Help", menu=help_menu)
        self.menu_bar, self.help_menu = bar, help_menu
        self._translate_menu()
        self.root.configure(menu=bar)

    def _translate_menu(self):
        for index, label in enumerate(DOCS):
            self.help_menu.entryconfigure(index, label=self.words.render(label))
        self.menu_bar.entryconfigure("end", label=self.words.render("Help"))

    def _change_language(self, _event=None):
        language = tuple(LANGUAGES)[self.language_menu.current()]
        if language == self.words.language:
            return
        self.words.select(language)
        self._wrap_needs()
        self.wizard.saved.language = language
        self.wizard.save()
        self.root.title(self.words.render("R5 Mod Assistant (prototype)"))
        self._translate_menu()
        for view in self.state.option_views():
            self._show_option(view)
        self._refresh_cbasic()
        self._refresh_prep()
        self._refresh_shots()

    def _show_option(self, view):
        combo = self.option_widgets[view.id][0]
        combo.configure(values=[self.words.render(c.label) for c in view.choices])
        index = next(i for i, c in enumerate(view.choices) if c.value == view.value)
        combo.current(index)
        return view.choices[index]

    def _disclosure(self, parent, title):
        """Return a content frame opened by the labelled disclosure above it."""
        button = self.words.widget(self.ttk.Button, parent, text=message(
            "▸  {title}",
            title=phrase(title)), style="Toggle.TButton")
        button.pack(anchor="w", pady=(M, 0))
        body = self.ttk.Frame(parent)

        def toggle():
            if body.winfo_manager():
                body.pack_forget()
                self.words.configure(button, text=message('▸  {title}', title=phrase(title)))
            else:
                body.pack(fill="x", after=button)
                self.words.configure(button, text=message('▾  {title}', title=phrase(title)))

        button.configure(command=toggle)
        return body

    def _size_navigation(self, main, width, height):
        # Keep the step column usable on small screens and the last sidebar tool within reach.
        compact = width < 940 or height < 740
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.sidebar.pack_forget()
            self.sidebar_rule.pack_forget()
            self.step_menu_button.pack(side="left")
        else:
            self.step_menu_button.pack_forget()
            self.sidebar.pack(side="left", fill="y", before=main)
            self.sidebar_rule.pack(side="left", fill="y", before=main)

    def _populate_step_menu(self):
        menu, wiz = self.step_menu, self.wizard
        menu.delete(0, "end")
        for step_id in wiz.path():
            info = STEP_BY_ID[step_id]
            label = f"{info.number}. {self.words.render(info.title)}"
            menu.add_command(label=label, command=lambda key=step_id: self._sidebar_clicked(key))
        menu.add_separator()
        if wiz.saved.mode == "update" or not wiz.one_time_done():
            label = "Show all nine steps" if wiz.saved.mode == "update" else "Update my card instead"
            menu.add_command(label=self.words.render(label), command=self._switch_mode)
        menu.add_command(label=self.words.render(shots.TITLE), command=lambda: self._open_utility(shots.SCREENSHOTS))

    def _language_header(self, main):
        header = self.ttk.Frame(main, padding=(XL, S))
        header.pack(side="top", fill="x")
        self.step_menu_button = self.words.widget(self.ttk.Menubutton, header, text="Steps")
        self.step_menu = self.tk.Menu(self.step_menu_button, tearoff=False, postcommand=self._populate_step_menu)
        self.step_menu_button.configure(menu=self.step_menu)
        self.language_label = self.words.widget(self.ttk.Label, header, text="Language", style="Caption.TLabel")
        self.language_menu = self.ttk.Combobox(header, state="readonly", values=tuple(LANGUAGES.values()), width=10)
        self.language_menu.set(LANGUAGES[self.words.language])
        self.language_menu.bind("<<ComboboxSelected>>", self._change_language)
        self.language_menu.pack(side="right")
        self.language_label.pack(side="right", padx=(0, S))

    def _footer(self, parent):
        footer = self.ttk.Frame(parent)
        footer.pack(side="bottom", fill="x")
        self.ttk.Separator(footer, orient="horizontal").pack(fill="x")
        nav = self.ttk.Frame(footer, padding=(XL, M))
        nav.pack(fill="x")
        self.back_button = self.words.widget(self.ttk.Button, nav, text="Back", command=self._back)
        self.back_button.grid(row=0, column=0, sticky="w")
        self.next_button = self.words.widget(self.ttk.Button, nav, text="Next", command=self._next, style="Primary.TButton")
        self.next_button.grid(row=0, column=2, sticky="e")
        self.nav_note = self.words.widget(self.ttk.Label, nav, text="", style="Caption.TLabel", wraplength=380, justify="right")
        self.nav_note.grid(row=0, column=1, sticky="ew", padx=M)
        nav.columnconfigure(1, weight=1)
        self.nav_note.bind("<Configure>", lambda e: self.nav_note.configure(wraplength=max(100, e.width - 2)))
        return footer

    # -- steps ---------------------------------------------------------------------

    def _panel(self, step_id: str, extra_details: str = "", *, utility: bool = False):
        """A wizard step: its title, what and why, what you do, a body for its controls, the
        app's check of the step, and a Details expander with the technical facts. A utility
        (outside the setup steps) has the same shape without the step's check."""
        tk, ttk, p = self.tk, self.ttk, self.theme.palette
        if utility:
            title, guide = shots.TITLE, shots.GUIDE
            eyebrow = "Tool  ·  Outside setup"
        else:
            info, guide = STEP_BY_ID[step_id], GUIDE[step_id]
            title = info.title
            eyebrow = message("Step {number}  ·  One-time setup" if info.one_time else "Step {number}", number=info.number)
        frame = ttk.Frame(self.content)
        canvas = tk.Canvas(frame, highlightthickness=0, borderwidth=0, background=p.bg, yscrollincrement=1)
        bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview, style="Thin.Vertical.TScrollbar")
        self.scrollbar_width = bar.winfo_reqwidth()
        inner = ttk.Frame(canvas, padding=(XL, L, XL, XL))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def sync(_e=None):
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), inner.winfo_reqheight()))
            if inner.winfo_reqheight() > canvas.winfo_height() > 1:
                if not bar.winfo_manager():
                    bar.pack(side="right", fill="y", before=canvas)
            elif bar.winfo_manager():
                bar.pack_forget()
                canvas.yview_moveto(0)

        def resize(e):
            canvas.itemconfigure(window, width=e.width)
            sync()
        inner.bind("<Configure>", sync)
        canvas.bind("<Configure>", resize)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)

        top = ttk.Frame(inner)
        top.pack(anchor="w", fill="x")
        self.words.widget(ttk.Label, top, text=eyebrow, style="Eyebrow.TLabel").pack(side="left")
        card_size = None if utility else guide.card
        if card_size and step_id != ROM:
            self._badge(top, card_size).pack(side="left", padx=(M, 0))
        self._text(inner, title, "Heading.TLabel", pady=(XS, 0), full=True)
        self._text(inner, guide.why, pady=(M, 0))
        body = ttk.Frame(inner)
        body.pack(fill="x", pady=(S, 0))
        check = None if utility or step_id == ROM else Callout(self, inner, pady=(L, 0))
        if step_id not in (ROM, BOOTDISK, CAMERA):
            help_box = self._disclosure(inner, "Step-by-step instructions")
            if isinstance(guide.todo, str):
                self._text(help_box, guide.todo)
            else:
                self._list(help_box, guide.todo, numbered=True)
            if getattr(guide, "needs", ()):
                self._needs_box(help_box, guide.needs, "What you’ll need")
        details_box = self._disclosure(inner, "Details")
        self._text(details_box, joined("\n\n", (t for t in (guide.details, extra_details) if t)),
                   "Details.TLabel", inset=CARD_INSET)
        (self.utilities if utility else self.panels)[step_id] = (frame, check, canvas)
        self._inners[step_id] = inner
        return body

    def _camera_panel(self):
        tab = self._panel(CAMERA, extra_details=CANNOT_DO)
        self._list(tab, GUIDE[CAMERA].todo, numbered=True)
        self.camera_var = self.tk.BooleanVar(value=False)
        self.words.widget(self.ttk.Checkbutton, tab, variable=self.camera_var, command=lambda: (
            self.wizard.set_camera_confirmed(self.camera_var.get()), self.refresh()),
            text="My camera is an original EOS R5 and shows 'Firmware Ver. 2.2.1'.").pack(anchor="w", pady=(S, 0))
        self.returning_owner = self.ttk.Frame(tab)
        self.update_button = self._button(self._buttons(self.returning_owner), "Update my card", self._update_path)
        needs = self._disclosure(tab, "What will I need for setup?")
        self._needs_box(needs, GUIDE[CAMERA].needs, "Equipment and files")

    def _cbasic_card_selector(self, parent):
        row = self.ttk.Frame(parent)
        row.pack(fill="x", pady=(S, 0))
        find_btn = self.words.widget(self.ttk.Button, row, text="Find SD cards", command=self._prep_find)
        find_btn.pack(side="left")
        choice = self.tk.StringVar()
        combo = self.ttk.Combobox(row, textvariable=choice, state="disabled", width=40)
        combo.pack(side="left", padx=(S, 0), fill="x", expand=True)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._select_card(combo))
        note = self._text(parent, "", "Caption.TLabel", pady=(XS, 0), inset=CARD_INSET, full=True)
        return find_btn, choice, combo, note

    def _select_card(self, combo):
        index = combo.current()
        candidates = self.prep.accepted
        self.prep.select(candidates[index].whole_id if 0 <= index < len(candidates) else None)
        self.refresh()

    def _bootdisk_panel(self):
        tab = self._panel(BOOTDISK)
        self.bootdisk_ack = self.tk.BooleanVar(value=False)
        self.bootdisk_var = self.tk.BooleanVar(value=False)
        self.bd_pages = {key: self.ttk.Frame(tab) for key in ("setup", "camera", "verify", "complete")}
        setup = self.bd_pages["setup"]
        self.words.widget(self.ttk.Checkbutton, setup, variable=self.bootdisk_ack, command=self.refresh,
            text="I understand this changes a permanent camera setting and there is no official way back."
            ).pack(anchor="w", pady=(M, S))
        self._text(setup, "At your computer", "Subheading.TLabel")
        guide = self.ttk.Frame(setup)
        guide.pack(fill="x")
        if self.prep.supported:
            self._text(guide, "Use your Mac’s built-in SD slot and a camera-formatted FAT32 SDHC card "
                       "with at least 300 MiB free. Back up your photos first.")
        else:
            self._text(guide, "Preparing a fresh card requires a Mac with a built-in SD slot. You can check "
                       "a returned card or use a card already marked for scripts on this computer.")
        selector = self.ttk.Frame(guide)
        if self.prep.supported:
            selector.pack(fill="x")
        self.bd_find_button, self.bd_card_choice, self.bd_card_combo, self.bd_card_note = self._cbasic_card_selector(selector)
        self.bd_enable_button = self._button(self._buttons(selector), "Prepare card to enable card startup…",
            lambda: self._prepare_cbasic_card("enable_bootdisk", self.bootdisk_guide_status), primary=True)
        self.words.widget(self.ttk.Button, guide, text="I’m returning with a card from the camera", style="Link.TButton",
                        command=lambda: self._bootdisk_page("verify")).pack(anchor="w", pady=(M, 0))
        existing = self._disclosure(guide, "My camera already starts from a card")
        self.bd_verify_file_button = self._button(self._buttons(existing), "Check an existing firmware copy…",
                                                self._verify_bootdisk_file)
        self.bootdisk_box = self.words.widget(self.ttk.Checkbutton, existing, variable=self.bootdisk_var, command=self._bootdisk_changed,
            text="I previously confirmed this setting by checking a firmware copy from this camera.")
        self.bootdisk_box.pack(anchor="w", pady=(M, 0))
        advanced = self._disclosure(guide, "Other card options and recovery")
        self.bd_stage_button = self._button(self._buttons(advanced), "Use a card already marked for scripts…",
            lambda: self._stage_cbasic_folder("enable_bootdisk", self.bootdisk_guide_status))
        self.bd_disable_button = self._button(self._buttons(advanced), "Prepare a disable card (hardware-untested)…",
            lambda: self._prepare_cbasic_card("disable_bootdisk", self.bootdisk_guide_status))
        camera = self.bd_pages["camera"]
        self.bd_camera_title = self._text(camera, "", "Subheading.TLabel")
        self._text(camera, "Disconnect AC power and use a fully charged battery.")
        self._list(camera, CAMERA_CARD_CHECKLIST, numbered=True)
        self._button(self._buttons(camera), "I’ve returned the card to my computer",
                     lambda: self._bootdisk_page("verify"), primary=True)
        verify = self.bd_pages["verify"]
        self._text(verify, "Back at your computer", "Subheading.TLabel")
        self._text(verify, "Insert the returned card and choose its folder. The Assistant reads the files "
                   "from the camera to check whether card startup is enabled.")
        self.bd_verify_card_button = self._button(self._buttons(verify), "Check the returned card…",
                                                self._verify_bootdisk_card, primary=True)
        self._text(self.bd_pages["complete"], "Camera startup is enabled. Continue to choose your features.")
        for page in ("camera", "verify", "complete"):
            self.words.widget(self.ttk.Button, self.bd_pages[page], text="Show setup and recovery options", style="Link.TButton",
                            command=lambda: self._bootdisk_page("setup")).pack(anchor="w", pady=(M, 0))
        self.bd_cbasic_busy = Busy(self, tab)
        self.bootdisk_guide_status = Callout(self, tab)

    def _bootdisk_page(self, page):
        if self._card_busy():
            return
        self.wizard.saved.bootdisk_page = page
        self.wizard.save()
        self.bootdisk_guide_status.clear()
        self.refresh()
        self.panels[BOOTDISK][2].yview_moveto(0)

    def _bootdisk_changed(self):
        self.wizard.saved.bootdisk_page = "auto"
        self.wizard.set_bootdisk("confirmed" if self.bootdisk_var.get() else "unknown")
        self.refresh()

    def _test_panel(self):
        tab = self._panel(TEST)
        self._text(tab, "Check each item as you test the camera", "Subheading.TLabel")
        self.test_vars, self.test_boxes = [], []
        for i, text in enumerate(TEST_CHECKLIST):
            var = self.tk.BooleanVar(value=False)
            box = self.words.widget(self.ttk.Checkbutton, tab, variable=var, text=text, command=lambda i=i, v=var: (
                self.wizard.set_test_item(i, v.get()), self.refresh()))
            box.pack(anchor="w", pady=(S, 0))
            self.test_vars.append(var)
            self.test_boxes.append(box)
        self._text(tab, TEST_RECOVERY, "Muted.TLabel", pady=(M, 0))
        row = self._buttons(tab, pady=(L, 0))
        self._button(row, "Open the test report form  ↗", lambda: webbrowser.open(self.wizard.report_url()))

    def _full_path(self):
        self.wizard.start_full()
        self.refresh()

    def _update_path(self):
        reasons = self.wizard.start_update()
        if reasons:
            self.messagebox.showinfo(self.words.render("Update my card"), self.words.render(joined("\n", reasons)))
        self.utility = None
        self._hint = None
        self.refresh()

    def _switch_mode(self):
        if self.wizard.saved.mode == "update":
            self._full_path()
        else:
            self._update_path()

    def _sidebar_clicked(self, step_id):
        self.utility = None
        self._hint = None
        if step_id in self.wizard.path() and not self.wizard.goto(step_id):
            info = STEP_BY_ID[step_id]
            self._hint = message(
                "Step {number} is locked. {reason}",
                number=info.number, reason=phrase(self.wizard.status(step_id).message))
        self.refresh()
        return "break"

    def _side_focus(self, step_id, delta):
        path = self.wizard.path()
        if step_id in path:
            target = path[max(0, min(len(path) - 1, path.index(step_id) + delta))]
            self.side_rows[target]["parts"][0].focus_set()
        return "break"

    def _side_hover(self, step_id, inside):
        if step_id in self.tool_rows:
            self.tool_rows[step_id]["hover"] = inside
            self._paint_tool_row(step_id)
            return
        self.side_rows[step_id]["hover"] = inside
        self._paint_row(step_id)

    def _open_utility(self, key):
        self.utility = key
        self.refresh()
        self.utilities[key][2].yview_moveto(0)
        return "break"

    def _paint_tool_row(self, key):
        p, f = self.theme.palette, self.theme.fonts
        row, bar, mark, title, _status = self.tool_rows[key]["parts"]
        current = key == self.utility
        bg = p.accent_subtle if current else (p.hover if self.tool_rows[key]["hover"] else p.surface)
        for widget in self.tool_rows[key]["parts"][2:]:
            widget.configure(background=bg)
        row.configure(background=bg, highlightbackground=bg)
        bar.configure(background=p.accent if current else bg)
        title.configure(font=f["subheading"] if current else f["body"])
        if current:
            self.draw_mark(mark, INFO, bg, text="↓")
        else:
            self.draw_mark(mark, NEUTRAL, bg, text="↓", ring=True, colour=p.text_muted)

    def _visible(self):
        """(id, (frame, check, canvas)) of what the content area shows: a utility or the current step."""
        if self.utility is not None:
            return self.utility, self.utilities[self.utility]
        return self.wizard.current, self.panels[self.wizard.current]

    def _paint_row(self, step_id):
        p, f, w = self.theme.palette, self.theme.fonts, self.wizard
        row, bar, mark, title, status = self.side_rows[step_id]["parts"]
        st, info = w.status(step_id).status, STEP_BY_ID[step_id]
        current = step_id == w.current and self.utility is None
        bg = p.accent_subtle if current else (p.hover if self.side_rows[step_id]["hover"] else p.surface)
        for widget in (row, mark, title, status):
            widget.configure(background=bg)
        row.configure(highlightbackground=bg)
        bar.configure(background=p.accent if current else bg)
        title.configure(font=f["subheading"] if current else f["body"],
                        foreground=p.text_disabled if st == BLOCKED else p.text)
        card_size = GUIDE[step_id].card
        self.words.configure(status, text=message('{status}  ·  {badge}', status=phrase(SIDEBAR_STATUS[st]),
                                                     badge=phrase(CARD_BADGES[card_size])) if card_size else SIDEBAR_STATUS[st])
        if st == DONE:
            self.draw_mark(mark, SUCCESS, bg)
        elif st == SKIPPED:
            self.draw_mark(mark, NEUTRAL, bg, text="–")
        elif current:
            self.draw_mark(mark, INFO, bg, text=str(info.number))
        else:
            self.draw_mark(mark, NEUTRAL, bg, text=str(info.number), ring=True,
                           colour=p.text_disabled if st == BLOCKED else p.text_muted)

    def _next(self):
        self._hint = None
        w = self.wizard
        if w.is_last() and not w.next_blockers():
            w.next()  # records the step; there is no step after it
            self.refresh()
            self._finish()
            return
        w.next()
        self.refresh()

    def _finish(self):
        if self.wizard.saved.mode == "update":
            text = ("Your card has the new build and it was read back.\n\n"
                    "Eject the card, put it in the camera and switch the camera on. If the camera freezes or "
                    "shows an error, remove the battery, then the card, and power on without the card.")
        else:
            text = ("Setup is complete. Thank you for testing.\n\n"
                    "Please send a test report with 'Open the test report form' on this step, including what "
                    "didn't work. Next time, choose 'Update my card' in the sidebar to change features and "
                    "copy a new build.")
        self.messagebox.showinfo(self.words.render("All done"), self.words.render(text))

    def _back(self):
        self._hint = None
        if self.utility is not None:
            self.utility = None  # back to the setup step that was open
        else:
            self.wizard.back()
        self.refresh()

    def _refresh_wizard(self):
        w = self.wizard
        path, current = w.path(), w.current
        if path != self._side_path:
            for info in STEPS:
                self.side_rows[info.id]["parts"][0].pack_forget()
            for step_id in path:
                self.side_rows[step_id]["parts"][0].pack(fill="x")
            self._side_path = list(path)
        for step_id in path:
            self._paint_row(step_id)
        missing = w.one_time_done()
        if w.saved.mode == "update":
            self.words.configure(self.mode_label, text="Path: Update my card. The one-time steps are already done.")
            self.words.configure(self.mode_link, text="Show all nine steps")
            self.mode_link.pack(anchor="w", padx=S, pady=(XS, 0), after=self.mode_label)
        else:
            self.words.configure(self.mode_label, text="Path: full setup. Done steps stay done when you come back.")
            self.words.configure(self.mode_link, text="Update my card instead")
            if missing:  # the short path needs the one-time steps
                self.mode_link.pack_forget()
            else:
                self.mode_link.pack(anchor="w", padx=S, pady=(XS, 0), after=self.mode_label)
        for key, (frame, _check, _canvas) in self.utilities.items():
            if key == self.utility:
                if not frame.winfo_manager():
                    frame.pack(fill="both", expand=True)
            elif frame.winfo_manager():
                frame.pack_forget()
        for key in self.tool_rows:
            self._paint_tool_row(key)
        for step_id, (frame, check, _canvas) in self.panels.items():
            if step_id == current and self.utility is None:
                st = w.status(step_id)
                if check is not None:
                    check.set(STEP_KINDS[st.status], STEP_HEADLINES[st.status], st.message.removeprefix("Skipped: ") if st.status == SKIPPED else st.message)
                if not frame.winfo_manager():
                    frame.pack(fill="both", expand=True)
            elif frame.winfo_manager():
                frame.pack_forget()
        if self.utility is not None:
            self.words.configure(self.back_button, state="normal", text="Back to setup")
            self.words.configure(self.next_button, state="disabled", text="Next")
            self.words.configure(self.nav_note, text="")
        else:
            self.words.configure(self.back_button, state="normal" if path.index(current) > 0 else "disabled", text="Back")
            blockers = w.next_blockers()
            self.words.configure(self.next_button, state="disabled" if blockers else "normal",
                                       text="Finish" if w.is_last() else "Continue" if current == ROM else "Next")
            self.words.configure(self.nav_note, text=self._hint or (blockers[0] if blockers else ""),
                                    style="Hint.Caption.TLabel" if self._hint else "Caption.TLabel")
        self.camera_var.set(w.saved.camera_confirmed)
        self.update_button.configure(state="disabled" if missing else "normal")
        if missing:
            self.returning_owner.pack_forget()
        else:
            self.returning_owner.pack(fill="x")
        self.bootdisk_var.set(w.saved.bootdisk in ("confirmed", "verified"))
        self.bootdisk_box.configure(state="normal" if not self._card_busy() and (self.bootdisk_ack.get() or
                                   self.bootdisk_var.get()) else "disabled")
        test_blocked = w.status(TEST).status == BLOCKED
        for i, (var, box) in enumerate(zip(self.test_vars, self.test_boxes)):
            var.set(i in w.saved.test_checked)
            box.configure(state="disabled" if test_blocked else "normal")

    def _bind_scrolling(self):
        """Wheel and trackpad scrolling for the current step (not for logs or drop-downs)."""
        def target(event):
            widget = event.widget
            if not isinstance(widget, self.tk.Misc) or isinstance(widget, (self.tk.Text, self.ttk.Combobox)):
                return None
            if not str(widget).startswith(str(self.content)):
                return None
            canvas = self._visible()[1][2]
            first, last = canvas.yview()
            return canvas if (first, last) != (0.0, 1.0) else None

        def scroll(event, pixels):
            canvas = target(event)
            if canvas is not None and pixels:
                canvas.yview_scroll(int(pixels), "units")

        def wheel(event):
            delta = event.delta
            scroll(event, -delta * 0.4 if abs(delta) >= 120 else -delta * 8)

        def touchpad(event):
            dy = int(event.delta) & 0xFFFF
            scroll(event, -(dy - 0x10000 if dy >= 0x8000 else dy))
        def reveal(event):
            """Keyboard focus scrolls the focused control into view."""
            key, (_frame, _check, canvas) = self._visible()
            widget, inner = event.widget, self._inners.get(key)
            if inner is None or not isinstance(widget, self.tk.Misc) or not str(widget).startswith(str(inner) + "."):
                return
            total = max(1, inner.winfo_height())
            top, height = canvas.canvasy(0), canvas.winfo_height()
            y = widget.winfo_rooty() - inner.winfo_rooty()
            if y - M < top:
                canvas.yview_moveto(max(0, y - M) / total)
            elif y + widget.winfo_height() + M > top + height:
                canvas.yview_moveto((y + widget.winfo_height() + M - height) / total)
        self.root.bind_all("<FocusIn>", reveal, add="+")
        self.root.bind_all("<MouseWheel>", wheel, add="+")
        self.root.bind_all("<Button-4>", lambda e: scroll(e, -48), add="+")
        self.root.bind_all("<Button-5>", lambda e: scroll(e, 48), add="+")
        try:
            self.root.bind_all("<TouchpadScroll>", touchpad, add="+")  # Tk 9 trackpads
        except self.tk.TclError:
            pass

    def _background(self, work, done):
        """Run work() off the UI thread; done(result) runs back on it."""
        def target():
            try:
                result = work()
            except Exception as exc:  # surfaced to the person, never swallowed
                result = exc
            self.events.put((done, result))
        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        """Deliver background results on the UI thread; a failing callback never stops the loop."""
        try:
            while True:
                try:
                    done, result = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    done(result)
                except Exception:
                    traceback.print_exc()
                    try:
                        self._append_log("Internal error (details printed to the terminal):\n"
                                         + traceback.format_exc(limit=1))
                    except Exception:
                        pass
        finally:
            self.root.after(100, self._poll)

    # -- tabs ---------------------------------------------------------------

    def _rom_tab(self):
        tab = self._panel(ROM)
        host = self.ttk.Frame(tab)
        host.pack(fill="x", pady=(M, 0))
        self.rom_pages = {name: self.ttk.Frame(host) for name in ("prepare", "camera", "return", "file")}
        start = self.rom_pages["prepare"]
        self._text(start, "At your computer", "Subheading.TLabel", pady=(0, S))
        self._badge(start, GUIDE[ROM].card).pack(anchor="w")
        if self.prep.supported:
            self._text(start, "Back up your photos first. Insert a camera-formatted SDHC card into your Mac’s "
                       "built-in SD slot. Use FAT32 with at least 300 MiB free.")
        else:
            self._text(start, "Preparing a new card requires a Mac with a built-in SD slot. On this computer, "
                       "you can use an existing firmware copy or import one from a returned card.")
        selector = self.ttk.Frame(start)
        if self.prep.supported:
            selector.pack(fill="x")
        self.rom_find_button, self.rom_card_choice, self.rom_card_combo, self.rom_card_note = (
            self._cbasic_card_selector(selector))
        self.rom_prep_row = self.ttk.Frame(selector)
        self.rom_prep_button = self._button(self.rom_prep_row, "Prepare this card…",
            lambda: self._prepare_cbasic_card("dump", self.rom_guide_status), primary=True)

        saved_copy = self.words.widget(self.ttk.Button, start, text="I already have a firmware copy…", command=self._pick_rom,
                                     style="Link.TButton" if self.prep.supported else "Primary.TButton")
        saved_copy.pack(anchor="w", pady=(M, 0))
        self.words.widget(self.ttk.Button, start, text="I’m returning with a card from the camera", style="Link.TButton",
                        command=lambda: self._rom_page("return")).pack(anchor="w")
        help_box = self.ttk.Frame(start)
        help_toggle = self.words.widget(self.ttk.Button, start, text="▸  Which SD card can I use?", style="Toggle.TButton")
        help_toggle.pack(anchor="w", pady=(M, 0))

        def toggle_card_help():
            if help_box.winfo_manager():
                help_box.pack_forget()
                self.words.configure(help_toggle, text="▸  Which SD card can I use?")
            else:
                help_box.pack(fill="x")
                self.words.configure(help_toggle, text="▾  Which SD card can I use?")

        help_toggle.configure(command=toggle_card_help)
        self._text(help_box, "For a fresh copy, use a card formatted in the camera, without the mod file "
                   "AUTOEXEC.BIN or boot markers. A USB reader cannot prepare it. If you already have a "
                   "firmware copy on this computer, you can choose that file without a card.")
        self.rom_stage_button = self._button(self._buttons(help_box, pady=(XS, 0)),
            "Use a card already marked for scripts…",
            lambda: self._stage_cbasic_folder("dump", self.rom_guide_status))

        camera = self.rom_pages["camera"]
        self._text(camera, "At your camera", "Subheading.TLabel", pady=(0, S))
        self._list(camera, CAMERA_CARD_CHECKLIST, numbered=True)
        self._button(self._buttons(camera), "I’ve returned the card to my computer",
                     lambda: self._rom_page("return"), primary=True)

        returned = self.rom_pages["return"]
        self._text(returned, "Back at your computer", "Subheading.TLabel", pady=(0, S))
        self._text(returned, "Choose the SD card in the folder picker. The Assistant will find the firmware "
                   "copy, save it on this computer and verify it.")
        self.rom_import_button = self._button(self._buttons(returned), "Import and check firmware copy…",
                                             self._import_rom_from_card, primary=True)
        self._button(self._buttons(returned), "Review camera instructions", lambda: self._rom_page("camera"))

        file = self.rom_pages["file"]
        self.rom_result_title = self._text(file, "", "Subheading.TLabel", pady=(0, S))
        self.rom_var = self.tk.StringVar()
        self._field(file, "Local firmware file", self.rom_var, [("Choose a different file…", self._pick_rom)])
        self.rom_status = StatusLine(self, file)
        for page in (camera, returned, file):
            self._button(self._buttons(page, pady=(S, 0)), "Back to firmware copy options",
                         lambda: self._rom_page("prepare"))
        self.rom_cbasic_busy = Busy(self, tab)
        self.rom_guide_status = Callout(self, tab)

    def _rom_page(self, page):
        if self._card_busy():
            return
        self.wizard.saved.rom_page = page
        self.wizard.save()
        self.rom_guide_status.clear()
        self.refresh()
        self.panels[ROM][2].yview_moveto(0)

    def _features_tab(self):
        tab = self._panel(FEATURES)
        self._text(tab, "Choose one feature set", "Subheading.TLabel")
        self.profile_var = self.tk.StringVar(value=self.state.profile)
        self.profile_cards = {}
        for view in self.state.profile_views():
            box = self._card(tab, pady=(M if not self.profile_cards else S, 0))
            self.words.widget(self.ttk.Radiobutton, box, text=view.label, value=view.id, variable=self.profile_var,
                                 style="Strong.TRadiobutton", command=self._profile_changed).pack(anchor="w")
            indent = self.ttk.Frame(box, padding=(26, 0, 0, 0))
            indent.pack(fill="x")
            self._text(indent, view.description, pady=(XS, 0), inset=CARD_INSET + 26)
            evidence = self._disclosure(indent, "What has been tested?")
            self._text(evidence, message(
                "{status_label}. {status_text}",
                status_label=phrase(view.status_label), status_text=phrase(view.status_text)), "Caption.TLabel", inset=CARD_INSET + 26)
            self.profile_cards[view.id] = box

        # Options and WAV inputs that the chosen profile doesn't use are hidden, not greyed out.
        self.option_widgets = {}
        for view in self.state.option_views():
            slot = Slot(self.ttk, tab, pady=(L, 0))
            box = self._card(slot.frame, view.label, pady=(0, 0))
            self._text(box, view.description, pady=(0, 0), inset=CARD_INSET)
            var = self.tk.StringVar()
            combo = self.ttk.Combobox(box, textvariable=var, state="readonly", width=40,
                                      values=[c.label for c in view.choices])
            combo.pack(anchor="w", fill="x", pady=(S, 0))
            combo.bind("<<ComboboxSelected>>", lambda _e, oid=view.id: self._option_changed(oid))
            reason = self._text(box, "", "Caption.TLabel", inset=CARD_INSET)
            self.option_widgets[view.id] = (combo, var, reason, slot, box)
            slot.hide(box)  # refresh() shows it with the slot's spacing when the profile uses it

        self.input_widgets = {}
        for i, view in enumerate(self.state.input_views()):
            slot = Slot(self.ttk, tab, pady=(L if i == 0 else S, 0))
            box = self._card(slot.frame, view.label, pady=(0, 0))
            var = self.tk.StringVar()
            (button,) = self._field(box, None, var, [("Choose WAV…", lambda i=view.id: self._pick_wav(i))],
                                    pady=(S, 0))
            status = StatusLine(self, box)
            self.input_widgets[view.id] = (var, button, status, slot, box)
            slot.hide(box)
        self.wav_note_slot = Slot(self.ttk, tab, pady=(S, 0))
        self.wav_note = self.words.widget(self.ttk.Label, self.wav_note_slot.frame, style="Caption.TLabel", justify="left", text=(
            "WAV files must be 48 kHz, mono, 16-bit PCM (see docs/usage.md for an ffmpeg command). "
            "They are embedded unchanged; nothing is trimmed or normalised."))
        self.wrap(self.wav_note)

    def _toolchain_tab(self):
        tab = self._panel(TOOLCHAIN)
        self.tc_status = StatusLine(self, tab)
        self.tc_install = self.ttk.Frame(tab)
        self._text(self.tc_install, "The Assistant checks for the compiler when it opens. If it’s already verified, "
                   "continue to the build. Otherwise install the matching compiler, then select its bin folder.")
        self.words.widget(self.ttk.Button, self.tc_install, text="Open the xPack releases page  ↗", style="Link.TButton", cursor="hand2",
                        command=lambda: webbrowser.open(checks.TOOLCHAIN_RELEASES_URL)).pack(anchor="w", pady=(S, 0))
        self.tc_var = self.tk.StringVar()
        self.tc_fields = self.ttk.Frame(tab)
        self.tc_fields.pack(fill="x")
        self._field(self.tc_fields, "Compiler’s bin folder", self.tc_var, [
            ("Choose bin folder…", self._pick_toolchain),
            ("Detect again", lambda: self._detect_toolchain(explicit=True))])

    def _build_tab(self):
        tab = self._panel(BUILD)
        self.out_var = self.tk.StringVar()
        self.blockers = Callout(self, tab)
        row = self._buttons(tab, pady=(L, 0))
        self.build_button = self._button(row, "Build my mod", self._build, primary=True)
        self.build_busy = Busy(self, tab)
        self.result = Callout(self, tab)
        destination = self._disclosure(tab, "Build folder")
        self._field(destination, "Save the next build in", self.out_var, [("Change…", self._pick_output)])
        self.memory_slot = Slot(self.ttk, tab, pady=(M, 0))
        self.memory = self.ttk.Frame(self.memory_slot.frame)
        self.words.widget(self.ttk.Label, self.memory, text="Canon memory left after this build", style="Caption.TLabel").pack(anchor="w")
        self.gauge = self.ttk.Progressbar(self.memory, maximum=100, style="Accent.Horizontal.TProgressbar")
        self.gauge.pack(fill="x", pady=(XS, 0))
        buttons = self._buttons(tab)
        self.open_out = self._button(buttons, "Open output folder", lambda: open_path(self.summary.output_dir),
                                     state="disabled")
        self.open_contents = self._button(buttons, "Open 'what's in this build'",
                                          lambda: open_path(self.summary.output_dir / runner.CONTENTS_NAME),
                                          state="disabled")
        self.log_toggle = self.words.widget(self.ttk.Button, tab, text="▸  Build log", style="Toggle.TButton", cursor="hand2",
                                          command=lambda: self._show_log(not self.log_box.winfo_manager()))
        self.log_toggle.pack(anchor="w", pady=(L, 0))
        self.log_slot = Slot(self.ttk, tab, fill="both", pady=(XS, 0))
        self.log, self.log_box = self._textbox(self.log_slot.frame, height=10, pady=(0, 0))
        self.log_box.pack_forget()

    def _show_log(self, show: bool):
        """The build log is folded away; it opens on request and when a build fails."""
        if show:
            self.log_box.pack(in_=self.log_slot.frame, fill="both", expand=True)
        else:
            self.log_slot.hide(self.log_box)
        self.words.configure(self.log_toggle, text="▾  Build log" if show else "▸  Build log")

    def _card_tab(self):
        tab = self._panel(COPY)
        self._text(tab, "Select the card’s top folder if it isn’t already shown.")
        self._text(tab, "Any existing autoexec.bin is backed up to the build folder on this computer first; nothing "
                        "on the card is deleted and the card is never formatted.", "Muted.TLabel")
        self.card_var = self.tk.StringVar()
        self._field(tab, "Card folder", self.card_var, [("Choose card folder…", self._pick_card)])
        self.card_report = Callout(self, tab)
        self.card_confirm = self.tk.BooleanVar(value=False)
        self.card_confirm_slot = Slot(self.ttk, tab, pady=(M, 0))  # shown only when there are warnings
        self.card_confirm_box = self.words.widget(self.ttk.Checkbutton,
            self.card_confirm_slot.frame, variable=self.card_confirm, command=self.refresh,
            text="I have checked the warnings above and this really is my prepared camera card.")
        row = self._buttons(tab)
        self.copy_button = self._button(row, "Copy mod to this card", self._copy, primary=True)
        self.copy_busy = Busy(self, tab)
        self.copy_result = Callout(self, tab)

    def _prep_tab(self):
        tab = self._panel(BOOTCARD, extra_details=EXPLAIN)
        Callout(self, tab, pady=(S, 0)).set(
            DANGER, FORMAT_WARNING, card_devices.UNTESTED_NOTE.get(self.prep.platform, "This system: not supported."))
        self._text(tab, "SD card", "Caption.TLabel", pady=(L, XS))
        row = self.ttk.Frame(tab)
        row.pack(fill="x")
        self.prep_find = self.words.widget(self.ttk.Button, row, text="Find SD cards", command=self._prep_find)
        self.prep_find.pack(side="left")
        self.prep_choice = self.tk.StringVar()
        self.prep_combo = self.ttk.Combobox(row, textvariable=self.prep_choice, state="disabled", width=40)
        self.prep_combo.pack(side="left", padx=(S, 0), fill="x", expand=True)
        self.prep_combo.bind("<<ComboboxSelected>>", lambda _e: self._select_card(self.prep_combo))
        self.prep_details = self._text(tab, "", "Muted.TLabel", full=True)
        buttons = self._buttons(tab)
        self.prep_button = self._button(buttons, "Prepare card", lambda: self._prep_run("mark"), primary=True)
        self.prep_busy = Busy(self, tab)
        self.prep_blockers = self._text(tab, "", "Caption.TLabel", full=True)
        self.prep_title = Callout(self, tab)
        self.prep_log_slot = Slot(self.ttk, tab, pady=(S, 0))
        self.prep_log, self.prep_log_box = self._textbox(self.prep_log_slot.frame, height=8, pady=(0, 0))
        self.prep_log_box.pack_forget()
        undo = self._disclosure(tab, "Restore a card backup")
        self._text(undo, "Puts back the boot sectors from a backup, with the same read, backup and confirmation "
                         "as Prepare.", "Caption.TLabel", pady=(0, 0), inset=CARD_INSET)
        buttons = self._buttons(undo, pady=(S, 0))
        self.prep_undo = self._button(buttons, "Undo (restore the latest backup)", lambda: self._prep_run("undo"))
        self.prep_restore = self._button(buttons, "Restore from a chosen backup…", lambda: self._prep_run("restore"))
        row = self._buttons(tab, pady=(L, 0))
        self._button(row, "My card is already prepared (skip this step)",
                     lambda: (self.wizard.set_card_prepared("elsewhere"), self.refresh()))

    def _card_busy(self) -> bool:
        return bool(self.cbasic_busy or self.prep.busy or self.prep.session is not None or self.copying)

    def _selected_rom_args(self) -> tuple[str, dict[str, str | None]]:
        options = {
            oid: (self.state.options[oid] if self.state.manifest.option_availability(self.state.profile, oid)[0] else None)
            for oid in self.state.manifest.options
        }
        return self.state.profile, options

    @staticmethod
    def _refused_cards(cards):
        rows = (message("{whole_id} ({model}): {reason}", whole_id=c.whole_id,
                        model=c.model or c.bus or "?", reason=phrase(c.refusals[0])) for c in cards)
        return message("Not offered: {cards}", cards=joined("; ", rows))

    def _refresh_cbasic(self):
        p = self.prep
        accepted = p.accepted
        busy = self._card_busy()
        values = [self.words.render(c.summary()) for c in accepted]
        selected_text = self.words.render(p.selected.summary()) if p.selected else ""
        lines: list[str] = []
        unsuitable = p.selected and (p.selected.filesystem != "FAT32" or
                                     (p.selected.disk_bytes or 0) >= layout.SDHC_MAX_SECTORS * layout.SECTOR)
        if p.selected:
            lines.append(message('Selected: {card}', card=phrase(p.selected.summary())))
            if unsuitable:
                lines.append("Note: guided Canon Basic requires a FAT32 SDHC card (up to 32 GB); "
                             "SDXC / exFAT cards are untested and refused.")
        elif p.searched and not accepted:
            lines.append(p.error or "No usable SD card found. Check the card is in the built-in SD slot and search again.")
        elif p.searched:
            lines.append("Choose your FAT32 SDHC card above.")
        elif not p.supported:
            lines.append("Preparing raw SCRIPT sectors is offered on macOS only; use 'Stage ... on folder' "
                         "if your card already has SCRIPT.")
        else:
            lines.append("Click 'Find SD cards' to locate your FAT32 SDHC card (up to 32 GB).")
        if p.refused:
            lines.append(self._refused_cards(p.refused))
        note = joined("\n", lines)
        for find_btn, choice, combo, note_label in (
            (self.rom_find_button, self.rom_card_choice, self.rom_card_combo, self.rom_card_note),
            (self.bd_find_button, self.bd_card_choice, self.bd_card_combo, self.bd_card_note),
        ):
            find_btn.configure(state="disabled" if busy else "normal")
            combo.configure(values=values, state="readonly" if accepted and not busy else "disabled")
            choice.set(selected_text)
            self.words.configure(note_label, text=note)

        self.words.configure(self.rom_card_note, text=note if p.searched or p.selected else "")
        if accepted:
            self.rom_card_combo.pack(side="left", padx=(S, 0), fill="x", expand=True)
        else:
            self.rom_card_combo.pack_forget()
        self.words.configure(self.rom_find_button, style="TButton" if p.selected else "Primary.TButton",
                                       text="Search again" if p.searched else "Find my SD card")
        if p.selected and not unsuitable:
            self.rom_prep_row.pack(fill="x", pady=(S, 0))
        else:
            self.rom_prep_row.pack_forget()
        for name, frame in self.rom_pages.items():
            if name == self.wizard.saved.rom_page:
                if not frame.winfo_manager():
                    frame.pack(fill="x")
            else:
                frame.pack_forget()
        can_raw = p.supported and p.selected is not None and not unsuitable and not busy
        self.rom_prep_button.configure(state="normal" if can_raw else "disabled")
        self.rom_stage_button.configure(state="normal" if not busy else "disabled")
        self.rom_import_button.configure(state="normal" if not busy else "disabled")
        page = self.wizard.saved.bootdisk_page
        if page == "auto":
            page = "complete" if self.wizard.saved.bootdisk in ("confirmed", "verified") else "setup"
        elif page.startswith("camera_"):
            self.words.configure(self.bd_camera_title, text="At your camera · disable card startup" if page == "camera_disable" else
                                 "At your camera · enable card startup")
            page = "camera"
        for name, frame in self.bd_pages.items():
            if name == page:
                if not frame.winfo_manager():
                    frame.pack(fill="x", before=self.bd_cbasic_busy.slot.frame)
            else:
                frame.pack_forget()
        ack = self.bootdisk_ack.get()
        self.bd_find_button.configure(state="normal" if ack and not busy else "disabled",
                                      style="TButton" if p.selected else "Primary.TButton")
        if not p.searched:
            self.words.configure(self.bd_card_note, text="")
        if accepted:
            self.bd_card_combo.pack(side="left", padx=(S, 0), fill="x", expand=True)
        else:
            self.bd_card_combo.pack_forget()
        if p.selected and not unsuitable:
            self.bd_enable_button.pack(side="left")
        else:
            self.bd_enable_button.pack_forget()
        self.bd_enable_button.configure(state="normal" if (ack and can_raw) else "disabled")
        self.bd_disable_button.configure(state="normal" if (ack and can_raw) else "disabled")
        self.bd_stage_button.configure(state="normal" if (ack and not busy) else "disabled")
        self.bd_verify_card_button.configure(state="normal" if not busy else "disabled")
        self.bd_verify_file_button.configure(state="normal" if not busy else "disabled")
        self.rom_cbasic_busy.set((self.cbasic_busy or p.busy) if self.wizard.current == ROM else None)
        self.bd_cbasic_busy.set(self.cbasic_busy if self.wizard.current == BOOTDISK else None)

    @staticmethod
    def _set_cbasic_status(callout: Callout, text: str, kind: str = NEUTRAL):
        if text.strip():
            callout.set(kind, *lines_of(text))
        else:
            callout.clear()

    @staticmethod
    def _cbasic_task_label(task: str) -> str:
        return {
            "dump": "SaveAllRomImageToFile() (read-only ROM dump)",
            "enable_bootdisk": "EnableBootDisk() + SaveAllRomImageToFile()",
            "disable_bootdisk": "DisableBootDisk() + SaveAllRomImageToFile()",
        }[task]

    def _default_card_initialdir(self) -> str:
        if self.prep.selected and self.prep.selected.mount_point:
            return self.prep.selected.mount_point
        return "/Volumes" if sys.platform == "darwin" else str(Path.home())

    def _prepare_cbasic_card(self, task: str, status_callout: Callout):
        if self._card_busy():
            self._set_cbasic_status(status_callout, "Wait for the current card operation to finish.", WARNING)
            return
        if not self.prep.supported:
            self._set_cbasic_status(status_callout, "Preparing raw SCRIPT sectors is offered on macOS only.", DANGER)
            return
        cand = self.prep.selected
        if cand is None:
            self._set_cbasic_status(status_callout, "Click 'Find SD cards' and choose your FAT32 SDHC card first.", WARNING)
            return
        if task == "disable_bootdisk":
            if not self.messagebox.askokcancel(
                self.words.render("Disable BootDisk (hardware-untested)"),
                self.words.render("DisableBootDisk() (0xE0B38D74) is the stock inverse in the R5 2.2.1 ROM, "
                "but it has NEVER been run on real camera hardware yet.\n\n"
                "Only proceed if you accept the risk of testing this on your camera. Continue?"),
                icon="warning",
            ):
                return

        ops = card_devices.MacOps()
        self.cbasic_busy = "Reading card and checking FAT32 SDHC layout (macOS asks for your password)..."
        self._set_cbasic_status(status_callout, self.cbasic_busy, INFO)
        self.refresh()

        def read_done(session):
            if isinstance(session, Exception):
                self.cbasic_busy = None
                self._set_cbasic_status(status_callout, message(
                    "Card preparation stopped\n{session}",
                    session=session), DANGER)
                self.refresh()
                self.messagebox.showerror(self.words.render("Canon Basic card preparation"), self.words.render(error_text(session)))
                return
            if session.needs_write:
                c = session.snapshot.candidate
                sectors = ", ".join(str(w.sector) for w in session.plan.writes)
                prompt = joined("\n\n", [
                    message('Prepare {card} for Canon Basic?', card=c.label()),
                    message('{model}, {whole_id}.', model=c.model, whole_id=c.whole_id),
                    message(
                        "This writes the Canon Basic SCRIPT marker at FAT32 boot sector byte 496 (sector(s) {sectors}, "
                        "{size:,} bytes), then stages script.req and extend.m for {task}.",
                        sectors=sectors, size=len(session.plan.writes) * 512,
                        task=phrase(self._cbasic_task_label(task))),
                    "Nothing else on the card is changed. macOS will ask for your administrator password.",
                    message('Backup of the card as it is now: {backup}', backup=session.backup.folder),
                ])
                if not self.messagebox.askokcancel(self.words.render("Prepare Canon Basic card"), self.words.render(prompt), icon="warning"):
                    self.cbasic_busy = "Mounting the card again..."
                    self.refresh()
                    self._background(lambda: card_flow.cancel(session, ops), lambda r: self._cbasic_cancelled(r, status_callout))
                    return
                self.cbasic_busy = "Writing SCRIPT marker and verifying (macOS asks for your password)..."
                self._set_cbasic_status(status_callout, self.cbasic_busy, INFO)
                self.refresh()

                def write_and_refresh():
                    report = card_flow.write(session, ops)
                    refreshed = None
                    if report.ok:
                        try:
                            refreshed = ops.refresh(session.snapshot.candidate)
                        except Exception:
                            refreshed = session.snapshot.candidate
                    return report, refreshed, session.backup.folder

                self._background(write_and_refresh, lambda r: self._cbasic_script_written(r, task, status_callout))
            else:
                def refresh_mounted():
                    try:
                        refreshed = ops.refresh(session.snapshot.candidate)
                    except Exception:
                        refreshed = session.snapshot.candidate
                    return None, refreshed, session.backup.folder

                self._background(refresh_mounted, lambda r: self._cbasic_script_written(r, task, status_callout))

        self._background(lambda: card_flow.start(cand, ops, self.prep.backup_root, kind="script"), read_done)

    def _cbasic_cancelled(self, report, status_callout: Callout):
        self.cbasic_busy = None
        lines = ["Cancelled. Nothing was written."]
        if not isinstance(report, Exception) and report is not None:
            lines = [report.title] + report.lines
        self._set_cbasic_status(status_callout, joined("\n", lines), WARNING)
        self.refresh()

    def _cbasic_script_written(self, outcome, task: str, status_callout: Callout):
        if isinstance(outcome, Exception):
            self.cbasic_busy = None
            self._set_cbasic_status(status_callout, message('Failed\n{outcome}', outcome=outcome), DANGER)
            self.refresh()
            self.messagebox.showerror(self.words.render("Canon Basic card preparation"), self.words.render(error_text(outcome)))
            return
        report, refreshed, backup_folder = outcome
        if report is not None and not report.ok:
            self.cbasic_busy = None
            msg = joined("\n", [report.title] + report.lines)
            self._set_cbasic_status(status_callout, msg, DANGER)
            self.refresh()
            self.messagebox.showinfo(self.words.render("Canon Basic card preparation"), self.words.render(msg))
            return
        mount_point = refreshed.mount_point if refreshed else ""
        if not mount_point:
            self.cbasic_busy = None
            msg = ("SCRIPT marker is on the card, but the card did not report a mount folder.\n"
                   "Mount the card and stage the files on its folder.")
            if task == "dump":
                msg = joined(" ", [msg, "Open ‘Which SD card can I use?’ and choose ‘Use a card already marked for scripts’."])
            self._set_cbasic_status(status_callout, msg, WARNING)
            self.refresh()
            self.messagebox.showinfo(self.words.render("Canon Basic card preparation"), self.words.render(msg))
            return
        prefix = [report.title] + report.lines if report is not None else ["Canon Basic SCRIPT marker already present."]
        self._stage_cbasic_folder_impl(Path(mount_point), task, status_callout, backup_dir=backup_folder,
                                       prefix_lines=prefix)

    def _stage_cbasic_folder(self, task: str, status_callout: Callout):
        if self._card_busy():
            self._set_cbasic_status(status_callout, "Wait for the current card operation to finish.", WARNING)
            return
        if task == "disable_bootdisk" and not self.messagebox.askokcancel(
            self.words.render("Disable BootDisk (hardware-untested)"),
            self.words.render("DisableBootDisk() (0xE0B38D74) has NEVER been run on real camera hardware yet. Continue?"),
            icon="warning",
        ):
            return
        folder = self.filedialog.askdirectory(
            title=self.words.render("Choose the FAT32 SDHC card's top folder (must already have SCRIPT)"),
            initialdir=self._default_card_initialdir(),
        )
        if not folder:
            return
        backup_dir = self.prep.backup_root / f"cbasic-{task}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self._stage_cbasic_folder_impl(Path(folder), task, status_callout, backup_dir=backup_dir)

    def _stage_cbasic_folder_impl(self, folder: Path, task: str, status_callout: Callout, *,
                                  backup_dir: Path, prefix_lines: list[str] | tuple[str, ...] = ()):
        try:
            info = card.inspect_card(folder)
        except OSError as exc:
            self.cbasic_busy = None
            self._set_cbasic_status(status_callout, message(
                "Could not inspect {folder}\n{exc}",
                folder=folder, exc=exc), DANGER)
            self.refresh()
            return
        if info.errors:
            self.cbasic_busy = None
            msg = joined(" ", info.errors)
            self._set_cbasic_status(status_callout, message('Card refused\n{msg}', msg=phrase(msg)), DANGER)
            self.refresh()
            self.messagebox.showerror(self.words.render("Card refused"), self.words.render(msg))
            return
        if info.existing is not None:
            self.cbasic_busy = None
            msg = message(
                "{name} is on this card. Remove or back up AUTOEXEC.BIN first so a BootDisk-enabled camera starts "
                "normal Canon firmware for Canon Basic.",
                name=info.existing.name)
            self._set_cbasic_status(status_callout, message('AUTOEXEC.BIN present\n{msg}', msg=phrase(msg)), DANGER)
            self.refresh()
            self.messagebox.showerror(self.words.render("AUTOEXEC.BIN present"), self.words.render(msg))
            return
        if info.warnings and not self.messagebox.askokcancel(
            self.words.render("Confirm card folder"),
            self.words.render(joined("\n\n", [joined("\n", info.warnings), "Stage script.req and extend.m in this folder anyway?"])),
            icon="warning",
        ):
            self.cbasic_busy = None
            self._set_cbasic_status(status_callout, "Staging cancelled.", WARNING)
            self.refresh()
            return

        self.cbasic_busy = "Staging script.req and extend.m..."
        self._set_cbasic_status(status_callout, self.cbasic_busy, INFO)
        self.refresh()

        def done(staged):
            self.cbasic_busy = None
            if isinstance(staged, Exception):
                self._set_cbasic_status(status_callout, message(
                    "Could not stage files\n{staged}",
                    staged=staged), DANGER)
                self.refresh()
                self.messagebox.showerror(self.words.render("Staging failed"), self.words.render(error_text(staged)))
                return
            mode_key = "dump" if task == "dump" else "bootdisk"
            self.cbasic_baselines[mode_key] = (staged.card_root, staged.baseline_gang1)
            if task == "dump":
                self._rom_page("camera")
                self._set_cbasic_status(status_callout, joined("\n", list(prefix_lines) + staged.messages), SUCCESS)
                return
            self._bootdisk_page("camera_disable" if task == "disable_bootdisk" else "camera_enable")
            self._set_cbasic_status(status_callout, joined("\n", list(prefix_lines) + staged.messages), SUCCESS)

        self._background(
            lambda: cbasic.stage_script_files(info.root, task, backup_dir, confirmed_warnings=info.warnings),
            done,
        )

    def _import_rom_from_card(self):
        self._collect_from_returned_card("rom", self.rom_guide_status)

    def _verify_bootdisk_card(self):
        self._collect_from_returned_card("bootdisk", self.bootdisk_guide_status)

    def _collect_from_returned_card(self, mode: str, status_callout: Callout):
        if self._card_busy():
            self._set_cbasic_status(status_callout, "Wait for the current card operation to finish.", WARNING)
            return
        folder = self.filedialog.askdirectory(
            title=self.words.render("Choose the returned SD card folder"),
            initialdir=self._default_card_initialdir(),
        )
        if not folder:
            return
        resolved = Path(folder).resolve()
        mode_key = "dump" if mode == "rom" else "bootdisk"
        baseline_entry = self.cbasic_baselines.get(mode_key)
        if baseline_entry and baseline_entry[0] == resolved:
            baseline_gang1, require_new = baseline_entry[1], True
        else:
            baseline_gang1, require_new = None, False
        before_rom = None
        if mode == "bootdisk" and self.state.rom_path is not None and self.state.rom_path.is_file():
            before_rom = self.state.rom_path
        profile, options = self._selected_rom_args()
        backup_dir = self.prep.backup_root / f"cbasic-returned-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        self.cbasic_busy = "Reading and verifying gang1NN.bin from the card..."
        self._set_cbasic_status(status_callout, self.cbasic_busy, INFO)
        self.refresh()

        def done(returned):
            self.cbasic_busy = None
            if isinstance(returned, Exception):
                self._set_cbasic_status(status_callout, message(
                    "Verification failed\n{returned}",
                    returned=returned), DANGER)
                self.refresh()
                self.messagebox.showerror(self.words.render("ROM dump / BootInfo check"), self.words.render(error_text(returned)))
                return
            self.cbasic_baselines.pop(mode_key, None)
            if mode == "rom":
                self.state.set_rom(returned.local_path)
                self.wizard.saved.rom_page = "file"
                self.wizard.save()
                if returned.bootinfo.state == "enabled":
                    self.wizard.saved.bootdisk_page = "auto"
                    self.wizard.set_bootdisk("verified", rom_path=returned.local_path, rom_sha256=returned.sha256)
                kind = SUCCESS
            else:
                self.wizard.saved.bootdisk_page = "auto"
                if returned.bootinfo.state == "enabled":
                    self.wizard.set_bootdisk("verified", rom_path=returned.local_path, rom_sha256=returned.sha256)
                    if self.state.rom_path is None:
                        self.state.set_rom(returned.local_path)
                    kind = SUCCESS
                else:
                    self.wizard.set_bootdisk("disabled")
                    kind = WARNING
            text = joined("\n", returned.messages)
            self._set_cbasic_status(status_callout, text, kind)
            self.refresh()
            self.messagebox.showinfo(self.words.render("ROM dump verified"), self.words.render(text))

        self._background(
            lambda: cbasic.collect_returned_dump(
                resolved,
                cbasic.DEFAULT_DUMP_ROOT,
                baseline_gang1=baseline_gang1,
                require_new=require_new,
                before_rom=before_rom,
                profile=profile,
                options=options,
                clean_scripts=True,
                backup_dir=backup_dir,
            ),
            done,
        )

    def _verify_bootdisk_file(self):
        if self._card_busy():
            return
        target: Path | None = None
        if self.state.rom_path is not None and self.state.rom_path.is_file():
            choice = self.messagebox.askyesnocancel(
                self.words.render("Verify BootDisk from ROM"),
                self.words.render(message(
                    "Check the ROM dump already selected in Step 2?\n\n{rom_path}\n\nYes = check Step 2's ROM\nNo = choose another ROM file",
                    rom_path=self.state.rom_path)),
            )
            if choice is None:
                return
            if choice:
                target = self.state.rom_path
        if target is None:
            picked = self.filedialog.askopenfilename(title=self.words.render("Choose a 64 MiB ROM dump (e.g. gang101.bin)"))
            if not picked:
                return
            target = Path(picked)

        before_path = None
        if self.state.rom_path is not None and self.state.rom_path.is_file() and target != self.state.rom_path:
            before_path = self.state.rom_path
        profile, options = self._selected_rom_args()
        self.cbasic_busy = "Checking the camera startup setting…"
        self._set_cbasic_status(self.bootdisk_guide_status, self.cbasic_busy, INFO)
        self.refresh()

        def done(result):
            self.cbasic_busy = None
            if isinstance(result, Exception):
                self.refresh()
                self._set_cbasic_status(self.bootdisk_guide_status, message(
                    "Check failed\n{result}",
                    result=result), DANGER)
                return
            if not result.ok:
                self._set_cbasic_status(self.bootdisk_guide_status, message(
                    "ROM refused\n{message}",
                    message=phrase(result.message)), DANGER)
                self.refresh()
                return
            self.wizard.saved.bootdisk_page = "auto"
            if result.facts.get("boot_disk_state") == "enabled":
                self.wizard.set_bootdisk("verified", rom_path=target, rom_sha256=result.facts["rom_sha256"])
                self._set_cbasic_status(self.bootdisk_guide_status, message(
                    "BootDisk enabled\n{message}",
                    message=phrase(result.message)), SUCCESS)
            else:
                self.wizard.set_bootdisk("disabled")
                self._set_cbasic_status(
                    self.bootdisk_guide_status,
                    message(
                        "BootDisk disabled\n{message} Enable BootDisk on the camera before running autoexec.bin.",
                        message=phrase(result.message)),
                    WARNING,
                )
            self.refresh()

        self._background(
            lambda: checks.check_bootinfo(target, before_path=before_path, profile=profile, options=options),
            done,
        )

    def _refresh_prep(self):
        p = self.prep
        accepted = p.accepted
        busy = self._card_busy()
        self.prep_combo.configure(values=[self.words.render(c.summary()) for c in accepted],
                                  state="readonly" if accepted and not busy else "disabled")
        self.prep_choice.set(self.words.render(p.selected.summary()) if p.selected else "")
        lines = p.selected.details() if p.selected else []
        if p.searched and not accepted:
            lines.append(p.error or "No usable SD card found. Check the card is in the built-in SD slot and search again.")
        if p.refused:
            lines.append(self._refused_cards(p.refused))
        self.words.configure(self.prep_details, text=joined("\n", lines))
        self.prep_details.pack_configure(pady=(S, 0) if lines else (0, 0))
        blocked = p.blockers() + ([message(
            "Wait: {cbasic_busy}",
            cbasic_busy=phrase(self.cbasic_busy))] if self.cbasic_busy else [])
        if p.selected:
            self.prep_button.pack(side="left")
        else:
            self.prep_button.pack_forget()
        if accepted:
            self.prep_combo.pack(side="left", padx=(S, 0), fill="x", expand=True)
        else:
            self.prep_combo.pack_forget()
        self.prep_find.configure(state="disabled" if busy else "normal",
                                 style="TButton" if p.selected else "Primary.TButton")
        for button in (self.prep_button, self.prep_undo, self.prep_restore):
            button.configure(state="disabled" if blocked else "normal")
        self.prep_busy.set(p.busy)
        self.words.configure(self.prep_blockers, text="" if not blocked else message(
            "Next:\n{steps}",
            steps=bullets(blocked)))
        if p.error:
            self.prep_title.set(DANGER, p.error)
            body = []
        elif p.report:
            self.prep_title.set(SUCCESS if p.report.ok else DANGER, p.report.title)
            body = p.report.lines
        else:
            self.prep_title.clear()
            body = []
        self._set_text(self.prep_log, joined("\n", body))
        if body:
            self.prep_log_slot.show(self.prep_log_box)
        else:
            self.prep_log_slot.hide(self.prep_log_box)

    def _prep_find(self):
        if self._card_busy():
            return
        self.prep.busy = "Looking for SD cards..."
        self.refresh()

        def done(result):
            self.prep.busy = None
            if isinstance(result, Exception):
                self.prep.set_candidates([])
                self.prep.error = message('Could not list cards: {result}', result=result)
            else:
                self.prep.set_candidates(result)
            self.refresh()
        self._background(card_devices.enumerate_candidates, done)

    def _prep_run(self, kind):
        """Prepare ("mark"), Undo ("undo": the latest backup) or Restore ("restore": a chosen backup)."""
        p = self.prep
        if p.blockers() or self.cbasic_busy:
            return
        cand, source = p.selected, None
        if kind == "undo":
            if card_flow.latest_backup(cand, p.backup_root) is None:  # early hint; the choice is made after the read
                p.failed(message(
                    "No backup of this card from before it was prepared was found in {backup_root}. Use 'Restore from a "
                    "chosen backup...' instead.",
                    backup_root=p.backup_root))
                self.refresh()
                return
            source = card_flow.LATEST
        elif kind == "restore":
            folder = self.filedialog.askdirectory(title=self.words.render("Choose a card backup folder (it contains manifest.json)"),
                                                  initialdir=str(p.backup_root))
            if not folder:
                return
            try:
                source = card_flow.load_backup(Path(folder))
            except card_flow.PrepFailure as exc:
                p.failed(error_text(exc))
                self.refresh()
                return
        p.restore_from = source if isinstance(source, card_flow.Backup) else None
        token = p.begin("Reading the card and saving a backup (macOS asks for your password)...")
        ops = card_devices.MacOps()
        self.refresh()

        def read_done(result):
            if isinstance(result, Exception):
                p.failed(message("Not prepared: {result}" if kind == "mark" else "Not restored: {result}", result=result))
                self.refresh()
                return
            verdict = p.read_finished(token, result)
            if verdict == "stale":
                p.busy = "Mounting the card again..."
                self.refresh()
                self._background(lambda: card_flow.cancel(result, ops) if result.needs_write else None,
                                 lambda _r: (setattr(p, "busy", None), self.refresh()))
                return
            self.refresh()
            if verdict == "done":
                if kind == "mark" and result.report.ok:
                    self.wizard.set_card_prepared("prepared")
                    self.refresh()
                return
            title, text = p.confirmation()
            ok = self.messagebox.askokcancel(self.words.render(title), self.words.render(text), icon="warning")
            if not ok or not p.may_write(token, result):
                p.busy = "Mounting the card again..."
                p.session = None
                self.refresh()
                note = "Cancelled at the confirmation." if not ok else \
                    "The card or choice changed during the confirmation; nothing was written."
                self._background(lambda: card_flow.cancel(result, ops), lambda r: self._prep_finished(r, note))
                return
            p.begin_write(result)
            self.refresh()
            self._background(lambda: card_flow.write(result, ops),
                             lambda r: self._prep_finished(r, None, show=True, kind=kind))
        self._background(lambda: card_flow.start(cand, ops, p.backup_root, restore_from=source), read_done)

    def _prep_finished(self, report, note, show=False, kind=None):
        if isinstance(report, Exception):  # write() and cancel() report instead of raising; be safe anyway
            report = card_flow.Report(False, "Card state unknown.", [f"{report}",
                                      "Read the card again (Prepare or Undo) before using it."])
        if kind == "mark" and report.ok:
            self.wizard.set_card_prepared("prepared")
        elif kind in ("undo", "restore") and report.ok:
            self.wizard.set_card_prepared(None)
        if note:
            report.lines.insert(0, note)
        self.prep.finished(report)
        self.refresh()
        if show:
            self.messagebox.showinfo(self.words.render("Card preparation"), self.words.render(joined("\n", [report.title] + report.lines)))

    def _on_close(self):
        p = self.prep
        if self.shots.busy and not self.messagebox.askokcancel(
                self.words.render("Quit"), self.words.render("Screenshots are still being converted. Quit anyway? The card is not changed.")):
            return
        if (p.busy or self.cbasic_busy) and not self.messagebox.askokcancel(
                self.words.render("Quit"), self.words.render("A card operation is still running. Quitting now can leave the card unmounted. Quit anyway?")):
            return
        if p.session is not None:
            try:
                card_flow.cancel(p.session, card_devices.MacOps())
            except Exception:
                pass
        self.root.destroy()

    # -- refresh --------------------------------------------------------------

    def refresh(self):
        s = self.state
        self._refresh_wizard()
        self.rom_var.set(str(s.rom_path or ""))
        check = s.rom_check()
        self.rom_status.show_check(check)
        self.words.configure(self.rom_result_title, text="Firmware copy verified" if check.status == OK else
                                        "Choose another firmware copy" if check.status == FAILED else
                                        "Checking your firmware copy…")
        self.profile_var.set(s.profile)
        for profile_id, box in self.profile_cards.items():
            box.configure(style="Selected.Card.TFrame" if profile_id == s.profile else "Card.TFrame")
        for view in s.option_views():
            combo, var, reason, slot, box = self.option_widgets[view.id]
            (slot.show if view.enabled else slot.hide)(box)
            combo.configure(state="readonly" if view.enabled else "disabled")
            choice = self._show_option(view)
            if not view.enabled:
                self.words.configure(reason, text=view.reason)
            elif choice.status_label:
                self.words.configure(reason, text=message(
                    "Status: {status_label}. {status_text}",
                    status_label=phrase(choice.status_label), status_text=phrase(choice.status_text)))
            else:
                self.words.configure(reason, text="Off. With both record cues off, no indicator code, hook or memory is added.")
        any_wav = False
        for view in s.input_views():
            var, button, status, slot, box = self.input_widgets[view.id]
            (slot.show if view.enabled else slot.hide)(box)
            any_wav = any_wav or view.enabled
            var.set(str(view.path or ""))
            button.configure(state="normal" if view.enabled else "disabled")
            if view.enabled:
                status.show_check(view.check)
            else:
                status.set(NEUTRAL, view.reason)
        (self.wav_note_slot.show if any_wav else self.wav_note_slot.hide)(self.wav_note)
        self.tc_var.set(str(s.toolchain_path or ""))
        if s.toolchain_check().status == OK or self._detecting:
            self.tc_install.pack_forget()
        else:
            self.tc_install.pack(fill="x", before=self.tc_fields)
        if not self._detecting:
            self.tc_status.show_check(s.toolchain_check())
        self.out_var.set(str(s.output_dir or ""))
        blockers = s.blockers()
        if blockers:
            self.blockers.set(NEUTRAL, "Before you can build:", bullets(blockers))
        else:
            self.blockers.set(SUCCESS, "Ready to build.")
        self.build_button.configure(state="disabled" if blockers else "normal")
        self.build_busy.set("Building... this usually takes under a minute." if s.building else None)
        ready_to_copy = self.summary is not None and self.card_info is not None and not self.card_info.errors \
            and (not self.card_info.warnings or self.card_confirm.get()) and not s.building and not self.copying \
            and not self.prep.blocks_copy and not self.cbasic_busy
        if self.card_info:
            self.copy_button.pack(side="left")
        else:
            self.copy_button.pack_forget()
        self.copy_button.configure(state="normal" if ready_to_copy else "disabled")
        warned = bool(self.card_info and self.card_info.warnings)
        self.card_confirm_box.configure(state="normal" if warned else "disabled")
        (self.card_confirm_slot.show if warned else self.card_confirm_slot.hide)(self.card_confirm_box)
        self.root.after_idle(self._offer_prepared_card)
        self.copy_busy.set("Copying..." if self.copying else None)
        self._refresh_cbasic()
        self._refresh_prep()
        self._refresh_shots()
        for what, input_id in s.needs_check():
            self._start_check(what, input_id)

    def _start_check(self, what, input_id=None):
        s = self.state
        key = s.begin(what, input_id)
        if what == "rom":
            rom, profile, options = key
            work = lambda: checks.check_rom(Path(rom), profile=profile, options=dict(options))
        elif what == "toolchain":
            work = lambda: checks.check_toolchain(Path(key[0]))
        else:
            work = lambda: checks.check_wav(Path(key[0]))

        def done(result):
            if isinstance(result, Exception):
                result = checks.CheckResult(False, message('Check failed: {result}', result=result))
            s.finish(what, key, result.ok, result.message, result.facts, input_id=input_id)
            self.refresh()
        self._background(work, done)

    # -- actions --------------------------------------------------------------

    def _pick_rom(self):
        if self._card_busy():
            return
        path = self.filedialog.askopenfilename(title=self.words.render("Choose your camera’s firmware copy (ROM dump)"))
        if path:
            self.state.set_rom(Path(path))
            self._rom_page("file")

    def _profile_changed(self):
        self.state.set_profile(self.profile_var.get())
        self._new_output()
        self.wizard.features_changed()
        self.refresh()

    def _option_changed(self, option_id):
        combo, var = self.option_widgets[option_id][:2]
        view = next(v for v in self.state.option_views() if v.id == option_id)
        value = view.choices[combo.current()].value
        try:
            self.state.set_option(option_id, value)
        except ValueError as exc:
            self.messagebox.showerror(self.words.render("Option not available"), self.words.render(error_text(exc)))
        self._new_output()
        self.wizard.features_changed()
        self.refresh()

    def _new_output(self):
        """A fresh, selection-named folder in the same parent (the builder needs a new folder each time)."""
        parent = self.state.output_dir.parent if self.state.output_dir else bundle.builds_root(ROOT)
        name = default_output_dir(self.state.profile, self.state.options, self.state.manifest).name
        self.state.set_output_dir(parent / name)

    def _pick_wav(self, input_id):
        path = self.filedialog.askopenfilename(title=self.words.render(self.state.manifest.inputs[input_id].label),
                                               filetypes=[(self.words.render("WAV audio"), "*.wav *.WAV"),
                                                          (self.words.render("All files"), "*")])
        if path:
            self.state.set_input(input_id, Path(path))
            self.wizard.features_changed()
            self.refresh()

    def _pick_toolchain(self):
        path = self.filedialog.askdirectory(title=self.words.render("Choose the toolchain's bin folder (contains arm-none-eabi-gcc)"))
        if path:
            self.state.set_toolchain(Path(path))
            self.refresh()

    def _detect_toolchain(self, explicit=False):
        self._detecting = True
        self.tc_status.set(BUSY, "Looking for an installed GNU Arm 15.2.1 toolchain...")

        def done(result):
            self._detecting = False
            self.refresh()
            if isinstance(result, Exception):
                self.tc_status.set(DANGER, message('Detection failed: {result}', result=result))
                return
            path, check, notes = result
            if path is None:
                headline = ("No GNU Arm 15.2.1 toolchain found. Download and unpack the compiler, "
                            "then choose its bin folder.")
                self.tc_status.set(DANGER, joined("\n", [headline, *notes]))
                return
            self.state.offer_detected_toolchain(path, check.message, check.facts, explicit=explicit)
            self.refresh()
        self._background(checks.detect_toolchain, done)

    def _pick_output(self):
        parent = self.filedialog.askdirectory(title=self.words.render("Choose where to create the new output folder"))
        if parent:
            name = default_output_dir(self.state.profile, self.state.options, self.state.manifest).name
            self.state.set_output_dir(Path(parent) / name)
            self.refresh()

    def _append_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _build(self):
        s = self.state
        argv = s.build_argv()
        out = s.output_dir
        s.building = True
        self.summary = None
        self.result.clear()
        self.memory_slot.hide(self.memory)
        self.gauge.configure(value=0)
        self._set_text(self.log, "")
        self._append_log("python3 tools/build.py " + " ".join(argv))
        self.refresh()

        def on_line(line):
            self.events.put((self._append_log, line))

        def done(outcome):
            s.building = False
            if isinstance(outcome, Exception):
                self.result.set(DANGER, "Build could not run", error_text(outcome))
                self._show_log(True)
            elif not outcome.ok:
                self.result.set(DANGER, "Build refused or failed", outcome.error_text())
                self._show_log(True)
            else:
                self._built(out)
            self._new_output()
            self.refresh()
        self._background(lambda: runner.run_build(argv, out, on_line), done)

    def _built(self, out):
        try:
            summary = runner.summarize(out)
            contents = runner.write_contents(summary, self.state.manifest)
        except (OSError, ValueError, KeyError) as exc:
            self.result.set(DANGER, "Build finished but its record could not be read", error_text(exc))
            return
        self.summary = summary
        self.wizard.set_build(summary if summary.sha256_matches_build_json else None,
                              note=None if summary.sha256_matches_build_json else "The build does not match build.json.")
        matches = summary.sha256_matches_build_json
        match = "matches build.json" if matches else "DOES NOT MATCH build.json"
        self.result.set(SUCCESS if matches else DANGER,
                        "Built and verified" if matches else "Built, but autoexec.bin DOES NOT MATCH build.json",
                        message(
                            "Built: {autoexec}\nSHA-256 {sha256} ({match})\n{memory}\nWhat's in this build: {contents}",
                            autoexec=summary.autoexec, sha256=summary.sha256, match=phrase(match),
                            memory=phrase(runner.memory_line(summary)), contents=contents), mono=True)
        self.gauge.configure(value=100 * summary.remaining_heap / summary.heap_pool)
        self.memory_slot.show(self.memory)
        self.open_out.configure(state="normal")
        self.open_contents.configure(state="normal")
        self._append_log(runner.contents_text(summary, self.state.manifest))

    def _offer_prepared_card(self):
        """On the Copy step, choose the card from step 7 for the person if it is mounted and nothing is chosen."""
        if self.utility is not None or self.wizard.current != COPY or self.card_var.get() or self._card_busy():
            return
        mount = self.prep.selected.mount_point if self.prep.selected else None
        if mount and Path(mount).is_dir():
            self._inspect_card_folder(mount)

    def _pick_card(self):
        path = self.filedialog.askdirectory(title=self.words.render("Choose the card's top folder"), initialdir=self._default_card_initialdir())
        if path:
            self._inspect_card_folder(path)

    def _inspect_card_folder(self, path):
        self.card_var.set(path)
        self.card_confirm.set(False)
        self.card_info = None
        self.card_report.set(BUSY, "Looking at the card...")
        self.refresh()
        self._background(lambda: card.inspect_card(Path(path)), lambda info: self._card_inspected(path, info))

    def _card_inspected(self, path, info):
        if self.card_var.get() != path:
            return  # another folder was chosen meanwhile
        if isinstance(info, Exception):
            self.card_report.set(DANGER, "Could not look at the card", error_text(info))
            return
        self.card_info = info
        lines = [message('Folder: {root}', root=info.root)]
        if info.total_bytes is not None:
            lines.append(message('Drive size: {size:,.1f} GB', size=info.total_bytes / 1000 ** 3))
        lines.append("DCIM folder: found" if info.has_dcim else "DCIM folder: not found")
        lines.append(message(
            "Existing autoexec.bin: {name}",
            name=info.existing.name) if info.existing else "Existing autoexec.bin: none")
        lines += [message(
            "REFUSED: {reason}",
            reason=phrase(e)) for e in info.errors] + [message(
            "Warning: {reason}",
            reason=phrase(w)) for w in info.warnings]
        if self.summary is None:
            lines.append("Build autoexec.bin first (step 6).")
        if info.errors:
            kind, headline = DANGER, "This folder is refused"
        elif info.warnings:
            kind, headline = WARNING, "Check the warnings before you copy"
        else:
            kind, headline = NEUTRAL, "Card folder checked"
        self.card_report.set(kind, headline, joined("\n", lines))
        self.refresh()

    def _copy(self):
        summary, info = self.summary, self.card_info
        backups = summary.output_dir / "card-backup"
        if not self.messagebox.askokcancel(
            self.words.render("Copy to card"),
            self.words.render(message(
                "Copy autoexec.bin to {root}?\n\nAny existing autoexec.bin is backed up to {backups} first.",
                root=info.root, backups=backups)),
        ):
            return
        if self.prep.blocks_copy or self.cbasic_busy:
            return  # a card preparation step is running or waiting for confirmation
        confirmed = list(info.warnings) if self.card_confirm.get() else []
        self.copying = self.prep.copying = True
        self.copy_result.clear()
        self.refresh()

        def done(result):
            self.copying = self.prep.copying = False
            if isinstance(result, (card.CardError, OSError)):
                self.copy_result.set(DANGER, "Not copied", error_text(result))
            elif isinstance(result, Exception):
                self.copy_result.set(DANGER, "Copy failed unexpectedly", error_text(result))
            else:
                self.copy_result.set(SUCCESS, "Copy verified. Eject the card before removing it.", joined("\n", result.messages))
                try:
                    self.wizard.set_copied(result.sha256)
                except ValueError:
                    pass  # a newer build replaced the one that was copied
                self.messagebox.showinfo(self.words.render("Done"), self.words.render(joined("\n\n", result.messages)))
            self.refresh()
        screenshot = summary.options.get("screenshot", "off") != "off"
        self._background(lambda: card.copy_to_card(summary.autoexec, summary.sha256, info.root, backups,
                                                   confirmed_warnings=confirmed, screenshot_folder=screenshot), done)

    # -- screenshots (a utility, not a setup step) ---------------------------------------

    def _screenshots_panel(self):
        tab = self._panel(shots.SCREENSHOTS, utility=True)
        Callout(self, tab, pady=(S, 0)).set(INFO, "Where these files come from", shots.LAB_NOTE)
        self.shots_src_var = self.tk.StringVar()
        self._field(tab, "Screenshots from (the card's top folder, DCIM, or an R5SHOTS folder)", self.shots_src_var,
                    [("Choose card or folder…", self._pick_shots_source)], pady=(L, 0))
        self.shots_src_status = StatusLine(self, tab)
        self.shots_out_var = self.tk.StringVar()
        self._field(tab, "Save the PNG files in (on this computer)", self.shots_out_var,
                    [("Change…", self._pick_shots_output)])
        self.shots_out_status = StatusLine(self, tab)
        row = self._buttons(tab, pady=(L, 0))
        self.shots_button = self._button(row, "Convert to PNG", self._convert_shots, primary=True)
        self.shots_open = self._button(row, "Open output folder", lambda: open_path(self.shots.output_dir))
        self.shots_busy = Busy(self, tab)
        self.shots_blockers = self._text(tab, "", "Caption.TLabel", full=True)
        self.shots_result = Callout(self, tab)
        self.shots_log_slot = Slot(self.ttk, tab, pady=(S, 0))
        self.shots_log, self.shots_log_box = self._textbox(self.shots_log_slot.frame, height=8, pady=(0, 0))
        self.shots_log_box.pack_forget()

    def _refresh_shots(self):
        st = self.shots
        src = st.source
        self.shots_src_var.set(str(src.picked) if src else "")
        if src is None:
            self.shots_src_status.set(NEUTRAL, "No folder chosen yet.")
        else:
            self.shots_src_status.set(SUCCESS if src.files else WARNING, src.describe())
        self.shots_out_var.set(str(st.output_dir))
        problem = st.output_problem()
        if problem:
            self.shots_out_status.set(DANGER, problem)
        elif st.output_dir.is_dir():
            self.shots_out_status.set(NEUTRAL, "This folder exists. Screenshots already converted into it are "
                                               "skipped; existing files are never overwritten.")
        else:
            self.shots_out_status.set(NEUTRAL, "A new folder. It is created when you convert.")
        blockers = st.blockers()
        self.shots_button.configure(state="disabled" if blockers else "normal")
        self.shots_open.configure(state="normal" if st.can_open_output() else "disabled")
        self.shots_busy.set(st.busy)
        shown = [b for b in blockers if b != problem and not (src and b == src.describe())] if not st.busy else []
        self.words.configure(self.shots_blockers, text=bullets(shown))
        self.shots_blockers.pack_configure(pady=(S, 0) if shown else (0, 0))
        report = st.report()
        if report is None:
            self.shots_result.clear()
            lines = []
        else:
            kind, headline, lines = report
            self.shots_result.set(kind, headline)
        self._set_text(self.shots_log, joined("\n", lines))
        if lines:
            self.shots_log_slot.show(self.shots_log_box)
        else:
            self.shots_log_slot.hide(self.shots_log_box)

    def _pick_shots_source(self):
        folder = self.filedialog.askdirectory(title=self.words.render("Choose the card's top folder, or a folder with screenshots"))
        if not folder:
            return
        self.shots.busy = "Looking for screenshots..."
        self.refresh()

        def done(result):
            self.shots.busy = None
            if isinstance(result, Exception):
                self.shots.set_source(shots.Source(Path(folder), None, (), False))
                self.shots.error = message('Could not look at {folder}: {result}', folder=folder, result=result)
            else:
                self.shots.set_source(result)
            self.refresh()
        self._background(lambda: shots.find_source(Path(folder)), done)

    def _pick_shots_output(self):
        folder = self.filedialog.askdirectory(title=self.words.render("Choose where to save the PNG files"))
        if folder:
            self.shots.set_output(Path(folder))
            self.refresh()

    def _convert_shots(self):
        st = self.shots
        if st.blockers():
            return
        source, out = st.begin()
        self.refresh()

        def progress(index, total, name):
            self.events.put((lambda _r: (st.progress(index, total, name), self._refresh_shots()), None))

        def done(result):
            st.finished(result)
            self.refresh()
        self._background(lambda: screenshots_mod.convert_folder(source.shots, out, picked=source.picked,
                                                                home=st.home, on_progress=progress), done)


def main() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, font as tkfont, messagebox, ttk
    except ImportError:
        print(
            "This Python has no Tkinter, which R5 Mod Assistant needs.\n"
            "- macOS/Windows: the python.org installer includes it.\n"
            "- Homebrew Python: install the matching python-tk formula.\n"
            "- Linux: install your distribution's python3-tk package.\n"
            "The command-line builder (tools/build.py) works without it.",
            file=sys.stderr,
        )
        return 1
    app = App(tk, ttk, filedialog, messagebox, tkfont)
    app.root.mainloop()
    return 0


def self_test() -> int:
    """`--self-test`: build the window hidden, show every step in both appearances, then exit.

    Saved progress goes to a temporary folder (the person's own wizard.json is never
    read or written) and the toolchain search is skipped. It also starts the builder the
    way a build does, with arguments the builder refuses, so nothing is built or written.
    For CI and for checking a packaged app; prints one line per check."""
    import tempfile
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, messagebox, ttk
    from gui import wizard as wizard_mod

    def check(ok, what):
        print(("ok    " if ok else "FAIL  ") + what, flush=True)
        if not ok:
            raise SystemExit(1)

    missing = [str(path) for path in DOCS.values() if not path.is_file()]
    check(not missing, "linked documents are present" + (f" (missing {', '.join(missing)})" if missing else ""))
    import toolchain
    try:
        toolchain.controlled_environment()  # the builder refuses to run with ambient build overrides
        refused = ""
    except ValueError as exc:
        refused = f" ({exc})"
    check(not refused, "the builder accepts this environment" + refused)
    saved_state, saved_detect = wizard_mod.DEFAULT_STATE_FILE, checks.detect_toolchain
    with tempfile.TemporaryDirectory() as tmp:
        wizard_mod.DEFAULT_STATE_FILE = Path(tmp) / "wizard.json"
        checks.detect_toolchain = lambda: (None, None, [])
        try:
            for appearance in ("light", "dark"):
                root = tk.Tk()
                root.withdraw()
                try:
                    app = App(tk, ttk, filedialog, messagebox, tkfont, root=root, appearance=appearance)
                    for step in STEPS:
                        app.wizard.saved.current = step.id
                        app.refresh()
                        root.update_idletasks()
                        check(app.panels[step.id][0].winfo_manager() != "", f"{appearance}: step {step.id}")
                    app._open_utility(shots.SCREENSHOTS)
                    root.update_idletasks()
                    check(app.utilities[shots.SCREENSHOTS][0].winfo_manager() != "", f"{appearance}: screenshots")
                finally:
                    root.destroy()
            out = Path(tmp) / "out"
            outcome = runner.run_build(["--profile", "xpan-crop", "--rom", "r.bin", "--toolchain", "t",
                                        "--output", str(out), "--startup-wav", "a.wav"], out, lambda _line: None)
            check(outcome.returncode == 2 and "forbids --startup-wav" in outcome.error_text() and not out.exists(),
                  f"builder starts and refuses bad arguments ({' '.join(bundle.builder_command(runner.BUILD_PY))})")
        finally:
            wizard_mod.DEFAULT_STATE_FILE, checks.detect_toolchain = saved_state, saved_detect
    print("self-test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
