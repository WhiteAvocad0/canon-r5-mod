"""Widget-free state of the "Prepare card" step (boot markers), so it can be tested headless.

The person picks a detected SD card from a list and presses Prepare (or Undo). In
the background the card is read and its boot area is backed up automatically and
verified; then either the result is reported (already prepared) or one confirmation
dialog names the card. Only after OK is anything written.

Invariants kept here (tested in tests/test_gui_prep_state.py):

- the dialog and the write use the card from the read that produced the backup
  (``session.snapshot.candidate``), never the list's current text;
- choosing another card, searching again, the card disappearing, or any write
  attempt discards the session (read, plan and backup handle);
- a read that finishes after the choice changed is thrown away (generation counter);
- after OK, the write starts only if the confirmed session is still the current one;
- one card operation at a time, and none while step 8 copies to the card (and step 8
  is blocked while a card operation or a confirmation is pending);
- every report names the backup folder (workflow adds it).
"""
from __future__ import annotations

from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from messages import joined, message, phrase

from cardprep import devices, workflow

FORMAT_WARNING = ("Format this card IN THE CAMERA first. Formatting erases everything on the card — "
                  "copy your photos and videos to your computer before formatting.")
EXPLAIN = (
    "Prepare card writes the two Canon boot markers (EOS_DEVELOP and BOOTDISK) into the card's boot sectors, so a "
    "camera whose BootDisk setting is already enabled loads autoexec.bin from it. This tool never formats, "
    "partitions or erases a card, and it refuses cards whose boot sectors do not look camera-formatted. "
    "Formatting removes the markers, so prepare the card again after every format. It does not enable BootDisk "
    "and never writes the camera.\n\n"
    "Before anything is written, the card's partition table and boot sectors are backed up automatically to a "
    "folder on this computer and checked; Undo puts them back. Only the planned sectors are written: one on "
    "FAT32, four on exFAT. macOS asks for your administrator password in its own window (once to read, once to "
    "write); this assistant never sees it.\n\n"
    "Prototype: verified on one Mac with a camera-formatted exFAT card; FAT32 cards are untested. "
    "Windows and Linux can only list cards."
)


class PrepState:
    def __init__(self, platform: str | None = None, backup_root: Path | None = None):
        self.platform = sys.platform if platform is None else platform
        self.backup_root = workflow.DEFAULT_BACKUP_ROOT if backup_root is None else Path(backup_root)
        self.candidates: list[devices.Candidate] = []
        self.searched = False
        self.selected: devices.Candidate | None = None
        self.generation = 0
        self.busy: str | None = None
        self.copying = False  # step 8 is copying to a card
        self.session: workflow.Session | None = None  # read and backed up, waiting for confirmation
        self.restore_from: workflow.Backup | None = None  # a chosen backup; None means the latest one
        self.report: workflow.Report | None = None
        self.error: str | None = None

    # -- choices --------------------------------------------------------------------

    @property
    def supported(self) -> bool:
        return devices.prepare_supported(self.platform)

    @property
    def accepted(self) -> list[devices.Candidate]:
        return [c for c in self.candidates if c.accepted]

    @property
    def refused(self) -> list[devices.Candidate]:
        return [c for c in self.candidates if not c.accepted]

    def _discard(self) -> None:
        """Forget everything derived from the previous choice or read."""
        self.generation += 1
        self.session = None
        self.report = self.error = None

    def set_candidates(self, candidates: list[devices.Candidate]) -> None:
        self.candidates = list(candidates)
        self.searched = True
        self.selected = None
        self.restore_from = None
        self._discard()

    def select(self, whole_id: str | None) -> None:
        """Choose one of the accepted cards by its device id (None clears the choice)."""
        if whole_id is None:
            chosen = None
        else:
            matches = [c for c in self.accepted if c.whole_id == whole_id]
            if not matches:
                raise ValueError(message('{whole_id} is not a detected, accepted SD card', whole_id=whole_id))
            chosen = matches[0]
        if chosen != self.selected:
            self.selected = chosen
            self.restore_from = None
            self._discard()

    def card_gone(self, message: str) -> None:
        self.selected = None
        self._discard()
        self.error = message

    def set_restore_from(self, backup: workflow.Backup | None) -> None:
        self.restore_from = backup
        self._discard()

    # -- what is allowed --------------------------------------------------------------

    @property
    def blocks_copy(self) -> bool:
        """Tab 6 must not touch the card while a card operation or confirmation is pending."""
        return self.busy is not None or self.session is not None

    def blockers(self) -> list[str]:
        reasons = []
        if self.busy:
            reasons.append(message('Wait: {busy}', busy=phrase(self.busy)))
        if self.copying:
            reasons.append("Wait: autoexec.bin is being copied to a card (step 8).")
        if self.session is not None:
            reasons.append("Answer the confirmation first.")
        if not self.supported:
            reasons.append("Preparing cards is offered on macOS only in this prototype "
                           "(Windows and Linux can only list cards, untested).")
        if not self.searched:
            reasons.append("Find SD cards.")
        elif self.selected is None:
            reasons.append("Choose your card from the list." if self.accepted
                           else "No usable SD card was found. Insert the card and search again.")
        return reasons

    # -- background steps -------------------------------------------------------------

    def begin(self, label: str) -> int:
        """Start a card operation; returns the token its results must carry."""
        if self.blockers():
            raise RuntimeError("a card operation is not allowed now: " + " ".join(self.blockers()))
        self.busy = label
        self.report = self.error = None
        return self.generation

    def _current(self, token: int, cand: devices.Candidate) -> bool:
        return token == self.generation and self.selected is not None and devices.same_card(self.selected, cand)

    def read_finished(self, token: int, session: workflow.Session) -> str:
        """'stale' (throw it away: the caller cancels it), 'done' (no write needed) or 'confirm'."""
        self.busy = None
        if not self._current(token, session.snapshot.candidate):
            return "stale"
        if session.report is not None:
            self.report = session.report
            return "done"
        self.session = session
        return "confirm"

    def confirmation(self) -> tuple[str, str]:
        """Title and text of the one confirmation dialog, from the read's own card."""
        s = self.session
        cand, plan = s.snapshot.candidate, s.plan
        sectors = ", ".join(str(w.sector) for w in plan.writes)
        if plan.kind == "mark":
            title, question = "Prepare card", message('Prepare {card}?', card=cand.label())
            what = message(
                "This writes the boot markers: sector(s) {sectors}, {size:,} bytes.",
                sectors=sectors, size=len(plan.writes) * 512)
        else:
            title, question = "Undo card preparation", message('Restore the boot sectors of {card}?', card=cand.label())
            what = message(
                "This puts back sector(s) {sectors} from the backup in {folder}.",
                sectors=sectors, folder=s.source.folder)
        return title, joined("\n\n", [
            question, message('{model}, {whole_id}.', model=cand.model, whole_id=cand.whole_id), what,
            "Nothing else on the card is changed. macOS will ask for your administrator password.",
            message('Backup of the card as it is now: {backup}', backup=s.backup.folder)])

    def may_write(self, token: int, session: workflow.Session) -> bool:
        """After OK: only the exact session that was confirmed, if nothing changed meanwhile."""
        return token == self.generation and self.session is session and self.busy is None and not self.copying \
            and self.selected is not None and devices.same_card(self.selected, session.snapshot.candidate)

    def begin_write(self, session: workflow.Session) -> None:
        self.busy = "Writing and verifying (macOS asks for your password)..."
        self.session = None  # a session is used for at most one write attempt

    def finished(self, report: workflow.Report) -> None:
        """Any outcome of a write, a cancel or a refusal."""
        self.busy = None
        self.session = None
        self.generation += 1
        self.report = report

    def failed(self, message: str) -> None:
        self.busy = None
        self.session = None
        self.generation += 1
        self.error = message
