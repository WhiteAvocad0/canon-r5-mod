"""Build-side owner of the opt-in xpan-crop one-press screenshot (--screenshot).

RATE in playback saves the screen (the OSD as DCIM/R5SHOTS/SS_nnnnn.YCA and the
picture layer as SS_nnnnn.PIC), composites both into DCIM/R5SHOTS/HWJPG.Y22 and
starts Canon's own Resize job on the current image, whose JPEG encode reads that
file instead of the photo: a 2400x1600 in-camera JPEG of the whole screen. RATE in
live view saves the two files only. A click confirms the capture; two low bips
mean a refusal, three a cancelled job.

Admits the --screenshot choice, derives the click / bip facts from tone_ref.py,
renders capture-cue-bindings.c (and, without the record indicator, the shared
rec_tone API/config bindings) and checks the linked image. No ROM bytes are read
here; hook windows and guard pages are admitted by the strict target contracts
(tools/contracts.py) before any of this runs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import record_indicator as ri
import tone_ref

TOOLS_DIR = Path(__file__).resolve().parent

OFF = "off"
RATE = "rate"
CHOICES = (OFF, RATE)
FLAG = "--screenshot"
HOOK_EXTENSION = "screenshot-hooks.json"
DEFINE = "-DR5_SCREENSHOT=1"
# rec_tone (preflight only without the record indicator) and tone_gen are shared
# with the record indicator; build_xpan_image links each module once.
C_MODULES = (
    "tone_gen", "rec_tone", "combo_observe", "combo_capture", "hw_jpeg_inject", "y22_stream", "picture_layer",
    "one_press",
)
ASM_MODULES = (("combo_observe_hook.S", "combo-observe-hook.o"), ("hw_jpeg_inject_hook.S", "hw-jpeg-inject-hook.o"))
SOURCE_NAMES = (
    "combo_observe.h", "combo_observe.c", "combo_observe_hook.S", "combo_capture.h", "combo_capture.c",
    "hw_jpeg_inject.h", "hw_jpeg_inject.c", "hw_jpeg_inject_hook.S", "y22_stream.h", "y22_stream.c",
    "picture_layer.h", "picture_layer.c", "one_press.h", "one_press.c",
)
TOOL_NAMES = ("screenshot.py", HOOK_EXTENSION)

HOOKS = (
    (0xE04A8028, 8, "0146784808b58368", "combo_gui_dispatch_entry"),
    (0xE0CCB284, 8, "2de9f0479a460c46", "inject_encode_entry"),
    (0xE0549258, 10, "2de9f84306464ff49278", "inject_develop_end_entry"),
)
SYMBOLS = (
    "combo_gui_dispatch_entry", "combo_hook_ring", "combo_worker_tick", "combo_observe_state", "combo_observe_step",
    "combo_observe_report", "combo_observe_api", "capture_api",
    "capture_cue_segments", "capture_cues", "capture_cue_pcm", "capture_cue_descriptor",
    "inject_encode_entry", "inject_develop_end_entry", "inject_record", "inject_report", "inject_ring", "inject_api",
    "inject_band_api",
    "y22_stream_init", "y22_stream_fill", "inject_pending", "inject_guard", "inject_y22_clear", "inject_y22_ready",
    "inject_y22_build", "inject_build", "inject_dma_row", "inject_native_uncached", "inject_capture_begin",
    "inject_capture_end", "inject_shared_strip", "inject_shared_row", "inject_y22",
    "pl_sample", "pl_select", "capture_pl",
    "one_press_arm", "one_press_release", "one_press_done", "one_press_end", "one_press_step", "one_press_report",
    "one_press_state", "one_press_block", "one_press_api", "inject_pic_row", "y22_composite_row",
)
# The rec_tone / tone_gen symbols a screenshot-only build links (no record indicator engine).
SHARED_SYMBOLS = (
    "rec_tone_preflight", "rec_tone_api", "rec_tone_config", "rec_tone_native_read", "rec_tone_native_clean",
    "tone_gen_fill", "tone_sin_q15", "tone_segment_valid",
)
CUES = ("click", "bip_low")  # combo_capture.h CAPTURE_CUE_CLICK / CAPTURE_CUE_BIP
TRIGGER_CODE = 50  # RATE (combo_observe.h COMBO_TRIGGER_CODE)
Y22_PATH = "B:/DCIM/R5SHOTS/HWJPG.Y22"
Y22_BYTES = 7_680_000
STRIP_BYTES = 38_400
BAND_REQUEST = 1_179_648
# Linked sizes (bytes) of BSS objects the native code assumes (combo_observe.c,
# hw_jpeg_inject.c, one_press.c); each must sit 32-aligned in owned BSS.
BSS_OBJECTS = {"combo_hook_ring": 1028, "inject_ring": 16 * 96 + 32, "one_press_block": 284, "inject_pic_row": 4000}


def hook_inventory() -> dict:
    """The committed hook extension (three hooks + guard pages)."""
    return json.loads((TOOLS_DIR / HOOK_EXTENSION).read_text())


def admit_choice(choice: str | None) -> str:
    choice = OFF if choice is None else choice
    if choice not in CHOICES:
        raise ValueError(f"unknown {FLAG} {choice!r}; choose one of {', '.join(CHOICES)}")
    return choice


def enabled(choice: str) -> bool:
    return choice != OFF


def cue_facts() -> dict:
    """The click and low bip as 32-byte slices of one BSS buffer (tone_ref.SCREENSHOT_CUES)."""
    cues, segments, offset = [], [], 0
    for name in CUES:
        shape = tone_ref.SCREENSHOT_CUES[name]
        samples = tone_ref.shape_samples(shape)
        if not shape or not all(tone_ref.segment_valid(s) for s in shape) or samples % tone_ref.samples_ms(1):
            raise ValueError("screenshot cue shape: " + name)
        raw = tone_ref.generate_segments(shape, clamp=False)
        peak = max(abs(v) for v in raw)
        if peak > 32767:
            raise ValueError("screenshot cue saturates: " + name)
        padded = tone_ref.padded(samples)
        pcm = tone_ref.to_bytes(tone_ref.generate_segments(shape))
        cues.append({
            "name": name, "segment_first": len(segments), "segment_count": len(shape), "offset_samples": offset,
            "samples": samples, "padded_samples": padded, "bytes": 2 * padded,
            "duration_ms": samples // tone_ref.samples_ms(1), "peak_q15": peak,
            "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        })
        segments += shape
        offset += padded
    return {
        "cues": cues,
        "segment_total": len(segments),
        "segments_hex": b"".join(tone_ref.pack_segment(s) for s in segments).hex(),
        "capacity_samples": offset,
    }


def facts(choice: str) -> dict:
    choice = admit_choice(choice)
    if not enabled(choice):
        raise ValueError("screenshot facts need --screenshot rate")
    return {
        "trigger": choice,
        "trigger_code": TRIGGER_CODE,
        "cues": cue_facts(),
        "se_id": ri.SE_ID,
        "y22_path": Y22_PATH,
        "y22_bytes": Y22_BYTES,
        "band_request": BAND_REQUEST,
    }


def capture_cue_bindings(t: dict) -> str:
    """Render capture-cue-bindings.c: segments, the cue table, BSS PCM and pending descriptors."""
    f = t["cues"]
    raw = bytes.fromhex(f["segments_hex"])
    rows = []
    for i in range(f["segment_total"]):
        samples, count, attack, release = struct.unpack_from("<IBBBx", raw, 32 * i)
        parts = [struct.unpack_from("<IHH", raw, 32 * i + 8 + 8 * k) for k in range(3)]
        rows.append("    {%su, %du, %du, %du, 0u, {%s}},\n"
                    % (samples, count, attack, release, ", ".join("{%su, %du, %du}" % (hex(a), b, c) for a, b, c in parts)))
    desc = b"".join(ri.pending_descriptor(c) for c in f["cues"])
    words = struct.unpack("<%dI" % (16 * len(f["cues"])), desc)
    rodata = ' __attribute__((section(".resident_rodata")))'
    return "".join([
        "/* Generated by screenshot.py from tone_ref.SCREENSHOT_CUES click / bip_low. */\n",
        '#include "combo_capture.h"\n#include "rec_tone.h"\n',
        f'_Static_assert(CAPTURE_CUES == {len(f["cues"])}, "cue count");\n',
        f'_Static_assert(REC_TONE_SE_ID == {t["se_id"]}u, "SE id");\n',
        f"const struct tone_segment capture_cue_segments[{f['segment_total']}]{rodata} = {{\n", *rows, "};\n",
        f"const struct capture_cue capture_cues[CAPTURE_CUES]{rodata} = {{\n",
        *["    {%du, %du, %du, %du, %du},\n" % (c["segment_first"], c["segment_count"], c["offset_samples"],
                                          c["padded_samples"], c["bytes"]) for c in f["cues"]],
        "};\n",
        f"const uint32_t capture_cue_segment_total{rodata} = {f['segment_total']}u;\n",
        f"const uint32_t capture_cue_capacity{rodata} = {f['capacity_samples']}u;\n",
        f"int16_t capture_cue_pcm[{f['capacity_samples']}] __attribute__((aligned(32)));\n",
        'uint32_t capture_cue_descriptor[CAPTURE_CUES][16] __attribute__((section(".resident_data"), aligned(32))) = {\n',
        *["    {" + ", ".join(hex(v) + "u" for v in words[16 * i:16 * i + 16]) + "},\n" for i in range(len(f["cues"]))],
        "};\n",
    ])


def tone_api_bindings(audio: dict) -> str:
    """rec-tone-bindings.c for a screenshot-only build: the shared API and config only."""
    cfg = ri.config_words({"rec_state_word": ri.REC_STATE_WORD}, audio)
    return "".join([
        "/* Generated by screenshot.py from config/firmware.json (audio section): the\n"
        " * rec_tone API and config the screenshot cues use; no record indicator cue. */\n",
        '#include "rec_tone.h"\n#include "rec_state.h"\n',
        '_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");\n',
        f'_Static_assert(REC_TONE_SE_ID == {ri.SE_ID}u && REC_STATE_WORD == {hex(ri.REC_STATE_WORD)}u, "owner constants");\n',
        ri.api_config_definitions(cfg, audio),
    ])


def verify_linked(data: bytes, symbols: dict, sizes: dict, t: dict, hooks: list[dict], *, indicator: bool,
                  minimum_user_left: int, remaining_user_bytes: int) -> dict:
    """Check the finalized image against the encoders; return the linked record."""
    missing = [n for n in (*SYMBOLS, *SHARED_SYMBOLS) if n not in symbols]
    if missing:
        raise ValueError("screenshot symbols missing from opt-in build: " + ", ".join(missing))
    if remaining_user_bytes < minimum_user_left:
        raise ValueError("screenshot Canon heap floor")
    records = [(int(h["va"], 16), h["length"], h["original_hex"], h["destination"]) for h in hooks
               if h["destination"] in {d for *_, d in HOOKS}]
    if sorted(records) != sorted((va, n, o.lower(), d) for va, n, o, d in HOOKS):
        raise ValueError("screenshot hook records differ")
    bss = lambda name, size: symbols["_bss_start"] <= symbols[name] <= symbols[name] + size <= symbols["_bss_objects_end"]
    resident = lambda name: symbols["_resident_start"] <= symbols[name] < symbols["_resident_data_end"]
    for name, size in BSS_OBJECTS.items():
        if symbols[name] % 32 or not bss(name, size):
            raise ValueError(name + " outside aligned owned BSS")
    if symbols["inject_shared_strip"] % 32 or sizes.get("inject_shared_strip") != STRIP_BYTES or not bss(
            "inject_shared_strip", STRIP_BYTES):
        raise ValueError("shared strip must be one aligned 38400-byte BSS strip")
    if not bss("combo_worker_tick", 4):
        raise ValueError("combo_worker_tick outside owned BSS")
    for name in ("combo_gui_dispatch_entry", "inject_encode_entry", "inject_develop_end_entry", "inject_record",
                 "combo_observe_api", "capture_api", "inject_api", "inject_band_api", "one_press_api",
                 "capture_cue_segments", "capture_cues", "capture_cue_descriptor", "rec_tone_api", "rec_tone_config"):
        if not resident(name):
            raise ValueError(name + " outside resident initialized section")
    loc = lambda name: symbols["_resident_load_offset"] + symbols[name] - symbols["_resident_start"]
    f = t["cues"]
    pcm_at, desc_at = symbols["capture_cue_pcm"], symbols["capture_cue_descriptor"]
    if pcm_at % 32 or not bss("capture_cue_pcm", 2 * f["capacity_samples"]):
        raise ValueError("screenshot cue PCM outside aligned owned BSS")
    desc = b"".join(ri.pending_descriptor(c) for c in f["cues"])
    if desc_at % 32 or data[loc("capture_cue_descriptor"):loc("capture_cue_descriptor") + len(desc)] != desc:
        raise ValueError("screenshot cue descriptors differ from the encoder")
    seg = bytes.fromhex(f["segments_hex"])
    if data[loc("capture_cue_segments"):loc("capture_cue_segments") + len(seg)] != seg:
        raise ValueError("screenshot cue segments differ from tone_ref")
    return {
        **t,
        "indicator_shares_tone_engine": indicator,
        "hooks": [{"va": hex(va), "length": n, "original_hex": o, "destination": d, "destination_address": symbols[d],
                   "resume": hex(va + n + 1)} for va, n, o, d in HOOKS],
        "ring_address": symbols["combo_hook_ring"],
        "cue_pcm_address": pcm_at,
        "cue_descriptor_address": desc_at,
        "one_press_state_address": symbols["one_press_state"],
        "remaining_user_bytes": remaining_user_bytes,
        "hardware_verified": False,
    }
