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

from cardprep import cbasic, devices as card_devices, workflow as card_flow
from gui import card, checks, runner, shots, theme
import screenshots as screenshots_mod
from gui.prep_state import EXPLAIN, FORMAT_WARNING, PrepState
from gui.state import CHECKING, FAILED, OK, AssistantState, default_output_dir
from gui.theme import BUSY, DANGER, INFO, NEUTRAL, SPACE, SUCCESS, WARNING
from gui.wizard import (BLOCKED, BOOTCARD, BOOTDISK, BUILD, CAMERA, COPY, DONE, FEATURES, GUIDE,
                        NEEDS_ACTION, ROM, SKIPPED, STEP_BY_ID, STEPS, TEST, TEST_CHECKLIST, TOOLCHAIN, Wizard)


def verify_build(out: Path):
    """On resume: the last build counts only if autoexec.bin still matches its build.json."""
    try:
        summary = runner.summarize(out)
    except (OSError, ValueError, KeyError):
        return None, f"The previous build in {out} can't be read any more; build again."
    if not summary.sha256_matches_build_json:
        return None, f"The previous build in {out} no longer matches its build.json; build again."
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
    "Getting started guide": ROOT / "docs" / "getting-started.md",
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
WARNING_TEXT = ("Experimental software for the original EOS R5 on firmware 2.2.1 only. "
                "Public builds are hardware-untested. Read the Getting started guide first.")
CHECK_KINDS = {OK: SUCCESS, FAILED: DANGER, CHECKING: BUSY}
STEP_KINDS = {DONE: SUCCESS, SKIPPED: NEUTRAL, BLOCKED: WARNING, NEEDS_ACTION: INFO}
STEP_HEADLINES = {DONE: "Check passed", SKIPPED: "Skipped", BLOCKED: "Blocked", NEEDS_ACTION: "To do"}
XS, S, M, L, XL = (SPACE[k] for k in ("xs", "s", "m", "l", "xl"))
CARD_INSET = 2 * M + 2  # a card's padding and border, for the width of the text inside it


def open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - opening a local folder/file the user selected
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def bullets(lines) -> str:
    return "\n".join(f"•  {line}" for line in lines)


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
    """A tinted box with a status icon, a short headline and optional details."""

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
        inset = CARD_INSET + 32
        app.wrap(self.headline, inset, full=True)
        app.wrap(self.body, inset, full=True)

    def set(self, kind, headline, body="", mono=False):
        self.kind = kind
        self.frame.configure(style=f"Callout.{kind}.TFrame")
        self.headline.configure(text=headline, style=f"Callout.{kind}.Headline.TLabel")
        self.body.configure(text=body, style=f"Callout.{kind}.Mono.TLabel" if mono else f"Callout.{kind}.TLabel")
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
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="x", pady=pady)
        self.icon = ttk.Label(self.frame, width=2, style=f"Status.{NEUTRAL}.Icon.TLabel")
        self.icon.pack(side="left", anchor="n")
        self.text = ttk.Label(self.frame, justify="left", style=f"Status.{NEUTRAL}.TLabel")
        self.text.pack(side="left", anchor="n", fill="x")
        app.wrap(self.text, 24, full=True)

    def set(self, kind, text):
        self.icon.configure(text=theme.GLYPHS[kind], style=f"Status.{kind}.Icon.TLabel")
        self.text.configure(text=text, style=f"Status.{kind}.TLabel")

    def show_check(self, check):
        self.set(CHECK_KINDS.get(check.status, NEUTRAL), check.message or "Not checked yet.")


class Busy:
    """An indeterminate progress bar with a caption, shown only while something runs."""

    def __init__(self, app, parent, pady=(12, 0)):
        ttk = app.ttk
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
            self.label.configure(text=text)
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
        self.wizard = Wizard(self.state, prep_supported=card_devices.prepare_supported())
        self._wrapped: list = []
        self._inners = {}  # step -> the scrolled frame holding its content
        self._detecting = False  # the toolchain search is running
        self._column = theme.CONTENT_MAX_WIDTH

        self.root = tk.Tk() if root is None else root
        self.theme = theme.apply(self.root, ttk, tkfont, appearance=appearance)
        self.root.title("R5 Mod Assistant (prototype)")
        self.root.minsize(*theme.MIN_WINDOW)
        width = max(theme.MIN_WINDOW[0], min(theme.DEFAULT_WINDOW[0], self.root.winfo_screenwidth() - 80))
        height = max(theme.MIN_WINDOW[1], min(theme.DEFAULT_WINDOW[1], self.root.winfo_screenheight() - 120))
        self.root.geometry(f"{width}x{height}")

        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)
        self._sidebar(shell)
        ttk.Separator(shell, orient="vertical").pack(side="left", fill="y")
        main = ttk.Frame(shell)
        main.pack(side="left", fill="both", expand=True)
        self._banner(main)
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
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.state.set_output_dir(default_output_dir(self.state.profile, self.state.options, self.state.manifest))
        self.wizard.load(verify_build=verify_build, verify_bootdisk=verify_bootdisk)
        self.summary = self.wizard.build_summary
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
        column = max(320, min(width, theme.CONTENT_MAX_WIDTH + 2 * XL) - 2 * XL)
        if column == self._column:
            return
        self._column = column
        for label, inset, full in self._wrapped:
            label.configure(wraplength=self._wrap_width(inset, full))
        choice_wrap = self._wrap_width(28, False)
        for name in ("TCheckbutton", "TRadiobutton"):
            self.ttk.Style(self.root).configure(name, wraplength=choice_wrap)

    def _text(self, parent, text="", style="Body.TLabel", pady=(S, 0), inset=0, full=False):
        label = self.ttk.Label(parent, text=text, style=style, justify="left")
        label.pack(anchor="w", fill="x", pady=pady)
        return self.wrap(label, inset, full)

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
            button = self.ttk.Button(row, text=text, command=command)
            button.pack(side="left", padx=(S, 0))
            made.append(button)
        return made

    def _buttons(self, parent, pady=(M, 0)):
        row = self.ttk.Frame(parent)
        row.pack(fill="x", pady=pady)
        return row

    def _button(self, row, text, command=None, primary=False, **kw):
        """A button at the end of a row; primary for the step's main action."""
        first = not row.winfo_children()
        button = self.ttk.Button(row, text=text, command=command, style="Primary.TButton" if primary else "TButton",
                                 **kw)
        button.pack(side="left", padx=(0 if first else S, 0))
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

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def draw_mark(self, canvas, kind, background, text=None, ring=False, colour=None):
        """A round status mark: a filled circle with a glyph, or an outlined ring."""
        p = self.theme.palette
        fg = colour or (self.theme.colours(kind)[0] if kind not in (NEUTRAL, BUSY) else p.text_muted)
        size = int(canvas.cget("width"))
        canvas.configure(background=background)
        canvas.delete("all")
        if ring:
            canvas.create_oval(1, 1, size - 2, size - 2, outline=fg, width=1.5)
            glyph_colour = fg
        else:
            canvas.create_oval(1, 1, size - 2, size - 2, fill=fg, outline=fg)
            glyph_colour = p.bg
        canvas.create_text(size / 2, size / 2, text=text or theme.GLYPHS[kind], fill=glyph_colour,
                           font=self.theme.fonts["eyebrow"])

    # -- frame: sidebar, banner, footer --------------------------------------------

    def _sidebar(self, parent):
        ttk, tk, p, f = self.ttk, self.tk, self.theme.palette, self.theme.fonts
        side = ttk.Frame(parent, style="Sidebar.TFrame", padding=(M, L, M, M), width=theme.SIDEBAR_WIDTH)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = ttk.Frame(side, style="Sidebar.TFrame")
        brand.pack(fill="x", padx=S)
        ttk.Label(brand, text="Canon R5 mod", style="Sidebar.Title.TLabel").pack(side="left")
        ttk.Label(brand, text="PROTOTYPE", style="SidebarBadge.TLabel").pack(side="left", padx=(S, 0))
        ttk.Label(side, text="R5 Mod Assistant", style="Sidebar.Caption.TLabel").pack(anchor="w", padx=S, pady=(2, 0))
        ttk.Label(side, text="STEPS", style="Sidebar.Eyebrow.TLabel").pack(anchor="w", padx=S, pady=(M + S, S))
        rows = ttk.Frame(side, style="Sidebar.TFrame")
        rows.pack(fill="x")
        self.side_rows = {}
        for info in STEPS:
            parts = self._side_row(rows, info.id, info.title, lambda s=info.id: self._sidebar_clicked(s))
            parts[0].bind("<Up>", lambda _e, s=info.id: self._side_focus(s, -1))
            parts[0].bind("<Down>", lambda _e, s=info.id: self._side_focus(s, 1))
            self.side_rows[info.id] = {"parts": parts, "hover": False}
        self._side_path = None
        self.mode_label = ttk.Label(side, text="", style="Sidebar.Caption.TLabel", justify="left",
                                    wraplength=theme.SIDEBAR_WIDTH - 2 * M - 2 * S)
        self.mode_label.pack(anchor="w", padx=S, pady=(M, 0))
        # Below the steps: at the smallest window height this can be cut off; step 1 links to it too.
        ttk.Label(side, text="TOOLS", style="Sidebar.Eyebrow.TLabel").pack(anchor="w", padx=S, pady=(M, XS))
        tools = ttk.Frame(side, style="Sidebar.TFrame")
        tools.pack(fill="x")
        self.tool_rows = {}
        parts = self._side_row(tools, shots.SCREENSHOTS, shots.TITLE, lambda: self._open_utility(shots.SCREENSHOTS))
        parts[0].pack(fill="x")
        parts[4].configure(text="Convert card screenshots")
        self.tool_rows[shots.SCREENSHOTS] = {"parts": parts, "hover": False}

    def _side_row(self, parent, key, text, on_click):
        """A sidebar row: a focus bar, a round mark, a title and a status caption."""
        tk, p, f = self.tk, self.theme.palette, self.theme.fonts
        row = tk.Frame(parent, background=p.surface, highlightthickness=2, highlightbackground=p.surface,
                       highlightcolor=p.accent, takefocus=1, cursor="hand2")
        bar = tk.Frame(row, width=3, background=p.surface)
        bar.grid(row=0, column=0, rowspan=2, sticky="ns")
        mark = tk.Canvas(row, width=22, height=22, highlightthickness=0, borderwidth=0, background=p.surface)
        mark.grid(row=0, column=1, rowspan=2, padx=(S, 10), pady=S)
        title = tk.Label(row, text=text, anchor="w", justify="left", background=p.surface,
                         foreground=p.text, font=f["body"], borderwidth=0, padx=0, pady=0)
        title.grid(row=0, column=2, sticky="sw", pady=(S, 0))
        status = tk.Label(row, text="", anchor="w", background=p.surface, foreground=p.text_muted,
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

    def _banner(self, parent):
        holder = self.ttk.Frame(parent, padding=(XL, M, XL, 0))
        holder.pack(fill="x")
        self.banner = Callout(self, holder, pady=(0, 0), fill=None)
        self.banner.set(WARNING, "", WARNING_TEXT)
        links = self.ttk.Frame(self.banner.frame, style="Banner.TFrame")
        links.grid(row=2, column=1, sticky="w", pady=(XS, 0))
        for label, path in DOCS.items():
            self.ttk.Button(links, text=label, style="Banner.Link.TButton", cursor="hand2",
                            command=lambda p=path: webbrowser.open(p.as_uri())).pack(side="left", padx=(0, M))

    def _footer(self, parent):
        footer = self.ttk.Frame(parent)
        footer.pack(side="bottom", fill="x")
        self.ttk.Separator(footer, orient="horizontal").pack(fill="x")
        nav = self.ttk.Frame(footer, padding=(XL, M))
        nav.pack(fill="x")
        self.back_button = self.ttk.Button(nav, text="←  Back", command=self._back)
        self.back_button.pack(side="left")
        self.next_button = self.ttk.Button(nav, text="Next  →", command=self._next, style="Primary.TButton")
        self.next_button.pack(side="right")
        self.nav_note = self.ttk.Label(nav, text="", style="Caption.TLabel", wraplength=380, justify="right")
        self.nav_note.pack(side="right", padx=M)
        return footer

    # -- steps ---------------------------------------------------------------------

    def _panel(self, step_id: str, extra_details: str = "", *, utility: bool = False):
        """A wizard step: its title, what and why, what you do, a body for its controls, the
        app's check of the step, and a Details expander with the technical facts. A utility
        (outside the setup steps) has the same shape without the step's check."""
        tk, ttk, p = self.tk, self.ttk, self.theme.palette
        if utility:
            title, guide = shots.TITLE, shots.GUIDE
            eyebrow = "TOOL   ·   NOT PART OF SETUP"
        else:
            info, guide = STEP_BY_ID[step_id], GUIDE[step_id]
            title = info.title
            eyebrow = f"STEP {info.number}" + ("   ·   ONE TIME" if info.one_time else "")
        frame = ttk.Frame(self.content)
        canvas = tk.Canvas(frame, highlightthickness=0, borderwidth=0, background=p.bg, yscrollincrement=1)
        bar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview, style="Thin.Vertical.TScrollbar")
        inner = ttk.Frame(canvas, padding=(XL, L, XL, XL))
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def sync(_e=None):
            canvas.configure(scrollregion=(0, 0, inner.winfo_reqwidth(), inner.winfo_reqheight()))
            if inner.winfo_reqheight() > canvas.winfo_height() > 1:
                if not bar.winfo_manager():
                    bar.pack(side="right", fill="y", before=canvas)
            elif bar.winfo_manager():
                bar.pack_forget()
                canvas.yview_moveto(0)

        def resize(e):
            canvas.itemconfigure(window, width=min(e.width, theme.CONTENT_MAX_WIDTH + 2 * XL))
            self._set_column(e.width)
            sync()
        inner.bind("<Configure>", sync)
        canvas.bind("<Configure>", resize)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)

        ttk.Label(inner, text=eyebrow, style="Eyebrow.TLabel").pack(anchor="w")
        self._text(inner, title, "Heading.TLabel", pady=(XS, 0), full=True)
        self._text(inner, guide.why, pady=(M, 0))
        self._text(inner, "What you do", "Subheading.TLabel", pady=(L, 0))
        self._text(inner, guide.todo, pady=(XS, 0))
        body = ttk.Frame(inner)
        body.pack(fill="x", pady=(S, 0))
        check = None if utility else Callout(self, inner, pady=(L, 0))
        details_box = ttk.Frame(inner, style="Details.TFrame", padding=M)
        self.wrap(ttk.Label(details_box, text="\n\n".join(t for t in (guide.details, extra_details) if t),
                            style="Details.TLabel", justify="left"), CARD_INSET).pack(anchor="w")
        toggle = ttk.Button(inner, text="▸  Details", style="Toggle.TButton", cursor="hand2")

        def flip():
            if details_box.winfo_manager():
                details_box.pack_forget()
                toggle.configure(text="▸  Details")
            else:
                details_box.pack(fill="x", pady=(S, 0), after=toggle)
                toggle.configure(text="▾  Details")
        toggle.configure(command=flip)
        toggle.pack(anchor="w", pady=(M, 0))
        (self.utilities if utility else self.panels)[step_id] = (frame, check, canvas)
        self._inners[step_id] = inner
        return body

    def _camera_panel(self):
        tab = self._panel(CAMERA, extra_details=CANNOT_DO)
        self.camera_var = self.tk.BooleanVar(value=False)
        self.ttk.Checkbutton(tab, variable=self.camera_var, command=lambda: (
            self.wizard.set_camera_confirmed(self.camera_var.get()), self.refresh()),
            text="My camera is an original EOS R5 and shows 'Firmware Ver. 2.2.1'.").pack(anchor="w", pady=(S, 0))
        modes = self._card(tab, "Choose your path", pady=(L, 0))
        row = self._buttons(modes, pady=(S, 0))
        self._button(row, "Set up from the start", self._full_path)
        self.update_button = self._button(row, "Update my card (Features > Build > Copy)", self._update_path)
        self.update_note = self._text(modes, "", "Caption.TLabel", pady=(S, 0), inset=CARD_INSET)
        tools = self._card(tab, "Screenshots from the camera", pady=(M, 0))
        self._text(tools, "Turn screenshots a lab build saved on the card into PNG pictures. Not part of the setup.",
                   pady=(0, 0), inset=CARD_INSET)
        row = self._buttons(tools, pady=(S, 0))
        self.shots_entry = self._button(row, "Convert screenshots...", lambda: self._open_utility(shots.SCREENSHOTS))

    def _cbasic_card_selector(self, parent):
        row = self.ttk.Frame(parent)
        row.pack(fill="x", pady=(S, 0))
        find_btn = self.ttk.Button(row, text="Find SD cards", command=self._prep_find)
        find_btn.pack(side="left")
        choice = self.tk.StringVar()
        combo = self.ttk.Combobox(row, textvariable=choice, state="disabled", width=40)
        combo.pack(side="left", padx=(S, 0), fill="x", expand=True)
        combo.bind("<<ComboboxSelected>>", lambda _e, var=choice: self._cbasic_card_selected(var))
        note = self._text(parent, "", "Caption.TLabel", pady=(XS, 0), inset=CARD_INSET, full=True)
        return find_btn, choice, combo, note

    def _cbasic_card_selected(self, choice_var):
        summary = choice_var.get()
        match = next((c for c in self.prep.accepted if c.summary() == summary), None)
        self.prep.select(match.whole_id if match else None)
        self.refresh()

    def _bootdisk_panel(self):
        tab = self._panel(BOOTDISK)
        Callout(self, tab, pady=(S, 0)).set(
            WARNING, "BootDisk is permanent",
            "It stays enabled inside the camera, formatting cards doesn't clear it, and there is no official way back.")
        self.bootdisk_ack = self.tk.BooleanVar(value=False)
        self.ttk.Checkbutton(tab, variable=self.bootdisk_ack, command=self.refresh, text=(
            "I understand that BootDisk is a permanent setting inside the camera, that there is no official way "
            "back, and that I use this at my own risk.")).pack(anchor="w", pady=(M, 0))
        guide = self._card(tab, "Guided BootDisk (Canon Basic card + ROM readback verification)", pady=(L, 0))
        self._text(guide,
                   "Tick the risk acknowledgement above first. Use a camera-formatted FAT32 SDHC card (up to 32 GB, "
                   ">= 300 MiB free, no AUTOEXEC.BIN, no native boot markers). Full battery, NO USB or AC, REMOVE any "
                   "CFexpress card, and never power off or open card/battery doors while the card light is on.\n"
                   "In the camera: power on -> press Playback -> press SET once -> wait until the card light stops "
                   "plus 60 s -> power off -> return the card and click 'Verify returned card (readback)'.",
                   "Caption.TLabel", pady=(0, 0), inset=CARD_INSET)
        self.bd_find_button, self.bd_card_choice, self.bd_card_combo, self.bd_card_note = \
            self._cbasic_card_selector(guide)
        row1 = self._buttons(guide, pady=(S, 0))
        self.bd_enable_button = self._button(
            row1, "Prepare Enable-BootDisk card...",
            lambda: self._prepare_cbasic_card("enable_bootdisk", self.bootdisk_guide_status), primary=True)
        self.bd_disable_button = self._button(
            row1, "Prepare Disable-BootDisk card (hardware-untested)...",
            lambda: self._prepare_cbasic_card("disable_bootdisk", self.bootdisk_guide_status))
        row2 = self._buttons(guide, pady=(S, 0))
        self.bd_stage_button = self._button(
            row2, "Stage Enable files on folder...",
            lambda: self._stage_cbasic_folder("enable_bootdisk", self.bootdisk_guide_status))
        self.bd_verify_card_button = self._button(
            row2, "Verify returned card (readback)...",
            self._verify_bootdisk_card, primary=True)
        self.bd_verify_file_button = self._button(
            row2, "Verify from a ROM dump file...",
            self._verify_bootdisk_file)
        self.bd_cbasic_busy = Busy(self, guide)
        self.bootdisk_guide_status = Callout(self, guide)
        self.bootdisk_var = self.tk.BooleanVar(value=False)
        self.bootdisk_box = self.ttk.Checkbutton(tab, variable=self.bootdisk_var, command=self._bootdisk_changed,
                                                 text="BootDisk is already enabled on my camera (it was confirmed "
                                                      "with a readback when it was enabled).")
        self.bootdisk_box.pack(anchor="w", pady=(M, 0))

    def _bootdisk_changed(self):
        self.wizard.set_bootdisk("confirmed" if self.bootdisk_var.get() else "unknown")
        self.refresh()

    def _test_panel(self):
        tab = self._panel(TEST)
        self.test_vars, self.test_boxes = [], []
        for i, text in enumerate(TEST_CHECKLIST):
            var = self.tk.BooleanVar(value=False)
            box = self.ttk.Checkbutton(tab, variable=var, text=text, command=lambda i=i, v=var: (
                self.wizard.set_test_item(i, v.get()), self.refresh()))
            box.pack(anchor="w", pady=(S, 0))
            self.test_vars.append(var)
            self.test_boxes.append(box)
        row = self._buttons(tab, pady=(L, 0))
        self._button(row, "Open the test report form  ↗", lambda: webbrowser.open(self.wizard.report_url()))

    def _full_path(self):
        self.wizard.start_full()
        self.refresh()

    def _update_path(self):
        reasons = self.wizard.start_update()
        if reasons:
            self.messagebox.showinfo("Update my card", "\n".join(reasons))
        self.refresh()

    def _sidebar_clicked(self, step_id):
        self.utility = None
        if step_id in self.wizard.path():
            self.wizard.goto(step_id)
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
        status.configure(text=st.capitalize())
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
        self.wizard.next()
        self.refresh()

    def _back(self):
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
        self.mode_label.configure(text="Path: Update my card (one-time steps already done)." if w.saved.mode == "update"
                                  else "Path: full setup. Done one-time steps stay done when you come back.")
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
                check.set(STEP_KINDS[st.status], STEP_HEADLINES[st.status], st.message.removeprefix("Skipped: "))
                if not frame.winfo_manager():
                    frame.pack(fill="both", expand=True)
            elif frame.winfo_manager():
                frame.pack_forget()
        if self.utility is not None:
            self.back_button.configure(state="normal", text="←  Back to setup")
            self.next_button.configure(state="disabled", text="Next  →")
            self.nav_note.configure(text="")
        else:
            self.back_button.configure(state="normal" if path.index(current) > 0 else "disabled", text="←  Back")
            blockers = w.next_blockers()
            last = current == path[-1]
            self.next_button.configure(state="disabled" if blockers else "normal", text="Finish" if last else "Next  →")
            self.nav_note.configure(text="" if not blockers or last else blockers[0])
        self.camera_var.set(w.saved.camera_confirmed)
        missing = w.one_time_done()
        self.update_button.configure(state="disabled" if missing else "normal")
        self.update_note.configure(text=("For returning owners, after the one-time steps: "
                                         + ", ".join(missing) + " still to do.") if missing else
                                   "One-time steps are done: 'Update my card' takes you straight to Features.")
        self.bootdisk_var.set(w.saved.bootdisk in ("confirmed", "verified"))
        self.bootdisk_box.configure(state="normal" if self.bootdisk_ack.get() or self.bootdisk_var.get() else "disabled")
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
        self._text(tab, "Pick your own 64 MiB ROM dump (often gang100.bin). It is read on this computer only and never "
                        "copied anywhere. The builder's own strict firmware check runs on it; there is no way to force it.")
        self.rom_var = self.tk.StringVar()
        self._field(tab, "ROM dump", self.rom_var, [("Choose ROM...", self._pick_rom)])
        self.rom_status = StatusLine(self, tab)
        guide = self._card(tab, "Make a ROM dump with a Canon Basic card (read-only)", pady=(L, 0))
        self._text(guide,
                   "Use a camera-formatted FAT32 SDHC card (up to 32 GB, >= 300 MiB free, no AUTOEXEC.BIN, no native "
                   "boot markers). Full battery, REMOVE any CFexpress card, and unplug USB/HDMI.\n"
                   "In the camera: insert only the SD card -> power on -> press Playback -> press SET once -> wait "
                   "until the card light stops plus 60 s -> power off -> return the card and import the dump below.",
                   "Caption.TLabel", pady=(0, 0), inset=CARD_INSET)
        self.rom_find_button, self.rom_card_choice, self.rom_card_combo, self.rom_card_note = \
            self._cbasic_card_selector(guide)
        row = self._buttons(guide, pady=(S, 0))
        self.rom_prep_button = self._button(
            row, "Prepare ROM-dump card...",
            lambda: self._prepare_cbasic_card("dump", self.rom_guide_status), primary=True)
        self.rom_stage_button = self._button(
            row, "Stage files on a chosen folder...",
            lambda: self._stage_cbasic_folder("dump", self.rom_guide_status))
        self.rom_import_button = self._button(
            row, "Import ROM dump from returned card...",
            self._import_rom_from_card, primary=True)
        self.rom_cbasic_busy = Busy(self, guide)
        self.rom_guide_status = Callout(self, guide)

    def _features_tab(self):
        tab = self._panel(FEATURES)
        self._text(tab, self.state.manifest.profile_exclusivity, "Muted.TLabel")
        self.profile_var = self.tk.StringVar(value=self.state.profile)
        self.profile_status, self.profile_cards = {}, {}
        for view in self.state.profile_views():
            box = self._card(tab, pady=(M if not self.profile_cards else S, 0))
            self.ttk.Radiobutton(box, text=f"{view.label} ({view.id})", value=view.id, variable=self.profile_var,
                                 style="Strong.TRadiobutton", command=self._profile_changed).pack(anchor="w")
            indent = self.ttk.Frame(box, padding=(26, 0, 0, 0))
            indent.pack(fill="x")
            self._text(indent, view.description, pady=(XS, 0), inset=CARD_INSET + 26)
            self.profile_status[view.id] = self._text(indent, f"Status: {view.status_label}. {view.status_text}",
                                                      "Caption.TLabel", inset=CARD_INSET + 26)
            self.profile_cards[view.id] = box

        self.option_widgets = {}
        for view in self.state.option_views():
            box = self._card(tab, view.label, pady=(L, 0))
            self._text(box, view.description, pady=(0, 0), inset=CARD_INSET)
            var = self.tk.StringVar()
            combo = self.ttk.Combobox(box, textvariable=var, state="readonly", width=40,
                                      values=[c.label for c in view.choices])
            combo.pack(anchor="w", pady=(S, 0))
            combo.bind("<<ComboboxSelected>>", lambda _e, oid=view.id: self._option_changed(oid))
            reason = self._text(box, "", "Caption.TLabel", inset=CARD_INSET)
            self.option_widgets[view.id] = (combo, var, reason)

        self.input_widgets = {}
        for i, view in enumerate(self.state.input_views()):
            box = self._card(tab, view.label, pady=(L if i == 0 else S, 0))
            var = self.tk.StringVar()
            (button,) = self._field(box, None, var, [("Choose WAV...", lambda i=view.id: self._pick_wav(i))],
                                    pady=(S, 0))
            status = StatusLine(self, box)
            self.input_widgets[view.id] = (var, button, status)
        self._text(tab, "WAV files must be 48 kHz, mono, 16-bit PCM (see docs/usage.md for an ffmpeg command). "
                        "They are embedded unchanged; nothing is trimmed or normalised.", "Caption.TLabel")
        row = self._buttons(tab, pady=(L, 0))
        self._button(row, "Use these features", lambda: (self.wizard.confirm_features(), self.refresh()), primary=True)

    def _toolchain_tab(self):
        tab = self._panel(TOOLCHAIN)
        self._text(tab, checks.TOOLCHAIN_INSTRUCTIONS)
        self.ttk.Button(tab, text="Open the xPack releases page  ↗", style="Link.TButton", cursor="hand2",
                        command=lambda: webbrowser.open(checks.TOOLCHAIN_RELEASES_URL)).pack(anchor="w", pady=(S, 0))
        self.tc_var = self.tk.StringVar()
        self._field(tab, "Toolchain bin folder", self.tc_var, [
            ("Choose bin folder...", self._pick_toolchain),
            ("Detect again", lambda: self._detect_toolchain(explicit=True))])
        self.tc_status = StatusLine(self, tab)

    def _build_tab(self):
        tab = self._panel(BUILD)
        self.out_var = self.tk.StringVar()
        self._field(tab, "Output folder (must be new)", self.out_var, [("Change...", self._pick_output)])
        self.blockers = Callout(self, tab)
        row = self._buttons(tab, pady=(L, 0))
        self.build_button = self._button(row, "Build autoexec.bin", self._build, primary=True)
        self.build_busy = Busy(self, tab)
        self.result = Callout(self, tab)
        self.memory_slot = Slot(self.ttk, tab, pady=(M, 0))
        self.memory = self.ttk.Frame(self.memory_slot.frame)
        self.ttk.Label(self.memory, text="Canon memory left after this build", style="Caption.TLabel").pack(anchor="w")
        self.gauge = self.ttk.Progressbar(self.memory, maximum=100, style="Accent.Horizontal.TProgressbar")
        self.gauge.pack(fill="x", pady=(XS, 0))
        buttons = self._buttons(tab)
        self.open_out = self._button(buttons, "Open output folder", lambda: open_path(self.summary.output_dir),
                                     state="disabled")
        self.open_contents = self._button(buttons, "Open 'what's in this build'",
                                          lambda: open_path(self.summary.output_dir / runner.CONTENTS_NAME),
                                          state="disabled")
        self._text(tab, "Build log", "Caption.TLabel", pady=(L, 0))
        self.log, _ = self._textbox(tab, height=10, pady=(XS, 0))

    def _card_tab(self):
        tab = self._panel(COPY)
        self._text(tab, "Only for a card that is already bootable (step 7, or prepared separately). Choose the card's "
                        "top folder (for example /Volumes/EOS_DIGITAL on a Mac or E:\\ on Windows). Any existing "
                        "autoexec.bin is backed up to the build folder on this computer first; nothing on the card is "
                        "deleted and the card is never formatted.")
        self.card_var = self.tk.StringVar()
        self._field(tab, "Card folder", self.card_var, [("Choose card folder...", self._pick_card)])
        self.card_report = Callout(self, tab)
        self.card_confirm = self.tk.BooleanVar(value=False)
        self.card_confirm_box = self.ttk.Checkbutton(
            tab, variable=self.card_confirm, command=self.refresh,
            text="I have checked the warnings above and this really is my prepared camera card.")
        self.card_confirm_box.pack(anchor="w", pady=(M, 0))
        row = self._buttons(tab)
        self.copy_button = self._button(row, "Copy autoexec.bin to card", self._copy, primary=True)
        self.copy_busy = Busy(self, tab)
        self.copy_result = Callout(self, tab)

    def _prep_tab(self):
        tab = self._panel(BOOTCARD, extra_details=EXPLAIN)
        Callout(self, tab, pady=(S, 0)).set(
            DANGER, FORMAT_WARNING, card_devices.UNTESTED_NOTE.get(self.prep.platform, "This system: not supported."))
        self._text(tab, "SD card", "Caption.TLabel", pady=(L, XS))
        row = self.ttk.Frame(tab)
        row.pack(fill="x")
        self.prep_find = self.ttk.Button(row, text="Find SD cards", command=self._prep_find)
        self.prep_find.pack(side="left")
        self.prep_choice = self.tk.StringVar()
        self.prep_combo = self.ttk.Combobox(row, textvariable=self.prep_choice, state="disabled", width=40)
        self.prep_combo.pack(side="left", padx=(S, 0), fill="x", expand=True)
        self.prep_combo.bind("<<ComboboxSelected>>", lambda _e: self._prep_selected())
        self.prep_details = self._text(tab, "", "Muted.TLabel", full=True)
        buttons = self._buttons(tab)
        self.prep_button = self._button(buttons, "Prepare card", lambda: self._prep_run("mark"), primary=True)
        self.prep_busy = Busy(self, tab)
        self.prep_blockers = self._text(tab, "", "Caption.TLabel", full=True)
        self.prep_title = Callout(self, tab)
        self.prep_log_slot = Slot(self.ttk, tab, pady=(S, 0))
        self.prep_log, self.prep_log_box = self._textbox(self.prep_log_slot.frame, height=8, pady=(0, 0))
        self.prep_log_box.pack_forget()
        undo = self._card(tab, "Undo a preparation", pady=(L, 0))
        self._text(undo, "Puts back the boot sectors from a backup, with the same read, backup and confirmation "
                         "as Prepare.", "Caption.TLabel", pady=(0, 0), inset=CARD_INSET)
        buttons = self._buttons(undo, pady=(S, 0))
        self.prep_undo = self._button(buttons, "Undo (restore the latest backup)", lambda: self._prep_run("undo"))
        self.prep_restore = self._button(buttons, "Restore from a chosen backup...", lambda: self._prep_run("restore"))
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

    def _refresh_cbasic(self):
        p = self.prep
        accepted = p.accepted
        busy = self._card_busy()
        values = [c.summary() for c in accepted]
        selected_text = p.selected.summary() if p.selected else ""
        lines: list[str] = []
        if p.selected:
            lines.append(f"Selected: {p.selected.summary()}")
            if p.selected.filesystem != "FAT32" or (p.selected.disk_bytes and p.selected.disk_bytes >= 0x4000000 * 512):
                lines.append("Note: guided Canon Basic requires a FAT32 SDHC card (up to 32 GB); "
                             "SDXC / exFAT cards are untested and refused.")
        elif p.searched and not accepted:
            lines.append("No usable SD card found.")
        elif p.searched:
            lines.append("Choose your FAT32 SDHC card above.")
        elif not p.supported:
            lines.append("Preparing raw SCRIPT sectors is offered on macOS only; use 'Stage ... on folder' "
                         "if your card already has SCRIPT.")
        else:
            lines.append("Click 'Find SD cards' to locate your FAT32 SDHC card (up to 32 GB).")
        if p.refused:
            lines.append("Not offered: " + "; ".join(f"{c.whole_id} ({c.model or c.bus or '?'}): {c.refusals[0]}"
                                                     for c in p.refused))
        note = "\n".join(lines)
        for find_btn, choice, combo, note_label in (
            (self.rom_find_button, self.rom_card_choice, self.rom_card_combo, self.rom_card_note),
            (self.bd_find_button, self.bd_card_choice, self.bd_card_combo, self.bd_card_note),
        ):
            find_btn.configure(state="disabled" if busy else "normal")
            combo.configure(values=values, state="readonly" if accepted and not busy else "disabled")
            choice.set(selected_text)
            note_label.configure(text=note)

        can_raw = p.supported and p.selected is not None and not busy
        self.rom_prep_button.configure(state="normal" if can_raw else "disabled")
        self.rom_stage_button.configure(state="normal" if not busy else "disabled")
        self.rom_import_button.configure(state="normal" if not busy else "disabled")
        ack = self.bootdisk_ack.get()
        self.bd_enable_button.configure(state="normal" if (ack and can_raw) else "disabled")
        self.bd_disable_button.configure(state="normal" if (ack and can_raw) else "disabled")
        self.bd_stage_button.configure(state="normal" if (ack and not busy) else "disabled")
        self.bd_verify_card_button.configure(state="normal" if not busy else "disabled")
        self.bd_verify_file_button.configure(state="normal" if not busy else "disabled")
        self.rom_cbasic_busy.set(self.cbasic_busy if self.wizard.current == ROM else None)
        self.bd_cbasic_busy.set(self.cbasic_busy if self.wizard.current == BOOTDISK else None)

    @staticmethod
    def _set_cbasic_status(callout: Callout, text: str, kind: str = NEUTRAL):
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            callout.clear()
        elif len(lines) == 1:
            callout.set(kind, lines[0])
        else:
            callout.set(kind, lines[0], "\n".join(lines[1:]))

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
                "Disable BootDisk (hardware-untested)",
                "DisableBootDisk() (0xE0B38D74) is the stock inverse in the R5 2.2.1 ROM, "
                "but it has NEVER been run on real camera hardware yet.\n\n"
                "Only proceed if you accept the risk of testing this on your camera. Continue?",
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
                self._set_cbasic_status(status_callout, f"Card preparation stopped\n{session}", DANGER)
                self.refresh()
                self.messagebox.showerror("Canon Basic card preparation", str(session))
                return
            if session.needs_write:
                c = session.snapshot.candidate
                sectors = ", ".join(str(w.sector) for w in session.plan.writes)
                prompt = "\n\n".join([
                    f"Prepare {c.label()} for Canon Basic?",
                    f"{c.model}, {c.whole_id}.",
                    f"This writes the Canon Basic SCRIPT marker at FAT32 boot sector byte 496 (sector(s) {sectors}, "
                    f"{len(session.plan.writes) * 512:,} bytes), then stages script.req and extend.m for "
                    f"{self._cbasic_task_label(task)}.",
                    "Nothing else on the card is changed. macOS will ask for your administrator password.",
                    f"Backup of the card as it is now: {session.backup.folder}",
                ])
                if not self.messagebox.askokcancel("Prepare Canon Basic card", prompt, icon="warning"):
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
        self._set_cbasic_status(status_callout, "\n".join(lines), WARNING)
        self.refresh()

    def _cbasic_script_written(self, outcome, task: str, status_callout: Callout):
        if isinstance(outcome, Exception):
            self.cbasic_busy = None
            self._set_cbasic_status(status_callout, f"Failed\n{outcome}", DANGER)
            self.refresh()
            self.messagebox.showerror("Canon Basic card preparation", str(outcome))
            return
        report, refreshed, backup_folder = outcome
        if report is not None and not report.ok:
            self.cbasic_busy = None
            msg = "\n".join([report.title] + report.lines)
            self._set_cbasic_status(status_callout, msg, DANGER)
            self.refresh()
            self.messagebox.showinfo("Canon Basic card preparation", msg)
            return
        mount_point = refreshed.mount_point if refreshed else ""
        if not mount_point:
            self.cbasic_busy = None
            msg = ("SCRIPT marker is on the card, but the card did not report a mount folder.\n"
                   "Mount the card and click 'Stage files on a chosen folder...'.")
            self._set_cbasic_status(status_callout, msg, WARNING)
            self.refresh()
            self.messagebox.showinfo("Canon Basic card preparation", msg)
            return
        prefix = [report.title] + report.lines if report is not None else ["Canon Basic SCRIPT marker already present."]
        self._stage_cbasic_folder_impl(Path(mount_point), task, status_callout, backup_dir=backup_folder,
                                       prefix_lines=prefix)

    def _stage_cbasic_folder(self, task: str, status_callout: Callout):
        if self._card_busy():
            self._set_cbasic_status(status_callout, "Wait for the current card operation to finish.", WARNING)
            return
        if task == "disable_bootdisk" and not self.messagebox.askokcancel(
            "Disable BootDisk (hardware-untested)",
            "DisableBootDisk() (0xE0B38D74) has NEVER been run on real camera hardware yet. Continue?",
            icon="warning",
        ):
            return
        folder = self.filedialog.askdirectory(
            title="Choose the FAT32 SDHC card's top folder (must already have SCRIPT)",
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
            self._set_cbasic_status(status_callout, f"Could not inspect {folder}\n{exc}", DANGER)
            self.refresh()
            return
        if info.errors:
            self.cbasic_busy = None
            msg = " ".join(info.errors)
            self._set_cbasic_status(status_callout, f"Card refused\n{msg}", DANGER)
            self.refresh()
            self.messagebox.showerror("Card refused", msg)
            return
        if info.existing is not None:
            self.cbasic_busy = None
            msg = (f"{info.existing.name} is on this card. Remove or back up AUTOEXEC.BIN first so a "
                   "BootDisk-enabled camera starts normal Canon firmware for Canon Basic.")
            self._set_cbasic_status(status_callout, f"AUTOEXEC.BIN present\n{msg}", DANGER)
            self.refresh()
            self.messagebox.showerror("AUTOEXEC.BIN present", msg)
            return
        if info.warnings and not self.messagebox.askokcancel(
            "Confirm card folder",
            "\n".join(info.warnings) + "\n\nStage script.req and extend.m in this folder anyway?",
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
                self._set_cbasic_status(status_callout, f"Could not stage files\n{staged}", DANGER)
                self.refresh()
                self.messagebox.showerror("Staging failed", str(staged))
                return
            mode_key = "dump" if task == "dump" else "bootdisk"
            self.cbasic_baselines[mode_key] = (staged.card_root, staged.baseline_gang1)
            camera_steps = (
                "Next on the camera (full battery, NO CFexpress/USB/HDMI): insert only this SD card -> "
                "power on -> press Playback -> press SET once -> wait until the card light stops plus 60 s -> "
                "power off -> return the card here."
            )
            lines = list(prefix_lines) + staged.messages + [camera_steps]
            text = "\n".join(lines)
            self._set_cbasic_status(status_callout, text, SUCCESS)
            self.refresh()
            self.messagebox.showinfo("Canon Basic card ready", text)

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
            title="Choose the returned SD card folder",
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
                self._set_cbasic_status(status_callout, f"Verification failed\n{returned}", DANGER)
                self.refresh()
                self.messagebox.showerror("ROM dump / BootInfo check", str(returned))
                return
            self.cbasic_baselines.pop(mode_key, None)
            if mode == "rom":
                self.state.set_rom(returned.local_path)
                if returned.bootinfo.state == "enabled":
                    self.wizard.set_bootdisk("verified", rom_path=returned.local_path, rom_sha256=returned.sha256)
                kind = SUCCESS
            else:
                if returned.bootinfo.state == "enabled":
                    self.wizard.set_bootdisk("verified", rom_path=returned.local_path, rom_sha256=returned.sha256)
                    if self.state.rom_path is None:
                        self.state.set_rom(returned.local_path)
                    kind = SUCCESS
                else:
                    self.wizard.set_bootdisk("disabled")
                    kind = WARNING
            text = "\n".join(returned.messages)
            self._set_cbasic_status(status_callout, text, kind)
            self.refresh()
            self.messagebox.showinfo("ROM dump verified", text)

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
                "Verify BootDisk from ROM",
                f"Check the ROM dump already selected in Step 2?\n\n{self.state.rom_path}\n\n"
                "Yes = check Step 2's ROM\nNo = choose another ROM file",
            )
            if choice is None:
                return
            if choice:
                target = self.state.rom_path
        if target is None:
            picked = self.filedialog.askopenfilename(title="Choose a 64 MiB ROM dump (e.g. gang101.bin)")
            if not picked:
                return
            target = Path(picked)

        before_path = None
        if self.state.rom_path is not None and self.state.rom_path.is_file() and target != self.state.rom_path:
            before_path = self.state.rom_path
        profile, options = self._selected_rom_args()
        self._set_cbasic_status(self.bootdisk_guide_status, "Checking ROM admission and BootInfo +0x04...", INFO)

        def done(result):
            if isinstance(result, Exception):
                self._set_cbasic_status(self.bootdisk_guide_status, f"Check failed\n{result}", DANGER)
                return
            if not result.ok:
                self._set_cbasic_status(self.bootdisk_guide_status, f"ROM refused\n{result.message}", DANGER)
                self.refresh()
                return
            if result.facts.get("boot_disk_state") == "enabled":
                self.wizard.set_bootdisk("verified", rom_path=target, rom_sha256=result.facts["rom_sha256"])
                self._set_cbasic_status(self.bootdisk_guide_status, f"BootDisk enabled\n{result.message}", SUCCESS)
            else:
                self.wizard.set_bootdisk("disabled")
                self._set_cbasic_status(
                    self.bootdisk_guide_status,
                    f"BootDisk disabled\n{result.message} Enable BootDisk on the camera before running autoexec.bin.",
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
        self.prep_combo.configure(values=[c.summary() for c in accepted],
                                  state="readonly" if accepted and not busy else "disabled")
        self.prep_choice.set(p.selected.summary() if p.selected else "")
        lines = p.selected.details() if p.selected else []
        if p.searched and not accepted:
            lines.append("No usable SD card found.")
        if p.refused:
            lines.append("Not offered: " + "; ".join(f"{c.whole_id} ({c.model or c.bus or '?'}): {c.refusals[0]}"
                                                     for c in p.refused))
        self.prep_details.configure(text="\n".join(lines))
        self.prep_details.pack_configure(pady=(S, 0) if lines else (0, 0))
        blocked = p.blockers() + ([f"Wait: {self.cbasic_busy}"] if self.cbasic_busy else [])
        self.prep_find.configure(state="disabled" if busy else "normal")
        for button in (self.prep_button, self.prep_undo, self.prep_restore):
            button.configure(state="disabled" if blocked else "normal")
        self.prep_busy.set(p.busy)
        self.prep_blockers.configure(text="" if not blocked else "Next:\n" + bullets(blocked))
        if p.error:
            self.prep_title.set(DANGER, p.error)
            body = []
        elif p.report:
            self.prep_title.set(SUCCESS if p.report.ok else DANGER, p.report.title)
            body = p.report.lines
        else:
            self.prep_title.clear()
            body = []
        self._set_text(self.prep_log, "\n".join(body))
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
                self.prep.error = f"Could not list cards: {result}"
            else:
                self.prep.set_candidates(result)
            self.refresh()
        self._background(card_devices.enumerate_candidates, done)

    def _prep_selected(self):
        summary = self.prep_choice.get()
        match = next((c for c in self.prep.accepted if c.summary() == summary), None)
        self.prep.select(match.whole_id if match else None)
        self.refresh()

    def _prep_run(self, kind):
        """Prepare ("mark"), Undo ("undo": the latest backup) or Restore ("restore": a chosen backup)."""
        p = self.prep
        if p.blockers() or self.cbasic_busy:
            return
        cand, source = p.selected, None
        if kind == "undo":
            if card_flow.latest_backup(cand, p.backup_root) is None:  # early hint; the choice is made after the read
                p.failed(f"No backup of this card from before it was prepared was found in {p.backup_root}. "
                         "Use 'Restore from a chosen backup...' instead.")
                self.refresh()
                return
            source = card_flow.LATEST
        elif kind == "restore":
            folder = self.filedialog.askdirectory(title="Choose a card backup folder (it contains manifest.json)",
                                                  initialdir=str(p.backup_root))
            if not folder:
                return
            try:
                source = card_flow.load_backup(Path(folder))
            except card_flow.PrepFailure as exc:
                p.failed(str(exc))
                self.refresh()
                return
        p.restore_from = source if isinstance(source, card_flow.Backup) else None
        token = p.begin("Reading the card and saving a backup (macOS asks for your password)...")
        ops = card_devices.MacOps()
        self.refresh()

        def read_done(result):
            if isinstance(result, Exception):
                p.failed(f"Not {'prepared' if kind == 'mark' else 'restored'}: {result}")
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
            ok = self.messagebox.askokcancel(title, text, icon="warning")
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
            self.messagebox.showinfo("Card preparation", "\n".join([report.title] + report.lines))

    def _on_close(self):
        p = self.prep
        if self.shots.busy and not self.messagebox.askokcancel(
                "Quit", "Screenshots are still being converted. Quit anyway? The card is not changed."):
            return
        if (p.busy or self.cbasic_busy) and not self.messagebox.askokcancel(
                "Quit", "A card operation is still running. Quitting now can leave the card unmounted. Quit anyway?"):
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
        self.rom_status.show_check(s.rom_check())
        self.profile_var.set(s.profile)
        for profile_id, box in self.profile_cards.items():
            box.configure(style="Selected.Card.TFrame" if profile_id == s.profile else "Card.TFrame")
        for view in s.option_views():
            combo, var, reason = self.option_widgets[view.id]
            combo.configure(state="readonly" if view.enabled else "disabled")
            choice = next(c for c in view.choices if c.value == view.value)
            var.set(choice.label)
            if not view.enabled:
                reason.configure(text=view.reason)
            elif choice.status_label:
                reason.configure(text=f"Status: {choice.status_label}. {choice.status_text}")
            else:
                reason.configure(text="Off. With both record cues off, no indicator code, hook or memory is added.")
        for view in s.input_views():
            var, button, status = self.input_widgets[view.id]
            var.set(str(view.path or ""))
            button.configure(state="normal" if view.enabled else "disabled")
            if view.enabled:
                status.show_check(view.check)
            else:
                status.set(NEUTRAL, view.reason)
        self.tc_var.set(str(s.toolchain_path or ""))
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
        self.copy_button.configure(state="normal" if ready_to_copy else "disabled")
        self.card_confirm_box.configure(state="normal" if self.card_info and self.card_info.warnings else "disabled")
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
                result = checks.CheckResult(False, f"Check failed: {result}")
            s.finish(what, key, result.ok, result.message, result.facts, input_id=input_id)
            self.refresh()
        self._background(work, done)

    # -- actions --------------------------------------------------------------

    def _pick_rom(self):
        path = self.filedialog.askopenfilename(title="Choose your R5 ROM dump")
        if path:
            self.state.set_rom(Path(path))
            self.refresh()

    def _profile_changed(self):
        self.state.set_profile(self.profile_var.get())
        self._new_output()
        self.wizard.features_changed()
        self.refresh()

    def _option_changed(self, option_id):
        combo, var, _ = self.option_widgets[option_id]
        view = next(v for v in self.state.option_views() if v.id == option_id)
        value = next(c.value for c in view.choices if c.label == var.get())
        try:
            self.state.set_option(option_id, value)
        except ValueError as exc:
            self.messagebox.showerror("Option not available", str(exc))
        self._new_output()
        self.wizard.features_changed()
        self.refresh()

    def _new_output(self):
        """A fresh, selection-named folder in the same parent (the builder needs a new folder each time)."""
        parent = self.state.output_dir.parent if self.state.output_dir else ROOT / "build"
        name = default_output_dir(self.state.profile, self.state.options, self.state.manifest).name
        self.state.set_output_dir(parent / name)

    def _pick_wav(self, input_id):
        path = self.filedialog.askopenfilename(title=self.state.manifest.inputs[input_id].label,
                                               filetypes=[("WAV audio", "*.wav *.WAV"), ("All files", "*")])
        if path:
            self.state.set_input(input_id, Path(path))
            self.wizard.features_changed()
            self.refresh()

    def _pick_toolchain(self):
        path = self.filedialog.askdirectory(title="Choose the toolchain's bin folder (contains arm-none-eabi-gcc)")
        if path:
            self.state.set_toolchain(Path(path))
            self.refresh()

    def _detect_toolchain(self, explicit=False):
        self._detecting = True
        self.tc_status.set(BUSY, "Looking for an installed GNU Arm 15.2.1 toolchain...")

        def done(result):
            self._detecting = False
            if isinstance(result, Exception):
                self.tc_status.set(DANGER, f"Detection failed: {result}")
                return
            path, check, notes = result
            if path is None:
                note = ("\n" + "\n".join(notes)) if notes else ""
                self.tc_status.set(DANGER, "No GNU Arm 15.2.1 toolchain found. Install it as described above, "
                                           "then choose its bin folder." + note)
                return
            self.state.offer_detected_toolchain(path, check.message, check.facts, explicit=explicit)
            self.refresh()
        self._background(checks.detect_toolchain, done)

    def _pick_output(self):
        parent = self.filedialog.askdirectory(title="Choose where to create the new output folder")
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
                self.result.set(DANGER, "Build could not run", str(outcome))
            elif not outcome.ok:
                self.result.set(DANGER, "Build refused or failed", outcome.error_text())
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
            self.result.set(DANGER, "Build finished but its record could not be read", str(exc))
            return
        self.summary = summary
        self.wizard.set_build(summary if summary.sha256_matches_build_json else None,
                              note=None if summary.sha256_matches_build_json else "The build does not match build.json.")
        matches = summary.sha256_matches_build_json
        match = "matches build.json" if matches else "DOES NOT MATCH build.json"
        self.result.set(SUCCESS if matches else DANGER,
                        "Built and verified" if matches else "Built, but autoexec.bin DOES NOT MATCH build.json",
                        f"Built: {summary.autoexec}\nSHA-256 {summary.sha256} ({match})\n"
                        f"{runner.memory_line(summary)}\nWhat's in this build: {contents}", mono=True)
        self.gauge.configure(value=100 * summary.remaining_heap / summary.heap_pool)
        self.memory_slot.show(self.memory)
        self.open_out.configure(state="normal")
        self.open_contents.configure(state="normal")
        self._append_log(runner.contents_text(summary, self.state.manifest))

    def _pick_card(self):
        path = self.filedialog.askdirectory(title="Choose the card's top folder")
        if not path:
            return
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
            self.card_report.set(DANGER, "Could not look at the card", str(info))
            return
        self.card_info = info
        lines = [f"Folder: {info.root}"]
        if info.total_bytes is not None:
            lines.append(f"Drive size: {info.total_bytes / 1000**3:,.1f} GB")
        lines.append("DCIM folder: " + ("found" if info.has_dcim else "not found"))
        lines.append("Existing autoexec.bin: " + (info.existing.name if info.existing else "none"))
        lines += ["REFUSED: " + e for e in info.errors] + ["Warning: " + w for w in info.warnings]
        if self.summary is None:
            lines.append("Build autoexec.bin first (step 6).")
        if info.errors:
            kind, headline = DANGER, "This folder is refused"
        elif info.warnings:
            kind, headline = WARNING, "Check the warnings before you copy"
        else:
            kind, headline = NEUTRAL, "Card folder checked"
        self.card_report.set(kind, headline, "\n".join(lines))
        self.refresh()

    def _copy(self):
        summary, info = self.summary, self.card_info
        backups = summary.output_dir / "card-backup"
        if not self.messagebox.askokcancel(
            "Copy to card",
            f"Copy autoexec.bin to {info.root}?\n\nAny existing autoexec.bin is backed up to {backups} first.",
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
                self.copy_result.set(DANGER, "Not copied", str(result))
            elif isinstance(result, Exception):
                self.copy_result.set(DANGER, "Copy failed unexpectedly", str(result))
            else:
                self.copy_result.set(SUCCESS, "Done", "\n".join(result.messages))
                try:
                    self.wizard.set_copied(result.sha256)
                except ValueError:
                    pass  # a newer build replaced the one that was copied
                self.messagebox.showinfo("Done", "\n\n".join(result.messages))
            self.refresh()
        self._background(lambda: card.copy_to_card(summary.autoexec, summary.sha256, info.root, backups,
                                                   confirmed_warnings=confirmed), done)

    # -- screenshots (a utility, not a setup step) ---------------------------------------

    def _screenshots_panel(self):
        tab = self._panel(shots.SCREENSHOTS, utility=True)
        Callout(self, tab, pady=(S, 0)).set(INFO, "Private lab feature", shots.LAB_NOTE)
        self.shots_src_var = self.tk.StringVar()
        self._field(tab, "Screenshots from (the card's top folder, DCIM, or an R5SHOTS folder)", self.shots_src_var,
                    [("Choose card or folder...", self._pick_shots_source)], pady=(L, 0))
        self.shots_src_status = StatusLine(self, tab)
        self.shots_out_var = self.tk.StringVar()
        self._field(tab, "Save the PNG files in (on this computer)", self.shots_out_var,
                    [("Change...", self._pick_shots_output)])
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
        self.shots_blockers.configure(text=bullets(shown))
        self.shots_blockers.pack_configure(pady=(S, 0) if shown else (0, 0))
        report = st.report()
        if report is None:
            self.shots_result.clear()
            lines = []
        else:
            kind, headline, lines = report
            self.shots_result.set(kind, headline)
        self._set_text(self.shots_log, "\n".join(lines))
        if lines:
            self.shots_log_slot.show(self.shots_log_box)
        else:
            self.shots_log_slot.hide(self.shots_log_box)

    def _pick_shots_source(self):
        folder = self.filedialog.askdirectory(title="Choose the card's top folder, or a folder with screenshots")
        if not folder:
            return
        self.shots.busy = "Looking for screenshots..."
        self.refresh()

        def done(result):
            self.shots.busy = None
            if isinstance(result, Exception):
                self.shots.set_source(shots.Source(Path(folder), None, (), False))
                self.shots.error = f"Could not look at {folder}: {result}"
            else:
                self.shots.set_source(result)
            self.refresh()
        self._background(lambda: shots.find_source(Path(folder)), done)

    def _pick_shots_output(self):
        folder = self.filedialog.askdirectory(title="Choose where to save the PNG files")
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


if __name__ == "__main__":
    raise SystemExit(main())
