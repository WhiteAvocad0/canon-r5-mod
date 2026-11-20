"""Widget-free state of the step-by-step setup wizard (issue #16), so it can be tested headless.

The wizard walks a new R5 owner through nine steps, one at a time. Every step has a
status (done, needs action, skipped or blocked) worked out from the same objects the
window uses (AssistantState for ROM, features, toolchain and build; PrepState for the
boot card) plus the few facts the person confirms here. Progress is kept in a small
JSON file so the window can be closed and reopened; on resume every stored fact that
can be re-checked (ROM, WAVs, toolchain, the last build) is checked again before it
counts.

Returning owners get a short path, "Update my card": Features, Toolchain (only if it
no longer passes), Build, Copy. It is offered only when the one-time steps (camera,
ROM, BootDisk) are already done.

Steps 2 (ROM dump) and 3 (BootDisk) guide a Canon Basic SDHC card flow that you run
yourself in the camera, with ROM readback verification, alongside picking an
existing ROM dump or confirming an earlier BootDisk setup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from gui.state import FAILED, OK, AssistantState

DONE, NEEDS_ACTION, SKIPPED, BLOCKED = "done", "needs action", "skipped", "blocked"
STATE_VERSION = 1
DEFAULT_STATE_FILE = Path.home() / ".r5-build-assistant" / "wizard.json"

CAMERA, ROM, BOOTDISK, FEATURES, TOOLCHAIN, BUILD, BOOTCARD, COPY, TEST = (
    "camera", "rom", "bootdisk", "features", "toolchain", "build", "bootcard", "copy", "test")
ONE_TIME = (CAMERA, ROM, BOOTDISK)

TEST_CHECKLIST = (
    "Camera off, USB and HDMI unplugged; only the prepared SD card inserted (no CFexpress for the first test).",
    "Powered on normally; the camera started as usual.",
    "Took a full-frame photo as a control: it looks normal.",
    "Tried the features in this build (for XPan: chose XPan in the menu, took landscape and portrait shots, "
    "checked the review and playback on the LCD and in the viewfinder).",
    "Waited for the card light to go out before switching off.",
)
REPORT_URLS = {
    "xpan-crop": "https://github.com/WhiteAvocad0/canon-r5-mod/issues/new?template=xpan-test-report.yml",
    "shutter-feedback": "https://github.com/WhiteAvocad0/canon-r5-mod/issues/new?template=r5-test-report.yml",
}


@dataclass(frozen=True)
class StepInfo:
    id: str
    number: int
    title: str
    summary: str
    one_time: bool = False
    placeholder: bool = False


STEPS = (
    StepInfo(CAMERA, 1, "Welcome and camera check", "Make sure you have an original EOS R5 on firmware 2.2.1.", True),
    StepInfo(ROM, 2, "ROM dump", "Your own copy of the camera's firmware, needed to build (one time, read-only).", True),
    StepInfo(BOOTDISK, 3, "Enable BootDisk", "A one-time, permanent camera setting that lets it start a program "
             "from the card.", True),
    StepInfo(FEATURES, 4, "Choose features", "Pick what the mod does."),
    StepInfo(TOOLCHAIN, 5, "Build tools", "The free GNU Arm compiler that turns the source into autoexec.bin."),
    StepInfo(BUILD, 6, "Build and verify", "Build autoexec.bin on this computer and check it."),
    StepInfo(BOOTCARD, 7, "Prepare the boot card", "Write the two boot markers onto a camera-formatted SD card."),
    StepInfo(COPY, 8, "Copy autoexec.bin", "Put the build on the prepared card."),
    StepInfo(TEST, 9, "First camera test", "A short checklist for the first power-on, and where to report."),
)
STEP_BY_ID = {s.id: s for s in STEPS}


@dataclass(frozen=True)
class StepStatus:
    status: str
    message: str


@dataclass
class Saved:
    """What survives closing the window. Facts that can be re-checked are re-checked on resume."""
    camera_confirmed: bool = False
    bootdisk: str = "unknown"  # "unknown", "confirmed" (by the person), "verified" (readback), "disabled"
    bootdisk_rom_path: str | None = None
    bootdisk_rom_sha256: str | None = None
    profile: str | None = None
    options: dict = field(default_factory=dict)
    rom_path: str | None = None
    inputs: dict = field(default_factory=dict)
    toolchain_path: str | None = None
    features_confirmed: bool = False
    build_dir: str | None = None
    card_prepared: str | None = None  # "prepared" (tab result) or "elsewhere" (the person says so)
    copied_sha256: str | None = None
    test_checked: list = field(default_factory=list)
    mode: str = "full"
    current: str = CAMERA
    saved_at: str = ""

    def to_json(self) -> dict:
        return {"version": STATE_VERSION, **self.__dict__}

    @classmethod
    def from_json(cls, data: dict) -> "Saved":
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        saved = cls(**known)
        if saved.current not in STEP_BY_ID:
            saved.current = CAMERA
        if saved.bootdisk not in ("unknown", "confirmed", "verified", "disabled"):
            saved.bootdisk = "unknown"
        return saved


class Wizard:
    def __init__(self, assistant: AssistantState, *, state_file: Path | None = None, prep_supported: bool = True):
        self.a = assistant
        self.state_file = DEFAULT_STATE_FILE if state_file is None else Path(state_file)
        self.prep_supported = prep_supported
        self.saved = Saved(profile=assistant.profile, options=dict(assistant.options))
        self.build_summary = None  # a runner.BuildSummary that still verifies
        self.build_note: str | None = None
        self.copy_ok_session = False

    # -- persistence ---------------------------------------------------------------

    def save(self) -> None:
        self.saved.profile, self.saved.options = self.a.profile, dict(self.a.options)
        self.saved.rom_path = str(self.a.rom_path) if self.a.rom_path else None
        self.saved.inputs = {k: str(v) for k, v in self.a.input_paths.items() if v}
        self.saved.toolchain_path = str(self.a.toolchain_path) if self.a.toolchain_path else None
        self.saved.saved_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_name(self.state_file.name + ".tmp")
            tmp.write_text(json.dumps(self.saved.to_json(), indent=2) + "\n")
            os.replace(tmp, self.state_file)
        except OSError:
            pass  # progress is a convenience; the wizard works without it

    def load(self, *, verify_build=None, verify_bootdisk=None) -> None:
        """Restore saved progress into the assistant state; everything re-checkable is checked again
        (ROM, WAVs and toolchain by the window's normal check loop; the build by ``verify_build``;
        verified BootDisk state by ``verify_bootdisk``)."""
        try:
            data = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return
        self.saved = Saved.from_json(data)
        s, a = self.saved, self.a
        if s.bootdisk == "verified":
            if not s.bootdisk_rom_path or not s.bootdisk_rom_sha256:
                s.bootdisk = "unknown"
                s.bootdisk_rom_path = None
                s.bootdisk_rom_sha256 = None
            elif verify_bootdisk is not None and not verify_bootdisk(Path(s.bootdisk_rom_path), s.bootdisk_rom_sha256):
                s.bootdisk = "unknown"
                s.bootdisk_rom_path = None
                s.bootdisk_rom_sha256 = None
        if s.profile in a.manifest.profiles:
            a.set_profile(s.profile)
        for oid, value in (s.options or {}).items():
            try:
                a.set_option(oid, value)
            except (KeyError, ValueError):
                pass
        a.set_rom(Path(s.rom_path) if s.rom_path else None)
        for input_id, path in (s.inputs or {}).items():
            if input_id in a.input_paths:
                a.set_input(input_id, Path(path))
        a.set_toolchain(Path(s.toolchain_path) if s.toolchain_path else None)
        copied, checked = s.copied_sha256, list(s.test_checked)
        if s.build_dir and verify_build is not None:
            summary, note = verify_build(Path(s.build_dir))
            self.set_build(summary, note=note, save=False)
        # The copy (and the test checklist) count only for the build that still verifies.
        if self.build_summary is not None and copied == self.build_summary.sha256:
            s.copied_sha256, s.test_checked = copied, checked
        else:
            s.copied_sha256, s.test_checked = None, []

    # -- facts the person confirms or the window records -----------------------------

    def set_camera_confirmed(self, value: bool) -> None:
        self.saved.camera_confirmed = bool(value)
        self.save()

    def set_bootdisk(
        self,
        value: str,
        *,
        rom_path: Path | None = None,
        rom_sha256: str | None = None,
    ) -> None:
        if value not in ("unknown", "confirmed", "verified", "disabled"):
            raise ValueError(f"unknown BootDisk state {value!r}")
        if value == "verified":
            if rom_path is None or not rom_sha256:
                raise ValueError("Verified BootDisk state requires a readback ROM dump path and SHA-256")
            self.saved.bootdisk_rom_path = str(rom_path)
            self.saved.bootdisk_rom_sha256 = str(rom_sha256)
        else:
            self.saved.bootdisk_rom_path = None
            self.saved.bootdisk_rom_sha256 = None
        self.saved.bootdisk = value
        self.save()

    def confirm_features(self) -> None:
        self.saved.features_confirmed = True
        self.save()

    def features_changed(self) -> None:
        """A different selection invalidates the build (and so the copy)."""
        self.saved.features_confirmed = False
        if self.build_summary is not None and not self._build_matches(self.build_summary):
            self.set_build(None)
        self.save()

    def set_build(self, summary, *, note: str | None = None, save: bool = True) -> None:
        self.build_summary = summary
        self.build_note = note
        self.saved.build_dir = str(summary.output_dir) if summary is not None else None
        self.saved.copied_sha256 = None
        self.copy_ok_session = False
        self.saved.test_checked = []
        if save:
            self.save()

    def set_card_prepared(self, how: str | None) -> None:
        if how not in (None, "prepared", "elsewhere"):
            raise ValueError(how)
        self.saved.card_prepared = how
        self.save()

    def set_copied(self, sha256: str) -> None:
        if self.build_summary is None or sha256 != self.build_summary.sha256:
            raise ValueError("the copied file is not the current build")
        self.saved.copied_sha256 = sha256
        self.copy_ok_session = True
        self.save()

    def set_test_item(self, index: int, checked: bool) -> None:
        items = set(self.saved.test_checked)
        (items.add if checked else items.discard)(index)
        self.saved.test_checked = sorted(i for i in items if 0 <= i < len(TEST_CHECKLIST))
        self.save()

    # -- statuses ------------------------------------------------------------------------

    def _build_matches(self, summary) -> bool:
        options = {k: v for k, v in self.a.options.items()} if self.a.profile == "xpan-crop" else {}
        return summary.profile == self.a.profile and summary.options == options and summary.sha256_matches_build_json

    def status(self, step_id: str) -> StepStatus:
        a, s = self.a, self.saved
        if step_id == CAMERA:
            return StepStatus(DONE, "Original EOS R5 on firmware 2.2.1, confirmed by you.") if s.camera_confirmed \
                else StepStatus(NEEDS_ACTION, "Check your camera's model and firmware, then confirm.")
        if step_id == ROM:
            check = a.rom_check()
            if a.rom_path is None:
                return StepStatus(NEEDS_ACTION, "Choose your 64 MiB ROM dump, or use the guided ROM-dump card below.")
            if check.status == OK:
                return StepStatus(DONE, check.message)
            if check.status == FAILED:
                return StepStatus(NEEDS_ACTION, "The ROM was refused: " + check.message)
            return StepStatus(NEEDS_ACTION, "Checking the ROM...")
        if step_id == BOOTDISK:
            if s.bootdisk == "verified":
                return StepStatus(DONE, "BootDisk is enabled (verified by ROM readback: BootInfo +0x04 = 0xFFFFFFFF).")
            if s.bootdisk == "confirmed":
                return StepStatus(DONE, "BootDisk is enabled, confirmed by you (not verified by the app).")
            if s.bootdisk == "disabled":
                return StepStatus(NEEDS_ACTION, "BootDisk is disabled (verified by ROM readback: BootInfo +0x04 = 0x00000000). Enable it to run autoexec.bin.")
            return StepStatus(NEEDS_ACTION, "Enable and verify BootDisk with a Canon Basic card, verify from a post-enable ROM dump, or confirm it is already enabled.")
        if step_id == FEATURES:
            missing = []
            for view in a.input_views():
                if view.policy == "required" and view.path is None:
                    missing.append(f"Pick the {view.label}.")
                elif view.policy == "required" and view.check.status != OK:
                    missing.append(f"The {view.label} was refused: {view.check.message}" if view.check.status == FAILED
                                   else f"The {view.label} is being checked.")
            if missing:
                return StepStatus(NEEDS_ACTION, " ".join(missing))
            return StepStatus(DONE, f"{a.manifest.profiles[a.profile].label}.") if s.features_confirmed \
                else StepStatus(NEEDS_ACTION, "Choose the features, then press 'Use these features'.")
        if step_id == TOOLCHAIN:
            check = a.toolchain_check()
            if check.status == OK:
                return StepStatus(DONE, check.message)
            if a.toolchain_path is None:
                return StepStatus(NEEDS_ACTION, "Install the GNU Arm 15.2.1 toolchain and choose its bin folder.")
            return StepStatus(NEEDS_ACTION, "The toolchain was refused: " + check.message if check.status == FAILED
                              else "Checking the toolchain...")
        if step_id == BUILD:
            needed = [STEP_BY_ID[x].title for x in (ROM, FEATURES, TOOLCHAIN) if self.status(x).status != DONE]
            if self.build_summary is not None and self._build_matches(self.build_summary):
                return StepStatus(DONE, f"autoexec.bin built and verified (SHA-256 {self.build_summary.sha256[:12]}...).")
            if needed:
                return StepStatus(BLOCKED, "Finish first: " + ", ".join(needed) + ".")
            return StepStatus(NEEDS_ACTION, self.build_note or "Press Build.")
        if step_id == BOOTCARD:
            if s.card_prepared == "prepared":
                return StepStatus(DONE, "The card has its boot markers.")
            if s.card_prepared == "elsewhere":
                return StepStatus(SKIPPED, "Skipped: you said your card is already prepared.")
            return StepStatus(NEEDS_ACTION, "Prepare the card, or say it is already prepared." if self.prep_supported
                              else "Preparing cards is offered on macOS only; say your card is already prepared.")
        if step_id == COPY:
            if self.status(BUILD).status != DONE:
                return StepStatus(BLOCKED, "Build autoexec.bin first.")
            if s.copied_sha256 and self.build_summary and s.copied_sha256 == self.build_summary.sha256:
                return StepStatus(DONE, "This build is on the card and was read back.")
            return StepStatus(NEEDS_ACTION, "Copy autoexec.bin to the prepared card.")
        if step_id == TEST:
            if self.status(COPY).status != DONE:
                return StepStatus(BLOCKED, "Copy autoexec.bin to the card first.")
            if len(s.test_checked) == len(TEST_CHECKLIST):
                return StepStatus(DONE, "Checklist complete. Please send a test report.")
            return StepStatus(NEEDS_ACTION, f"{len(s.test_checked)} of {len(TEST_CHECKLIST)} items checked.")
        raise KeyError(step_id)

    def statuses(self) -> dict[str, StepStatus]:
        return {step.id: self.status(step.id) for step in STEPS}

    # -- paths and navigation ----------------------------------------------------------

    def one_time_done(self) -> list[str]:
        """One-time steps that are not done (empty means the short path is available)."""
        return [STEP_BY_ID[x].title for x in ONE_TIME if self.status(x).status != DONE]

    def path(self) -> list[str]:
        if self.saved.mode == "update":
            steps = [FEATURES] + ([TOOLCHAIN] if self.status(TOOLCHAIN).status != DONE else []) + [BUILD, COPY]
            return steps
        return [s.id for s in STEPS]

    def start_update(self) -> list[str]:
        """Switch to the short path; returns the reasons it isn't available (empty on success)."""
        missing = self.one_time_done()
        if missing:
            return [f"First finish the one-time steps: {', '.join(missing)}."]
        self.saved.mode = "update"
        self.saved.features_confirmed = False
        self.saved.current = FEATURES
        self.save()
        return []

    def start_full(self) -> None:
        self.saved.mode = "full"
        self.save()

    @property
    def current(self) -> str:
        if self.saved.current not in self.path():
            self.saved.current = self.path()[0]
        return self.saved.current

    def can_leave(self, step_id: str | None = None) -> bool:
        """Next is allowed once the step is done or skipped."""
        return self.status(step_id or self.current).status in (DONE, SKIPPED)

    def next_blockers(self) -> list[str]:
        st = self.status(self.current)
        if st.status in (DONE, SKIPPED):
            return [] if self.current != self.path()[-1] else ["This is the last step."]
        return [st.message]

    def next(self) -> bool:
        if self.next_blockers():
            return False
        path = self.path()
        self.saved.current = path[path.index(self.current) + 1]
        self.save()
        return True

    def back(self) -> bool:
        path = self.path()
        i = path.index(self.current)
        if i == 0:
            return False
        self.saved.current = path[i - 1]
        self.save()
        return True

    def can_goto(self, step_id: str) -> bool:
        """The sidebar may jump to any step on the path that is not blocked."""
        return step_id in self.path() and self.status(step_id).status != BLOCKED

    def goto(self, step_id: str) -> bool:
        if not self.can_goto(step_id):
            return False
        self.saved.current = step_id
        self.save()
        return True

    def report_url(self) -> str:
        return REPORT_URLS.get(self.a.profile, REPORT_URLS["xpan-crop"])


# -- the words on each step: what and why, what you do, and technical details ----------


@dataclass(frozen=True)
class Guide:
    why: str
    todo: str
    details: str


GUIDE = {
    CAMERA: Guide(
        "This mod only works on an original Canon EOS R5 with firmware 2.2.1. Other models (R5 C, R5 Mark II, "
        "R6 family) and other firmware versions are not supported, and using it there can damage your camera.",
        "On the camera, press MENU and open the yellow Setup (wrench) tab. Find 'Firmware' (on the last Setup "
        "page). It must say 'Firmware Ver. 2.2.1'. The camera's top or front must say 'EOS R5' with no 'C' or "
        "'Mark II'. Then tick the box below. Never upgrade or downgrade the firmware to make it fit.",
        "Everything the builder links against is checked against an exact R5 2.2.1 ROM (strict admission, no "
        "--force). The camera check here is your confirmation; the ROM check in step 2 verifies the firmware "
        "itself."),
    ROM: Guide(
        "The build uses small parts of Canon's firmware, read from your own camera's ROM. They can't be shared, "
        "so everyone makes their own copy once. Dumping the ROM only reads; it changes nothing in the camera.",
        "If you already have your 64 MiB ROM dump (often gang100.bin), choose it below. Otherwise, use a "
        "camera-formatted FAT32 SDHC card (up to 32 GB) with the guided ROM-dump controls below: prepare the "
        "card with the Canon Basic SCRIPT marker and script files, run it once in the camera (no CFexpress, USB "
        "or HDMI; power on, press Playback, press SET once, wait until the card light stops plus 60 s, then "
        "power off), and import the returned dump.",
        "The dump must be exactly 67,108,864 bytes and pass the builder's strict admission (check_rom: checksum, "
        "startup bytes and, for XPan, fingerprinted pages), plus the four BootInfo/IsScriptMode fingerprints "
        "(0xE0B38CD4, 0xE00068C0, 0xE0006FC4, 0xE06F73DC). Canon Basic on R5 2.2.1 uses SCRIPT (6 B) at FAT32 "
        "VBR byte 496 (0x1F0), 17-byte script.req ('for DC_scriptdisk'), and extend.m calling "
        "SaveAllRomImageToFile() (requires >= 300 MiB free and no AUTOEXEC.BIN or native boot markers). "
        "SDXC/exFAT cards (>= 0x4000000 sectors) check LBA-1 for SCRIPT and are untested, so guided Canon "
        "Basic refuses them. Keep your ROM dump private; gang2NN.bin is not needed."),
    BOOTDISK: Guide(
        "A stock R5 ignores programs on the card. The BootDisk setting tells it to look for one at power-on. "
        "It is stored inside the camera, it is PERMANENT across card formats and card removal, and there is no "
        "official Canon uninstaller.",
        "Tick the risk acknowledgement below. Then either: (1) prepare a Canon Basic Enable-BootDisk card (FAT32 "
        "SDHC up to 32 GB, no native boot markers, no AUTOEXEC.BIN), run it once in the camera (full battery, no "
        "CFexpress/USB/HDMI, press Playback then SET once, wait for the card light to stop plus 60 s without "
        "powering off or opening doors), and verify the returned card so the app checks BootInfo +0x04 = "
        "0xFFFFFFFF and removes extend.m; (2) verify from an existing post-enable ROM dump; or (3) confirm below "
        "if BootDisk was already enabled earlier.",
        "EnableBootDisk() (0xE0B38D62) writes 0xFFFFFFFF to BootInfo +0x04 (file offset 0x3FF8004). The guided "
        "script calls EnableBootDisk() followed by SaveAllRomImageToFile() so the returned gang1NN.bin lets the "
        "app verify BootInfo +0x04, check loader invariants (+0x00 == 0, +0x0C/+0x10..+0x1F/+0x24 == 0xFFFFFFFF), "
        "compare the first 48 bytes against an earlier dump, and immediately remove extend.m and script.req so a "
        "stray SET cannot repeat a flash write. DisableBootDisk() (0xE0B38D74, writing 0x00000000) is the stock "
        "inverse in the R5 2.2.1 ROM, but it has NOT been tested on hardware yet. The app itself never writes "
        "camera flash or NVRAM: you run the card in the camera."),
    FEATURES: Guide(
        "The mod comes in two versions. XPan adds a 2.71:1 panoramic crop; Custom sounds plays your own startup "
        "and shutter sounds. They can't be combined.",
        "Choose one, and any options. For Custom sounds, choose your two WAV files. Then press 'Use these features'.",
        "The choices come from config/features.json, the same manifest the command-line builder uses, so the "
        "window can't offer a combination the builder would refuse. Every public build is hardware-untested; "
        "the status lines say what was tested on private lab builds."),
    TOOLCHAIN: Guide(
        "A free compiler (GNU Arm 15.2.1) turns the source code into autoexec.bin on your computer.",
        "The app looks for it automatically. If it isn't found, download xPack GNU Arm Embedded GCC 15.2.1-1.1 "
        "for your computer, unpack it, and choose its 'bin' folder.",
        "Each candidate goes through the builder's toolchain admission (target arm-none-eabi, GCC 15.2.1). "
        "The app never downloads anything itself."),
    BUILD: Guide(
        "Now the app builds the mod from your ROM and the features you chose, all on this computer.",
        "Press Build and wait (usually under a minute). The result shows the file and its checksum.",
        "The build runs python3 tools/build.py with the exact arguments shown in the log, in a separate process. "
        "The result is re-hashed and compared with build.json, and BUILD-CONTENTS.txt lists what is inside."),
    BOOTCARD: Guide(
        "The camera only starts autoexec.bin from a card that carries two short boot markers. Formatting a card "
        "removes them, so this is needed once per format.",
        "Format the card IN THE CAMERA first (this erases it; copy your photos off before). Then choose it below "
        "and press Prepare card. If your card is already prepared, say so instead.",
        "Only the boot-marker sectors are written (FAT32: 1 sector; exFAT: 4), after an automatic, verified "
        "backup and one confirmation; Undo restores the backup. Raw access goes through macOS authopen; nothing "
        "runs as root. Verified on the owner's Mac with a 128 GB exFAT card; FAT32 is untested."),
    COPY: Guide(
        "The camera loads the mod from a file called AUTOEXEC.BIN at the top of the card.",
        "Choose the card's top folder (for example /Volumes/EOS_DIGITAL) and press Copy. Then eject the card.",
        "Any existing autoexec.bin is backed up to the build folder first. The copy goes to a temporary file, is "
        "read back, renamed, and read back again against build.json."),
    TEST: Guide(
        "A short, careful first test tells you whether everything works, and helps others if you report it.",
        "Work through the checklist with the camera. If the camera freezes or shows an error, remove the battery, "
        "then the card, and power on without the card. Then report what you saw, including what didn't work.",
        "Never upload your ROM, photos or card images in a report. Quote the source_manifest_sha256 from "
        "build.json so others can tell which build you tested."),
}
