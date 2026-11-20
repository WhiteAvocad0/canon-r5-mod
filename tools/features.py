"""Owner of the feature manifest (config/features.json).

The manifest is the single source of truth for build profiles, per-profile input
policies (WAVs required or forbidden), opt-in options, their dependencies on a
profile, their choices and their validation status. tools/build.py derives its
CLI choices and selection rules from it, and the GUI build assistant derives its
checklist from it, so the GUI cannot offer a combination the builder refuses.

This module parses and answers questions only. It never reads ROMs, WAVs or
toolchains, and it never formats user-facing CLI errors (build.py owns those).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
FEATURES_PATH = ROOT / "config" / "features.json"
SCHEMA = "canon-r5-mod.features.v1"
INPUT_POLICIES = ("required", "forbidden")

# Violation kinds, in the order selection_violations reports them.
OPTION_REQUIRES_PROFILE = "option_requires_profile"
UNKNOWN_PROFILE = "unknown_profile"
FORBIDDEN_INPUT = "forbidden_input"
MISSING_INPUT = "missing_input"


class FeatureManifestError(ValueError):
    pass


@dataclass(frozen=True)
class StatusLevel:
    id: str
    label: str
    text: str


@dataclass(frozen=True)
class InputSpec:
    id: str
    flag: str
    label: str
    cli_help: str


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    status: str | None
    status_detail: str | None


@dataclass(frozen=True)
class Option:
    id: str
    flag: str
    label: str
    description: str
    profiles: tuple[str, ...]
    unavailable_reason: str
    default: str
    cli_help: str
    choices: tuple[Choice, ...]

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(c.value for c in self.choices)

    def choice(self, value: str) -> Choice:
        for c in self.choices:
            if c.value == value:
                return c
        raise KeyError(value)


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    description: str
    status: str
    status_detail: str
    inputs: tuple[tuple[str, str], ...]  # (input id, policy) in manifest input order

    def inputs_with(self, policy: str) -> tuple[str, ...]:
        return tuple(i for i, p in self.inputs if p == policy)


@dataclass(frozen=True)
class Violation:
    kind: str
    profile: str
    subjects: tuple[str, ...]  # option ids or input ids, in manifest order


def _str(raw: Mapping, key: str, where: str) -> str:
    value = raw.get(key)
    if type(value) is not str or not value:
        raise FeatureManifestError(f"{where}.{key} must be a non-empty string")
    return value


def _opt_str(raw: Mapping, key: str, where: str) -> str | None:
    value = raw.get(key)
    if value is not None and (type(value) is not str or not value):
        raise FeatureManifestError(f"{where}.{key} must be null or a non-empty string")
    return value


class FeatureManifest:
    """Parsed, cross-checked feature manifest."""

    def __init__(self, raw: object):
        if type(raw) is not dict or raw.get("schema") != SCHEMA:
            raise FeatureManifestError(f"feature manifest schema must be {SCHEMA}")
        self.target = _str(raw, "target", "features")
        self.public_build_note = _str(raw, "public_build_note", "features")
        self.profile_cli_help = _str(raw, "profile_cli_help", "features")
        self.profile_exclusivity = _str(raw, "profile_exclusivity", "features")

        levels = raw.get("status_levels")
        if type(levels) is not dict or not levels:
            raise FeatureManifestError("features.status_levels must be a non-empty object")
        self.status_levels: dict[str, StatusLevel] = {}
        for k, v in levels.items():
            if type(v) is not dict:
                raise FeatureManifestError(f"status_levels.{k} must be an object")
            self.status_levels[k] = StatusLevel(k, _str(v, "label", f"status_levels.{k}"), _str(v, "text", f"status_levels.{k}"))

        inputs = raw.get("inputs")
        if type(inputs) is not dict or not inputs:
            raise FeatureManifestError("features.inputs must be a non-empty object")
        self.inputs: dict[str, InputSpec] = {}
        for k, v in inputs.items():
            if type(v) is not dict:
                raise FeatureManifestError(f"inputs.{k} must be an object")
            flag = _str(v, "flag", f"inputs.{k}")
            if not flag.startswith("--") or any(spec.flag == flag for spec in self.inputs.values()):
                raise FeatureManifestError(f"inputs.{k}.flag must be unique and start with --")
            self.inputs[k] = InputSpec(k, flag, _str(v, "label", f"inputs.{k}"), _str(v, "cli_help", f"inputs.{k}"))

        profiles = raw.get("profiles")
        if type(profiles) is not list or not profiles:
            raise FeatureManifestError("features.profiles must be a non-empty list")
        self.profiles: dict[str, Profile] = {}
        for index, p in enumerate(profiles):
            where = f"profiles[{index}]"
            if type(p) is not dict:
                raise FeatureManifestError(f"{where} must be an object")
            pid = _str(p, "id", where)
            if pid in self.profiles:
                raise FeatureManifestError(f"duplicate profile {pid!r}")
            status = self._status_ref(_str(p, "status", where), where)
            policies = p.get("inputs")
            if type(policies) is not dict or set(policies) != set(self.inputs):
                raise FeatureManifestError(f"{where}.inputs must give a policy for every manifest input")
            if any(v not in INPUT_POLICIES for v in policies.values()):
                raise FeatureManifestError(f"{where}.inputs policies must be one of {INPUT_POLICIES}")
            self.profiles[pid] = Profile(
                pid, _str(p, "label", where), _str(p, "description", where), status,
                _str(p, "status_detail", where), tuple((i, policies[i]) for i in self.inputs),
            )
        self.default_profile = _str(raw, "default_profile", "features")
        if self.default_profile not in self.profiles:
            raise FeatureManifestError("features.default_profile must name a profile")

        options = raw.get("options")
        if type(options) is not list:
            raise FeatureManifestError("features.options must be a list")
        self.options: dict[str, Option] = {}
        flags = {spec.flag for spec in self.inputs.values()}
        for index, o in enumerate(options):
            where = f"options[{index}]"
            if type(o) is not dict:
                raise FeatureManifestError(f"{where} must be an object")
            oid = _str(o, "id", where)
            flag = _str(o, "flag", where)
            if oid in self.options or flag in flags or not flag.startswith("--"):
                raise FeatureManifestError(f"{where} id/flag must be unique and flag must start with --")
            flags.add(flag)
            allowed = o.get("profiles")
            if type(allowed) is not list or not allowed or any(a not in self.profiles for a in allowed):
                raise FeatureManifestError(f"{where}.profiles must list known profiles")
            if len(set(allowed)) != len(allowed):
                raise FeatureManifestError(f"{where}.profiles must not repeat")
            raw_choices = o.get("choices")
            if type(raw_choices) is not list or not raw_choices:
                raise FeatureManifestError(f"{where}.choices must be a non-empty list")
            choices = []
            for ci, c in enumerate(raw_choices):
                cw = f"{where}.choices[{ci}]"
                if type(c) is not dict:
                    raise FeatureManifestError(f"{cw} must be an object")
                cstatus = _opt_str(c, "status", cw)
                if cstatus is not None:
                    self._status_ref(cstatus, cw)
                choices.append(Choice(_str(c, "value", cw), _str(c, "label", cw), cstatus, _opt_str(c, "status_detail", cw)))
            values = [c.value for c in choices]
            if len(set(values)) != len(values):
                raise FeatureManifestError(f"{where}.choices values must be unique")
            default = _str(o, "default", where)
            if default not in values:
                raise FeatureManifestError(f"{where}.default must be one of its choices")
            self.options[oid] = Option(
                oid, flag, _str(o, "label", where), _str(o, "description", where), tuple(allowed),
                _str(o, "unavailable_reason", where), default, _str(o, "cli_help", where), tuple(choices),
            )

    def _status_ref(self, status: str, where: str) -> str:
        if status not in self.status_levels:
            raise FeatureManifestError(f"{where}.status {status!r} is not a declared status level")
        return status

    # -- questions --------------------------------------------------------

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(self.profiles)

    def options_for(self, profile_id: str) -> tuple[Option, ...]:
        return tuple(o for o in self.options.values() if profile_id in o.profiles)

    def option_availability(self, profile_id: str, option_id: str) -> tuple[bool, str | None]:
        """Whether an option can be selected under a profile, with the one-line reason if not."""
        option = self.options[option_id]
        if profile_id in option.profiles:
            return True, None
        return False, option.unavailable_reason

    def selection_violations(
        self,
        profile_id: str,
        *,
        inputs: Mapping[str, bool],
        options: Mapping[str, str | None],
    ) -> list[Violation]:
        """Every rule a selection breaks, in a fixed order.

        inputs maps input id -> whether the caller supplied it. options maps option
        id -> the selected value, or None when the caller did not select it. Choice
        values are not checked here: the CLI restricts them with argparse choices
        from this manifest and the option's build owner admits them.
        """
        unknown = (set(inputs) - set(self.inputs)) | (set(options) - set(self.options))
        if unknown:
            raise FeatureManifestError("unknown selection keys: " + ", ".join(sorted(unknown)))
        found: list[Violation] = []
        for option in self.options.values():
            if options.get(option.id) is not None and profile_id not in option.profiles:
                found.append(Violation(OPTION_REQUIRES_PROFILE, profile_id, (option.id,)))
        profile = self.profiles.get(profile_id)
        if profile is None:
            found.append(Violation(UNKNOWN_PROFILE, profile_id, ()))
            return found
        forbidden = tuple(i for i in profile.inputs_with("forbidden") if inputs.get(i))
        if forbidden:
            found.append(Violation(FORBIDDEN_INPUT, profile_id, forbidden))
        missing = tuple(i for i in profile.inputs_with("required") if not inputs.get(i))
        if missing:
            found.append(Violation(MISSING_INPUT, profile_id, missing))
        return found

    def describe_violation(self, v: Violation) -> str:
        """Plain-language description for people (the GUI). The CLI keeps its own wording."""
        if v.kind == OPTION_REQUIRES_PROFILE:
            return self.options[v.subjects[0]].unavailable_reason
        if v.kind == UNKNOWN_PROFILE:
            return f"Unknown profile {v.profile!r}."
        labels = ", ".join(self.inputs[i].label for i in v.subjects)
        if v.kind == FORBIDDEN_INPUT:
            return f"The {v.profile} profile does not use: {labels}."
        return f"The {v.profile} profile needs: {labels}."


def load(path: Path = FEATURES_PATH) -> FeatureManifest:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureManifestError(f"cannot read feature manifest {path}: {exc}") from exc
    return FeatureManifest(raw)
