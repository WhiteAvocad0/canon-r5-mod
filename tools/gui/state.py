"""Widget-free state of R5 Mod Assistant.

Everything the window shows or enables is derived here from the feature manifest
and the latest check results, so it can be tested without a display. Check results
are keyed by what they checked; a result for an older selection is ignored and the
input shows as needing a fresh check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1]
ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from messages import joined, message, phrase

import features
from gui import bundle

UNCHECKED, CHECKING, OK, FAILED = "unchecked", "checking", "ok", "failed"


@dataclass
class Check:
    key: tuple | None = None
    status: str = UNCHECKED
    message: str = ""
    facts: dict | None = None


@dataclass(frozen=True)
class ChoiceView:
    value: str
    label: str
    status_label: str | None
    status_text: str | None


@dataclass(frozen=True)
class OptionView:
    id: str
    label: str
    description: str
    enabled: bool
    reason: str | None
    value: str
    choices: tuple[ChoiceView, ...]


@dataclass(frozen=True)
class InputView:
    id: str
    label: str
    policy: str
    enabled: bool
    reason: str | None
    path: Path | None
    check: Check


@dataclass(frozen=True)
class ProfileView:
    id: str
    label: str
    description: str
    selected: bool
    status_label: str
    status_text: str


def default_output_dir(profile: str, options: dict[str, str], manifest: features.FeatureManifest,
                       now: datetime | None = None) -> Path:
    """A fresh folder named after the selection, under the ignored build/ directory (or ~/R5-builds in the app)."""
    now = datetime.now() if now is None else now
    parts = [profile] + [
        v for oid, v in options.items() if v != manifest.options[oid].default
    ]
    return bundle.builds_root(ROOT) / ("gui-" + "-".join(parts) + now.strftime("-%Y%m%d-%H%M%S"))


class AssistantState:
    def __init__(self, manifest: features.FeatureManifest | None = None):
        self.manifest = features.load() if manifest is None else manifest
        self.profile = self.manifest.default_profile
        self.options = {oid: o.default for oid, o in self.manifest.options.items()}
        self.rom_path: Path | None = None
        self.rom = Check()
        self.input_paths: dict[str, Path | None] = {i: None for i in self.manifest.inputs}
        self.input_checks: dict[str, Check] = {i: Check() for i in self.manifest.inputs}
        self.toolchain_path: Path | None = None
        self.toolchain = Check()
        self.output_dir: Path | None = None
        self.building = False

    # -- selection ----------------------------------------------------------

    def set_profile(self, profile: str) -> None:
        if profile not in self.manifest.profiles:
            raise ValueError(f"unknown profile {profile!r}")
        self.profile = profile
        for oid, option in self.manifest.options.items():
            if profile not in option.profiles:
                self.options[oid] = option.default

    def set_option(self, option_id: str, value: str) -> None:
        option = self.manifest.options[option_id]
        if value not in option.values:
            raise ValueError(f"unknown {option.flag} value {value!r}")
        enabled, reason = self.manifest.option_availability(self.profile, option_id)
        if not enabled and value != option.default:
            raise ValueError(reason)
        self.options[option_id] = value

    def input_policy(self, input_id: str) -> str:
        return dict(self.manifest.profiles[self.profile].inputs)[input_id]

    def set_rom(self, path: Path | None) -> None:
        self.rom_path = None if path is None else Path(path)

    def set_input(self, input_id: str, path: Path | None) -> None:
        self.input_paths[input_id] = None if path is None else Path(path)

    def set_toolchain(self, path: Path | None) -> None:
        self.toolchain_path = None if path is None else Path(path)

    def offer_detected_toolchain(self, path: Path, message: str, facts: dict | None, *, explicit: bool) -> bool:
        """Use an auto-detected, already-admitted toolchain unless the person chose one.

        Start-up detection never replaces a folder the person picked; pressing
        "Detect again" (explicit) may. Returns whether the detection was applied."""
        if self.toolchain_path is not None and not explicit:
            return False
        self.set_toolchain(path)
        self.finish("toolchain", self.toolchain_key(), True, message, facts)
        return True

    def set_output_dir(self, path: Path | None) -> None:
        self.output_dir = None if path is None else Path(path)

    # -- check bookkeeping ----------------------------------------------------

    def rom_key(self) -> tuple | None:
        """ROM admission depends on the profile and the record cues (extra fingerprinted pages).

        The key is (rom path, profile, ((option id, value or None if unavailable), ...))."""
        if self.rom_path is None:
            return None
        options = tuple(
            (oid, self.options[oid] if self.manifest.option_availability(self.profile, oid)[0] else None)
            for oid in self.manifest.options
        )
        return (str(self.rom_path), self.profile, options)

    def input_key(self, input_id: str) -> tuple | None:
        path = self.input_paths[input_id]
        return None if path is None else (str(path),)

    def toolchain_key(self) -> tuple | None:
        return None if self.toolchain_path is None else (str(self.toolchain_path),)

    @staticmethod
    def _current(check: Check, key: tuple | None) -> Check:
        if key is None:
            return Check()
        if check.key != key:
            return Check(key=key)
        return check

    def rom_check(self) -> Check:
        return self._current(self.rom, self.rom_key())

    def input_check(self, input_id: str) -> Check:
        return self._current(self.input_checks[input_id], self.input_key(input_id))

    def toolchain_check(self) -> Check:
        return self._current(self.toolchain, self.toolchain_key())

    def _key(self, what: str, input_id: str | None) -> tuple | None:
        if what == "rom":
            return self.rom_key()
        if what == "toolchain":
            return self.toolchain_key()
        return self.input_key(input_id)

    def begin(self, what: str, input_id: str | None = None) -> tuple | None:
        """Mark a check ("rom", "toolchain" or "input") as running; returns the key its result must carry."""
        key = self._key(what, input_id)
        if key is not None:
            self._store(what, input_id, Check(key, CHECKING, "Checking..."))
        return key

    def finish(self, what: str, key: tuple, ok: bool, message: str, facts: dict | None = None,
               input_id: str | None = None) -> bool:
        """Record a result if it still matches the current selection. Returns whether it applied."""
        if key != self._key(what, input_id):
            return False
        self._store(what, input_id, Check(key, OK if ok else FAILED, message, facts))
        return True

    def _store(self, what: str, input_id: str | None, check: Check) -> None:
        if what == "rom":
            self.rom = check
        elif what == "toolchain":
            self.toolchain = check
        else:
            self.input_checks[input_id] = check

    def needs_check(self) -> list[tuple[str, str | None]]:
        """Inputs whose current selection has no result or a stale one."""
        todo: list[tuple[str, str | None]] = []
        if self.rom_check().status == UNCHECKED and self.rom_key() is not None:
            todo.append(("rom", None))
        for input_id in self.manifest.inputs:
            if self.input_policy(input_id) == "required" and self.input_key(input_id) is not None \
                    and self.input_check(input_id).status == UNCHECKED:
                todo.append(("input", input_id))
        if self.toolchain_key() is not None and self.toolchain_check().status == UNCHECKED:
            todo.append(("toolchain", None))
        return todo

    # -- views ----------------------------------------------------------------

    def profile_views(self) -> list[ProfileView]:
        views = []
        for p in self.manifest.profiles.values():
            level = self.manifest.status_levels[p.status]
            views.append(ProfileView(p.id, p.label, p.description, p.id == self.profile, level.label,
                                     message(
                                         "{text} {status_detail}",
                                         text=phrase(level.text), status_detail=phrase(p.status_detail))))
        return views

    def option_views(self) -> list[OptionView]:
        views = []
        for oid, option in self.manifest.options.items():
            enabled, reason = self.manifest.option_availability(self.profile, oid)
            choices = []
            for c in option.choices:
                level = self.manifest.status_levels.get(c.status) if c.status else None
                text = None if level is None else joined(" ", (t for t in (level.text, c.status_detail) if t))
                choices.append(ChoiceView(c.value, c.label, None if level is None else level.label, text))
            views.append(OptionView(oid, option.label, option.description, enabled, reason,
                                    self.options[oid], tuple(choices)))
        return views

    def input_views(self) -> list[InputView]:
        views = []
        for input_id, spec in self.manifest.inputs.items():
            policy = self.input_policy(input_id)
            reason = None if policy == "required" else (
                message('Not used by {profile}: this profile adds no custom sounds.', profile=self.profile)
            )
            views.append(InputView(input_id, spec.label, policy, policy == "required", reason,
                                   self.input_paths[input_id], self.input_check(input_id)))
        return views

    # -- build ----------------------------------------------------------------

    def selection_violations(self) -> list[features.Violation]:
        """Manifest rules the CLI arguments this state produces would break (none by construction)."""
        return self.manifest.selection_violations(
            self.profile,
            inputs={i: self.input_policy(i) == "required" and self.input_paths[i] is not None for i in self.manifest.inputs},
            options={oid: (v if v != self.manifest.options[oid].default else None) for oid, v in self.options.items()},
        )

    def blockers(self) -> list[str]:
        """Plain reasons the Build button is disabled; empty means ready."""
        reasons: list[str] = []
        if self.building:
            reasons.append("A build is already running.")
        rom = self.rom_check()
        if self.rom_path is None:
            reasons.append("Pick your ROM dump.")
        elif rom.status != OK:
            reasons.append({CHECKING: "The ROM is being checked.", FAILED: message(
                "The ROM was refused: {reason}",
                reason=phrase(rom.message))}
                           .get(rom.status, "The ROM has not been checked for this selection."))
        for view in self.input_views():
            if view.policy != "required":
                continue
            if view.path is None:
                reasons.append(message('Pick the {label}.', label=phrase(view.label)))
            elif view.check.status != OK:
                reasons.append(message(
                    "The {label} is not accepted yet.",
                    label=phrase(view.label)) if view.check.status != FAILED
                               else message(
                                   "The {label} was refused: {message}",
                                   label=phrase(view.label), message=phrase(view.check.message)))
        tc = self.toolchain_check()
        if self.toolchain_path is None:
            reasons.append("Choose the GNU Arm 15.2.1 toolchain's bin folder.")
        elif tc.status != OK:
            reasons.append("The toolchain is not accepted yet." if tc.status != FAILED
                           else message("The toolchain was refused: {reason}", reason=phrase(tc.message)))
        if self.output_dir is None:
            reasons.append("Choose an output folder.")
        else:
            problem = output_dir_problem(self.output_dir)
            if problem:
                reasons.append(problem)
        # Missing inputs are already listed above; any other violation would be a state bug.
        reasons.extend(self.manifest.describe_violation(v) for v in self.selection_violations()
                       if v.kind != features.MISSING_INPUT)
        return reasons

    def build_argv(self) -> list[str]:
        """tools/build.py arguments for the current selection. Only valid when blockers() is empty."""
        if self.blockers():
            raise ValueError("selection is not ready to build")
        argv = ["--profile", self.profile, "--rom", str(self.rom_path)]
        for input_id, spec in self.manifest.inputs.items():
            if self.input_policy(input_id) == "required":
                argv += [spec.flag, str(self.input_paths[input_id])]
        argv += ["--toolchain", str(self.toolchain_path), "--output", str(self.output_dir)]
        for oid, option in self.manifest.options.items():
            if self.options[oid] != option.default:
                argv += [option.flag, self.options[oid]]
        return argv


def output_dir_problem(path: Path) -> str | None:
    """Why the builder would refuse this output folder, checked without creating it."""
    import build

    try:
        build.check_output_dir(Path(path))
    except ValueError as exc:
        return message('Output folder refused: {exc}', exc=exc)
    if Path(path).exists():
        return "The output folder already exists; the builder needs a new folder."
    return None
