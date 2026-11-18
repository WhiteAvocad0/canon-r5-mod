"""Widget-free state of the "Prepare card" step (boot markers), so it can be tested headless.

The order is fixed: explain (the person confirms the card was formatted in the
camera and their files are copied off), select a detected SD card, show its
details, confirm by typing its volume name, read the card and save a verified
backup, then write. Choosing another card, editing the typed name or finding
cards again throws away everything derived from the previous choice, so a plan
or backup can only ever be used for the card and read it came from.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from cardprep import devices, layout, workflow

EXPLAIN = (
    "This step writes the two Canon boot markers (EOS_DEVELOP and BOOTDISK) into the boot sector of an SD card, "
    "so a camera whose BootDisk setting is already enabled loads autoexec.bin from it. "
    "Format the card IN THE CAMERA first (Setup menu, Format card): this tool does not format, partition or erase "
    "anything, and it refuses cards that do not look camera-formatted. Formatting removes the markers, so you redo "
    "this step after every format. It does not enable BootDisk and never writes the camera.\n\n"
    "Before anything is written, the card's partition table (MBR) and boot sectors are saved to a timestamped "
    "folder on this computer, and you can restore them from there. Only the planned sectors are written: one "
    "sector on FAT32, four on exFAT (the markers and the boot checksum in both boot regions). Reading and writing "
    "raw sectors needs your administrator password, which macOS asks for in its own window; this assistant never "
    "sees it.\n\n"
    "Prototype: the real-card path has not yet been tested on a real card. Windows and Linux can only list cards."
)
ACKNOWLEDGE = "I formatted this card in the camera and copied off any photos I want to keep."


@dataclass(frozen=True)
class Step:
    label: str
    done: bool


class PrepState:
    def __init__(self, platform: str | None = None, backup_root: Path | None = None):
        self.platform = sys.platform if platform is None else platform
        self.backup_root = workflow.DEFAULT_BACKUP_ROOT if backup_root is None else Path(backup_root)
        self.acknowledged = False
        self.candidates: list[devices.Candidate] = []
        self.searched = False
        self.selected: devices.Candidate | None = None
        self.typed_name = ""
        self.snapshot: workflow.Snapshot | None = None
        self.plan: layout.Plan | None = None
        self.backup: workflow.Backup | None = None
        self.restore_from: workflow.Backup | None = None
        self.report: workflow.Report | None = None
        self.busy: str | None = None
        self.error: str | None = None

    # -- inputs -----------------------------------------------------------------

    @property
    def supported(self) -> bool:
        return devices.prepare_supported(self.platform)

    @property
    def accepted(self) -> list[devices.Candidate]:
        return [c for c in self.candidates if c.accepted]

    @property
    def refused(self) -> list[devices.Candidate]:
        return [c for c in self.candidates if not c.accepted]

    def _forget_card_work(self) -> None:
        self.snapshot = self.plan = self.backup = self.report = None
        self.error = None

    def set_acknowledged(self, value: bool) -> None:
        self.acknowledged = bool(value)

    def set_candidates(self, candidates: list[devices.Candidate]) -> None:
        self.candidates = list(candidates)
        self.searched = True
        self.selected = None
        self.typed_name = ""
        self._forget_card_work()

    def select(self, whole_id: str | None) -> None:
        """Choose one of the accepted cards by its device id (None clears the choice)."""
        if whole_id is None:
            chosen = None
        else:
            matches = [c for c in self.accepted if c.whole_id == whole_id]
            if not matches:
                raise ValueError(f"{whole_id} is not a detected, accepted SD card")
            chosen = matches[0]
        if chosen != self.selected:
            self.selected = chosen
            self.typed_name = ""
            self._forget_card_work()

    def set_typed_name(self, text: str) -> None:
        if text != self.typed_name:
            self.typed_name = text
            self._forget_card_work()

    def set_restore_from(self, backup: workflow.Backup | None) -> None:
        self.restore_from = backup
        self._forget_card_work()

    @property
    def name_confirmed(self) -> bool:
        """The exact volume name, typed by the person (case and spaces matter)."""
        return self.selected is not None and bool(self.selected.volume_name) and \
            self.typed_name == self.selected.volume_name

    # -- results from background steps ---------------------------------------------

    def _is_current(self, cand: devices.Candidate) -> bool:
        return self.selected is not None and cand.identity() == self.selected.identity() and self.name_confirmed

    def read_done(self, snapshot: workflow.Snapshot, plan: layout.Plan, backup: workflow.Backup) -> bool:
        """Accept a finished read only if it is still for the card and confirmation shown now."""
        if not self._is_current(snapshot.candidate) or backup.area != snapshot.area or plan.before_area != snapshot.area:
            return False
        self.snapshot, self.plan, self.backup, self.report, self.error = snapshot, plan, backup, None, None
        return True

    def write_done(self, report: workflow.Report) -> None:
        """After any write attempt the card may have changed: the old read and plan are never reused."""
        self.report = report
        self.snapshot = self.plan = None

    def failed(self, message: str) -> None:
        self.error = message

    # -- what is allowed --------------------------------------------------------------

    def _card_blockers(self, restoring: bool = False) -> list[str]:
        reasons = []
        if self.busy:
            reasons.append(f"Wait: {self.busy}")
        if not self.supported:
            reasons.append("Preparing cards is offered on macOS only in this prototype "
                           "(Windows and Linux can only list cards, untested).")
        if not self.acknowledged and not restoring:
            reasons.append("Confirm that the card was formatted in the camera and your photos are copied off.")
        if not self.searched:
            reasons.append("Search for SD cards.")
        elif self.selected is None:
            reasons.append("Choose your card from the detected SD cards." if self.accepted
                           else "No usable SD card was found. Insert the camera-formatted card and search again.")
        elif not self.name_confirmed:
            reasons.append(f"Type the card's volume name exactly ({self.selected.volume_name}) to confirm.")
        return reasons

    def read_blockers(self, *, restoring: bool = False) -> list[str]:
        reasons = self._card_blockers(restoring)
        if restoring and self.restore_from is None:
            reasons.append("Choose a backup folder to restore from.")
        return reasons

    def write_blockers(self) -> list[str]:
        reasons = self._card_blockers(restoring=self.plan is not None and self.plan.kind == "restore")
        if not reasons:
            if self.snapshot is None or self.plan is None:
                reasons.append("Read the card first (this also saves the backup).")
            elif self.backup is None:
                reasons.append("The backup has not been saved and verified.")
            elif not self.plan.writes_needed:
                reasons.append("Nothing to write.")
            elif self.snapshot.candidate.identity() != self.selected.identity():
                reasons.append("The read is for a different card; read the card again.")
        return reasons

    def steps(self) -> list[Step]:
        return [
            Step("1. Format the card in the camera, then confirm here", self.acknowledged),
            Step("2. Choose the SD card", self.selected is not None),
            Step("3. Type its volume name to confirm", self.name_confirmed),
            Step("4. Read the card and save a backup", self.backup is not None),
            Step("5. Write the markers and verify", bool(self.report and self.report.ok)),
        ]

    def plan_lines(self) -> list[str]:
        if self.plan is None:
            return []
        lines = layout.describe_plan(self.plan)
        if self.backup is not None:
            lines.append(f"Backup saved and verified: {self.backup.folder}")
        return lines
