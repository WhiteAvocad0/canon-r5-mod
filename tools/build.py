"""Host-only offline builder for Canon EOS R5 (2.2.1) shutter-feedback sound mod.

Profiles, WAV input rules and opt-in options come from config/features.json (tools/features.py).
Reads selected regular input files and writes the selected output directory only.
Never contacts a camera, modifies raw card sectors, mutates source files, or installs tools.
Requires explicit 64 MiB R5 2.2.1 ROM dump, 48 kHz mono 16-bit PCM WAVE files, and GCC 15.2.1 toolchain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
SRC_DIR = ROOT / "src" / "r5_221"
XPAN_TOOLS_DIR = ROOT / "src" / "experimental" / "xpan" / "tools"
CONFIG_DIR = ROOT / "config"

for _path in (TOOLS_DIR, XPAN_TOOLS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from audio_log import SCHEMA as AUDIO_LOG_SCHEMA, build_template
from audio_profile import (
    DESCRIPTOR_BYTES,
    DESCRIPTOR_BYTES_OFFSET,
    DESCRIPTOR_PTR_OFFSET,
    admit_pcm,
    descriptor,
)
from contracts import (
    MANIFEST as M,
    TARGET_CONTRACT,
    XPAN_TARGET_CONTRACT,
    admit_rom_with_provenance,
    deployment,
    patch_startup,
    validate_layout,
    validate_target_contract_consistency,
    validate_write_buffer,
)
import features
from image_format import finalize_image, inspect_image
from input_files import hash_regular_file, read_regular_file
import record_indicator as ri
import screenshot as sc
from toolchain import POLICY_PATH, admit_toolchain, controlled_environment

PROVENANCE_FILENAMES = ("conversion.json", "pcm-facts.json")

FEATURES = features.load()
RECORD_INDICATOR = FEATURES.options["record_indicator"]
RECORD_STOP_INDICATOR = FEATURES.options["record_stop_indicator"]
SCREENSHOT = FEATURES.options["screenshot"]
# Option id (also the build_image parameter it fills) -> its owner module and choice catalog.
OPTION_CATALOGS = {
    "record_indicator": (ri, "CHOICES"),
    "record_stop_indicator": (ri, "STOP_CHOICES"),
    "screenshot": (sc, "CHOICES"),
}


def check_feature_catalog(manifest: features.FeatureManifest = FEATURES) -> None:
    """Fail closed when the feature manifest drifts from the owners it describes."""
    if manifest.default_profile != "shutter-feedback" or set(manifest.profile_ids) != {"shutter-feedback", "xpan-crop"}:
        raise ValueError("feature manifest profiles disagree with the profiles build_image implements")
    if set(manifest.inputs) != {"startup_wav", "shutter_wav"} or set(manifest.options) != set(OPTION_CATALOGS):
        raise ValueError("feature manifest inputs/options disagree with build_image parameters")
    for spec in (*manifest.inputs.values(), *manifest.options.values()):
        if spec.flag != "--" + spec.id.replace("_", "-"):
            raise ValueError(f"feature manifest flag {spec.flag} does not name parameter {spec.id}")
    for oid, (owner, catalog) in OPTION_CATALOGS.items():
        option = manifest.options[oid]
        if option.values != getattr(owner, catalog) or option.default != owner.OFF:
            raise ValueError(f"feature manifest {oid} choices disagree with {owner.__name__}.{catalog}")


check_feature_catalog()


def _flags(input_ids) -> list[str]:
    return [FEATURES.inputs[i].flag for i in input_ids]


def _api_violations(
    profile: str, *, startup_wav: Path | None, shutter_wav: Path | None, options: dict[str, str | None]
) -> list[features.Violation]:
    """Manifest rules a build_image call breaks. options maps option id -> build_image argument.

    The API treats an option's default ("off") as not selected."""
    return FEATURES.selection_violations(
        profile,
        inputs={"startup_wav": startup_wav is not None, "shutter_wav": shutter_wav is not None},
        options={
            oid: None if options.get(oid) in (None, option.default) else options[oid]
            for oid, option in FEATURES.options.items()
        },
    )


def _api_message(v: features.Violation) -> str:
    """build_image's wording for one violation."""
    profile = v.profile
    if v.kind == features.OPTION_REQUIRES_PROFILE:
        option = FEATURES.options[v.subjects[0]]
        return f"{option.flag} requires --profile {' or '.join(option.profiles)}"
    if v.kind == features.UNKNOWN_PROFILE:
        return f"Unsupported build profile: {profile!r}"
    if v.kind == features.FORBIDDEN_INPUT:
        return f"--profile {profile} forbids {' and '.join(_flags(FEATURES.profiles[profile].inputs_with('forbidden')))}"
    return f"{profile} profile requires {' and '.join(_flags(FEATURES.profiles[profile].inputs_with('required')))}"


def api_selection_error(
    profile: str, *, startup_wav: Path | None, shutter_wav: Path | None, options: dict[str, str | None]
) -> str | None:
    """The first manifest rule a build_image call breaks, in build_image's wording."""
    violations = _api_violations(profile, startup_wav=startup_wav, shutter_wav=shutter_wav, options=options)
    return _api_message(violations[0]) if violations else None


def cli_selection_error(args: argparse.Namespace) -> str | None:
    """The first manifest rule parsed CLI arguments break, in the CLI's wording.

    The CLI treats any explicit option flag, including its default value, as selected."""
    violations = FEATURES.selection_violations(
        args.profile,
        inputs={i: getattr(args, i) is not None for i in FEATURES.inputs},
        options={o: getattr(args, o) for o in FEATURES.options},
    )
    if not violations:
        return None
    v = violations[0]
    if v.kind == features.OPTION_REQUIRES_PROFILE:
        option = FEATURES.options[v.subjects[0]]
        return f"{option.flag} requires --profile {' or '.join(option.profiles)}"
    if v.kind == features.FORBIDDEN_INPUT:
        flags = _flags(FEATURES.profiles[args.profile].inputs_with("forbidden"))
        return f"--profile {args.profile} is silent and forbids {' / '.join(flags)}"
    if v.kind == features.MISSING_INPUT:
        return f"the following arguments are required for --profile {args.profile}: {', '.join(_flags(v.subjects))}"
    raise ValueError(f"unexpected selection violation {v.kind}")


_PROTECTED_SOURCE_DIRS = (
    (ROOT / "src").resolve(),
    TOOLS_DIR.resolve(),
    CONFIG_DIR.resolve(),
    (ROOT / "tests").resolve(),
    (ROOT / "docs").resolve(),
    (ROOT / ".github").resolve(),
)


def discover_asset_provenance(resolved_wav_path: str | Path) -> tuple[dict[str, str], list[Path]]:
    """Discover optional sibling conversion/pcm-facts provenance once from the resolved WAV directory."""
    wav_dir = Path(resolved_wav_path).parent
    asset_provenance: dict[str, str] = {}
    provenance_paths: list[Path] = []
    for name in PROVENANCE_FILENAMES:
        candidate = wav_dir / name
        if candidate.is_file():
            resolved = candidate.resolve(strict=True)
            asset_provenance[name] = str(resolved)
            provenance_paths.append(resolved)
    return asset_provenance, provenance_paths


def manifest_input_key(path_str: str | Path) -> str:
    """Use repo-relative keys inside the repository and resolved absolute paths for external inputs."""
    path = Path(path_str)
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def shutter_worker_source_header(sw: dict) -> str:
    """Render sound-worker-source.h from the owning firmware.json shutter_worker section."""
    return (
        "\n".join(
            [
                f"#define SOUND_WORKER_CREATE_TASK_ADDR {hex(sw['create_task'])}u",
                f"#define SOUND_WORKER_START_TASK_ADDR {hex(sw['start_task'])}u",
                f"#define SOUND_WORKER_DELETE_TASK_ADDR {hex(sw['delete_task'])}u",
                f"#define SOUND_WORKER_SLEEP_MS_ADDR {hex(sw['sleep_ms'])}u",
                f"#define SOUND_WORKER_CFG_STACK_BYTES {sw['stack_bytes']}u",
                f"#define SOUND_WORKER_CFG_PRIORITY {sw['priority']}",
                f"#define SOUND_WORKER_CFG_OPTIONS {sw['options']}u",
                f"#define SOUND_WORKER_CFG_READINESS_LIMIT {sw['readiness_limit']}u",
                f"#define SOUND_WORKER_CONTEXT_BASE {hex(sw['context_base'])}u",
            ]
        )
        + "\n"
    )


def observer_source_header(observer: dict, source_id: str) -> str:
    """Validate the owning observer config and render observer-source.h."""
    protocol = observer.get("protocol")
    if protocol != "R5O2":
        raise ValueError("observer protocol must be R5O2")
    properties = observer.get("properties")
    if (
        type(properties) is not list
        or not properties
        or any(type(p) is not int or not 0 <= p <= 0xFFFFFFFF for p in properties)
    ):
        raise ValueError("observer properties must be a non-empty u32 list")
    if len(set(properties)) != len(properties):
        raise ValueError("observer properties must not contain duplicates")
    if properties[-1] != observer["beep_property"]:
        raise ValueError("beep_property must be last in observer properties")
    if type(observer["max_notifications_logged"]) is not int or observer["max_notifications_logged"] <= 0:
        raise ValueError("invalid max_notifications_logged")
    slot = observer.get("counter_context_slot")
    offset = observer.get("counter_field_offset")
    if type(slot) is not int or not 0 < slot <= 0xFFFFFFFF or slot % 4:
        raise ValueError("invalid counter_context_slot")
    if type(offset) is not int or not 0 <= offset <= 0xFFFFFFFF or offset % 4:
        raise ValueError("invalid counter_field_offset")
    prop_list = ", ".join(hex(p) + "u" for p in properties)
    lines = [
        "#define OBSERVER_SOURCE_ID " + json.dumps(source_id),
        "#define OBSERVER_PROTOCOL " + json.dumps(protocol),
        *(
            f"#define OBSERVER_{name.upper()} {hex(observer[name])}u"
            for name in (
                "beep_property",
                "class",
                "level",
                "max_notifications_logged",
                "max_dumps",
                "counter_context_slot",
                "counter_field_offset",
            )
        ),
        f"#define OBSERVER_PROPERTY_COUNT {len(properties)}u",
        f"#define OBSERVER_PROPERTY_LIST {{{prop_list}}}",
    ]
    return "\n".join(lines) + "\n"


def admit_audio_role(wav_path: Path) -> tuple[bytes, dict, dict[str, str], list[Path]]:
    """Admit one audio role's PCM and discover its optional sibling provenance once."""
    pcm, facts = admit_pcm(wav_path)
    provenance, provenance_paths = discover_asset_provenance(facts["resolved_path"])
    return pcm, facts, provenance, provenance_paths


def verify_linked_audio_role(
    data: bytes,
    symbols: dict,
    out: Path,
    *,
    pcm: bytes,
    pcm_symbol: str,
    end_symbol: str,
    desc_symbol: str,
    desc_filename: str,
) -> None:
    """Verify linked 32-byte-aligned PCM payload and canonical 64-byte descriptor for one role."""
    location = lambda name: symbols["_resident_load_offset"] + symbols[name] - symbols["_resident_start"]
    pcm_start = location(pcm_symbol)
    desc_start = location(desc_symbol)
    expected_desc = descriptor(symbols[pcm_symbol], len(pcm))
    if symbols[pcm_symbol] % 32 or symbols[end_symbol] - symbols[pcm_symbol] != len(pcm) or data[pcm_start : pcm_start + len(pcm)] != pcm:
        raise ValueError(f"linked {pcm_symbol} geometry/identity")
    if (out / desc_filename).read_bytes() != expected_desc:
        raise ValueError(f"generated {desc_filename} mismatch")
    if symbols[desc_symbol] % 32 or symbols[pcm_symbol] - symbols[desc_symbol] != DESCRIPTOR_BYTES:
        raise ValueError(f"linked {desc_symbol} alignment/layout")
    if data[desc_start : desc_start + DESCRIPTOR_BYTES] != expected_desc:
        raise ValueError(f"linked {desc_symbol} mismatch")


def check_output_dir(output_dir: Path) -> Path:
    """Refuse output locations inside the repository's source directories, without creating anything."""
    out = Path(output_dir).resolve()
    if out == ROOT.resolve():
        raise ValueError("refusing to use repository root as --output directory")
    for protected in _PROTECTED_SOURCE_DIRS:
        if out == protected or out.is_relative_to(protected):
            raise ValueError(f"refusing to write build output inside source directory: {out}")
    return out


def _validate_output_dir(output_dir: Path) -> Path:
    out = check_output_dir(output_dir)
    out.mkdir(exist_ok=False, parents=True)
    return out


def _admit_rom_file(rom_path: Path, *, manifest: dict, target_contract: dict) -> tuple[str, Path, bytes, dict]:
    """Read and strictly admit the selected shutter-feedback ROM (no output, no toolchain)."""
    rom_caller_path = str(rom_path)
    rom_resolved = Path(rom_path).resolve(strict=True)
    rom_bytes = read_regular_file(rom_resolved, exact_bytes=manifest["dump_bytes"])
    startup, rom_provenance = admit_rom_with_provenance(
        rom_bytes, manifest=manifest, contract=target_contract
    )
    return rom_caller_path, rom_resolved, startup, rom_provenance


def check_rom(
    rom_path: Path,
    *,
    profile: str = FEATURES.default_profile,
    record_indicator: str | None = None,
    record_stop_indicator: str | None = None,
    screenshot: str | None = None,
    manifest: dict = M,
    target_contract: dict = TARGET_CONTRACT,
    xpan_contract: dict = XPAN_TARGET_CONTRACT,
    xpan_hooks: list[dict] | None = None,
) -> dict:
    """Run the exact ROM admission a build of this selection runs, and stop.

    Reads the ROM only: no toolchain, no output directory, no writes. Raises the
    same errors the build would raise for the ROM. Returns a short summary."""
    # WAV inputs are not part of a ROM check; every other selection rule applies.
    options = {
        "record_indicator": record_indicator, "record_stop_indicator": record_stop_indicator, "screenshot": screenshot,
    }
    violations = [
        v
        for v in _api_violations(profile, startup_wav=None, shutter_wav=None, options=options)
        if v.kind != features.MISSING_INPUT
    ]
    if violations:
        raise ValueError(_api_message(violations[0]))
    if profile == "xpan-crop":
        from xpan_build import admit_xpan_rom

        admitted = admit_xpan_rom(
            rom_path,
            indicator=record_indicator,
            stop_indicator=record_stop_indicator,
            screenshot=screenshot,
            manifest=manifest,
            target_contract=target_contract,
            xpan_contract=xpan_contract,
            xpan_hooks=xpan_hooks,
        )
        rom_resolved, provenance = admitted.rom_resolved, admitted.rom_provenance
    else:
        _, rom_resolved, _, provenance = _admit_rom_file(rom_path, manifest=manifest, target_contract=target_contract)
    summary = {
        "rom_admitted": True,
        "profile": profile,
        "record_indicator": ri.admit_choice(record_indicator, "start") if profile == "xpan-crop" else None,
        "record_stop_indicator": ri.admit_choice(record_stop_indicator, "stop") if profile == "xpan-crop" else None,
        "screenshot": sc.admit_choice(screenshot) if profile == "xpan-crop" else None,
        "target": provenance["target"],
        "resolved_path": str(rom_resolved),
        "bytes": provenance["bytes"],
        "sha256": provenance["sha256"],
        "word_sum": provenance["word_sum"],
        "admission_mode": provenance["admission_mode"],
        "verified_code_regions": provenance["verified_code_regions"],
    }
    if "verified_xpan_regions" in provenance:
        summary["verified_xpan_regions"] = provenance["verified_xpan_regions"]
    return summary


def build_image(
    *,
    rom_path: Path,
    startup_wav: Path | None = None,
    shutter_wav: Path | None = None,
    toolchain_dir: Path,
    output_dir: Path,
    profile: str = "shutter-feedback",
    manifest: dict = M,
    target_contract: dict = TARGET_CONTRACT,
    xpan_contract: dict = XPAN_TARGET_CONTRACT,
    xpan_hooks: list[dict] | None = None,
    policy_path: Path = POLICY_PATH,
    schema: dict = AUDIO_LOG_SCHEMA,
    record_indicator: str | None = None,
    record_stop_indicator: str | None = None,
    screenshot: str | None = None,
) -> dict:
    """Execute an offline build for either shutter-feedback (default) or xpan-crop."""
    error = api_selection_error(
        profile,
        startup_wav=startup_wav,
        shutter_wav=shutter_wav,
        options={
            "record_indicator": record_indicator, "record_stop_indicator": record_stop_indicator,
            "screenshot": screenshot,
        },
    )
    if error:
        raise ValueError(error)
    if profile == "xpan-crop":
        from xpan_build import build_xpan_image

        return build_xpan_image(
            rom_path=rom_path,
            toolchain_dir=toolchain_dir,
            output_dir=output_dir,
            validate_output_dir=_validate_output_dir,
            manifest_input_key=manifest_input_key,
            observer_source_header=observer_source_header,
            shutter_worker_source_header=shutter_worker_source_header,
            manifest=manifest,
            target_contract=target_contract,
            xpan_contract=xpan_contract,
            xpan_hooks=xpan_hooks,
            policy_path=policy_path,
            record_indicator=record_indicator,
            record_stop_indicator=record_stop_indicator,
            screenshot=screenshot,
        )
    """Execute the full offline shutter-feedback build into a fresh output directory."""
    env = controlled_environment()
    validate_target_contract_consistency(manifest, target_contract)
    _, toolchain_admission = admit_toolchain(toolchain_dir, policy_path=policy_path)

    rom_caller_path, rom_resolved, startup, rom_provenance = _admit_rom_file(
        rom_path, manifest=manifest, target_contract=target_contract
    )
    actual_rom_sha256 = rom_provenance["sha256"]

    pcm, audio_facts, asset_provenance, provenance_paths = admit_audio_role(startup_wav)
    shutter_pcm, shutter_facts, shutter_provenance, shutter_provenance_paths = admit_audio_role(shutter_wav)

    # Create output directory only after inputs and toolchain pass initial validation.
    out = _validate_output_dir(output_dir)

    profile = "shutter-feedback"
    configuration = {
        "profile": profile,
        "audio": audio_facts,
        "shutter_audio": shutter_facts,
        "capture_observer": True,
        "shutter_feedback": True,
    }

    tracked = (
        [
            SRC_DIR / n
            for n in (
                "handoff.S",
                "audio.S",
                "observer.S",
                "footer.S",
                "handoff.ld",
                "observer.h",
                "observer.c",
                "sound_worker.h",
                "sound_worker.c",
                "sound_boot.h",
                "sound_boot.c",
            )
        ]
        + [
            CONFIG_DIR / n
            for n in (
                "firmware.json",
                "target_contract.json",
                "audio_log_format.json",
                "toolchain_policy.json",
                "features.json",
            )
        ]
        + [
            TOOLS_DIR / n
            for n in (
                "build.py",
                "features.py",
                "contracts.py",
                "toolchain.py",
                "image_format.py",
                "input_files.py",
                "audio_profile.py",
                "audio_log.py",
                "wav_memo.py",
            )
        ]
        + provenance_paths
        + shutter_provenance_paths
    )
    fingerprint = lambda: {str(p): hash_regular_file(p) for p in tracked}
    inputs = fingerprint()
    commands: list[dict] = []

    source_manifest = {
        "inputs": {manifest_input_key(name): h for name, h in inputs.items()},
        "configuration": configuration,
    }
    manifest_bytes = json.dumps(source_manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    (out / "source-manifest.json").write_bytes(manifest_bytes)
    source_digest = hashlib.sha256(manifest_bytes).hexdigest()

    body, log_offsets = build_template(actual_rom_sha256, source_digest)
    (out / "audio-log-template.txt").write_bytes(body)
    (out / "audio.pcm").write_bytes(pcm)
    (out / "audio-descriptor.bin").write_bytes(bytes(DESCRIPTOR_BYTES))
    (out / "shutter.pcm").write_bytes(shutter_pcm)
    (out / "shutter-descriptor.bin").write_bytes(bytes(DESCRIPTOR_BYTES))
    (out / "audio-log-offsets.inc").write_text(
        "".join(".word " + str(log_offsets[key]) + "\n" for key in schema["field_order"])
    )
    (out / "witness-template.bin").write_bytes(body)
    path = schema["path"]
    (out / "witness-path.inc").write_text(".asciz " + json.dumps(path) + "\n")

    def run(tool: str, *argv: str | Path) -> str:
        exe_path = toolchain_admission["executables"][f"arm-none-eabi-{tool}"]["path"]
        cmd = [exe_path, *map(str, argv)]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=out, env=env)
        commands.append({"argv": cmd, "returncode": p.returncode})
        with (out / "build.log").open("a") as f:
            f.write(json.dumps(cmd) + "\n" + p.stdout + p.stderr + "\n")
        if p.returncode:
            raise RuntimeError(f"command failed ({tool}); see {out / 'build.log'}")
        return p.stdout

    defs = {k: v for k, v in manifest.items() if type(v) is int}
    defs["audio_once"] = 1
    defs["audio_dual"] = 1
    defs["observe_capture"] = 1
    defs["shutter_feedback"] = 1
    (out / "firmware.inc").write_text("".join(".equ FW_" + k.upper() + ", " + hex(v) + "\n" for k, v in defs.items()))

    witness = manifest["witness"]
    fields = {name: index * 4 for index, name in enumerate(schema["field_order"])}
    extra = {name: 4 * (len(fields) + index) for index, name in enumerate(manifest["audio"]["extra_state_fields"])}
    state_words = len(fields) + len(extra)
    dep_words = len([name for name in manifest["audio"]["extra_state_fields"] if name.startswith("dep_")])
    observer = manifest["observer"]
    sw = manifest["shutter_worker"]

    with (out / "firmware.inc").open("a") as f:
        for name, value in witness["bindings"].items():
            f.write(".equ WITNESS_" + name.upper() + ", " + hex(value) + "\n")
        for prefix, section in (
            ("STATE_", witness["state_offsets"]),
            ("STAGE_", witness["stages"]),
            ("ATTEMPT_", witness["attempt_flags"]),
        ):
            for name, value in section.items():
                f.write(".equ " + prefix + name.upper() + ", " + str(value) + "\n")
        for name, value in {
            "BODY_LENGTH": len(body),
            "STATE_BYTES": 4 * len(witness["state_offsets"]),
            "CONTEXT_BASE": witness["context_base"],
            "MOUNT_ATTEMPT_LIMIT": witness["mount_attempt_limit"],
            "SLEEP_MS": witness["sleep_ms"],
        }.items():
            f.write(".equ WITNESS_" + name + ", " + str(value) + "\n")
        for name, value in manifest["audio"].items():
            if type(value) is int:
                f.write(".equ AUDIO_" + name.upper() + ", " + hex(value) + "\n")
        for prefix, values in (
            ("AF_", fields),
            ("OF_", schema["observation_flags"]),
            ("AO_", schema["outcomes"]),
            ("AUDIO_", extra),
            ("DEP_OF_", manifest["audio"]["dep_obs_flags"]),
        ):
            for name, value in values.items():
                f.write(".equ " + prefix + name.upper() + ", " + str(value) + "\n")
        f.write(
            ".equ AUDIO_PCM_BYTES, " + str(len(pcm))
            + "\n.equ AUDIO_LOG_FIELD_COUNT, " + str(len(fields))
            + "\n.equ AUDIO_DEP_OBS_WORDS, " + str(dep_words)
            + "\n.equ AUDIO_STATE_WORDS, " + str(state_words)
            + "\n.equ AUDIO_STATE_BYTES, " + str(4 * state_words)
            + "\n.equ AUDIO_DESC_PTR_OFFSET, " + str(DESCRIPTOR_PTR_OFFSET)
            + "\n.equ AUDIO_DESC_BYTES_OFFSET, " + str(DESCRIPTOR_BYTES_OFFSET) + "\n"
        )
        f.write(".equ SHUTTER_PCM_BYTES, " + str(len(shutter_pcm)) + "\n")
        f.write(".equ OBSERVER_PUBLIC_SLOT, " + hex(observer["public_slot"]) + "\n")

    (out / "observer-source.h").write_text(observer_source_header(observer, source_digest[:16]))
    (out / "observer-bindings.c").write_text(
        '#include "observer.h"\n'
        '#include "sound_boot.h"\n'
        '_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");\n'
        '__attribute__((section(".resident_text")))\n'
        "unsigned observer_read_counter(unsigned *context_out, unsigned *value_out)\n"
        "{\n"
        "    uintptr_t ctx = 0;\n"
        "    unsigned ok = counter_read_slot((const volatile uintptr_t *)OBSERVER_COUNTER_CONTEXT_SLOT,\n"
        "                                    OBSERVER_COUNTER_FIELD_OFFSET, &ctx, value_out);\n"
        "    *context_out = (unsigned)ctx;\n"
        "    return ok;\n"
        "}\n"
        'const struct observer_api observer_api __attribute__((section(".resident_rodata"))) = {\n'
        + "".join(
            " (" + type_name + ")" + hex(observer[name]) + ",\n"
            for name, type_name in (
                ("register_slave", "observer_register_fn"),
                ("ack", "observer_ack_fn"),
                ("debug", "observer_debug_fn"),
            )
        )
        + " observer_read_counter,\n"
        + " sound_boot_report,\n"
        + "};\n"
    )

    (out / "sound-worker-source.h").write_text(shutter_worker_source_header(sw))
    (out / "sound-worker-bindings.c").write_text(
        '#include "observer.h"\n'
        '#include "sound_worker.h"\n'
        '#include "sound_boot.h"\n'
        '#include "sound-worker-source.h"\n'
        '_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");\n'
        '_Static_assert(SOUND_WORKER_CFG_STACK_BYTES == SOUND_WORKER_STACK_BYTES, "stack_bytes mismatch");\n'
        '_Static_assert(SOUND_WORKER_CFG_PRIORITY == SOUND_WORKER_PRIORITY, "priority mismatch");\n'
        '_Static_assert(SOUND_WORKER_CFG_OPTIONS == SOUND_WORKER_OPTIONS, "options mismatch");\n'
        '_Static_assert(SOUND_WORKER_CFG_READINESS_LIMIT == SOUND_WORKER_READINESS_LIMIT, "readiness_limit mismatch");\n'
        "extern const unsigned shutter_descriptor[];\n"
        "extern unsigned audio_attempt_descriptor(const unsigned *descriptor, unsigned readiness_limit);\n"
        '__attribute__((section(".resident_text")))\n'
        "unsigned sound_worker_play_shutter(void)\n"
        "{\n"
        "    return audio_attempt_descriptor(shutter_descriptor, SOUND_WORKER_READINESS_LIMIT);\n"
        "}\n"
        '__attribute__((section(".resident_text")))\n'
        "void sound_boot_read_hw_context(unsigned *cpsr_out, unsigned *slot_100c_out, int *slot_102c_out)\n"
        "{\n"
        "    unsigned cpsr = 0u;\n"
        '    __asm__ volatile ("mrs %0, cpsr" : "=r"(cpsr));\n'
        "    *cpsr_out = cpsr;\n"
        "    *slot_100c_out = *(const volatile unsigned *)(uintptr_t)(SOUND_WORKER_CONTEXT_BASE + 12u);\n"
        "    *slot_102c_out = *(const volatile int *)(uintptr_t)(SOUND_WORKER_CONTEXT_BASE + 44u);\n"
        "}\n"
        'const struct sound_worker_api sound_worker_api __attribute__((section(".resident_rodata"))) = {\n'
        "    (sound_worker_create_fn)SOUND_WORKER_CREATE_TASK_ADDR,\n"
        "    (sound_worker_start_fn)SOUND_WORKER_START_TASK_ADDR,\n"
        "    (sound_worker_delete_fn)SOUND_WORKER_DELETE_TASK_ADDR,\n"
        "    observer_read_counter,\n"
        "    sound_worker_play_shutter,\n"
        "    (sound_worker_sleep_fn)SOUND_WORKER_SLEEP_MS_ADDR,\n"
        "};\n"
        'const struct sound_boot_api sound_boot_api __attribute__((section(".resident_rodata"))) = {\n'
        "    sound_boot_read_hw_context,\n"
        "    sound_worker_start,\n"
        "};\n"
    )

    (out / "startup.bin").write_bytes(startup)
    linker = (SRC_DIR / "handoff.ld").read_text()
    for key, value in {
        "AUTOEXEC_BASE": manifest["autoexec_base"],
        "RESIDENT_BASE": manifest["resident_base"],
        "STACK_BYTES": manifest["private_stack_bytes"],
        "OBJECTS_START": manifest["objects_start"],
    }.items():
        linker = linker.replace("@" + key + "@", hex(value))
    (out / "handoff.ld").write_text(linker)

    observer_objects: list[Path] = []
    c_sources = [
        (SRC_DIR / "observer.c", "observer.o"),
        (out / "observer-bindings.c", "observer-bindings.o"),
        (SRC_DIR / "sound_worker.c", "sound_worker.o"),
        (SRC_DIR / "sound_boot.c", "sound_boot.o"),
        (out / "sound-worker-bindings.c", "sound-worker-bindings.o"),
    ]
    for source_file, object_name in c_sources:
        run(
            "gcc", "-c", "-std=c11", "-Os", "-mcpu=cortex-a9", "-mthumb", "-mno-unaligned-access",
            "-ffreestanding", "-fno-builtin", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
            "-fno-stack-protector", "-fno-common", "-Wall", "-Wextra", "-Werror", "-DOBSERVER_FIRMWARE",
            "-I", SRC_DIR, "-I", out, source_file, "-o", out / object_name,
        )
        observer_objects.append(out / object_name)

    def compile_link() -> dict[str, int]:
        run(
            "gcc", "-c", "-x", "assembler-with-cpp", "-mcpu=cortex-a9", "-marm", "-ffreestanding",
            "-nostdlib", "-Wa,--fatal-warnings", "-I", SRC_DIR, "-I", out,
            SRC_DIR / "handoff.S", "-o", out / "handoff.o",
        )
        run(
            "gcc", "-c", "-x", "assembler-with-cpp", "-mcpu=cortex-a9", "-marm", "-ffreestanding",
            "-nostdlib", SRC_DIR / "footer.S", "-o", out / "footer.o",
        )
        run(
            "ld", "--no-undefined", "--no-warn-rwx-segments",
            "-T", out / "handoff.ld", "-Map=" + str(out / "handoff.map"),
            "-o", out / "handoff.elf", out / "handoff.o", *observer_objects, out / "footer.o",
        )
        txt = run("nm", "-n", out / "handoff.elf")
        return {name: int(addr, 16) for addr, _kind, name in (line.split() for line in txt.splitlines() if len(line.split()) == 3)}

    symbols = compile_link()
    layout = validate_layout(symbols, manifest=manifest)
    write_buffer = validate_write_buffer(
        symbols["witness_body"], len(body), symbols["_resident_start"], symbols["_resident_data_end"]
    )
    patched = patch_startup(startup, symbols["copied_startup"], symbols, manifest=manifest)
    (out / "startup.bin").write_bytes(patched)
    (out / "audio-descriptor.bin").write_bytes(descriptor(symbols["audio_pcm"], len(pcm)))
    (out / "shutter-descriptor.bin").write_bytes(descriptor(symbols["shutter_pcm"], len(shutter_pcm)))

    final = compile_link()
    if final != symbols:
        raise ValueError("layout changed after startup/descriptor patching")

    undefined = run("nm", "-u", out / "handoff.elf").strip()
    relocs = run("readelf", "-rW", out / "handoff.elf")
    disasm = run("objdump", "-d", out / "handoff.elf")
    if undefined or re.search(r"\bR_ARM_", relocs):
        raise ValueError("unresolved symbols/relocations")

    (out / "symbols.json").write_text(json.dumps(symbols, indent=2) + "\n")
    (out / "disassembly.txt").write_text(disasm)
    (out / "relocations.txt").write_text(relocs)

    run("objcopy", "-O", "binary", out / "handoff.elf", out / "handoff.unfinalized")
    raw = (out / "handoff.unfinalized").read_bytes()
    if raw[:4] != bytes.fromhex("00bf01e0") or raw[7] != 0xEA:
        raise ValueError("dual entry prefix")

    startup_disasm = run(
        "objdump", "-D", "-b", "binary", "-m", "arm", "-M", "force-thumb",
        "--adjust-vma=" + hex(symbols["copied_startup"]), out / "startup.bin",
    )
    (out / "startup-disassembly.txt").write_text(startup_disasm)

    for patch in manifest["patches"]:
        addr = symbols["copied_startup"] + patch["offset"]
        target = symbols[patch["symbol"]] & ~1
        line = next((l for l in startup_disasm.splitlines() if l.strip().startswith(f"{addr:x}:")), "")
        if not re.search(r"\b(?:0x)?" + f"{target:x}" + r"\b", line):
            raise ValueError("patch disassembly differs: " + line)

    # Re-verify that no input file changed during compilation/linking.
    try:
        current_inputs = fingerprint()
    except OSError as exc:
        raise ValueError("build inputs changed") from exc
    if current_inputs != inputs:
        raise ValueError("build inputs changed")
    if hash_regular_file(rom_resolved, exact_bytes=rom_provenance["bytes"]) != actual_rom_sha256:
        raise ValueError("selected ROM changed during build")
    if hash_regular_file(audio_facts["resolved_path"], exact_bytes=audio_facts["wav_bytes"]) != audio_facts["wav_sha256"]:
        raise ValueError("selected startup WAV changed during build")
    if hash_regular_file(shutter_facts["resolved_path"], exact_bytes=shutter_facts["wav_bytes"]) != shutter_facts["wav_sha256"]:
        raise ValueError("selected shutter WAV changed during build")

    data = finalize_image(raw)
    facts = inspect_image(data)

    verify_linked_audio_role(
        data, symbols, out,
        pcm=pcm, pcm_symbol="audio_pcm", end_symbol="audio_pcm_end",
        desc_symbol="audio_descriptor", desc_filename="audio-descriptor.bin",
    )
    verify_linked_audio_role(
        data, symbols, out,
        pcm=shutter_pcm, pcm_symbol="shutter_pcm", end_symbol="shutter_pcm_end",
        desc_symbol="shutter_descriptor", desc_filename="shutter-descriptor.bin",
    )

    loc = lambda name: symbols["_resident_load_offset"] + symbols[name] - symbols["_resident_start"]
    props_expected = struct.pack(f"<{len(observer['properties'])}I", *observer["properties"])
    obs_api_expected = struct.pack(
        "<5I",
        observer["register_slave"],
        observer["ack"],
        observer["debug"],
        symbols["observer_read_counter"] | 1,
        symbols["sound_boot_report"] | 1,
    )
    if data[loc("properties") : loc("properties") + len(props_expected)] != props_expected:
        raise ValueError("linked observer property list mismatch")
    if data[loc("observer_api") : loc("observer_api") + len(obs_api_expected)] != obs_api_expected:
        raise ValueError("linked observer API table mismatch")

    sw_api_expected = struct.pack(
        "<6I",
        sw["create_task"],
        sw["start_task"],
        sw["delete_task"],
        symbols["observer_read_counter"] | 1,
        symbols["sound_worker_play_shutter"] | 1,
        sw["sleep_ms"],
    )
    sb_api_expected = struct.pack(
        "<2I",
        symbols["sound_boot_read_hw_context"] | 1,
        symbols["sound_worker_start"] | 1,
    )
    if data[loc("sound_worker_api") : loc("sound_worker_api") + len(sw_api_expected)] != sw_api_expected:
        raise ValueError("linked sound_worker API table mismatch")
    if data[loc("sound_boot_api") : loc("sound_boot_api") + len(sb_api_expected)] != sb_api_expected:
        raise ValueError("linked sound_boot API table mismatch")

    for sym in (
        "sound_worker_start",
        "sound_worker_entry",
        "sound_worker_poll_once",
        "sound_worker_set_enabled",
        "sound_worker_is_enabled",
        "sound_worker_play_shutter",
        "sound_worker_api",
        "sound_boot_record_boot_return",
        "sound_boot_record_startup_outcome",
        "sound_boot_context_is_ready",
        "sound_boot_start_worker",
        "sound_boot_report",
        "sound_boot_read_hw_context",
        "sound_boot_api",
        "sound_boot_state",
    ):
        if not symbols["_resident_start"] <= symbols[sym] < symbols["_resident_data_end"]:
            raise ValueError(f"{sym} outside resident initialized section")
    if not symbols["_bss_start"] <= symbols["sound_worker_state"] < symbols["_private_stack_top"]:
        raise ValueError("sound_worker_state outside resident BSS")

    autoexec_path = out / "autoexec.bin"
    autoexec_path.write_bytes(data)

    audio_record = {
        **audio_facts,
        "asset_provenance": asset_provenance,
        "linked_pcm_pointer": symbols["audio_pcm"],
        "linked_descriptor_pointer": symbols["audio_descriptor"],
        "readiness_limit": manifest["audio"]["poll_limit"],
        "observation_limit": manifest["audio"]["poll_limit"],
        "sleep_ms": manifest["audio"]["sleep_ms"],
        "log_fields": fields,
        "log_offsets": log_offsets,
        "extra_state_fields": extra,
        "schema": schema,
        "submitted_on_host": False,
    }
    shutter_record = {
        **shutter_facts,
        "asset_provenance": shutter_provenance,
        "linked_pcm_pointer": symbols["shutter_pcm"],
        "linked_descriptor_pointer": symbols["shutter_descriptor"],
        "observation_limit": manifest["audio"]["poll_limit"],
        "sleep_ms": manifest["audio"]["sleep_ms"],
        "submitted_on_host": False,
    }
    shutter_feedback_record = {
        "bindings": {k: sw[k] for k in ("create_task", "start_task", "delete_task", "sleep_ms")},
        "stack_bytes": sw["stack_bytes"],
        "priority": sw["priority"],
        "options": sw["options"],
        "readiness_limit": sw["readiness_limit"],
        "context_base": sw["context_base"],
        "fio_skipped": True,
        "sound_worker_api_pointers": {
            "create_task": sw["create_task"],
            "start_task": sw["start_task"],
            "delete_task": sw["delete_task"],
            "read_counter": symbols["observer_read_counter"] | 1,
            "play_shutter": symbols["sound_worker_play_shutter"] | 1,
            "sleep_ms": sw["sleep_ms"],
        },
        "sound_boot_api_pointers": {
            "read_context": symbols["sound_boot_read_hw_context"] | 1,
            "start_worker": symbols["sound_worker_start"] | 1,
        },
        "worker_rows": ["worker_boot", "worker_task", "worker_playback", "worker_baseline", "worker_counters"],
    }

    caller_input_hashes = {
        "rom": {
            "caller_input_path": rom_caller_path,
            "resolved_path": str(rom_resolved),
            "bytes": rom_provenance["bytes"],
            "sha256": actual_rom_sha256,
            "word_sum": rom_provenance["word_sum"],
            "admission_mode": rom_provenance["admission_mode"],
        },
        "startup_wav": {
            "caller_input_path": audio_facts["caller_input_path"],
            "resolved_path": audio_facts["resolved_path"],
            "wav_sha256": audio_facts["wav_sha256"],
            "source_pcm_sha256": audio_facts["source_pcm_sha256"],
            "source_pcm_bytes": audio_facts["source_pcm_bytes"],
            "embedded_pcm_sha256": audio_facts["embedded_pcm_sha256"],
            "embedded_pcm_bytes": audio_facts["embedded_pcm_bytes"],
            "padding_bytes": audio_facts["padding_bytes"],
        },
        "shutter_wav": {
            "caller_input_path": shutter_facts["caller_input_path"],
            "resolved_path": shutter_facts["resolved_path"],
            "wav_sha256": shutter_facts["wav_sha256"],
            "source_pcm_sha256": shutter_facts["source_pcm_sha256"],
            "source_pcm_bytes": shutter_facts["source_pcm_bytes"],
            "embedded_pcm_sha256": shutter_facts["embedded_pcm_sha256"],
            "embedded_pcm_bytes": shutter_facts["embedded_pcm_bytes"],
            "padding_bytes": shutter_facts["padding_bytes"],
        },
        "toolchain": {
            "caller_input_path": toolchain_admission["caller_input_path"],
            "resolved_path": toolchain_admission["resolved_path"],
            "target": toolchain_admission["target"],
            "gcc_version": toolchain_admission["gcc_version"],
            "executables": {k: v["sha256"] for k, v in toolchain_admission["executables"].items()},
        },
    }

    record = {
        "profile": profile,
        "target": target_contract["target"],
        "output_image": str(autoexec_path),
        "caller_input_hashes": caller_input_hashes,
        "rom_admission": rom_provenance,
        "audio": audio_record,
        "shutter_audio": shutter_record,
        "shutter_feedback": shutter_feedback_record,
        "configuration": configuration,
        "host_build_ok": True,
        "runtime_executed": False,
        "camera_or_card_actions": False,
        "deployment": deployment(),
        "reservation": layout,
        "format": facts,
        "undefined_symbols": False,
        "retained_relocations": False,
        "dual_entry_valid": True,
        "roundtrip_patch_check": True,
        "inputs": {manifest_input_key(k): v for k, v in inputs.items()},
        "toolchain_admission": toolchain_admission,
        "commands": commands,
        "output": str(out),
        "witness": {
            **witness,
            "expected_sha256": hashlib.sha256(body).hexdigest(),
            "source_manifest_sha256": source_digest,
            "expected_bytes": len(body),
            "path": path,
            "fio_skipped": True,
            "write_buffer": write_buffer,
            "stack_policy": "Retain Canon init-task stack for scheduling calls; private stack only during early CamIF",
        },
        "observer": {
            **observer,
            "capture_trigger": True,
            "initial_notifications": "retained without classification",
            "observed_payload_scope": "first_u32_when_len_ge_4",
            "source_id": source_digest[:16],
            "native_execution_verified": False,
            "limitations": [
                "Canonical registration can assert on request OOM",
                "Stock logging/ACK internals have no proven nonblocking bound",
                "8-byte MechaCounter second word is outside FIRST_U32 prefix",
                "Single-photo worker triggers on positive release counter delta; busy effects drop without delayed replay",
            ],
        },
    }
    (out / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def build_parser() -> argparse.ArgumentParser:
    """The public CLI; profile, WAV and option arguments come from config/features.json."""
    ap = argparse.ArgumentParser(
        description=(
            "Offline builder for Canon EOS R5 (firmware 2.2.1) shutter-feedback sound mod (default) "
            "or silent experimental XPan crop-only preview (--profile xpan-crop)."
        )
    )
    ap.add_argument(
        "--profile",
        choices=FEATURES.profile_ids,
        default=FEATURES.default_profile,
        help=FEATURES.profile_cli_help,
    )
    ap.add_argument("--rom", type=Path, required=True, help="Path to user's 64 MiB Canon EOS R5 2.2.1 ROM dump")
    for spec in FEATURES.inputs.values():
        ap.add_argument(spec.flag, type=Path, required=False, help=spec.cli_help)
    ap.add_argument("--toolchain", type=Path, required=True, help="Directory containing arm-none-eabi GCC 15.2.1 binaries")
    ap.add_argument("--output", type=Path, required=True, help="New output directory for autoexec.bin and build artifacts")
    for option in FEATURES.options.values():
        ap.add_argument(option.flag, choices=option.values, default=None, help=option.cli_help)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    error = cli_selection_error(args)
    if error:
        ap.error(error)

    record = build_image(
        rom_path=args.rom,
        startup_wav=args.startup_wav,
        shutter_wav=args.shutter_wav,
        toolchain_dir=args.toolchain,
        output_dir=args.output,
        profile=args.profile,
        record_indicator=args.record_indicator,
        record_stop_indicator=args.record_stop_indicator,
        screenshot=args.screenshot,
    )
    indicator = record.get("record_indicator") or {}
    print(
        json.dumps(
            {
                "host_build_ok": True,
                "target": record["target"],
                "output_image": record["output_image"],
                "bytes": record["format"]["bytes"],
                "sha256": record["format"]["sha256"],
                "rom_sha256": record["caller_input_hashes"]["rom"]["sha256"],
                "reservation": record["reservation"],
                "record_indicator": indicator.get("cue", "off")
                if args.profile in RECORD_INDICATOR.profiles
                else None,
                "record_stop_indicator": indicator.get("stop_cue", "off")
                if args.profile in RECORD_STOP_INDICATOR.profiles
                else None,
                **({"screenshot": (record.get("screenshot") or {}).get("trigger", "off")}
                   if args.profile in SCREENSHOT.profiles else {}),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
