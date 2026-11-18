"""Build-side owner of the opt-in xpan-crop movie record indicator (issue #14).

Admits the --record-indicator choice, derives the cue facts from tone_ref.py (the
host owner of every cue shape), renders rec-tone-bindings.c from the
config/firmware.json audio section, and checks the linked image against the same
encoders. No ROM bytes are read here; hook windows and guard pages are admitted by
the strict target contracts (tools/contracts.py) before any of this runs.
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
HOOK_EXTENSION = "record-indicator-hooks.json"
DEFINE = "-DR5_RECORD_INDICATOR=1"
C_MODULES = ("rec_state", "tone_gen", "rec_tone")
ASM_MODULES = (("rec_state_hook.S", "rec-state-hook.o"),)
SOURCE_NAMES = (
    "rec_state.h", "rec_state.c", "rec_state_hook.S", "tone_gen.h", "tone_gen.c", "rec_tone.h", "rec_tone.c",
)
TOOL_NAMES = ("record_indicator.py", "tone_ref.py", "record_indicator_log.py", HOOK_EXTENSION)
# Every symbol the opt-in links; none may appear in a build with the indicator off.
SYMBOLS = (
    "rec_state_entry", "rec_state_observe", "rec_press_seq",
    "rec_tone_step", "rec_tone_preflight", "rec_tone_report", "rec_tone_state", "rec_tone_api",
    "rec_tone_config", "rec_tone_pcm", "rec_tone_descriptor", "rec_tone_segments",
    "rec_tone_native_read", "rec_tone_native_clean", "tone_gen_fill", "tone_sin_q15",
)

# A cue longer than SE_PATIENCE_MS may be cut by a Canon SE requested mid-cue
# (E08B1CAC waits about 187 ms, then SE_BeepStop): a known trade-off of the
# 250 ms ding, recorded per build. MAX_MS bounds the buffer.
SE_PATIENCE_MS = 150
MAX_MS = 250
SE_ID = 2
ACTRL_MASK = (1 << 1) | (1 << 4) | (1 << 5)
REC_STATE_MASK = (1 << 1) | (1 << 2) | (1 << 3)
RETRY_MASK = (1 << 2) | (1 << 7)  # REC_TONE_PF_BUSY, REC_TONE_PF_ACTRL_STATE
BUSY_TICKS = 10
OBSERVE_TICKS = 25
REC_STATE_WORD = 0x16DD4
REC_PRESS_CALLER = 0xE0B0B883
CONFIG_WORDS = 17
API_BYTES = 16


def hook_inventory() -> dict:
    """The committed hook extension (GUI_SetRecordingState entry + guard pages)."""
    return json.loads((TOOLS_DIR / HOOK_EXTENSION).read_text())


def admit_choice(choice: str | None) -> str:
    choice = OFF if choice is None else choice
    if choice not in CHOICES:
        raise ValueError(f"unknown --record-indicator {choice!r}; choose one of {', '.join(CHOICES)}")
    return choice


def facts(cue: str) -> dict:
    """Admit one cue and derive every build-time fact from tone_ref."""
    if cue == OFF or cue not in tone_ref.VARIANTS:
        raise ValueError(f"unknown record indicator cue {cue!r}")
    shape = tone_ref.VARIANTS[cue]
    if not shape or not all(tone_ref.segment_valid(seg) for seg in shape):
        raise ValueError("invalid record indicator shape: " + cue)
    samples = tone_ref.shape_samples(shape)
    if samples % tone_ref.samples_ms(1) or samples > tone_ref.samples_ms(MAX_MS):
        raise ValueError("record indicator cue length: " + cue)
    raw = tone_ref.generate_segments(shape, clamp=False)
    peak = max(abs(v) for v in raw)
    if peak > 32767:
        raise ValueError("record indicator cue saturates: " + cue)
    pcm = tone_ref.to_bytes(tone_ref.generate_segments(shape))
    padded = tone_ref.padded(samples)
    duration_ms = samples // tone_ref.samples_ms(1)
    return {
        "cue": cue,
        "segment_count": len(shape),
        "segments_hex": b"".join(tone_ref.pack_segment(seg) for seg in shape).hex(),
        "samples": samples,
        "padded_samples": padded,
        "bytes": 2 * padded,
        "duration_ms": duration_ms,
        "peak_q15": peak,
        "exceeds_se_patience": duration_ms > SE_PATIENCE_MS,
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        "trigger": "press",
        "rec_press_caller": REC_PRESS_CALLER,
        "rec_state_word": REC_STATE_WORD,
        "rec_state_mask": REC_STATE_MASK,
        "retry_mask": RETRY_MASK,
        "se_id": SE_ID,
        "actrl_mask": ACTRL_MASK,
        "busy_ticks": BUSY_TICKS,
        "observe_ticks": OBSERVE_TICKS,
        "descriptor_bytes": DESCRIPTOR_BYTES,
    }


def pending_descriptor(t: dict) -> bytes:
    """Canonical ABeep descriptor with PTR 0: unavailable until the worker publishes."""
    data = bytearray(descriptor(32, t["bytes"]))
    struct.pack_into("<I", data, DESCRIPTOR_PTR_OFFSET, 0)
    return bytes(data)


def config_words(t: dict, audio: dict) -> tuple[int, ...]:
    """Exact struct rec_tone_config image; every slot comes from firmware.json audio."""
    words = (
        t["segment_count"], t["padded_samples"], t["bytes"], t["rec_state_word"],
        audio["singleton_slot"], audio["audioctrl_slot"], audio["audioctrl_handle_offset"],
        audio["audio_ic_slot"], audio["audio_ic_init_offset"], audio["output_context_slot"],
        audio["icu_cache_base"], audio["icu_cache_kind_offset"], audio["icu_cache_init_offset"],
        audio["icu_kind_vaif"], audio["icu_kind_ak"], audio["ak_param_slot"], audio["vaif_param_slot"],
    )
    if len(words) != CONFIG_WORDS or any(type(w) is not int or not 0 <= w <= 0xFFFFFFFF for w in words):
        raise ValueError("record indicator config words")
    return words


def segment_initializer(t: dict) -> str:
    """C initializer rows of rec_tone_segments from the packed tone_ref image."""
    raw = bytes.fromhex(t["segments_hex"])
    rows = []
    for i in range(t["segment_count"]):
        samples, count, attack, release = struct.unpack_from("<IBBBx", raw, 32 * i)
        partials = [struct.unpack_from("<IHH", raw, 32 * i + 8 + 8 * k) for k in range(3)]
        rows.append(
            "    {%su, %du, %du, %du, 0u, {%s}},\n"
            % (samples, count, attack, release, ", ".join("{%su, %du, %du}" % (hex(a), b, c) for a, b, c in partials))
        )
    return "".join(rows)


def bindings_source(t: dict, audio: dict) -> str:
    """Render rec-tone-bindings.c from the owning firmware.json audio section."""
    for key in ("state_query", "beep_start"):
        if type(audio.get(key)) is not int or not audio[key] & 1:
            raise ValueError("invalid Thumb address for audio." + key)
    desc = struct.unpack("<16I", pending_descriptor(t))
    cfg = config_words(t, audio)
    return "".join([
        "/* Generated by record_indicator.py from config/firmware.json (audio section). */\n",
        '#include "rec_tone.h"\n#include "rec_state.h"\n',
        '_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");\n',
        f"_Static_assert(TONE_PADDED_SAMPLES(TONE_SAMPLES_MS({t['duration_ms']}u)) == {t['padded_samples']}u, "
        '"padded count");\n',
        f"_Static_assert(REC_TONE_ACTRL_MASK == {hex(t['actrl_mask'])}u && REC_STATE_WORD == "
        f"{hex(t['rec_state_word'])}u, \"owner constants\");\n",
        f"_Static_assert(REC_TONE_SE_ID == {t['se_id']}u && REC_TONE_REC_STATE_MASK == {hex(t['rec_state_mask'])}u "
        f"&& REC_TONE_RETRY_MASK == {hex(t['retry_mask'])}u, \"submission policy\");\n",
        f"_Static_assert(REC_TONE_BUSY_TICKS == {t['busy_ticks']}u && REC_TONE_OBSERVE_TICKS == "
        f"{t['observe_ticks']}u, \"tick bounds\");\n",
        f"_Static_assert(REC_PRESS_CALLER == {hex(t['rec_press_caller'])}u, \"press caller\");\n",
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
        f"const struct tone_segment rec_tone_segments[{t['segment_count']}] "
        '__attribute__((section(".resident_rodata"))) = {\n',
        segment_initializer(t),
        "};\n",
        'const struct rec_tone_config rec_tone_config __attribute__((section(".resident_rodata"))) = {\n',
        "    " + ", ".join(hex(v) + "u" for v in cfg) + ",\n};\n",
        f"int16_t rec_tone_pcm[{t['padded_samples']}] __attribute__((aligned(32)));\n",
        'uint32_t rec_tone_descriptor[REC_TONE_DESC_WORDS] __attribute__((section(".resident_data"), aligned(32))) = {\n',
        "    " + ", ".join(hex(v) + "u" for v in desc) + ",\n};\n",
    ])


def verify_linked(data: bytes, symbols: dict, t: dict, audio: dict, *, minimum_user_left: int,
                  remaining_user_bytes: int) -> dict:
    """Check the finalized image against the encoders; return the linked record."""
    missing = [n for n in SYMBOLS if n not in symbols]
    if missing:
        raise ValueError("record indicator symbols missing from opt-in build: " + ", ".join(missing))
    if remaining_user_bytes < minimum_user_left:
        raise ValueError("record indicator Canon heap floor")
    loc = lambda name: symbols["_resident_load_offset"] + symbols[name] - symbols["_resident_start"]
    pcm_at, desc_at = symbols["rec_tone_pcm"], symbols["rec_tone_descriptor"]
    if pcm_at % 32 or not symbols["_bss_start"] <= pcm_at < pcm_at + t["bytes"] <= symbols["_bss_objects_end"]:
        raise ValueError("record indicator PCM outside aligned owned BSS")
    if desc_at % 32 or not (
        symbols["_resident_start"] <= desc_at < desc_at + t["descriptor_bytes"] <= symbols["_resident_data_end"]
    ):
        raise ValueError("record indicator descriptor outside aligned resident data")
    for name in ("rec_tone_config", "rec_tone_segments", "rec_tone_api", "rec_state_entry", "rec_tone_step"):
        if not symbols["_resident_start"] <= symbols[name] < symbols["_resident_data_end"]:
            raise ValueError(name + " outside resident initialized section")
    desc = pending_descriptor(t)
    if data[loc("rec_tone_descriptor") : loc("rec_tone_descriptor") + len(desc)] != desc:
        raise ValueError("linked record indicator descriptor differs from encoder")
    cfg = struct.pack(f"<{CONFIG_WORDS}I", *config_words(t, audio))
    if data[loc("rec_tone_config") : loc("rec_tone_config") + len(cfg)] != cfg:
        raise ValueError("linked record indicator config differs")
    seg = bytes.fromhex(t["segments_hex"])
    if data[loc("rec_tone_segments") : loc("rec_tone_segments") + len(seg)] != seg:
        raise ValueError("linked record indicator segments differ from tone_ref")
    api = struct.pack(
        "<4I", symbols["rec_tone_native_read"] | 1, audio["state_query"], audio["beep_start"],
        symbols["rec_tone_native_clean"] | 1,
    )
    if data[loc("rec_tone_api") : loc("rec_tone_api") + API_BYTES] != api:
        raise ValueError("linked record indicator API table differs")
    return {
        **t,
        "pcm_address": pcm_at,
        "pcm_bss": True,
        "descriptor_address": desc_at,
        "descriptor_hex": desc.hex(),
        "config_address": symbols["rec_tone_config"],
        "segments_address": symbols["rec_tone_segments"],
        "hook_entry": symbols["rec_state_entry"],
        "api": {
            "read": symbols["rec_tone_native_read"] | 1,
            "query": audio["state_query"],
            "beep_start": audio["beep_start"],
            "clean": symbols["rec_tone_native_clean"] | 1,
        },
    }
