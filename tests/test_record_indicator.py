"""Host tests for the opt-in xpan-crop movie record indicator (issue #14).

No ROM, camera, card or ARM toolchain: the actual tone_gen.c, rec_state.c and
rec_tone.c are compiled with the host C compiler and driven through an injected
fake ABeep/AudioCtrl API. Real-ROM build verification is separately opt-in
(set R5_RECORD_INDICATOR_BUILD to a build directory made with --record-indicator).
"""
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
XPAN_SRC = ROOT / "src" / "experimental" / "xpan"
XPAN_TOOLS = XPAN_SRC / "tools"
R5_221_SRC = ROOT / "src" / "r5_221"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(XPAN_TOOLS))

import feature_contract  # noqa: E402
import record_indicator as ri  # noqa: E402
import record_indicator_log  # noqa: E402
import tone_ref  # noqa: E402
from contracts import MANIFEST, XPAN_TARGET_CONTRACT, TARGET_CONTRACT, validate_xpan_target_contract_consistency  # noqa: E402
from host_cc import find_host_cc  # noqa: E402
from ram_capture_log import RECORD_BYTES, decode_ram_records  # noqa: E402
from test_xpan_models_logs import _pack_kuwa, _pack_record  # noqa: E402

AUDIO = MANIFEST["audio"]
SOURCE = "0123456789abcdef"
# The cue PCM of the private lab build the owner tested (ding, 12000 samples).
DING_PCM_SHA256 = "50ed6d35b3fa6b384a45fc1a2daafe34f7c15d693e4647850c0f0acddcd89c3f"
PF = dict(OK=0, NOT_READY=1, BUSY=2, STOCK_OWNED=3, STATE_INVALID=4, ACTRL_MISSING=5, ACTRL_INVALID=6,
          ACTRL_STATE=7, AUDIO_IC=8, OUTPUT_CTX=9, ICU_CACHE=10, PARAM_TABLE=11)
OUT = dict(NONE=0, SETTLED=1, STOPPED=2, REJECTED=3, TIMEOUT=4, CONTEXT_CHANGED=5, UNEXPECTED_PHASE=6,
           STATE_INVALID=7, BUFFER_CHANGED=8, SUBMIT_NONZERO=9)
U = ctypes.c_uint32
I = ctypes.c_int32
_TMP = tempfile.TemporaryDirectory(prefix="r5-record-indicator-")


def _compile(name, sources, extra=()):
    lib = Path(_TMP.name) / (name + ".so")
    subprocess.run([find_host_cc(), "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", *extra,
                    "-I", _TMP.name, "-I", str(XPAN_SRC), "-I", str(R5_221_SRC), *map(str, sources), "-o", str(lib)],
                   check=True)
    return ctypes.CDLL(str(lib))


# --- tone_gen.c against tone_ref.py ----------------------------------------

class Partial(ctypes.Structure):
    _fields_ = [("phase_inc", U), ("amplitude_q15", ctypes.c_uint16), ("decay", ctypes.c_uint16)]


class Segment(ctypes.Structure):
    _fields_ = [("samples", U), ("partial_count", ctypes.c_uint8), ("attack_shift", ctypes.c_uint8),
                ("release_shift", ctypes.c_uint8), ("reserved", ctypes.c_uint8), ("partials", Partial * 3)]


_TONE = None


def tone_lib():
    global _TONE
    if _TONE is None:
        _TONE = _compile("tone_gen", [XPAN_SRC / "tone_gen.c"])
        _TONE.tone_gen_fill.restype = U
        _TONE.tone_gen_fill.argtypes = [ctypes.c_void_p, U, ctypes.c_void_p, U]
        _TONE.tone_sin_q15.restype = I
        _TONE.tone_sin_q15.argtypes = [U]
        _TONE.tone_segment_valid.argtypes = [ctypes.c_void_p]
    return _TONE


def c_segments_raw(raw, count):
    arr = (Segment * max(count, 1))()
    ctypes.memmove(arr, raw, len(raw))
    return arr


def c_fill(segments, capacity=None, offset=0, raw=None, count=None):
    raw = b"".join(tone_ref.pack_segment(s) for s in segments) if raw is None else raw
    count = len(segments) if count is None else count
    arr = c_segments_raw(raw, count)
    total = tone_ref.padded(sum(s[0] for s in segments)) if segments else 16
    capacity = total if capacity is None else capacity
    buf = ctypes.create_string_buffer(b"\x5a" * (2 * (capacity + 64) + 64))
    base = (ctypes.addressof(buf) + 31) & ~31
    n = tone_lib().tone_gen_fill(base + 2 * offset, capacity, ctypes.addressof(arr), count)
    data = ctypes.string_at(base + 2 * offset, 2 * (capacity + 16))
    return n, list(struct.unpack("<%dh" % (capacity + 16), data))


class ToneGenTests(unittest.TestCase):
    def test_sine_matches_python_and_accuracy_bound(self):
        lib = tone_lib()
        for x in list(range(0, 1 << 15, 7)) + [32767]:
            for q in range(4):
                phase = (q << 30) | (x << 15)
                self.assertEqual(lib.tone_sin_q15(phase), tone_ref.sin_q15(phase))
        self.assertLessEqual(tone_ref.sin_max_error_lsb(), 3.72)

    def test_catalog_cues_c_bit_exact_padded_and_unsaturated(self):
        for name, shape in tone_ref.VARIANTS.items():
            want = tone_ref.generate_segments(shape)
            n, got = c_fill(shape)
            self.assertEqual(n, len(want), name)
            self.assertEqual(got[:n], want, name)
            self.assertEqual(got[n:], [0x5A5A] * 16, name + ": wrote past the padded count")
            self.assertEqual(tone_ref.generate_segments(shape, clamp=False), want, name + " saturates")
            self.assertLessEqual(max(abs(v) for v in want), 31000, name)
            self.assertEqual(n % 16, 0)

    def test_ding_is_the_private_build_cue(self):
        pcm = tone_ref.to_bytes(tone_ref.generate_segments(tone_ref.VARIANTS["ding"]))
        self.assertEqual(hashlib.sha256(pcm).hexdigest(), DING_PCM_SHA256)
        t = ri.facts("ding")
        self.assertEqual((t["samples"], t["padded_samples"], t["bytes"], t["duration_ms"]), (12000, 12000, 24000, 250))
        self.assertEqual(t["pcm_sha256"], DING_PCM_SHA256)
        self.assertTrue(t["exceeds_se_patience"])

    def test_ding_shape_definition_and_decay(self):
        (samples, attack, release, partials), = tone_ref.VARIANTS["ding"]
        self.assertEqual((samples, attack, release), (12000, 7, 8))
        hz = [tone_ref.effective_hz(i) for i, _, _ in partials]
        self.assertAlmostEqual(hz[1] / hz[0], 2.756, places=3)
        self.assertAlmostEqual(hz[2] / hz[0], 5.404, places=3)
        amps = [a for _, a, _ in partials]
        self.assertAlmostEqual(20 * math.log10(amps[1] / amps[0]), -9, delta=0.05)
        self.assertAlmostEqual(20 * math.log10(amps[2] / amps[0]), -18, delta=0.05)
        values = tone_ref.generate_segments(tone_ref.VARIANTS["ding"])
        self.assertEqual(values[0], 0)
        self.assertEqual(values[samples - 1], 0)
        peak_at = max(range(len(values)), key=lambda k: abs(values[k]))
        self.assertLess(peak_at, 400)
        # The fundamental envelope arithmetic follows e^(-t/tau), tau = 65536/decay samples.
        env = tone_ref.ENV_ONE
        for _ in range(4800):
            env -= (env >> 16) * 20
        self.assertAlmostEqual(env / tone_ref.ENV_ONE, math.exp(-4800 * 20 / 65536), delta=0.01)

    def test_single_tone_equals_the_independent_oracle(self):
        (samples, fade, _, ((inc, amp, _),)), = tone_ref.VARIANTS["2k150loud"]
        self.assertEqual(tone_ref.generate_segments(tone_ref.VARIANTS["2k150loud"]),
                         tone_ref.generate(inc, samples, fade, amp))

    def test_random_shapes_c_bit_exact(self):
        rng = random.Random(14)
        for _ in range(25):
            shape = []
            for _ in range(rng.randint(1, 3)):
                if rng.random() < 0.25:
                    shape.append(tone_ref.segment(rng.randint(1, 400)))
                    continue
                count = rng.randint(1, 3)
                amps = [rng.randint(1, 65535 // count) for _ in range(count)]
                partials = [(rng.randint(1, 0x7FFFFFFF), min(a, 32767), rng.randint(0, 200)) for a in amps]
                a_s, r_s = rng.randint(0, 6), rng.randint(0, 6)
                shape.append(tone_ref.segment(rng.randint((1 << a_s) + (1 << r_s), 900), partials, a_s, r_s))
            n, got = c_fill(shape)
            self.assertEqual(got[:n], tone_ref.generate_segments(shape))

    def test_invalid_shapes_are_rejected_without_writing(self):
        good = tone_ref.VARIANTS["2k150loud"][0]
        raw = bytearray(tone_ref.pack_segment(good))
        # struct tone_segment: samples u32, partial_count +4, attack +5, release +6,
        # reserved +7, partials from +8 (8 bytes each).
        cases = {
            "reserved": (7, 1),
            "partial_count": (4, 4),
            "unused partial": (16, 1),  # phase_inc of partial 1 with count 1
            "attack shift": (5, 15),
        }
        for name, (offset, value) in cases.items():
            bad = bytearray(raw)
            bad[offset] = value
            n, got = c_fill([good], raw=bytes(bad), count=1)
            self.assertEqual(n, 0, name)
            self.assertEqual(got, [0x5A5A] * len(got), name)
        n, _ = c_fill([good], capacity=tone_ref.padded(good[0]) - 16)
        self.assertEqual(n, 0, "capacity")
        n, _ = c_fill([good], offset=1)
        self.assertEqual(n, 0, "misaligned destination")
        for seg in (good, tone_ref.segment(10), tone_ref.segment(10, (), 1, 0)):
            arr = c_segments_raw(tone_ref.pack_segment(seg), 1)
            self.assertEqual(bool(tone_lib().tone_segment_valid(ctypes.addressof(arr))), tone_ref.segment_valid(seg))


# --- rec_state.c + rec_tone.c step machine ---------------------------------

SHIM = r'''
#include "rec_tone.h"
#include "rec_state.h"
#include "observer.h"
#include <stdarg.h>
#include <string.h>
typedef uint32_t (*py_read_fn)(uint32_t);
typedef int32_t (*py_query_fn)(uint32_t);
typedef int32_t (*py_beep_fn)(uint32_t, uint32_t, uint32_t);
static py_read_fn py_read; static py_query_fn py_query; static py_beep_fn py_beep;
void host_set(py_read_fn r, py_query_fn q, py_beep_fn b) { py_read = r; py_query = q; py_beep = b; }
static uint32_t h_read(uint32_t a) { return py_read(a); }
static int32_t h_query(uint32_t h) { return py_query(h); }
static int32_t h_beep(uint32_t id, const uint32_t *d) { return py_beep(id, d == rec_tone_descriptor, d[1]); }
uintptr_t host_clean_start[8]; uint32_t host_clean_bytes[8]; uint32_t host_clean_count;
static void h_clean(const volatile void *p, uint32_t n)
{
    if (host_clean_count < 8u) { host_clean_start[host_clean_count] = (uintptr_t)p; host_clean_bytes[host_clean_count] = n; }
    host_clean_count++;
}
const struct rec_tone_api rec_tone_api = {h_read, h_query, h_beep, h_clean};
uint32_t rec_state_host_read(uint32_t a) { return py_read(a); }
@CONFIG@
#define ROWS 16
const char *host_fmt[ROWS]; uint32_t host_args[ROWS][8]; uint32_t host_nargs[ROWS]; uint32_t host_rows;
static void host_debug(unsigned c, unsigned l, const char *fmt, ...)
{
    (void)c; (void)l;
    if (host_rows >= ROWS) return;
    unsigned n = 0;
    for (const char *s = fmt; *s; s++) if (*s == '%') n++;
    va_list ap; va_start(ap, fmt);
    for (unsigned i = 0; i < n && i < 8; i++) host_args[host_rows][i] = va_arg(ap, uint32_t);
    va_end(ap);
    host_fmt[host_rows] = fmt; host_nargs[host_rows] = n; host_rows++;
}
const struct observer_api observer_api = {0, 0, host_debug, 0, 0};
uintptr_t host_pcm(void) { return (uintptr_t)rec_tone_pcm; }
uintptr_t host_desc(void) { return (uintptr_t)rec_tone_descriptor; }
uint32_t host_sizes(void) { return (uint32_t)(sizeof(struct rec_tone_state) << 16 | sizeof(struct rec_tone_attempt)); }
void host_reset(void)
{
    memset(&rec_tone_state, 0, sizeof rec_tone_state);
    atomic_store(&rec_press_seq, 0u);
    rec_tone_descriptor[1] = 0u;
    memset(rec_tone_pcm, 0x5a, sizeof rec_tone_pcm);
    host_clean_count = 0u; host_rows = 0u;
}
uint32_t host_press_seq(void) { return atomic_load(&rec_press_seq); }
'''


class Attempt(ctypes.Structure):
    _fields_ = [("context", U), ("handle", U), ("se_id", U), ("actrl_state", U), ("state_before", I),
                ("phase_before", U), ("result_before", U), ("last_id_before", U), ("state_after", I),
                ("phase_after", U), ("result_after", U), ("last_id_after", U), ("route", U * 3),
                ("submit_rc", I), ("preflight", U), ("outcome", U), ("rejection", U),
                ("ticks_to_active", U), ("ticks_to_settle", U)]


class State(ctypes.Structure):
    _fields_ = [(n, U) for n in ("publish_attempted", "ready", "fault", "mode", "seen", "pending", "pending_ticks",
                                 "steps", "presses", "coalesced", "submits", "submit_nonzero")] + [
        ("last_rc", I), ("dropped_unready", U), ("dropped_not_recording", U), ("preflight_drop", U * 12),
        ("outcomes", U * 10), ("obs_ticks", U), ("active_seen", U), ("provisional_reject", U), ("last", Attempt)]


READ = ctypes.CFUNCTYPE(U, U)
QUERY = ctypes.CFUNCTYPE(I, U)
BEEP = ctypes.CFUNCTYPE(I, U, U, U)


def host_config(t, words=None, desc=None):
    """The build's own encoders (segments, config, descriptor), optionally altered."""
    w = list(ri.config_words(t, AUDIO))
    for index, value in (words or {}).items():
        w[index] = value
    d = list(struct.unpack("<16I", ri.pending_descriptor(t)))
    for index, value in (desc or {}).items():
        d[index] = value
    return (f"const struct tone_segment rec_tone_segments[{t['segment_count']}] = {{\n{ri.segment_initializer(t)}}};\n"
            "const struct rec_tone_config rec_tone_config = {" + ",".join(hex(v) + "u" for v in w) + "};\n"
            f"int16_t rec_tone_pcm[{t['padded_samples']}] __attribute__((aligned(32)));\n"
            "uint32_t rec_tone_descriptor[16] __attribute__((aligned(32))) = {" + ",".join(hex(v) + "u" for v in d) + "};\n")


def build_step_lib(name, t, **alter):
    d = Path(_TMP.name)
    (d / "observer-source.h").write_text(
        f'#define OBSERVER_SOURCE_ID "{SOURCE}"\n#define OBSERVER_CLASS 0x84u\n#define OBSERVER_LEVEL 3u\n'
        "#define OBSERVER_MAX_NOTIFICATIONS_LOGGED 1u\n#define OBSERVER_MAX_DUMPS 8u\n")
    (d / (name + ".c")).write_text(SHIM.replace("@CONFIG@", host_config(t, **alter)))
    lib = _compile(name, [d / (name + ".c"), XPAN_SRC / "rec_tone.c", XPAN_SRC / "tone_gen.c",
                          XPAN_SRC / "rec_state.c"], extra=("-DREC_STATE_HOST",))
    lib.host_set.argtypes = [READ, QUERY, BEEP]
    lib.host_pcm.restype = ctypes.c_size_t
    lib.host_desc.restype = ctypes.c_size_t
    lib.host_sizes.restype = U
    lib.host_press_seq.restype = U
    lib.rec_tone_preflight.argtypes = [ctypes.POINTER(Attempt), U]
    lib.rec_tone_preflight.restype = U
    lib.rec_state_observe.argtypes = [ctypes.POINTER(U)]
    lib.rec_tone_report.argtypes = [ctypes.c_uint]
    sizes = lib.host_sizes()
    assert sizes >> 16 == ctypes.sizeof(State) and sizes & 0xFFFF == ctypes.sizeof(Attempt), hex(sizes)
    return lib


class Camera:
    """Fake Canon memory + ABeep/AudioCtrl. Unknown reads are recorded."""
    CTX, ABH, AC, ACH, IC, OUTC, AKT = 0x30000, 0x8001, 0x31000, 0x9002, 0x32000, 0x33000, 0x34000

    def __init__(self, lib, actrl_state=5, rec_state=0):
        self.lib = lib
        a = AUDIO
        self.mem = {a["singleton_slot"]: self.CTX, self.CTX: 1, self.CTX + 4: self.ABH, self.CTX + 0x24: 0,
                    self.CTX + 0x28: 0, self.CTX + 0x2C: 0, self.CTX + 0x40: 0, self.CTX + 0x48: 3, self.CTX + 0x4C: 0,
                    a["audioctrl_slot"]: self.AC, self.AC + a["audioctrl_handle_offset"]: self.ACH,
                    a["audio_ic_slot"]: self.IC, self.IC + a["audio_ic_init_offset"]: 1,
                    a["output_context_slot"]: self.OUTC, self.OUTC + 4: 0, self.OUTC + 8: 0, self.OUTC + 12: 7,
                    a["icu_cache_base"] + a["icu_cache_init_offset"]: 1,
                    a["icu_cache_base"] + a["icu_cache_kind_offset"]: a["icu_kind_ak"],
                    a["ak_param_slot"]: self.AKT, a["vaif_param_slot"]: 0, ri.REC_STATE_WORD: rec_state}
        self.states = {self.ABH: 1, self.ACH: actrl_state}
        self.beeps, self.unowned = [], []
        self.rc = 0
        self._r, self._q, self._b = READ(self.read), QUERY(self.query), BEEP(self.beep)
        lib.host_set(self._r, self._q, self._b)

    def read(self, a):
        if a not in self.mem:
            self.unowned.append(a)
            return 0
        return self.mem[a]

    def query(self, h):
        return self.states.get(h, -1)

    def beep(self, se_id, ours, ptr):
        self.beeps.append((se_id, ours, ptr))
        if self.rc == 0:
            self.mem[self.CTX + 0x48] = 0  # SE_BeepStart: desc[0]==1 -> phase 0
            self.states[self.ABH] = 2
        return self.rc

    def active(self, ptr=None, nbytes=None):
        self.mem[self.CTX + 0x48] = 1
        self.mem[self.CTX + 0x28] = self.lib.host_pcm() & 0xFFFFFFFF if ptr is None else ptr
        self.mem[self.CTX + 0x2C] = 24000 if nbytes is None else nbytes
        self.mem[self.CTX + 0x40] = 2

    def complete(self, result=0, state=1):
        self.mem[self.CTX + 0x48] = 3
        self.mem[self.CTX + 0x24] = result
        self.states[self.ABH] = state


class StepMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = ri.facts("ding")
        cls.lib = build_step_lib("rt_ding", cls.t)

    def setUp(self):
        self.lib.host_reset()
        self.cam = Camera(self.lib)

    def st(self):
        return State.in_dll(self.lib, "rec_tone_state")

    def step(self, n=1):
        for _ in range(n):
            self.lib.rec_tone_step()

    def observe(self, old, new, lr):
        self.cam.mem[ri.REC_STATE_WORD] = old
        saved = (U * 6)(new, 0, 0, 0, 0, lr)
        self.lib.rec_state_observe(saved)
        self.cam.mem[ri.REC_STATE_WORD] = new

    def press(self):
        self.observe(0, 1, ri.REC_PRESS_CALLER)

    def settle(self):
        self.step()
        self.cam.active()
        self.step()
        self.cam.complete()
        self.step()

    def test_publishes_once_bit_exact_clean_then_pointer(self):
        self.step()
        st = self.st()
        self.assertEqual((st.publish_attempted, st.ready, st.fault), (1, 1, 0))
        pcm = ctypes.string_at(self.lib.host_pcm(), self.t["bytes"])
        self.assertEqual(hashlib.sha256(pcm).hexdigest(), DING_PCM_SHA256)
        self.assertEqual(self.lib.host_pcm() % 32, 0)
        self.assertEqual(self.lib.host_desc() % 32, 0)
        starts = (ctypes.c_size_t * 8).in_dll(self.lib, "host_clean_start")
        sizes = (U * 8).in_dll(self.lib, "host_clean_bytes")
        self.assertEqual(U.in_dll(self.lib, "host_clean_count").value, 2)
        self.assertEqual([(starts[0], sizes[0]), (starts[1], sizes[1])],
                         [(self.lib.host_pcm(), 24000), (self.lib.host_desc(), 64)])
        desc = (U * 16).in_dll(self.lib, "rec_tone_descriptor")
        self.assertEqual(list(desc), list(struct.unpack("<16I", ri.pending_descriptor(self.t)))[:1]
                         + [self.lib.host_pcm() & 0xFFFFFFFF] + list(struct.unpack("<16I", ri.pending_descriptor(self.t)))[2:])
        self.step(5)
        self.assertEqual(U.in_dll(self.lib, "host_clean_count").value, 2, "published more than once")
        self.assertEqual(self.cam.beeps, [])

    def test_trigger_counts_only_the_rec_press_caller_from_idle(self):
        cases = [(0, 1, 0xE0B0B883, 1), (0, 1, 0xE0B0B75D, 0), (0, 1, 0xE0B0B881, 0), (1, 1, 0xE0B0B883, 0),
                 (0, 2, 0xE0B0B883, 0), (5, 1, 0xE0B0B883, 0), (1, 2, 0xE0B0BF2B, 0), (2, 3, 0xE0B0BD2D, 0),
                 (0, 1, 0xE0B0C5DB, 0)]
        for old, new, lr, want in cases:
            before = self.lib.host_press_seq()
            self.observe(old, new, lr)
            self.assertEqual(self.lib.host_press_seq() - before, want, (old, new, hex(lr)))

    def test_hardware_sequence_submits_one_cue_on_the_press_tick(self):
        self.step()
        self.press()
        self.step()
        self.assertEqual(self.cam.beeps, [(2, 1, self.lib.host_pcm() & 0xFFFFFFFF)])
        for old, new, lr in ((1, 1, 0xE0B0B735), (1, 2, 0xE0B0BF2B), (2, 3, 0xE0B0BD2D)):
            self.observe(old, new, lr)
            self.step()
        self.cam.active()
        self.step()
        self.cam.complete()
        self.step()
        for old, new, lr in ((3, 5, 0xE0B0B5BF), (5, 0, 0xE0B0B513)):
            self.observe(old, new, lr)
            self.step(3)
        st = self.st()
        self.assertEqual((st.presses, st.submits, st.outcomes[OUT["SETTLED"]], st.fault), (1, 1, 1, 0))
        self.assertEqual(len(self.cam.beeps), 1)
        self.assertEqual(self.cam.unowned, [])

    def test_pending_cue_dropped_outside_states_1_2_3_and_never_replayed(self):
        for state in (0, 4, 5, 6, 0xFFFFFFFF):
            self.lib.host_reset()
            self.cam = Camera(self.lib)
            self.step()
            self.press()
            self.cam.states[self.cam.ABH] = 2  # busy: the cue stays pending
            self.step()
            self.cam.mem[ri.REC_STATE_WORD] = state
            self.cam.states[self.cam.ABH] = 1
            self.step(15)
            self.assertEqual(self.cam.beeps, [], state)
            self.assertEqual(self.st().dropped_not_recording, 1, state)
        for state in (1, 2, 3):
            self.lib.host_reset()
            self.cam = Camera(self.lib)
            self.step()
            self.press()
            self.cam.mem[ri.REC_STATE_WORD] = state
            self.step()
            self.assertEqual(len(self.cam.beeps), 1, state)

    def test_busy_and_audioctrl_retry_at_most_ten_ticks(self):
        for how in ("busy", "actrl"):
            for clear_on, want in ((3, 1), (11, 1), (12, 0)):
                self.lib.host_reset()
                self.cam = Camera(self.lib)
                self.step()
                self.press()
                for tick in range(1, 16):
                    blocked = tick < clear_on
                    if how == "busy":
                        self.cam.states[self.cam.ABH] = 2 if blocked else 1
                    else:
                        self.cam.states[self.cam.ACH] = 3 if blocked else 5
                    self.step()
                self.assertEqual(len(self.cam.beeps), want, (how, clear_on))
                reason = PF["BUSY"] if how == "busy" else PF["ACTRL_STATE"]
                self.assertEqual(self.st().preflight_drop[reason], 1 - want, (how, clear_on))

    def test_other_preflight_failures_drop_at_once(self):
        self.step()
        self.press()
        self.cam.mem[self.cam.CTX + 0x4C] = 1
        self.step()
        self.assertEqual(self.cam.beeps, [])
        self.assertEqual(self.st().preflight_drop[PF["STOCK_OWNED"]], 1)
        self.assertEqual(self.st().pending, 0)

    def test_presses_coalesce_into_one_cue_and_a_later_press_plays_again(self):
        self.step()
        self.press()
        self.press()
        self.press()
        self.step()
        self.press()  # while observing
        self.settle()
        st = self.st()
        self.assertEqual((st.presses, st.coalesced, st.submits), (4, 3, 1))
        self.press()
        self.step()
        self.assertEqual(len(self.cam.beeps), 2)

    def test_timeout_latches_the_fault_and_stops_submissions(self):
        self.step()
        self.press()
        self.step(1 + ri.OBSERVE_TICKS + 1)
        st = self.st()
        self.assertEqual((st.outcomes[OUT["TIMEOUT"]], st.fault), (1, 3))
        self.cam.complete()
        self.press()
        self.step(3)
        self.assertEqual(len(self.cam.beeps), 1)
        self.assertEqual(self.st().dropped_unready, 1)

    def test_rejection_needs_two_ticks_and_stopped_is_not_a_fault(self):
        self.step()
        self.press()
        self.step()
        self.cam.complete(result=27)
        self.step()
        self.assertEqual(self.st().mode, 1, "provisional on the first phase-3 tick")
        self.step()
        self.assertEqual((self.st().outcomes[OUT["REJECTED"]], self.st().last.rejection), (1, 27))
        self.press()
        self.step()
        self.cam.active()
        self.step()
        self.cam.mem[self.cam.CTX + 0x48] = 2
        self.step()
        st = self.st()
        self.assertEqual((st.outcomes[OUT["STOPPED"]], st.fault, st.submits), (1, 0, 2))

    def test_submit_nonzero_is_counted_without_observation_or_retry(self):
        self.cam.rc = 5
        self.step()
        self.press()
        self.step(4)
        st = self.st()
        self.assertEqual((len(self.cam.beeps), st.submit_nonzero, st.last_rc, st.mode), (1, 1, 5, 0))

    def test_foreign_buffer_is_not_ours(self):
        self.step()
        self.press()
        self.step()
        self.cam.active(ptr=0x123460, nbytes=4096)
        self.step()
        self.assertEqual(self.st().active_seen, 0)
        self.cam.active()
        self.step()
        self.cam.active(ptr=0x123460, nbytes=4096)
        self.step()
        self.assertEqual(self.st().outcomes[OUT["BUFFER_CHANGED"]], 1)

    def test_preflight_admits_audioctrl_states_1_4_5_only(self):
        for actrl in range(8):
            self.cam.states[self.cam.ACH] = actrl
            a = Attempt()
            why = self.lib.rec_tone_preflight(ctypes.byref(a), ri.ACTRL_MASK)
            self.assertEqual(why, PF["OK"] if actrl in (1, 4, 5) else PF["ACTRL_STATE"], actrl)
        self.cam.states[self.cam.ACH] = 5
        # The shutter-feedback mask (state 1 only) would refuse the in-recording state 5.
        self.assertEqual(self.lib.rec_tone_preflight(ctypes.byref(Attempt()), 1 << 1), PF["ACTRL_STATE"])

    def test_preflight_reasons_follow_audio_s_order(self):
        a = AUDIO
        steps = [
            (lambda: self.cam.mem.__setitem__(a["vaif_param_slot"], 0) or
             self.cam.mem.__setitem__(self.cam.AKT, 0) or self.cam.mem.__setitem__(a["ak_param_slot"], 0),
             PF["PARAM_TABLE"]),
            (lambda: self.cam.mem.__setitem__(a["icu_cache_base"] + a["icu_cache_init_offset"], 0), PF["ICU_CACHE"]),
            (lambda: self.cam.mem.__setitem__(a["output_context_slot"], 0), PF["OUTPUT_CTX"]),
            (lambda: self.cam.mem.__setitem__(self.cam.IC + a["audio_ic_init_offset"], 0), PF["AUDIO_IC"]),
            (lambda: self.cam.states.__setitem__(self.cam.ACH, 2), PF["ACTRL_STATE"]),
            (lambda: self.cam.states.pop(self.cam.ACH), PF["ACTRL_INVALID"]),
            (lambda: self.cam.mem.__setitem__(a["audioctrl_slot"], 0), PF["ACTRL_MISSING"]),
            (lambda: self.cam.mem.__setitem__(self.cam.CTX + 0x48, 1), PF["BUSY"]),
            (lambda: self.cam.mem.__setitem__(self.cam.CTX + 0x4C, 1), PF["STOCK_OWNED"]),
            (lambda: self.cam.states.pop(self.cam.ABH), PF["STATE_INVALID"]),
            (lambda: self.cam.mem.__setitem__(a["singleton_slot"], 0), PF["NOT_READY"]),
        ]
        self.assertEqual(self.lib.rec_tone_preflight(ctypes.byref(Attempt()), ri.ACTRL_MASK), PF["OK"])
        for mutate, want in steps:
            mutate()
            self.assertEqual(self.lib.rec_tone_preflight(ctypes.byref(Attempt()), ri.ACTRL_MASK), want)

    def test_report_rows_fit_kuwa_and_decode(self):
        self.step()
        self.press()
        self.settle()
        self.press()
        self.cam.mem[ri.REC_STATE_WORD] = 0
        self.step()
        self.lib.rec_tone_report(7)
        rows = ri_rows = record_indicator_log.report_rows()
        n = U.in_dll(self.lib, "host_rows").value
        self.assertEqual(n, len(rows))
        fmts = (ctypes.c_char_p * 16).in_dll(self.lib, "host_fmt")
        args = ((U * 8) * 16).in_dll(self.lib, "host_args")
        nargs = (U * 16).in_dll(self.lib, "host_nargs")
        packed = []
        for k, (name, fmt, names) in enumerate(ri_rows):
            text = fmts[k].decode()
            self.assertEqual(text, fmt.replace("@SOURCE@", SOURCE), name)
            self.assertEqual(nargs[k], len(names), name)
            self.assertLessEqual(11 + len(text) + 1, RECORD_BYTES - 4 * len(names), name + " exceeds the KUWA record")
            packed.append(_pack_record(100 + k, 0, text, list(args[k])[: nargs[k]]))
        decoded = record_indicator_log.decode(_pack_kuwa(packed), SOURCE)
        self.assertEqual(decoded["rejected_groups"], [])
        (group,) = decoded["groups"]
        c = group["counters"]
        self.assertEqual(group["report"], 7)
        self.assertEqual((c["ready"], c["fault"], c["presses"], c["submits"], c["settled"],
                          c["dropped_not_recording"], c["last_rc"]), (1, 0, 2, 1, 1, 1, 0))
        other = record_indicator_log.decode(_pack_kuwa(packed), "fedcba9876543210")
        self.assertEqual(other["groups"], [])

    def test_faulty_tables_publish_nothing(self):
        cases = {"bytes": dict(words={2: 23968}), "segments": dict(words={0: 0}),
                 "capacity": dict(words={1: 11984, 2: 23968}), "desc kind": dict(desc={0: 0}),
                 "desc bytes": dict(desc={2: 23968}), "desc ptr": dict(desc={1: 0x1000})}
        for k, (name, alter) in enumerate(cases.items()):
            lib = build_step_lib(f"rt_fault{k}", self.t, **alter)
            cam = Camera(lib)  # fresh library: the linked tables are exactly as altered
            lib.rec_tone_step()
            st = State.in_dll(lib, "rec_tone_state")
            self.assertEqual(st.ready, 0, name)
            self.assertIn(st.fault, (1, 2), name)
            desc = (U * 16).in_dll(lib, "rec_tone_descriptor")
            self.assertNotEqual(desc[1], lib.host_pcm() & 0xFFFFFFFF, name)
            cam.mem[ri.REC_STATE_WORD] = 0
            lib.rec_state_observe((U * 6)(1, 0, 0, 0, 0, ri.REC_PRESS_CALLER))
            cam.mem[ri.REC_STATE_WORD] = 1
            lib.rec_tone_step()
            self.assertEqual(cam.beeps, [], name)


# --- build plumbing, contracts and CLI (no toolchain) ----------------------

class BuildPlumbingTests(unittest.TestCase):
    def test_choices_and_facts(self):
        self.assertEqual(ri.CHOICES[0], "off")
        self.assertIn("ding", ri.CHOICES)
        self.assertEqual(ri.admit_choice(None), "off")
        with self.assertRaisesRegex(ValueError, "unknown"):
            ri.admit_choice("loud")
        for cue in ri.CHOICES[1:]:
            t = ri.facts(cue)
            self.assertLessEqual(t["duration_ms"], ri.MAX_MS)
            self.assertEqual(t["bytes"] % 32, 0)
            self.assertEqual((t["se_id"], t["trigger"], t["actrl_mask"]), (2, "press", 0x32))
            self.assertEqual(t["rec_state_mask"], 0b1110)
        self.assertFalse(ri.facts("chirp2")["exceeds_se_patience"])

    def test_descriptor_config_and_bindings_come_from_firmware_json(self):
        t = ri.facts("ding")
        desc = struct.unpack("<16I", ri.pending_descriptor(t))
        self.assertEqual((desc[0], desc[1], desc[2], desc[9], desc[10], desc[11]), (1, 0, 24000, 48, 16, 1))
        words = ri.config_words(t, AUDIO)
        self.assertEqual(words[:4], (1, 12000, 24000, 0x16DD4))
        self.assertEqual(words[4], AUDIO["singleton_slot"])
        src = ri.bindings_source(t, AUDIO)
        self.assertIn(f"(rec_tone_query_fn){hex(AUDIO['state_query'])}u", src)
        self.assertIn(f"(rec_tone_beep_fn){hex(AUDIO['beep_start'])}u", src)
        self.assertIn("int16_t rec_tone_pcm[12000] __attribute__((aligned(32)));", src)
        self.assertIn('.resident_data"), aligned(32)', src)
        bad = dict(AUDIO, beep_start=AUDIO["beep_start"] & ~1)
        with self.assertRaisesRegex(ValueError, "Thumb"):
            ri.bindings_source(t, bad)
        header = (XPAN_SRC / "rec_tone.h").read_text()
        self.assertIn("_Static_assert(sizeof(struct rec_tone_config) == 68u", header)
        self.assertEqual(ri.CONFIG_WORDS * 4, 68)

    def test_hook_inventory_replays_the_exact_original_prologue(self):
        inv = ri.hook_inventory()
        self.assertNotIn("rom_sha256", inv)
        (hook,) = inv["hooks"]
        self.assertEqual((hook["va"], hook["length"], hook["original_bytes"]), (0xE07C6FBC, 8, "10b5044603460321"))
        self.assertEqual(hook["resume_thumb"], (hook["va"] + hook["length"]) | 1)
        asm = (XPAN_SRC / "rec_state_hook.S").read_text()
        self.assertIn(f"ldr pc,=0x{hook['resume_thumb']:08x}", asm)
        self.assertIn(f".global {hook['destination_symbol']}", asm)
        # push {r4,lr}; mov r4,r0; mov r3,r0; movs r1,#3 as Thumb-16 encodings.
        encode = {"push {r4,lr}": 0xB510, "mov r4,r0": 0x4604, "mov r3,r0": 0x4603, "movs r1,#3": 0x2103}
        replay = asm[asm.index("pop {r0-r3,r12,lr}"):asm.index("ldr pc,")].splitlines()[1:]
        self.assertEqual(b"".join(struct.pack("<H", encode[line.strip()]) for line in replay if line.strip()).hex(),
                         hook["original_bytes"])
        self.assertEqual(inv["source_guard_pages"], ["0xe0a98000", "0xe0b0b000"])

    def test_capacity_and_default_inventory(self):
        caps = {"pages": feature_contract.native_limit("multi_plan.h", "MULTI_MAX_PAGES"),
                "tables": feature_contract.native_limit("multi_plan.h", "MULTI_MAX_TABLES"),
                "patches": feature_contract.native_limit("code_patch.h", "CODE_PATCH_MAX")}
        for optional in ((), (ri.HOOK_EXTENSION,)):
            contract, hooks, _ = feature_contract.load_hooks(optional_extensions=optional)
            pages = {int(h["va"], 16) & ~4095 for h in hooks}
            self.assertLessEqual(len(hooks), caps["patches"])
            self.assertLessEqual(len(pages), caps["pages"])
            self.assertLessEqual(len({(p >> 20) & 15 for p in pages}), caps["tables"])
            names = [h["destination"] for h in hooks]
            self.assertEqual("rec_state_entry" in names, bool(optional))
            if optional:
                self.assertIn(0xE07C6000, pages)
                self.assertTrue({"0xe0a98000", "0xe0b0b000"} <= set(contract["source_guard_pages"]))
            else:
                self.assertNotIn("optional_hook_extensions", contract)
                self.assertNotIn(0xE07C6000, pages)
        with self.assertRaisesRegex(ValueError, "optional hook extension"):
            feature_contract.load_hooks(optional_extensions=("frame-native-hooks.json",))

    def test_indicator_pages_are_admitted_only_by_their_own_fingerprints(self):
        contract, hooks, _ = feature_contract.load_hooks(optional_extensions=(ri.HOOK_EXTENSION,))
        guards = [int(v, 16) for v in contract["source_guard_pages"]]
        validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, hooks=hooks,
                                                  record_indicator=True, guard_pages=guards)
        with self.assertRaisesRegex(ValueError, "0xe07c6fbc"):
            validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, hooks=hooks)
        with self.assertRaisesRegex(ValueError, "guard page"):
            validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT,
                                                      hooks=hooks[:-1], guard_pages=guards)
        fps = {e["address"] for e in XPAN_TARGET_CONTRACT["record_indicator_code_fingerprints"]}
        self.assertEqual(fps, {0xE07C6000, 0xE0A98000, 0xE0B0B000})
        base = {e["address"] for e in XPAN_TARGET_CONTRACT["extra_code_fingerprints"]}
        self.assertFalse(fps & base)

    def test_cli_rejects_invalid_or_misplaced_indicator_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            for argv in (["--profile", "xpan-crop", "--record-indicator", "loud"],
                         ["--record-indicator", "ding", "--startup-wav", "a.wav", "--shutter-wav", "b.wav"],
                         ["--record-indicator", "off", "--startup-wav", "a.wav", "--shutter-wav", "b.wav"]):
                out = Path(tmp) / "out"
                proc = subprocess.run([sys.executable, str(ROOT / "tools" / "build.py"), "--rom", "r.bin",
                                       "--toolchain", "t", "--output", str(out), *argv], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2, argv)
                self.assertIn("--record-indicator", proc.stderr)
                self.assertFalse(out.exists())
        import build as build_mod
        with self.assertRaisesRegex(ValueError, "requires --profile xpan-crop"):
            build_mod.build_image(rom_path=Path("r.bin"), startup_wav=Path("a"), shutter_wav=Path("b"),
                                  toolchain_dir=Path("t"), output_dir=Path("o"), record_indicator="ding")


def _synthetic_indicator_rom():
    """The public synthetic XPan ROM plus synthetic indicator hook and pages."""
    import copy
    from test_xpan_contracts import make_synthetic_xpan_contract_and_rom
    manifest, contract, xpan_contract, hooks, rom = make_synthetic_xpan_contract_and_rom()
    xpan_contract = copy.deepcopy(xpan_contract)
    base = manifest["rom_base"]
    for region in xpan_contract["record_indicator_code_fingerprints"]:
        off = region["address"] - base
        pattern = hashlib.sha256(region["name"].encode()).digest()
        rom[off : off + 4096] = (pattern * 128)[:4096]
    (h,) = ri.hook_inventory()["hooks"]
    rom[h["va"] - base : h["va"] - base + h["length"]] = bytes.fromhex(h["original_bytes"])
    for region in xpan_contract["record_indicator_code_fingerprints"]:
        off = region["address"] - base
        region["sha256"] = hashlib.sha256(rom[off : off + 4096]).hexdigest()
    indicator_hooks = [*hooks, {"va": hex(h["va"]), "length": h["length"], "destination": h["destination_symbol"],
                                "original_hex": h["original_bytes"]}]
    return manifest, contract, xpan_contract, hooks, indicator_hooks, rom


class MockBuildTests(unittest.TestCase):
    def _build(self, indicator, hooks_key):
        import build as build_mod
        from test_xpan_build_cli import write_xpan_mock_toolchain
        from unittest import mock
        manifest, contract, xpan_contract, hooks, indicator_hooks, rom = _synthetic_indicator_rom()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "rom.bin").write_bytes(bytes(rom))
        tc = root / "tc"
        tc.mkdir()
        write_xpan_mock_toolchain(tc, manifest)
        out = root / "out"
        with mock.patch.dict(os.environ, {}, clear=True):
            call = lambda: build_mod.build_image(
                rom_path=root / "rom.bin", toolchain_dir=tc, output_dir=out, profile="xpan-crop", manifest=manifest,
                target_contract=contract, xpan_contract=xpan_contract,
                xpan_hooks=indicator_hooks if hooks_key else hooks, record_indicator=indicator)
            return call, out

    def test_default_build_links_no_indicator(self):
        call, out = self._build(None, False)
        record = call()
        self.assertIsNone(record["record_indicator"])
        self.assertIsNone(record["configuration"]["record_indicator"])
        self.assertFalse((out / "rec-tone-bindings.c").exists())
        log = (out / "build.log").read_text()
        self.assertNotIn("R5_RECORD_INDICATOR", log)
        self.assertNotIn("rec_state_hook.S", log)
        feature = json.loads((out / "feature-contract.json").read_text())
        self.assertEqual(len(feature["hooks"]), 34)
        self.assertEqual(len(feature["pages"]), 20)
        self.assertEqual(len(feature["source_guard_pages"]), 137)

    def test_indicator_build_admits_compiles_and_fails_closed_at_the_linked_check(self):
        call, out = self._build("ding", True)
        # The mock linker emits no indicator symbols: the linked-image owner must refuse.
        with self.assertRaisesRegex(ValueError, "record indicator symbols missing"):
            call()
        log = (out / "build.log").read_text()
        self.assertIn("-DR5_RECORD_INDICATOR=1", log)
        for name in ("rec_state.c", "tone_gen.c", "rec_tone.c", "rec-tone-bindings.c", "rec_state_hook.S"):
            self.assertIn(name, log)
        feature = json.loads((out / "feature-contract.json").read_text())
        self.assertEqual(len(feature["hooks"]), 35)
        self.assertEqual(len(feature["pages"]), 21)
        self.assertEqual(len(feature["source_guard_pages"]), 139)
        manifest = json.loads((out / "source-manifest.json").read_text())
        self.assertEqual(manifest["configuration"]["record_indicator"]["cue"], "ding")
        self.assertIn("src/experimental/xpan/rec_tone.c", manifest["inputs"])

    def test_indicator_requires_its_hook_and_fingerprinted_pages(self):
        call, _ = self._build("ding", False)
        with self.assertRaisesRegex(ValueError, "record indicator hook"):
            call()
        call, _ = self._build(None, True)
        with self.assertRaisesRegex(ValueError, "record indicator hook"):
            call()

    def test_indicator_rejects_a_rom_whose_guard_page_differs(self):
        import build as build_mod
        from test_xpan_build_cli import write_xpan_mock_toolchain
        manifest, contract, xpan_contract, _, indicator_hooks, rom = _synthetic_indicator_rom()
        rom[0xE0B0B87C - manifest["rom_base"]] ^= 0xFF
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rom.bin").write_bytes(bytes(rom))
            (root / "tc").mkdir()
            write_xpan_mock_toolchain(root / "tc", manifest)
            with self.assertRaisesRegex(ValueError, "record_indicator_page_e0b0b000"):
                build_mod.build_image(rom_path=root / "rom.bin", toolchain_dir=root / "tc", output_dir=root / "out",
                                      profile="xpan-crop", manifest=manifest, target_contract=contract,
                                      xpan_contract=xpan_contract, xpan_hooks=indicator_hooks, record_indicator="ding")
            self.assertFalse((root / "out").exists())


@unittest.skipUnless(os.environ.get("R5_RECORD_INDICATOR_BUILD"), "opt-in real-ROM build verification")
class RealBuildTests(unittest.TestCase):
    def test_linked_indicator_tables(self):
        out = Path(os.environ["R5_RECORD_INDICATOR_BUILD"])
        build = json.loads((out / "build.json").read_text())
        symbols = json.loads((out / "symbols.json").read_text())
        data = (out / "autoexec.bin").read_bytes()
        t = ri.facts(build["record_indicator"]["cue"])
        again = ri.verify_linked(data, symbols, t, AUDIO, minimum_user_left=MANIFEST["minimum_user_left"],
                                 remaining_user_bytes=build["reservation"]["remaining_user_bytes"])
        self.assertEqual(again["pcm_address"], build["record_indicator"]["pcm_address"])
        feature = json.loads((out / "feature-contract.json").read_text())
        self.assertIn("rec_state_entry", [h["destination"] for h in feature["hooks"]])


if __name__ == "__main__":
    unittest.main()
