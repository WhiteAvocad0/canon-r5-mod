"""Canon R5 mod build assistant (prototype, issue #16 Phase 1).

Run:  python3 tools/gui/app.py

Standard-library Python 3.11+ with Tkinter only. This window is a thin view: the
decisions live in state.py, prep_state.py, checks.py, runner.py, card.py and
tools/cardprep, and the build itself is tools/build.py with all of its checks. It
never downloads anything, never formats or partitions a card and never contacts a
camera. Boot sectors are written only by the "Prepare card" tab (issue #16 Phase 2),
after the person confirms the card, through the operating system's password prompt.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser

if sys.version_info < (3, 11):
    sys.exit("The build assistant needs Python 3.11 or newer (https://www.python.org/downloads/).")

TOOLS_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from cardprep import devices as card_devices, workflow as card_flow
from gui import card, checks, runner
from gui.prep_state import EXPLAIN, FORMAT_WARNING, PrepState
from gui.state import CHECKING, FAILED, OK, AssistantState, default_output_dir

DOCS = {
    "Getting started guide": ROOT / "docs" / "getting-started.md",
    "Prerequisites": ROOT / "docs" / "usage.md",
    "About this assistant": ROOT / "docs" / "gui.md",
}
CANNOT_DO = """\
This assistant builds autoexec.bin on your computer, can write the two boot
markers onto a card you formatted in the camera (tab 5, macOS only, with a backup
first), and copies autoexec.bin to the card. It cannot, and will not try to:

  - dump your camera's ROM (you need your own 64 MiB gang100.bin first);
  - enable the camera's BootDisk setting (a permanent change inside the camera);
  - format, partition or erase a card, or write its partition table (MBR);
  - write anything to a card except the boot-marker sectors shown in tab 5;
  - write the camera's firmware or NVRAM;
  - download the toolchain (you install it yourself; see the Toolchain tab);
  - test anything on the camera, or tell you whether your camera will work.

Every build made here is a public repackaged build and is hardware-untested.
Original Canon EOS R5 on firmware 2.2.1 only. If the camera freezes or shows an
error, remove the battery, then the card; see the Getting started guide.
"""
WARNING = ("Experimental software for the original EOS R5 on firmware 2.2.1 only. "
           "Public builds are hardware-untested. Read the Getting started guide first.")
STATUS_COLOURS = {OK: "#1b7f3b", FAILED: "#b3261e", CHECKING: "#7a5c00"}


def open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - opening a local folder/file the user selected
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class App:
    def __init__(self, tk, ttk, filedialog, messagebox, scrolledtext):
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.state = AssistantState()
        self.events: queue.Queue = queue.Queue()
        self.summary: runner.BuildSummary | None = None
        self.card_info: card.CardInspection | None = None
        self.copying = False
        self.prep = PrepState()

        self.root = tk.Tk()
        self.root.title("Canon R5 mod build assistant (prototype)")
        self.root.minsize(760, 560)
        self.root.geometry("860x760")
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=WARNING, foreground="#b3261e", wraplength=720, justify="left").pack(anchor="w")
        links = ttk.Frame(outer)
        links.pack(anchor="w", pady=(4, 8))
        for label, path in DOCS.items():
            ttk.Button(links, text=label, command=lambda p=path: webbrowser.open(p.as_uri())).pack(side="left", padx=(0, 6))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self._rom_tab()
        self._features_tab()
        self._toolchain_tab()
        self._build_tab(scrolledtext)
        self._prep_tab(scrolledtext)
        self._card_tab()
        cannot = ttk.Frame(self.notebook, padding=10)
        ttk.Label(cannot, text=CANNOT_DO, justify="left").pack(anchor="w")
        self.notebook.add(cannot, text="What this tool cannot do")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.state.set_output_dir(default_output_dir(self.state.profile, self.state.options, self.state.manifest))
        self.root.after(100, self._poll)
        self._detect_toolchain()
        self.refresh()

    # -- helpers ------------------------------------------------------------

    def _tab(self, title: str):
        frame = self.ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=title)
        return frame

    def _status(self, parent):
        label = self.ttk.Label(parent, text="", wraplength=700, justify="left")
        label.pack(anchor="w", pady=(4, 0))
        return label

    def _show(self, label, check):
        label.configure(text=check.message or "Not checked yet.", foreground=STATUS_COLOURS.get(check.status, "#444"))

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
        tab = self._tab("1. ROM")
        self.ttk.Label(tab, wraplength=700, justify="left", text=(
            "Pick your own 64 MiB ROM dump (often gang100.bin). It is read on this computer only and never "
            "copied anywhere. The builder's own strict firmware check runs on it; there is no way to force it."
        )).pack(anchor="w")
        row = self.ttk.Frame(tab)
        row.pack(fill="x", pady=8)
        self.rom_var = self.tk.StringVar()
        self.ttk.Entry(row, textvariable=self.rom_var, state="readonly").pack(side="left", fill="x", expand=True)
        self.ttk.Button(row, text="Choose ROM...", command=self._pick_rom).pack(side="left", padx=6)
        self.rom_status = self._status(tab)

    def _features_tab(self):
        tab = self._tab("2. Features")
        self.ttk.Label(tab, text=self.state.manifest.profile_exclusivity, wraplength=700).pack(anchor="w")
        self.profile_var = self.tk.StringVar(value=self.state.profile)
        self.profile_status = {}
        for view in self.state.profile_views():
            box = self.ttk.Frame(tab)
            box.pack(fill="x", pady=(8, 0))
            self.ttk.Radiobutton(box, text=f"{view.label} ({view.id})", value=view.id, variable=self.profile_var,
                                 command=self._profile_changed).pack(anchor="w")
            self.ttk.Label(box, text=view.description, wraplength=680, justify="left").pack(anchor="w", padx=22)
            self.profile_status[view.id] = self.ttk.Label(box, text=f"Status: {view.status_label}. {view.status_text}",
                                                          wraplength=680, justify="left", foreground="#555")
            self.profile_status[view.id].pack(anchor="w", padx=22)

        self.option_widgets = {}
        for view in self.state.option_views():
            box = self.ttk.LabelFrame(tab, text=view.label, padding=6)
            box.pack(fill="x", pady=(10, 0))
            self.ttk.Label(box, text=view.description, wraplength=680).pack(anchor="w")
            var = self.tk.StringVar()
            combo = self.ttk.Combobox(box, textvariable=var, state="readonly", width=48,
                                      values=[c.label for c in view.choices])
            combo.pack(anchor="w", pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _e, oid=view.id: self._option_changed(oid))
            reason = self.ttk.Label(box, text="", wraplength=680, foreground="#555", justify="left")
            reason.pack(anchor="w")
            self.option_widgets[view.id] = (combo, var, reason)

        self.input_widgets = {}
        for view in self.state.input_views():
            box = self.ttk.LabelFrame(tab, text=view.label, padding=6)
            box.pack(fill="x", pady=(10, 0))
            row = self.ttk.Frame(box)
            row.pack(fill="x")
            var = self.tk.StringVar()
            self.ttk.Entry(row, textvariable=var, state="readonly").pack(side="left", fill="x", expand=True)
            button = self.ttk.Button(row, text="Choose WAV...", command=lambda i=view.id: self._pick_wav(i))
            button.pack(side="left", padx=6)
            status = self._status(box)
            self.input_widgets[view.id] = (var, button, status)
        self.ttk.Label(tab, wraplength=700, foreground="#555", justify="left", text=(
            "WAV files must be 48 kHz, mono, 16-bit PCM (see docs/usage.md for an ffmpeg command). "
            "They are embedded unchanged; nothing is trimmed or normalised.")).pack(anchor="w", pady=(6, 0))

    def _toolchain_tab(self):
        tab = self._tab("3. Toolchain")
        self.ttk.Label(tab, text=checks.TOOLCHAIN_INSTRUCTIONS, wraplength=700, justify="left").pack(anchor="w")
        self.ttk.Button(tab, text="Open the xPack releases page",
                        command=lambda: webbrowser.open(checks.TOOLCHAIN_RELEASES_URL)).pack(anchor="w", pady=6)
        row = self.ttk.Frame(tab)
        row.pack(fill="x", pady=4)
        self.tc_var = self.tk.StringVar()
        self.ttk.Entry(row, textvariable=self.tc_var, state="readonly").pack(side="left", fill="x", expand=True)
        self.ttk.Button(row, text="Choose bin folder...", command=self._pick_toolchain).pack(side="left", padx=6)
        self.ttk.Button(row, text="Detect again", command=lambda: self._detect_toolchain(explicit=True)).pack(side="left")
        self.tc_status = self._status(tab)

    def _build_tab(self, scrolledtext):
        tab = self._tab("4. Build")
        row = self.ttk.Frame(tab)
        row.pack(fill="x")
        self.ttk.Label(row, text="Output folder (must be new):").pack(side="left")
        self.out_var = self.tk.StringVar()
        self.ttk.Entry(row, textvariable=self.out_var, state="readonly").pack(side="left", fill="x", expand=True, padx=6)
        self.ttk.Button(row, text="Change...", command=self._pick_output).pack(side="left")
        self.blockers = self.ttk.Label(tab, text="", wraplength=700, justify="left", foreground="#7a5c00")
        self.blockers.pack(anchor="w", pady=6)
        self.build_button = self.ttk.Button(tab, text="Build autoexec.bin", command=self._build)
        self.build_button.pack(anchor="w")
        self.log = scrolledtext.ScrolledText(tab, height=10, state="disabled", font="TkFixedFont")
        self.log.pack(fill="both", expand=True, pady=6)
        self.result = self.ttk.Label(tab, text="", wraplength=700, justify="left")
        self.result.pack(anchor="w")
        self.gauge = self.ttk.Progressbar(tab, maximum=100, length=400)
        self.gauge.pack(anchor="w", pady=4)
        buttons = self.ttk.Frame(tab)
        buttons.pack(anchor="w")
        self.open_out = self.ttk.Button(buttons, text="Open output folder", state="disabled",
                                        command=lambda: open_path(self.summary.output_dir))
        self.open_out.pack(side="left")
        self.open_contents = self.ttk.Button(buttons, text="Open 'what's in this build'", state="disabled",
                                             command=lambda: open_path(self.summary.output_dir / runner.CONTENTS_NAME))
        self.open_contents.pack(side="left", padx=6)

    def _card_tab(self):
        tab = self._tab("6. Copy to card")
        self.ttk.Label(tab, wraplength=700, justify="left", text=(
            "Only for a card that is already bootable (tab 5, or prepared separately). Choose the card's top folder "
            "(for example /Volumes/EOS_DIGITAL on a Mac or E:\\ on Windows). Any existing autoexec.bin is "
            "backed up to the build folder on this computer first; nothing on the card is deleted and the "
            "card is never formatted.")).pack(anchor="w")
        row = self.ttk.Frame(tab)
        row.pack(fill="x", pady=8)
        self.card_var = self.tk.StringVar()
        self.ttk.Entry(row, textvariable=self.card_var, state="readonly").pack(side="left", fill="x", expand=True)
        self.ttk.Button(row, text="Choose card folder...", command=self._pick_card).pack(side="left", padx=6)
        self.card_report = self.ttk.Label(tab, text="", wraplength=700, justify="left")
        self.card_report.pack(anchor="w")
        self.card_confirm = self.tk.BooleanVar(value=False)
        self.card_confirm_box = self.ttk.Checkbutton(
            tab, variable=self.card_confirm, command=self.refresh,
            text="I have checked the warnings above and this really is my prepared camera card.")
        self.card_confirm_box.pack(anchor="w", pady=4)
        self.copy_button = self.ttk.Button(tab, text="Copy autoexec.bin to card", command=self._copy)
        self.copy_button.pack(anchor="w")
        self.copy_result = self.ttk.Label(tab, text="", wraplength=700, justify="left")
        self.copy_result.pack(anchor="w", pady=6)

    def _prep_tab(self, scrolledtext):
        tab = self._tab("5. Prepare card")
        self.ttk.Label(tab, text=FORMAT_WARNING, wraplength=740, justify="left", foreground="#b3261e",
                       font=("TkDefaultFont", 0, "bold")).pack(anchor="w")
        self.ttk.Label(tab, text=EXPLAIN, wraplength=740, justify="left").pack(anchor="w", pady=(4, 0))
        self.ttk.Label(tab, text=card_devices.UNTESTED_NOTE.get(self.prep.platform, "This system: not supported."),
                       foreground="#b3261e", wraplength=740, justify="left").pack(anchor="w", pady=(4, 0))
        row = self.ttk.Frame(tab)
        row.pack(fill="x", pady=(6, 0))
        self.prep_find = self.ttk.Button(row, text="Find SD cards", command=self._prep_find)
        self.prep_find.pack(side="left")
        self.prep_choice = self.tk.StringVar()
        self.prep_combo = self.ttk.Combobox(row, textvariable=self.prep_choice, state="disabled", width=70)
        self.prep_combo.pack(side="left", padx=6, fill="x", expand=True)
        self.prep_combo.bind("<<ComboboxSelected>>", lambda _e: self._prep_selected())
        self.prep_details = self.ttk.Label(tab, text="", wraplength=740, justify="left")
        self.prep_details.pack(anchor="w", pady=(4, 0))
        buttons = self.ttk.Frame(tab)
        buttons.pack(anchor="w", pady=4)
        self.prep_button = self.ttk.Button(buttons, text="Prepare card", command=lambda: self._prep_run("mark"))
        self.prep_button.pack(side="left")
        self.prep_undo = self.ttk.Button(buttons, text="Undo (restore the latest backup)",
                                         command=lambda: self._prep_run("undo"))
        self.prep_undo.pack(side="left", padx=6)
        self.prep_restore = self.ttk.Button(buttons, text="Restore from a chosen backup...",
                                            command=lambda: self._prep_run("restore"))
        self.prep_restore.pack(side="left")
        self.prep_blockers = self.ttk.Label(tab, text="", wraplength=740, justify="left", foreground="#7a5c00")
        self.prep_blockers.pack(anchor="w", pady=2)
        self.prep_title = self.ttk.Label(tab, text="", wraplength=740, justify="left")
        self.prep_title.pack(anchor="w")
        self.prep_log = scrolledtext.ScrolledText(tab, height=9, state="disabled", font="TkFixedFont")
        self.prep_log.pack(fill="both", expand=True, pady=4)

    def _refresh_prep(self):
        p = self.prep
        accepted = p.accepted
        self.prep_combo.configure(values=[c.summary() for c in accepted],
                                  state="readonly" if accepted and not p.busy and p.session is None else "disabled")
        self.prep_choice.set(p.selected.summary() if p.selected else "")
        lines = p.selected.details() if p.selected else []
        if p.searched and not accepted:
            lines.append("No usable SD card found.")
        if p.refused:
            lines.append("Not offered: " + "; ".join(f"{c.whole_id} ({c.model or c.bus or '?'}): {c.refusals[0]}"
                                                     for c in p.refused))
        self.prep_details.configure(text="\n".join(lines))
        blocked = p.blockers()
        self.prep_find.configure(state="disabled" if p.busy or p.session is not None or p.copying else "normal")
        for button in (self.prep_button, self.prep_undo, self.prep_restore):
            button.configure(state="disabled" if blocked else "normal")
        self.prep_blockers.configure(text="" if not blocked else "Next:\n- " + "\n- ".join(blocked))
        if p.error:
            title, body, colour = p.error, [], STATUS_COLOURS[FAILED]
        elif p.report:
            title, body = p.report.title, p.report.lines
            colour = STATUS_COLOURS[OK] if p.report.ok else STATUS_COLOURS[FAILED]
        else:
            title, body, colour = "", [], "#444"
        self.prep_title.configure(text=title, foreground=colour)
        self.prep_log.configure(state="normal")
        self.prep_log.delete("1.0", "end")
        self.prep_log.insert("end", "\n".join(body))
        self.prep_log.configure(state="disabled")

    def _prep_find(self):
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
        if p.blockers():
            return
        cand, source = p.selected, None
        if kind == "undo":
            source = card_flow.latest_backup(cand, p.backup_root)
            if source is None:
                p.failed(f"No backup of this card from before it was prepared was found in {p.backup_root}. "
                         "Use 'Restore from a chosen backup...' instead.")
                self.refresh()
                return
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
        p.restore_from = source
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
            self._background(lambda: card_flow.write(result, ops), lambda r: self._prep_finished(r, None, show=True))
        self._background(lambda: card_flow.start(cand, ops, p.backup_root, restore_from=source), read_done)

    def _prep_finished(self, report, note, show=False):
        if isinstance(report, Exception):  # write() and cancel() report instead of raising; be safe anyway
            report = card_flow.Report(False, "Card state unknown.", [f"{report}",
                                      "Read the card again (Prepare or Undo) before using it."])
        if note:
            report.lines.insert(0, note)
        self.prep.finished(report)
        self.refresh()
        if show:
            self.messagebox.showinfo("Card preparation", "\n".join([report.title] + report.lines))

    def _on_close(self):
        p = self.prep
        if p.busy and not self.messagebox.askokcancel(
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
        self.rom_var.set(str(s.rom_path or ""))
        self._show(self.rom_status, s.rom_check())
        self.profile_var.set(s.profile)
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
                reason.configure(text="Off: no indicator code, hook or memory is added.")
        for view in s.input_views():
            var, button, status = self.input_widgets[view.id]
            var.set(str(view.path or ""))
            button.configure(state="normal" if view.enabled else "disabled")
            if view.enabled:
                self._show(status, view.check)
            else:
                status.configure(text=view.reason, foreground="#555")
        self.tc_var.set(str(s.toolchain_path or ""))
        self._show(self.tc_status, s.toolchain_check())
        self.out_var.set(str(s.output_dir or ""))
        blockers = s.blockers()
        self.blockers.configure(text="Ready to build." if not blockers else "Before you can build:\n- " + "\n- ".join(blockers))
        self.build_button.configure(state="disabled" if blockers else "normal")
        ready_to_copy = self.summary is not None and self.card_info is not None and not self.card_info.errors \
            and (not self.card_info.warnings or self.card_confirm.get()) and not s.building and not self.copying \
            and not self.prep.blocks_copy
        self.copy_button.configure(state="normal" if ready_to_copy else "disabled")
        self.card_confirm_box.configure(state="normal" if self.card_info and self.card_info.warnings else "disabled")
        self._refresh_prep()
        for what, input_id in s.needs_check():
            self._start_check(what, input_id)

    def _start_check(self, what, input_id=None):
        s = self.state
        key = s.begin(what, input_id)
        if what == "rom":
            rom, profile, indicator = key
            work = lambda: checks.check_rom(Path(rom), profile=profile, record_indicator=indicator)
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
            self.refresh()

    def _pick_toolchain(self):
        path = self.filedialog.askdirectory(title="Choose the toolchain's bin folder (contains arm-none-eabi-gcc)")
        if path:
            self.state.set_toolchain(Path(path))
            self.refresh()

    def _detect_toolchain(self, explicit=False):
        self.tc_status.configure(text="Looking for an installed GNU Arm 15.2.1 toolchain...", foreground="#7a5c00")

        def done(result):
            if isinstance(result, Exception):
                self.tc_status.configure(text=f"Detection failed: {result}", foreground=STATUS_COLOURS[FAILED])
                return
            path, check, notes = result
            if path is None:
                note = ("\n" + "\n".join(notes)) if notes else ""
                self.tc_status.configure(text="No GNU Arm 15.2.1 toolchain found. Install it as described above, "
                                         "then choose its bin folder." + note, foreground=STATUS_COLOURS[FAILED])
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
        self.result.configure(text="Building... this usually takes under a minute.", foreground="#444")
        self.gauge.configure(value=0)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._append_log("python3 tools/build.py " + " ".join(argv))
        self.refresh()

        def on_line(line):
            self.events.put((self._append_log, line))

        def done(outcome):
            s.building = False
            if isinstance(outcome, Exception):
                self.result.configure(text=f"Build could not run: {outcome}", foreground=STATUS_COLOURS[FAILED])
            elif not outcome.ok:
                self.result.configure(text="Build refused or failed: " + outcome.error_text(),
                                      foreground=STATUS_COLOURS[FAILED])
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
            self.result.configure(text=f"Build finished but its record could not be read: {exc}",
                                  foreground=STATUS_COLOURS[FAILED])
            return
        self.summary = summary
        match = "matches build.json" if summary.sha256_matches_build_json else "DOES NOT MATCH build.json"
        self.result.configure(foreground=STATUS_COLOURS[OK] if summary.sha256_matches_build_json else STATUS_COLOURS[FAILED],
                              text=(f"Built: {summary.autoexec}\nSHA-256 {summary.sha256} ({match})\n"
                                    f"{runner.memory_line(summary)}\nWhat's in this build: {contents}"))
        self.gauge.configure(value=100 * summary.remaining_heap / summary.heap_pool)
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
        self.card_report.configure(text="Looking at the card...", foreground="#444")
        self.refresh()
        self._background(lambda: card.inspect_card(Path(path)), lambda info: self._card_inspected(path, info))

    def _card_inspected(self, path, info):
        if self.card_var.get() != path:
            return  # another folder was chosen meanwhile
        if isinstance(info, Exception):
            self.card_report.configure(text=f"Could not look at the card: {info}", foreground=STATUS_COLOURS[FAILED])
            return
        self.card_info = info
        lines = [f"Folder: {info.root}"]
        if info.total_bytes is not None:
            lines.append(f"Drive size: {info.total_bytes / 1000**3:,.1f} GB")
        lines.append("DCIM folder: " + ("found" if info.has_dcim else "not found"))
        lines.append("Existing autoexec.bin: " + (info.existing.name if info.existing else "none"))
        lines += ["REFUSED: " + e for e in info.errors] + ["Warning: " + w for w in info.warnings]
        if self.summary is None:
            lines.append("Build autoexec.bin first (tab 4).")
        colour = STATUS_COLOURS[FAILED] if info.errors else (STATUS_COLOURS[CHECKING] if info.warnings else "#444")
        self.card_report.configure(text="\n".join(lines), foreground=colour)
        self.refresh()

    def _copy(self):
        summary, info = self.summary, self.card_info
        backups = summary.output_dir / "card-backup"
        if not self.messagebox.askokcancel(
            "Copy to card",
            f"Copy autoexec.bin to {info.root}?\n\nAny existing autoexec.bin is backed up to {backups} first.",
        ):
            return
        if self.prep.blocks_copy:
            return  # a card preparation step is running or waiting for confirmation
        confirmed = list(info.warnings) if self.card_confirm.get() else []
        self.copying = self.prep.copying = True
        self.copy_result.configure(text="Copying...", foreground="#444")
        self.refresh()

        def done(result):
            self.copying = self.prep.copying = False
            if isinstance(result, (card.CardError, OSError)):
                self.copy_result.configure(text=f"Not copied: {result}", foreground=STATUS_COLOURS[FAILED])
            elif isinstance(result, Exception):
                self.copy_result.configure(text=f"Copy failed unexpectedly: {result}", foreground=STATUS_COLOURS[FAILED])
            else:
                self.copy_result.configure(text="\n".join(result.messages), foreground=STATUS_COLOURS[OK])
                self.messagebox.showinfo("Done", "\n\n".join(result.messages))
            self.refresh()
        self._background(lambda: card.copy_to_card(summary.autoexec, summary.sha256, info.root, backups,
                                                   confirmed_warnings=confirmed), done)


def main() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
    except ImportError:
        print(
            "This Python has no Tkinter, which the build assistant needs.\n"
            "- macOS/Windows: the python.org installer includes it.\n"
            "- Homebrew Python: install the matching python-tk formula.\n"
            "- Linux: install your distribution's python3-tk package.\n"
            "The command-line builder (tools/build.py) works without it.",
            file=sys.stderr,
        )
        return 1
    app = App(tk, ttk, filedialog, messagebox, scrolledtext)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
