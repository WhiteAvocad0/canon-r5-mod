"""Canon R5 mod build assistant (prototype, issue #16 Phase 1).

Run:  python3 tools/gui/app.py

Standard-library Python 3.11+ with Tkinter only. This window is a thin view: the
decisions live in state.py, checks.py, runner.py and card.py, and the build itself
is tools/build.py with all of its checks. It never downloads anything, never
formats or partitions a card, never writes boot sectors and never contacts a camera.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import webbrowser

if sys.version_info < (3, 11):
    sys.exit("The build assistant needs Python 3.11 or newer (https://www.python.org/downloads/).")

TOOLS_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from gui import card, checks, runner
from gui.state import CHECKING, FAILED, OK, AssistantState, default_output_dir

DOCS = {
    "Getting started guide": ROOT / "docs" / "getting-started.md",
    "Prerequisites": ROOT / "docs" / "usage.md",
    "About this assistant": ROOT / "docs" / "gui.md",
}
CANNOT_DO = """\
This assistant only builds autoexec.bin on your computer and copies it to a card
that is ALREADY prepared. It cannot, and will not try to:

  - dump your camera's ROM (you need your own 64 MiB gang100.bin first);
  - enable the camera's BootDisk setting (a permanent change inside the camera);
  - make an SD card bootable (the EOS_DEVELOP / BOOTDISK boot markers);
  - format, partition or erase a card, or write raw disk sectors;
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
        self._card_tab()
        cannot = ttk.Frame(self.notebook, padding=10)
        ttk.Label(cannot, text=CANNOT_DO, justify="left").pack(anchor="w")
        self.notebook.add(cannot, text="What this tool cannot do")

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
        try:
            while True:
                done, result = self.events.get_nowait()
                done(result)
        except queue.Empty:
            pass
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
        self.ttk.Button(row, text="Detect again", command=self._detect_toolchain).pack(side="left")
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
        tab = self._tab("5. Copy to card")
        self.ttk.Label(tab, wraplength=700, justify="left", text=(
            "Only for a card that is already bootable (prepared separately). Choose the card's top folder "
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
            and (not self.card_info.warnings or self.card_confirm.get()) and not s.building
        self.copy_button.configure(state="normal" if ready_to_copy else "disabled")
        self.card_confirm_box.configure(state="normal" if self.card_info and self.card_info.warnings else "disabled")
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

    def _detect_toolchain(self):
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
            self.state.set_toolchain(path)
            key = self.state.toolchain_key()
            self.state.finish("toolchain", key, True, check.message, check.facts)
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
        info = card.inspect_card(Path(path))
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
        try:
            result = card.copy_to_card(summary.autoexec, summary.sha256, info.root, backups,
                                       confirmed=self.card_confirm.get())
        except (card.CardError, OSError) as exc:
            self.copy_result.configure(text=f"Not copied: {exc}", foreground=STATUS_COLOURS[FAILED])
            return
        self.copy_result.configure(text="\n".join(result.messages), foreground=STATUS_COLOURS[OK])
        self.messagebox.showinfo("Done", "\n\n".join(result.messages))


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
