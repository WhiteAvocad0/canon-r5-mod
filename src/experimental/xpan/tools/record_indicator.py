"""Build-side owner of the opt-in xpan-crop movie record indicator (issue #14).

Admits the --record-indicator (start cue) and --record-stop-indicator (stop cue)
choices, derives the cue facts from tone_ref.py (the host owner of every cue
shape), renders rec-tone-bindings.c from the config/firmware.json audio section,
and checks the linked image against the same encoders. No ROM bytes are read
here; hook windows and guard pages are admitted by the strict target contracts
(tools/contracts.py) before any of this runs.

Both cues share one observer hook, one worker and one set of fingerprinted
pages: a build with either cue (or both) links the same hook inventory, and a
build with neither links none of it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import tone_ref
from audio_profile import DESCRIPTOR_BYTES, DESCRIPTOR_PTR_OFFSET, descriptor

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent

OFF = "off"
DEFAULT_CUE = "ding"
CHOICES = (OFF, *tone_ref.VARIANTS)
STOP_CHOICES = (OFF, *tone_ref.STOP_VARIANTS)
# rec_tone_cues index (REC_TONE_CUE_START / REC_TONE_CUE_STOP) and catalog per role.
ROLES = ("start", "stop")
CATALOGS = {"start": tone_ref.VARIANTS, "stop": tone_ref.STOP_VARIANTS}
FLAGS = {"start": "--record-indicator", "stop": "--record-stop-indicator"}
HOOK_EXTENSION = "record-indicator-hooks.json"
DEFINE = "-DR5_RECORD_INDICATOR=1"
C_MODULES = ("rec_state", "tone_gen", "rec_tone")
ASM_MODULES = (("rec_state_hook.S", "rec-state-hook.o"),)
SOURCE_NAMES = (
    "rec_state.h", "rec_state.c", "rec_state_hook.S", "tone_gen.h", "tone_gen.c", "rec_tone.h", "rec_tone.c",
)
TOOL_NAMES = ("record_indicator.py", "tone_ref.py", "record_indicator_log.py", HOOK_EXTENSION)
# Linked whenever either cue is selected.
SYMBOLS = (
    "rec_state_entry", "rec_state_observe", "rec_press_seq", "rec_stop_seq",
    "rec_tone_step", "rec_tone_preflight", "rec_tone_report", "rec_tone_state", "rec_tone_api",
    "rec_tone_config", "rec_tone_cues", "rec_tone_native_read", "rec_tone_native_clean",
    "tone_gen_fill", "tone_sin_q15", "tone_segment_valid",
)
# Linked only with their own cue: (segments, pcm, descriptor).
CUE_SYMBOLS = {
    "start": ("rec_tone_segments", "rec_tone_pcm", "rec_tone_descriptor"),
    "stop": ("rec_tone_stop_segments", "rec_tone_stop_pcm", "rec_tone_stop_descriptor"),
}
# None of these may appear in a build with both cues off.
ALL_SYMBOLS = (*SYMBOLS, *(n for role in ROLES for n in CUE_SYMBOLS[role]))

# A cue longer than SE_PATIENCE_MS may be cut by a Canon SE requested mid-cue
# (E08B1CAC waits about 187 ms, then SE_BeepStop): a known trade-off of the
# 250 ms ding and dong, recorded per build. MAX_MS bounds each buffer.
SE_PATIENCE_MS = 150
MAX_MS = 250
SE_ID = 2
ACTRL_MASK = (1 << 1) | (1 << 4) | (1 << 5)
STOP_ACTRL_MASK = (1 << 1) | (1 << 4)
REC_STATE_MASK = (1 << 1) | (1 << 2) | (1 << 3)
STOP_STATE_MASK = 1 << 0
RETRY_MASK = (1 << 2) | (1 << 7)  # REC_TONE_PF_BUSY, REC_TONE_PF_ACTRL_STATE
STOP_RETRY_MASK = (1 << 2) | (1 << 7)
BUSY_TICKS = 10
OBSERVE_TICKS = 25
REC_STATE_WORD = 0x16DD4
REC_PRESS_CALLER = 0xE0B0B883
REC_CONFIRMED_CALLER = 0xE0B0BD2D
CONFIG_WORDS = 14
CUE_WORDS = 6  # struct rec_tone_cue on the 32-bit target
API_BYTES = 16


def hook_inventory() -> dict:
    """The committed hook extension (GUI_SetRecordingState entry + guard pages)."""
    return json.loads((TOOLS_DIR / HOOK_EXTENSION).read_text())


def admit_choice(choice: str | None, role: str = "start") -> str:
    choices = (OFF, *CATALOGS[role])
    choice = OFF if choice is None else choice
    if choice not in choices:
        raise ValueError(f"unknown {FLAGS[role]} {choice!r}; choose one of {', '.join(choices)}")
    return choice


def enabled(start: str, stop: str) -> bool:
    """Whether the hook, worker and pages are linked (either cue selected)."""
    return start != OFF or stop != OFF


def cue_facts(name: str, role: str) -> dict:
    """Admit one cue of one role and derive its build-time facts from tone_ref."""
    if role not in CATALOGS or name == OFF or name not in CATALOGS[role]:
        raise ValueError(f"unknown record indicator {role} cue {name!r}")
    shape = CATALOGS[role][name]
    if not shape or not all(tone_ref.segment_valid(seg) for seg in shape):
        raise ValueError("invalid record indicator shape: " + name)
    samples = tone_ref.shape_samples(shape)
    if samples % tone_ref.samples_ms(1) or samples > tone_ref.samples_ms(MAX_MS):
        raise ValueError("record indicator cue length: " + name)
    raw = tone_ref.generate_segments(shape, clamp=False)
    peak = max(abs(v) for v in raw)
    if peak > 32767:
        raise ValueError("record indicator cue saturates: " + name)
    pcm = tone_ref.to_bytes(tone_ref.generate_segments(shape))
    padded = tone_ref.padded(samples)
    duration_ms = samples // tone_ref.samples_ms(1)
    return {
        "cue": name,
        "role": role,
        "segment_count": len(shape),
        "segments_hex": b"".join(tone_ref.pack_segment(seg) for seg in shape).hex(),
        "samples": samples,
        "padded_samples": padded,
        "bytes": 2 * padded,
        "duration_ms": duration_ms,
        "peak_q15": peak,
        "exceeds_se_patience": duration_ms > SE_PATIENCE_MS,
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
    }


def facts(start: str, stop: str = OFF) -> dict:
    """Admit the start/stop selection and derive every build-time fact."""
    start, stop = admit_choice(start, "start"), admit_choice(stop, "stop")
    if not enabled(start, stop):
        raise ValueError("record indicator facts need a start or stop cue")
    return {
        "cue": start,
        "stop_cue": stop,
        "cues": {
            role: cue_facts(name, role) if name != OFF else None for role, name in (("start", start), ("stop", stop))
        },
        "trigger": "press",
        "rec_press_caller": REC_PRESS_CALLER,
        "stop_trigger": "idle after confirmed start",
        "rec_confirmed_caller": REC_CONFIRMED_CALLER,
        "rec_state_word": REC_STATE_WORD,
        "rec_state_mask": REC_STATE_MASK,
        "stop_state_mask": STOP_STATE_MASK,
        "retry_mask": RETRY_MASK,
        "stop_retry_mask": STOP_RETRY_MASK,
        "se_id": SE_ID,
        "actrl_mask": ACTRL_MASK,
        "stop_actrl_mask": STOP_ACTRL_MASK,
        "busy_ticks": BUSY_TICKS,
        "observe_ticks": OBSERVE_TICKS,
        "descriptor_bytes": DESCRIPTOR_BYTES,
    }


def pending_descriptor(cue: dict) -> bytes:
    """Canonical ABeep descriptor with PTR 0: unavailable until the worker publishes."""
    data = bytearray(descriptor(32, cue["bytes"]))
    struct.pack_into("<I", data, DESCRIPTOR_PTR_OFFSET, 0)
    return bytes(data)


def config_words(t: dict, audio: dict) -> tuple[int, ...]:
    """Exact struct rec_tone_config image; every slot comes from firmware.json audio."""
    words = (
        t["rec_state_word"],
        audio["singleton_slot"], audio["audioctrl_slot"], audio["audioctrl_handle_offset"],
        audio["audio_ic_slot"], audio["audio_ic_init_offset"], audio["output_context_slot"],
        audio["icu_cache_base"], audio["icu_cache_kind_offset"], audio["icu_cache_init_offset"],
        audio["icu_kind_vaif"], audio["icu_kind_ak"], audio["ak_param_slot"], audio["vaif_param_slot"],
    )
    if len(words) != CONFIG_WORDS or any(type(w) is not int or not 0 <= w <= 0xFFFFFFFF for w in words):
        raise ValueError("record indicator config words")
    return words


def cue_words(t: dict, symbols: dict) -> tuple[int, ...]:
    """Exact rec_tone_cues image (both roles) for the linked symbol addresses."""
    words: list[int] = []
    for role in ROLES:
        cue = t["cues"][role]
        if cue is None:
            words += [0] * CUE_WORDS
        else:
            words += [*(symbols[n] for n in CUE_SYMBOLS[role]), cue["segment_count"], cue["padded_samples"],
                      cue["bytes"]]
    return tuple(words)


def segment_initializer(cue: dict) -> str:
    """C initializer rows of one cue's segments from the packed tone_ref image."""
    raw = bytes.fromhex(cue["segments_hex"])
    rows = []
    for i in range(cue["segment_count"]):
        samples, count, attack, release = struct.unpack_from("<IBBBx", raw, 32 * i)
        partials = [struct.unpack_from("<IHH", raw, 32 * i + 8 + 8 * k) for k in range(3)]
        rows.append(
            "    {%su, %du, %du, %du, 0u, {%s}},\n"
            % (samples, count, attack, release, ", ".join("{%su, %du, %du}" % (hex(a), b, c) for a, b, c in partials))
        )
    return "".join(rows)


def cue_definitions(t: dict, *, firmware: bool = True) -> str:
    """Segments, aligned BSS PCM, pending descriptor and the rec_tone_cues table.

    firmware=False drops the resident section attributes (host tests)."""
    rodata = ' __attribute__((section(".resident_rodata")))' if firmware else ""
    desc_attr = ' __attribute__((section(".resident_data"), aligned(32)))' if firmware else " __attribute__((aligned(32)))"
    parts, entries = [], []
    for role in ROLES:
        cue = t["cues"][role]
        if cue is None:
            entries.append("    {0, 0, 0, 0u, 0u, 0u},\n")
            continue
        segs, pcm, desc_name = CUE_SYMBOLS[role]
        desc = struct.unpack("<16I", pending_descriptor(cue))
        parts += [
            f"const struct tone_segment {segs}[{cue['segment_count']}]{rodata} = {{\n",
            segment_initializer(cue),
            "};\n",
            f"int16_t {pcm}[{cue['padded_samples']}] __attribute__((aligned(32)));\n",
            f"uint32_t {desc_name}[REC_TONE_DESC_WORDS]{desc_attr} = {{\n",
            "    " + ", ".join(hex(v) + "u" for v in desc) + ",\n};\n",
        ]
        entries.append(
            f"    {{{segs}, {pcm}, {desc_name}, {cue['segment_count']}u, {cue['padded_samples']}u, {cue['bytes']}u}},\n"
        )
    parts += [f"const struct rec_tone_cue rec_tone_cues[REC_TONE_CUES]{rodata} = {{\n", *entries, "};\n"]
    return "".join(parts)


def bindings_source(t: dict, audio: dict) -> str:
    """Render rec-tone-bindings.c from the owning firmware.json audio section."""
    for key in ("state_query", "beep_start"):
        if type(audio.get(key)) is not int or not audio[key] & 1:
            raise ValueError("invalid Thumb address for audio." + key)
    cfg = config_words(t, audio)
    padded = "".join(
        f"_Static_assert(TONE_PADDED_SAMPLES(TONE_SAMPLES_MS({c['duration_ms']}u)) == {c['padded_samples']}u, "
        f'"{role} padded count");\n'
        for role, c in t["cues"].items() if c is not None
    )
    return "".join([
        "/* Generated by record_indicator.py from config/firmware.json (audio section). */\n",
        '#include "rec_tone.h"\n#include "rec_state.h"\n',
        '_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");\n',
        f'_Static_assert(sizeof(struct rec_tone_cue) == {4 * CUE_WORDS}u, "rec_tone cue ABI");\n',
        padded,
        f"_Static_assert(REC_TONE_ACTRL_MASK == {hex(t['actrl_mask'])}u && REC_TONE_STOP_ACTRL_MASK == "
        f"{hex(t['stop_actrl_mask'])}u && REC_STATE_WORD == {hex(t['rec_state_word'])}u, \"owner constants\");\n",
        f"_Static_assert(REC_TONE_SE_ID == {t['se_id']}u && REC_TONE_REC_STATE_MASK == {hex(t['rec_state_mask'])}u "
        f"&& REC_TONE_STOP_STATE_MASK == {hex(t['stop_state_mask'])}u, \"submission policy\");\n",
        f"_Static_assert(REC_TONE_RETRY_MASK == {hex(t['retry_mask'])}u && REC_TONE_STOP_RETRY_MASK == "
        f"{hex(t['stop_retry_mask'])}u, \"retry policy\");\n",
        f"_Static_assert(REC_TONE_BUSY_TICKS == {t['busy_ticks']}u && REC_TONE_OBSERVE_TICKS == "
        f"{t['observe_ticks']}u, \"tick bounds\");\n",
        f"_Static_assert(REC_PRESS_CALLER == {hex(t['rec_press_caller'])}u && REC_CONFIRMED_CALLER == "
        f"{hex(t['rec_confirmed_caller'])}u, \"trigger callers\");\n",
        '__attribute__((section(".resident_text")))\n',
        "static uint32_t rec_tone_native_read(uint32_t address)\n{\n"
        "    return *(const volatile uint32_t *)(uintptr_t)address;\n}\n",
        '__attribute__((section(".resident_text")))\n',
        "static void rec_tone_native_clean(const volatile void *start, uint32_t bytes)\n{\n",
        "    uint32_t line = (uint32_t)(uintptr_t)start, end = line + bytes;\n    line &= ~31u;\n",
        '    __asm__ volatile("dsb sy" ::: "memory");\n',
        "    for (; line < end; line += 32u)\n"
        '        __asm__ volatile("mcr p15,0,%0,c7,c10,1" :: "r"(line) : "memory");\n',
        '    __asm__ volatile("dsb sy" ::: "memory");\n}\n',
        'const struct rec_tone_api rec_tone_api __attribute__((section(".resident_rodata"))) = {\n',
        "    rec_tone_native_read,\n",
        f"    (rec_tone_query_fn){hex(audio['state_query'])}u,\n",
        f"    (rec_tone_beep_fn){hex(audio['beep_start'])}u,\n",
        "    rec_tone_native_clean,\n};\n",
        'const struct rec_tone_config rec_tone_config __attribute__((section(".resident_rodata"))) = {\n',
        "    " + ", ".join(hex(v) + "u" for v in cfg) + ",\n};\n",
        cue_definitions(t),
    ])


def verify_linked(data: bytes, symbols: dict, t: dict, audio: dict, *, minimum_user_left: int,
                  remaining_user_bytes: int) -> dict:
    """Check the finalized image against the encoders; return the linked record."""
    linked = [role for role in ROLES if t["cues"][role] is not None]
    missing = [n for n in (*SYMBOLS, *(n for role in linked for n in CUE_SYMBOLS[role])) if n not in symbols]
    if missing:
        raise ValueError("record indicator symbols missing from opt-in build: " + ", ".join(missing))
    unselected = [n for role in ROLES if role not in linked for n in CUE_SYMBOLS[role] if n in symbols]
    if unselected:
        raise ValueError("record indicator cue linked without its option: " + ", ".join(unselected))
    if remaining_user_bytes < minimum_user_left:
        raise ValueError("record indicator Canon heap floor")
    loc = lambda name: symbols["_resident_load_offset"] + symbols[name] - symbols["_resident_start"]
    resident = lambda name, size: symbols["_resident_start"] <= symbols[name] <= symbols[name] + size <= symbols[
        "_resident_data_end"]
    cues = {}
    for role in ROLES:
        cue = t["cues"][role]
        if cue is None:
            cues[role] = None
            continue
        segs, pcm, desc_name = CUE_SYMBOLS[role]
        pcm_at, desc_at = symbols[pcm], symbols[desc_name]
        if pcm_at % 32 or not symbols["_bss_start"] <= pcm_at < pcm_at + cue["bytes"] <= symbols["_bss_objects_end"]:
            raise ValueError(f"record indicator {role} PCM outside aligned owned BSS")
        if desc_at % 32 or not resident(desc_name, t["descriptor_bytes"]):
            raise ValueError(f"record indicator {role} descriptor outside aligned resident data")
        desc = pending_descriptor(cue)
        if data[loc(desc_name) : loc(desc_name) + len(desc)] != desc:
            raise ValueError(f"linked record indicator {role} descriptor differs from encoder")
        seg = bytes.fromhex(cue["segments_hex"])
        if not resident(segs, len(seg)) or data[loc(segs) : loc(segs) + len(seg)] != seg:
            raise ValueError(f"linked record indicator {role} segments differ from tone_ref")
        cues[role] = {
            **cue,
            "pcm_address": pcm_at,
            "pcm_bss": True,
            "descriptor_address": desc_at,
            "descriptor_hex": desc.hex(),
            "segments_address": symbols[segs],
        }
    for name in ("rec_tone_config", "rec_tone_cues", "rec_tone_api", "rec_state_entry", "rec_tone_step"):
        if not symbols["_resident_start"] <= symbols[name] < symbols["_resident_data_end"]:
            raise ValueError(name + " outside resident initialized section")
    cfg = struct.pack(f"<{CONFIG_WORDS}I", *config_words(t, audio))
    if data[loc("rec_tone_config") : loc("rec_tone_config") + len(cfg)] != cfg:
        raise ValueError("linked record indicator config differs")
    table = struct.pack(f"<{2 * CUE_WORDS}I", *cue_words(t, symbols))
    if data[loc("rec_tone_cues") : loc("rec_tone_cues") + len(table)] != table:
        raise ValueError("linked record indicator cue table differs")
    api = struct.pack(
        "<4I", symbols["rec_tone_native_read"] | 1, audio["state_query"], audio["beep_start"],
        symbols["rec_tone_native_clean"] | 1,
    )
    if data[loc("rec_tone_api") : loc("rec_tone_api") + API_BYTES] != api:
        raise ValueError("linked record indicator API table differs")
    return {
        **t,
        "cues": cues,
        "config_address": symbols["rec_tone_config"],
        "cue_table_address": symbols["rec_tone_cues"],
        "hook_entry": symbols["rec_state_entry"],
        "api": {
            "read": symbols["rec_tone_native_read"] | 1,
            "query": audio["state_query"],
            "beep_start": audio["beep_start"],
            "clean": symbols["rec_tone_native_clean"] | 1,
        },
    }
