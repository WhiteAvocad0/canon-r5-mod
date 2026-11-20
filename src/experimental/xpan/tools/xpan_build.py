"""Offline host builder for the experimental silent XPan crop profile (EOS R5 2.2.1)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
from typing import Callable, NamedTuple

XPAN_TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC_DIR = XPAN_TOOLS_DIR.parent
XPAN_RESIDENT_DIR = XPAN_SRC_DIR / "resident"
XPAN_ROW_DIR = XPAN_SRC_DIR / "frame-row"
REPO_ROOT = XPAN_SRC_DIR.parents[2]
R5_221_SRC_DIR = REPO_ROOT / "src" / "r5_221"
TOOLS_DIR = REPO_ROOT / "tools"
CONFIG_DIR = REPO_ROOT / "config"

import feature_contract
import frame_native_profile
import frame_row_profile
import menu_vector
import playback_mask_profile
import playback_native_profile
import q_label_profile
import record_indicator as ri

from contracts import (
    MANIFEST as M,
    TARGET_CONTRACT,
    XPAN_TARGET_CONTRACT,
    admit_xpan_rom_with_provenance,
    deployment,
    patch_startup,
    validate_layout,
    validate_write_buffer,
)
from image_format import finalize_image, inspect_image
from input_files import hash_regular_file, read_regular_file
from toolchain import POLICY_PATH, admit_toolchain, controlled_environment

FORBIDDEN_AUDIO_SYMBOLS = (
    "audio_attempt",
    "audio_attempt_descriptor",
    "audio_submit_once",
    "audio_render",
    "audio_descriptor",
    "shutter_descriptor",
    "audio_packed",
    "shutter_packed",
    "pcm_packed_roles",
    "pcm_runtime_prepare",
    "pcm_runtime_report",
    "pcm_runtime_ready",
    "pcm_runtime_state",
    "packed_pcm_decode",
    "puff",
    "sound_worker_play_shutter",
)

# XPan automatic post-shot review fix (always linked): hook entries and owners.
REVIEW_FIX_SYMBOLS = (
    "xpan_review_authority_entry",
    "xpan_review_authority_observe",
    "xpan_review_outline_entry",
    "xpan_review_outline_decide",
    "xpan_review_gate_crop",
    "review_crop_entry",
    "review_crop_original",
    "review_crop_native_post",
    "review_crop_apply",
)

XPAN_RESIDENT_MODULES = (
    "root_plan",
    "root_profile",
    "root_native",
    "menu_table",
    "menu_native",
)

XPAN_C_MODULES = (
    "multi_plan",
    "code_patch",
    "boot_policy",
    "feature_pages",
    "heap_span",
    "heap_native",
    "gui_crop",
    "rect_crop",
    "jpeg_crop",
    "jpeg_crop_trace",
    "frame_tags",
    "frame_memory",
    "frame_native",
    "frame_native_trace",
    "frame_disable_trace",
    "export_refresh",
    "export_refresh_native",
    "playback_box",
    "playback_tags",
    "playback_native",
    "playback_mask_native",
    "q_label",
    "playback_report",
    "playback_reject",
    "playback_first_view",
    "ifd_memory",
    "xpan_authority",
    "xpan_review_gate",
    "review_crop",
)

XPAN_ASM_MODULES = (
    ("target_page.S", "target-page.o"),
    ("root_switch.S", "root-switch.o"),
    ("menu_hooks.S", "menu-hooks.o"),
    ("gui_crop_trampoline.S", "gui-trampoline.o"),
    ("rect_crop_trampoline.S", "rect-trampoline.o"),
    ("jpeg_crop_hooks.S", "jpeg-hooks.o"),
    ("frame_native_hooks.S", "frame-native-hooks.o"),
    ("export_refresh_hook.S", "export-refresh-hook.o"),
    ("playback_native_hooks.S", "playback-native-hooks.o"),
    ("playback_mask_hooks.S", "playback-mask-hooks.o"),
    ("q_label_hooks.S", "q-label-hooks.o"),
    ("xpan_authority_hook.S", "xpan-authority-hook.o"),
    ("xpan_review_outline_hook.S", "xpan-review-outline-hook.o"),
    ("review_crop_hook.S", "review-crop-hook.o"),
)

XPAN_HEADER_NAMES = (
    "root_plan.h",
    "root_native.h",
    "menu_table.h",
    "menu_native.h",
    "multi_plan.h",
    "code_patch.h",
    "boot_policy.h",
    "feature_pages.h",
    "geometry.h",
    "heap_span.h",
    "heap_native.h",
    "gui_crop.h",
    "rect_crop.h",
    "jpeg_crop.h",
    "jpeg_crop_trace.h",
    "frame_tags.h",
    "frame_memory.h",
    "frame_native.h",
    "frame_native_profile.h",
    "frame_native_trace.h",
    "frame_disable_trace.h",
    "export_refresh.h",
    "playback_box.h",
    "playback_tags.h",
    "playback_native.h",
    "playback_native_profile.h",
    "playback_mask_native.h",
    "playback_mask_profile.h",
    "q_label.h",
    "q_label_profile.h",
    "playback_report.h",
    "playback_reject.h",
    "playback_first_view.h",
    "ifd_memory.h",
    "xpan_authority.h",
    "xpan_review_gate.h",
    "review_crop.h",
)


def _check_profile_headers_sync() -> None:
    if json.loads((XPAN_TOOLS_DIR / "frame-native-hooks.json").read_text()) != frame_native_profile.inventory():
        raise ValueError("framing hook inventory drift")
    if (XPAN_SRC_DIR / "frame_native_profile.h").read_text() != frame_native_profile.header():
        raise ValueError("framing source profile drift")
    for name, mod in (
        ("playback_native", playback_native_profile),
        ("playback_mask", playback_mask_profile),
    ):
        inv_name = "playback-native-hooks.json" if name == "playback_native" else "playback-mask-hooks.json"
        if json.loads((XPAN_TOOLS_DIR / inv_name).read_text()) != mod.inventory():
            raise ValueError(name + " hook inventory drift")
        if (XPAN_SRC_DIR / f"{name}_profile.h").read_text() != mod.header():
            raise ValueError(name + " source profile drift")
    if json.loads((XPAN_TOOLS_DIR / "q-label-hooks.json").read_text()) != q_label_profile.inventory():
        raise ValueError("Q label hook inventory drift")
    if (XPAN_SRC_DIR / "q_label_profile.h").read_text() != q_label_profile.header():
        raise ValueError("Q label source profile drift")


def _tracked_input_paths() -> list[Path]:
    paths: list[Path] = []
    paths.extend(
        R5_221_SRC_DIR / n
        for n in ("observer.h", "observer.S", "sound_boot.h", "sound_boot.c", "sound_worker.h", "footer.S")
    )
    paths.extend(
        XPAN_RESIDENT_DIR / n
        for n in (
            "boot_mmu.h",
            "boot_mmu.c",
            "boot_mmu_capture.inc",
            "handoff.S",
            "handoff.ld",
            "observer.c",
            "sound_worker.c",
        )
    )
    paths.extend(XPAN_ROW_DIR / n for n in ("frame_row.h", "frame_row.c"))
    paths.extend(XPAN_SRC_DIR / f"{n}.c" for n in XPAN_RESIDENT_MODULES)
    paths.extend(XPAN_SRC_DIR / f"{n}.c" for n in XPAN_C_MODULES)
    paths.extend(XPAN_SRC_DIR / n for n in XPAN_HEADER_NAMES)
    paths.extend(XPAN_SRC_DIR / s for s, _ in XPAN_ASM_MODULES)
    paths.extend(XPAN_SRC_DIR / n for n in ri.SOURCE_NAMES)
    paths.extend(
        CONFIG_DIR / n
        for n in (
            "firmware.json", "target_contract.json", "xpan_target_contract.json", "toolchain_policy.json",
            "features.json",
        )
    )
    paths.extend(
        TOOLS_DIR / n
        for n in ("build.py", "features.py", "contracts.py", "toolchain.py", "image_format.py", "input_files.py")
    )
    paths.extend(
        XPAN_TOOLS_DIR / n
        for n in (
            "xpan_build.py",
            "feature_contract.py",
            "frame_row_profile.py",
            "menu_vector.py",
            "frame_native_profile.py",
            "playback_native_profile.py",
            "playback_mask_profile.py",
            "q_label_profile.py",
            "feature-contract.json",
            "frame-native-hooks.json",
            "playback-native-hooks.json",
            "playback-mask-hooks.json",
            "q-label-hooks.json",
            "xpan-review-hooks.json",
            "xpan_font.json",
            *ri.TOOL_NAMES,
        )
    )
    paths.append(TOOLS_DIR / "audio_profile.py")
    return paths


class XPanRomAdmission(NamedTuple):
    rom_caller_path: str
    rom_resolved: Path
    rom_bytes: bytes
    hooks: list[dict]
    startup: bytes
    rom_provenance: dict
    row_profile: dict
    vector_bytes: bytes
    vector_proof: dict


def admit_xpan_rom(
    rom_path: Path,
    *,
    indicator: str | None,
    stop_indicator: str | None,
    manifest: dict = M,
    target_contract: dict = TARGET_CONTRACT,
    xpan_contract: dict = XPAN_TARGET_CONTRACT,
    xpan_hooks: list[dict] | None = None,
) -> XPanRomAdmission:
    """Strictly admit the selected ROM for an XPan build of this start/stop cue choice.

    Reads the ROM and the committed hook inventory only and derives the ROM-backed
    framing rows and menu vector in memory; writes nothing. build_xpan_image runs
    exactly this before it creates its output directory."""
    indicator = ri.admit_choice(indicator, "start")
    stop_indicator = ri.admit_choice(stop_indicator, "stop")
    indicator_on = ri.enabled(indicator, stop_indicator)
    optional_extensions = (ri.HOOK_EXTENSION,) if indicator_on else ()
    rom_caller_path = str(rom_path)
    rom_resolved = Path(rom_path).resolve(strict=True)
    rom_bytes = read_regular_file(rom_resolved, exact_bytes=manifest["dump_bytes"])

    loaded_contract, loaded_hooks, _ = feature_contract.load_hooks(optional_extensions=optional_extensions)
    hooks_to_check = loaded_hooks if xpan_hooks is None else xpan_hooks
    if any(h["destination"] == "rec_state_entry" for h in hooks_to_check) != indicator_on:
        raise ValueError(
            f"record indicator hook inventory disagrees with --record-indicator {indicator} "
            f"--record-stop-indicator {stop_indicator}"
        )

    startup, rom_provenance = admit_xpan_rom_with_provenance(
        rom_bytes,
        manifest=manifest,
        contract=target_contract,
        xpan_contract=xpan_contract,
        hooks=hooks_to_check,
        record_indicator=indicator_on,
        guard_pages=[int(v, 16) for v in loaded_contract["source_guard_pages"]],
    )

    # Derive ROM-backed framing rows and XPan menu vector only after strict ROM admission.
    row_profile = frame_row_profile.build_profile_from_rom(
        rom_bytes,
        rom_sha256=rom_provenance["sha256"],
        expected_table_sha256=xpan_contract["framing_table"]["sha256"],
        caller_path=rom_caller_path,
        resolved_path=str(rom_resolved),
    )
    vector_bytes, _, vector_proof = menu_vector.build_from_rom(
        rom_bytes,
        expected_resource_sha256=xpan_contract["menu_vector_resource"]["sha256"],
        resource_va=xpan_contract["menu_vector_resource"]["address"],
        resource_bytes=xpan_contract["menu_vector_resource"]["bytes"],
        linearize=True,
    )
    return XPanRomAdmission(
        rom_caller_path, rom_resolved, rom_bytes, hooks_to_check, startup, rom_provenance, row_profile,
        vector_bytes, vector_proof,
    )


def build_xpan_image(
    *,
    rom_path: Path,
    toolchain_dir: Path,
    output_dir: Path,
    validate_output_dir: Callable[[Path], Path],
    manifest_input_key: Callable[[str | Path], str],
    observer_source_header: Callable[[dict, str], str],
    shutter_worker_source_header: Callable[[dict], str],
    manifest: dict = M,
    target_contract: dict = TARGET_CONTRACT,
    xpan_contract: dict = XPAN_TARGET_CONTRACT,
    xpan_hooks: list[dict] | None = None,
    policy_path: Path = POLICY_PATH,
    record_indicator: str | None = None,
    record_stop_indicator: str | None = None,
) -> dict:
    """Execute the offline experimental XPan crop-only build into a fresh directory.

    Silent by default. record_indicator selects the opt-in movie record start cue
    and record_stop_indicator the stop cue (issue #14); with both "off" (the
    default) none of their code, hook or pages is linked."""
    indicator = ri.admit_choice(record_indicator, "start")
    stop_indicator = ri.admit_choice(record_stop_indicator, "stop")
    indicator_on = ri.enabled(indicator, stop_indicator)
    optional_extensions = (ri.HOOK_EXTENSION,) if indicator_on else ()
    indicator_facts = ri.facts(indicator, stop_indicator) if indicator_on else None
    env = controlled_environment()
    _check_profile_headers_sync()
    _, toolchain_admission = admit_toolchain(toolchain_dir, policy_path=policy_path)

    (
        rom_caller_path, rom_resolved, rom_bytes, hooks_to_check, startup, rom_provenance, row_profile,
        vector_bytes, vector_proof,
    ) = admit_xpan_rom(
        rom_path,
        indicator=indicator,
        stop_indicator=stop_indicator,
        manifest=manifest,
        target_contract=target_contract,
        xpan_contract=xpan_contract,
        xpan_hooks=xpan_hooks,
    )
    actual_rom_sha256 = rom_provenance["sha256"]

    # Create output directory only after all inputs and derived resources validate cleanly.
    out = validate_output_dir(output_dir)

    boot_va = 0xE0100000
    boot_off = boot_va - manifest["rom_base"]
    (out / "target-page.bin").write_bytes(rom_bytes[boot_off : boot_off + 4096])
    frame_row_profile.write_generated(out / "frame-row", row_profile)
    (out / "menu-vector.bin").write_bytes(vector_bytes)
    (out / "menu-vector-source.h").write_text(f"#define MENU_VECTOR_BYTES {len(vector_bytes)}u\n")
    feature = feature_contract.generate(
        out,
        rom_bytes,
        actual_rom_sha256=actual_rom_sha256,
        hooks_override=hooks_to_check,
        rom_base=manifest["rom_base"],
        optional_extensions=optional_extensions,
    )

    profile = "crop-only"
    configuration = {
        "profile": profile,
        "audio": None,
        "shutter_audio": None,
        "packed_audio": None,
        "lossless_packed_full_audio": False,
        "capture_observer": True,
        "shutter_feedback": False,
        "boot_mmu_observations": True,
        "secondary_entry_observations": True,
        "private_feature_root": True,
        "native_crop_large_trial": True,
        "native_rear_lcd_trial": True,
        "captured_frame_tags": True,
        "precopy_report_refresh": True,
        "xpan_instant_review_fix": True,
        "feature_contract": feature,
        "menu_vector_proof": vector_proof,
        "record_indicator": indicator_facts,
    }

    tracked = _tracked_input_paths()
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

    body = (
        f"R5.221 HANDOFF: CANON_INIT_RETURNED\nROM_SHA256={actual_rom_sha256}\nSOURCE_SHA256={source_digest}\n"
    ).encode("ascii")
    (out / "expected-R5BOOT.TXT").write_bytes(body)
    (out / "witness-template.bin").write_bytes(body)
    witness_path = manifest["witness"]["path"]
    (out / "witness-path.inc").write_text(".asciz " + json.dumps(witness_path) + "\n")

    def run(tool: str, *argv: str | Path) -> str:
        exe_path = toolchain_admission["executables"][f"arm-none-eabi-{tool}"]["path"]
        cmd = [exe_path, *map(str, argv)]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=out, env=env)
        commands.append({"argv": cmd, "returncode": proc.returncode})
        with (out / "build.log").open("a") as f:
            f.write(json.dumps(cmd) + "\n" + proc.stdout + proc.stderr + "\n")
        if proc.returncode:
            raise RuntimeError(f"command failed ({tool}); see {out / 'build.log'}")
        return proc.stdout

    defs = {k: v for k, v in manifest.items() if type(v) is int}
    defs["audio_once"] = 0
    defs["audio_dual"] = 0
    defs["observe_capture"] = 1
    defs["shutter_feedback"] = 0
    defs["crop_only"] = 1
    (out / "firmware.inc").write_text("".join(".equ FW_" + k.upper() + ", " + hex(v) + "\n" for k, v in defs.items()))

    witness = manifest["witness"]
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
        "    (sound_worker_play_shutter_fn)0,\n"
        "    (sound_worker_sleep_fn)SOUND_WORKER_SLEEP_MS_ADDR,\n"
        "};\n"
        'const struct sound_boot_api sound_boot_api __attribute__((section(".resident_rodata"))) = {\n'
        "    sound_boot_read_hw_context,\n"
        "    sound_worker_start,\n"
        "};\n"
    )

    if indicator_on:
        (out / "rec-tone-bindings.c").write_text(ri.bindings_source(indicator_facts, manifest["audio"]))

    (out / "startup.bin").write_bytes(startup)
    linker = (XPAN_RESIDENT_DIR / "handoff.ld").read_text()
    for key, value in {
        "AUTOEXEC_BASE": manifest["autoexec_base"],
        "RESIDENT_BASE": manifest["resident_base"],
        "STACK_BYTES": manifest["private_stack_bytes"],
        "OBJECTS_START": manifest["objects_start"],
    }.items():
        linker = linker.replace("@" + key + "@", hex(value))
    (out / "handoff.ld").write_text(linker)

    indicator_defines = [ri.DEFINE] if indicator_on else []
    objects: list[Path] = []
    c_sources: list[tuple[Path, str]] = [
        (XPAN_RESIDENT_DIR / "observer.c", "observer.o"),
        (out / "observer-bindings.c", "observer-bindings.o"),
        (XPAN_RESIDENT_DIR / "sound_worker.c", "sound_worker.o"),
        (R5_221_SRC_DIR / "sound_boot.c", "sound_boot.o"),
        (XPAN_RESIDENT_DIR / "boot_mmu.c", "boot_mmu.o"),
        *((XPAN_SRC_DIR / f"{name}.c", f"{name}.o") for name in XPAN_RESIDENT_MODULES),
        (out / "sound-worker-bindings.c", "sound-worker-bindings.o"),
        *((XPAN_SRC_DIR / f"{name}.c", f"{name}.o") for name in XPAN_C_MODULES),
        (XPAN_ROW_DIR / "frame_row.c", "frame-row.o"),
    ]
    if indicator_on:
        c_sources += [(XPAN_SRC_DIR / f"{name}.c", f"{name}.o") for name in ri.C_MODULES]
        c_sources.append((out / "rec-tone-bindings.c", "rec-tone-bindings.o"))
    for source_file, object_name in c_sources:
        run(
            "gcc", "-c", "-std=c11", "-Os", "-DR5_CROP_ONLY=1", *indicator_defines, "-mgeneral-regs-only",
            "-DOBS_FIRMWARE",
            "-mcpu=cortex-a9", "-mthumb", "-mno-unaligned-access", "-ffreestanding", "-fno-builtin",
            "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-fno-common",
            "-fstack-usage", "-Wall", "-Wextra", "-Werror", "-DOBSERVER_FIRMWARE",
            "-I", out, "-I", out / "frame-row", "-I", XPAN_RESIDENT_DIR, "-I", R5_221_SRC_DIR, "-I", XPAN_SRC_DIR,
            source_file, "-o", out / object_name,
        )
        objects.append(out / object_name)

    for source_name, object_name in (*XPAN_ASM_MODULES, *(ri.ASM_MODULES if indicator_on else ())):
        run(
            "gcc", "-c", "-x", "assembler-with-cpp", "-DR5_CROP_ONLY=1", *indicator_defines, "-mcpu=cortex-a9",
            "-marm",
            "-ffreestanding", "-nostdlib",
            "-I", out, "-I", XPAN_RESIDENT_DIR, "-I", R5_221_SRC_DIR, "-I", XPAN_SRC_DIR,
            XPAN_SRC_DIR / source_name, "-o", out / object_name,
        )
        objects.append(out / object_name)

    def compile_link() -> dict[str, int]:
        run(
            "gcc", "-c", "-x", "assembler-with-cpp", "-DR5_CROP_ONLY=1", *indicator_defines, "-mcpu=cortex-a9",
            "-marm", "-ffreestanding", "-nostdlib", "-Wa,--fatal-warnings",
            "-I", out, "-I", XPAN_RESIDENT_DIR, "-I", R5_221_SRC_DIR, "-I", XPAN_SRC_DIR,
            XPAN_RESIDENT_DIR / "handoff.S", "-o", out / "handoff.o",
        )
        run(
            "gcc", "-c", "-x", "assembler-with-cpp", "-mcpu=cortex-a9", "-marm", "-ffreestanding", "-nostdlib",
            R5_221_SRC_DIR / "footer.S", "-o", out / "footer.o",
        )
        run(
            "ld", "--no-undefined", "--no-warn-rwx-segments",
            "-T", out / "handoff.ld", "-Map=" + str(out / "handoff.map"),
            "-o", out / "handoff.elf", out / "handoff.o", *objects, out / "footer.o",
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

    final = compile_link()
    if final != symbols:
        raise ValueError("layout changed after startup patching")

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

    try:
        current_inputs = fingerprint()
    except OSError as exc:
        raise ValueError("build inputs changed") from exc
    if current_inputs != inputs:
        raise ValueError("build inputs changed")
    if hash_regular_file(rom_resolved, exact_bytes=rom_provenance["bytes"]) != actual_rom_sha256:
        raise ValueError("selected ROM changed during build")

    data = finalize_image(raw)
    facts = inspect_image(data)

    for forbidden_sym in FORBIDDEN_AUDIO_SYMBOLS:
        if forbidden_sym in symbols:
            raise ValueError(f"forbidden audio symbol retained in xpan-crop build: {forbidden_sym}")
    if indicator_on:
        indicator_record = ri.verify_linked(
            data,
            symbols,
            indicator_facts,
            manifest["audio"],
            minimum_user_left=manifest["minimum_user_left"],
            remaining_user_bytes=layout["remaining_user_bytes"],
        )
    else:
        indicator_record = None
        retained = [n for n in ri.ALL_SYMBOLS if n in symbols]
        if retained:
            raise ValueError("record indicator retained without opt-in: " + ", ".join(retained))

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
        0,
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
        "sound_worker_api",
        "sound_boot_record_boot_return",
        "sound_boot_record_startup_outcome",
        "sound_boot_context_is_ready",
        "sound_boot_start_worker",
        "sound_boot_report",
        "sound_boot_read_hw_context",
        "sound_boot_api",
        "sound_boot_state",
        *REVIEW_FIX_SYMBOLS,
    ):
        if not symbols["_resident_start"] <= symbols[sym] < symbols["_resident_data_end"]:
            raise ValueError(f"{sym} outside resident initialized section")
    if not symbols["_bss_start"] <= symbols["sound_worker_state"] < symbols["_private_stack_top"]:
        raise ValueError("sound_worker_state outside resident BSS")

    autoexec_path = out / "autoexec.bin"
    autoexec_path.write_bytes(data)

    caller_input_hashes = {
        "rom": {
            "caller_input_path": rom_caller_path,
            "resolved_path": str(rom_resolved),
            "bytes": rom_provenance["bytes"],
            "sha256": actual_rom_sha256,
            "word_sum": rom_provenance["word_sum"],
            "admission_mode": rom_provenance["admission_mode"],
        },
        "toolchain": {
            "caller_input_path": toolchain_admission["caller_input_path"],
            "resolved_path": toolchain_admission["resolved_path"],
            "target": toolchain_admission["target"],
            "gcc_version": toolchain_admission["gcc_version"],
            "executables": {k: v["sha256"] for k, v in toolchain_admission["executables"].items()},
        },
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
            "play_shutter": 0,
            "sleep_ms": sw["sleep_ms"],
        },
        "sound_boot_api_pointers": {
            "read_context": symbols["sound_boot_read_hw_context"] | 1,
            "start_worker": symbols["sound_worker_start"] | 1,
        },
        "worker_rows": ["worker_boot", "worker_task", "worker_playback", "worker_baseline", "worker_counters"],
    }

    record = {
        "profile": profile,
        "build_profile": "xpan-crop",
        "target": target_contract["target"],
        "output_image": str(autoexec_path),
        "caller_input_hashes": caller_input_hashes,
        "rom_admission": rom_provenance,
        "audio": None,
        "shutter_audio": None,
        "record_indicator": indicator_record,
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
            "path": witness_path,
            "fio_skipped": True,
            "write_buffer": write_buffer,
            "stack_policy": "Retain Canon init-task stack for scheduling calls; private stack only during early CamIF",
        },
        "observer": {
            **observer,
            "capture_trigger": False,
            "initial_notifications": "retained without classification",
            "observed_payload_scope": "first_u32_when_len_ge_4",
            "source_id": source_digest[:16],
            "native_execution_verified": False,
            "limitations": [
                "Canonical registration can assert on request OOM",
                "Stock logging/ACK internals have no proven nonblocking bound",
                "8-byte MechaCounter second word is outside FIRST_U32 prefix",
                "Headless XPan worker runs boot_mmu_tick only; shutter audio disabled"
                if not indicator_on
                else "XPan worker runs rec_tone_step then boot_mmu_tick; shutter audio disabled",
            ],
        },
    }
    (out / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    return record
