"""Widget-free state of the Screenshots utility (outside the setup steps), so it can be tested headless.

The person picks the card's top folder, its DCIM or R5SHOTS folder, or any folder holding a
copied R5SHOTS. Every SS_*.YCA there is converted to PNG (tools/screenshots.py) into a folder
on this computer, by default ~/Pictures/R5 screenshots/<date>. Nothing is ever written to
the card or the folder the screenshots come from.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import screenshots
from gui import card
from gui.theme import DANGER, SUCCESS, WARNING
from gui.wizard import Guide

SCREENSHOTS = "screenshots"  # the utility's id (not a setup step)
TITLE = "Screenshots"

LAB_NOTE = ("A build with the One-press screenshot option (--screenshot rate) saves these screen files. Its "
            "JPEG screenshots are ordinary photos and need no conversion; this tool turns the SS_*.YCA "
            "on-screen display files into PNG.")


GUIDE = Guide(
    "A lab build of the mod can save what the camera's screen shows as files in DCIM/R5SHOTS on the SD card. "
    "The camera's own playback can't show them, so this converts them to ordinary PNG pictures on this computer.",
    "Put the card in this computer (or copy its R5SHOTS folder here) and choose it below. Check where the PNG "
    "files go, then press Convert to PNG. Eject the card when you are done.",
    "Each SS_nnnnn.YCA holds an R5SS header and the camera's display layer: 4:2:2 YCbCr with alpha (UYVA), "
    "1024x682 for the rear LCD or 1600x1200 for the viewfinder. It is the on-screen display only; the live "
    "picture underneath is not in it. The colours use the BT.601 matrix at full range, the same conversion as "
    "the lab's converter; alpha is not used. Files with an unknown version, size or layout are refused and "
    "listed. The card and the folder you choose are only read; a file converted before into the same output "
    "folder (same name, size and contents) is skipped, and an existing PNG is never overwritten. Command line: "
    "python3 tools/screenshots.py <R5SHOTS folder> <output folder>.")


def default_output_dir(today: date | None = None, home: Path | None = None) -> Path:
    home = Path.home() if home is None else Path(home)
    return home / "Pictures" / "R5 screenshots" / (today or date.today()).isoformat()


@dataclass(frozen=True)
class Source:
    picked: Path
    shots: Path | None   # the R5SHOTS folder, if found
    files: tuple[Path, ...]
    on_card: bool        # R5SHOTS is in DCIM at the top of a drive, as on a camera card

    def describe(self) -> str:
        if self.shots is None:
            return (f"No {screenshots.SHOTS_FOLDER} folder in {self.picked}. Choose the card's top folder, "
                    f"its DCIM folder, or a copied {screenshots.SHOTS_FOLDER} folder.")
        n = len(self.files)
        where = " on the camera card" if self.on_card else ""
        if not n:
            return f"{self.shots}{where} has no screenshots (SS_*.YCA)."
        return f"{n} screenshot{'s' if n != 1 else ''} in {self.shots}{where}."


def find_source(picked: Path) -> Source:
    picked = Path(picked)
    shots = screenshots.find_shots_folder(picked)
    if shots is None:
        return Source(picked, None, (), False)
    files = tuple(screenshots.list_shots(shots))
    dcim = shots.parent
    on_card = dcim.name.casefold() == "dcim" and os.path.ismount(dcim.parent)
    return Source(picked, shots, files, on_card)


class ShotsState:
    def __init__(self, *, today: date | None = None, home: Path | None = None):
        self.home = home
        self.source: Source | None = None
        self.output_dir = default_output_dir(today, home)
        self.busy: str | None = None
        self.result: screenshots.BatchResult | None = None
        self.error: str | None = None

    def set_source(self, source: Source) -> None:
        self.source, self.result, self.error = source, None, None

    def set_output(self, path: Path) -> None:
        self.output_dir, self.result, self.error = Path(path), None, None

    def output_problem(self) -> str | None:
        """Why the output folder is refused: a system or home-level folder (the same rule the
        copy step uses for cards), or a folder inside the screenshots' folder or on the card."""
        home = Path.home() if self.home is None else Path(self.home)
        resolved = Path(os.path.abspath(self.output_dir.expanduser()))
        protected = card.protected_location(resolved, windows=os.name == "nt", home=home.resolve(),
                                            environ=os.environ)
        if protected:
            return protected.replace("not a camera card", "not a folder for screenshots")
        if self.source is not None and self.source.shots is not None:
            try:
                screenshots.check_output(self.output_dir, self.source.shots, self.source.picked, home=home)
            except screenshots.ScreenshotError as exc:
                return str(exc)
        return None

    def blockers(self) -> list[str]:
        if self.busy:
            return [f"Wait: {self.busy}"]
        if self.source is None:
            return ["Choose the card or a folder with screenshots."]
        if self.source.shots is None or not self.source.files:
            return [self.source.describe()]
        problem = self.output_problem()
        return [problem] if problem else []

    def begin(self) -> tuple[Source, Path]:
        if self.blockers():
            raise RuntimeError(self.blockers()[0])
        self.busy, self.result, self.error = "Converting...", None, None
        return self.source, self.output_dir

    def progress(self, index: int, total: int, name: str | None) -> None:
        if self.busy and name:
            self.busy = f"Converting {index + 1} of {total}: {name}"

    def finished(self, result) -> None:
        self.busy = None
        if isinstance(result, Exception):
            self.result, self.error = None, f"Not converted: {result}"
        else:
            self.result, self.error = result, None

    def report(self) -> tuple[str, str, list[str]] | None:
        """(kind, headline, lines) for the result, or None."""
        if self.error:
            return DANGER, self.error, []
        r = self.result
        if r is None:
            return None
        lines = [f"Saved in {r.out_dir}"]
        lines += [f"{c.source.name}: {c.header.describe()} -> {c.png.name}" for c in r.converted]
        lines += [f"{c.source.name}: already converted ({c.png.name})" for c in r.skipped]
        lines += [f"{name}: not converted ({reason})" for name, reason in r.errors]
        if self.source is not None and self.source.on_card:
            lines.append(card.eject_hint())
        if r.errors:
            kind = WARNING if r.converted or r.skipped else DANGER
        else:
            kind = SUCCESS
        return kind, r.summary().capitalize() + ".", lines

    def can_open_output(self) -> bool:
        return not self.busy and self.output_dir.is_dir()
