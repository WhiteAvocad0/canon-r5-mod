"""Host tests for the opt-in xpan-crop movie record indicator (issue #14).

No ROM, camera, card or ARM toolchain: the actual tone_gen.c, rec_state.c and
rec_tone.c are compiled with the host C compiler and driven through an injected
fake ABeep/AudioCtrl API. Real-ROM build verification is separately opt-in (set
R5_RECORD_INDICATOR_BUILD to a build directory made with --record-indicator
and/or --record-stop-indicator).
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
# The cue PCMs of the private lab builds the owner tested (12000 samples each):
# the ding at the REC press and the dong at the end of a take.
DING_PCM_SHA256 = "50ed6d35b3fa6b384a45fc1a2daafe34f7c15d693e4647850c0f0acddcd89c3f"
DONG_PCM_SHA256 = "93d29c8b342896f196d3bfb090cd0826a5870cfa1a350af799dfedf8d04df24a"
PF = dict(OK=0, NOT_READY=1, BUSY=2, STOCK_OWNED=3, STATE_INVALID=4, ACTRL_MISSING=5, ACTRL_INVALID=6,
          ACTRL_STATE=7, AUDIO_IC=8, OUTPUT_CTX=9, ICU_CACHE=10, PARAM_TABLE=11)
OUT = dict(NONE=0, SETTLED=1, STOPPED=2, REJECTED=3, TIMEOUT=4, CONTEXT_CHANGED=5, UNEXPECTED_PHASE=6,
           STATE_INVALID=7, BUFFER_CHANGED=8, SUBMIT_NONZERO=9)
U = ctypes.c_uint32
I = ctypes.c_int32
_TMP = tempfile.TemporaryDirectory(prefix="r5-record-indicator-")


def _compile(name, sources, extra=()):
    lib = Path(_TMP.name) / (name + ".so")
    # rec_tone.c compiles its engine only for the record indicator (a --screenshot-only
    # build links its preflight alone), so these host libraries define it.
    subprocess.run([find_host_cc(), "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    "-DR5_RECORD_INDICATOR=1", *extra,
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
        for name, shape in (*tone_ref.VARIANTS.items(), *tone_ref.STOP_VARIANTS.items()):
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
        t = ri.cue_facts("ding", "start")
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


def bin_mag(values, hz, lo=0, hi=None):
    """Single-bin DFT magnitude of values[lo:hi] at hz."""
    hi = len(values) if hi is None else hi
    re_, im = 0.0, 0.0
    for n in range(lo, hi):
        w = 2 * math.pi * hz * n / 48000
        re_ += values[n] * math.cos(w)
        im += values[n] * math.sin(w)
    return math.hypot(re_, im)


def note_amplitude(values, hz, lo, hi):
    """Amplitude (Q15 units) of a sinusoid at hz over values[lo:hi]."""
    return 2 * bin_mag(values, hz, lo, hi) / (hi - lo)


class DongTests(unittest.TestCase):
    """Record-stop counterpart of the ding: G6 1568 Hz (90 ms) then C6 1047 Hz (160 ms), each
    with its 2.756 x bar partial at -9 dB; 250 ms, same peak as the ding, lower and darker."""

    @classmethod
    def setUpClass(cls):
        cls.segs = tone_ref.STOP_VARIANTS["dong"]
        cls.v = tone_ref.generate_segments(cls.segs)
        cls.ding = tone_ref.generate_segments(tone_ref.VARIANTS["ding"])

    def test_dong_is_the_private_build_cue_and_only_a_stop_cue(self):
        pcm = tone_ref.to_bytes(self.v)
        self.assertEqual(hashlib.sha256(pcm).hexdigest(), DONG_PCM_SHA256)
        n, got = c_fill(self.segs)
        self.assertEqual(got[:n], self.v, "C fill is bit-exact to tone_ref")
        t = ri.cue_facts("dong", "stop")
        self.assertEqual((t["samples"], t["padded_samples"], t["bytes"], t["duration_ms"]), (12000, 12000, 24000, 250))
        self.assertEqual(t["pcm_sha256"], DONG_PCM_SHA256)
        self.assertTrue(t["exceeds_se_patience"])
        self.assertNotIn("dong", tone_ref.VARIANTS)
        self.assertNotIn("ding", tone_ref.STOP_VARIANTS)

    def test_definition(self):
        (n1, a1, r1, p1), (n2, a2, r2, p2) = self.segs
        self.assertEqual((n1, a1, r1, n2, a2, r2), (4320, 7, 8, 7680, 7, 8))
        self.assertEqual([p[0] for p in p1], [tone_ref.phase_inc(h) for h in (1568, 4321)])
        self.assertEqual([p[0] for p in p2], [tone_ref.phase_inc(h) for h in (1047, 2886)])
        self.assertEqual([p[1] for p in p1 + p2], [25290, 8973] * 2)
        self.assertEqual([p[2] for p in p1 + p2], [40, 80, 24, 48])
        self.assertAlmostEqual(4321 / 1568, 2.756, delta=0.001)
        self.assertAlmostEqual(2886 / 1047, 2.756, delta=0.001)
        self.assertAlmostEqual(20 * math.log10(8973 / 25290), -9.0, delta=0.01)
        # Falling an equal-tempered fifth (2^(7/12)) to C6, an octave below the ding's C7.
        self.assertAlmostEqual(1568 / 1047, 2 ** (7 / 12), delta=0.001)
        self.assertAlmostEqual(2093 / 1047, 2.0, delta=0.002)
        self.assertEqual(tone_ref.to_bytes(tone_ref.generate_segments(self.segs)), tone_ref.to_bytes(self.v))

    def test_length_ends_peak_and_loudness_match_the_ding(self):
        v = self.v
        self.assertEqual(len(v), len(self.ding), "250 ms like the ding")
        self.assertEqual(len(v), 12000)
        self.assertEqual((v[0], v[4319], v[4320], v[-1]), (0, 0, 0, 0), "each note starts and ends at 0")
        peak = max(abs(x) for x in v)
        ding_peak = max(abs(x) for x in self.ding)
        self.assertTrue(30900 <= peak <= 31000, peak)
        self.assertLess(abs(20 * math.log10(peak / ding_peak)), 0.05)
        self.assertEqual(tone_ref.generate_segments(self.segs, clamp=False), v, "the partial sum never saturates")
        rms = lambda x: 10 * math.log10(sum(s * s for s in x) / len(x) / 32768 ** 2)
        # Digital RMS within 2 dB of the ding (a lower fundamental; speaker response unknown).
        self.assertLess(abs(rms(v) - rms(self.ding)), 2.0)

    def test_notes_sit_at_their_frequencies_and_fall(self):
        v = self.v
        a, b = (128, 128 + 1920), (4320 + 128, 4320 + 128 + 1920)   # 40 ms after each attack
        self.assertGreater(bin_mag(v, 1568, *a), 20 * bin_mag(v, 1047, *a))
        self.assertGreater(bin_mag(v, 1047, *b), 20 * bin_mag(v, 1568, *b))
        # Nothing at the ding's 2093 Hz: the two cues are spectrally distinct.
        self.assertLess(bin_mag(v, 2093, 0, 12000), 0.02 * bin_mag(self.ding, 2093, 0, 12000))
        self.assertLess(bin_mag(self.ding, 1047, 0, 12000), 0.02 * bin_mag(v, 1047, 0, 12000))

    def test_each_note_decays_and_its_overtone_faster(self):
        for (lo, hz, upper, tau_ms) in ((0, 1568, 4321, 65536 / 40 / 48), (4320, 1047, 2886, 65536 / 24 / 48)):
            early = note_amplitude(self.v, hz, lo + 480, lo + 960)
            late = note_amplitude(self.v, hz, lo + 2400, lo + 2880)
            self.assertAlmostEqual(math.log(early / late), 40 / tau_ms, delta=0.05)
            # The overtone's decay is twice the fundamental's: its log ratio over 40 ms doubles.
            up = math.log(note_amplitude(self.v, upper, lo + 480, lo + 960) /
                          note_amplitude(self.v, upper, lo + 2400, lo + 2880))
            self.assertAlmostEqual(up, 2 * 40 / tau_ms, delta=0.15)


# --- rec_state.c + rec_tone.c step machine ---------------------------------

SHIM = r"""
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
/* Reports which linked cue's descriptor was submitted: 1 start, 2 stop, 0 neither. */
static int32_t h_beep(uint32_t id, const uint32_t *d)
{
    uint32_t which = 0u;
    for (uint32_t i = 0u; i < REC_TONE_CUES; ++i)
        if (d && d == rec_tone_cues[i].descriptor)
            which = i + 1u;
    return py_beep(id, which, d ? d[1] : 0u);
}
uintptr_t host_clean_start[8]; uint32_t host_clean_bytes[8]; uint32_t host_clean_count;
static void h_clean(const volatile void *p, uint32_t n)
{
    if (host_clean_count < 8u) {
        host_clean_start[host_clean_count] = (uintptr_t)p;
        host_clean_bytes[host_clean_count] = n;
    }
    host_clean_count++;
}
const struct rec_tone_api rec_tone_api = {h_read, h_query, h_beep, h_clean};
static int resetting;
uint32_t rec_state_host_read(uint32_t a) { return resetting ? 0u : py_read(a); }
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
uintptr_t host_pcm(uint32_t cue) { return (uintptr_t)rec_tone_cues[cue].pcm; }
uintptr_t host_desc(uint32_t cue) { return (uintptr_t)rec_tone_cues[cue].descriptor; }
uint32_t host_desc_ptr(uint32_t cue) { return rec_tone_cues[cue].descriptor ? rec_tone_cues[cue].descriptor[1] : 0u; }
uint32_t host_sizes(void) { return (uint32_t)(sizeof(struct rec_tone_state) << 16 | sizeof(struct rec_tone_attempt)); }
void host_reset(void)
{
    static const uint32_t idle[6];
    memset(&rec_tone_state, 0, sizeof rec_tone_state);
    resetting = 1;
    rec_state_observe(idle); /* a 0 -> 0 idle set clears the take latch through the hook itself */
    resetting = 0;
    atomic_store(&rec_press_seq, 0u);
    atomic_store(&rec_stop_seq, 0u);
    for (uint32_t i = 0u; i < REC_TONE_CUES; ++i) {
        const struct rec_tone_cue *c = &rec_tone_cues[i];
        if (c->descriptor)
            c->descriptor[1] = 0u;
        if (c->pcm)
            memset(c->pcm, 0x5a, 2u * c->padded_samples);
    }
    host_clean_count = 0u; host_rows = 0u;
}
uint32_t host_press_seq(void) { return atomic_load(&rec_press_seq); }
uint32_t host_stop_seq(void) { return atomic_load(&rec_stop_seq); }
void host_add_press(uint32_t d) { atomic_fetch_add_explicit(&rec_press_seq, d, memory_order_release); }
void host_add_stop(uint32_t d) { atomic_fetch_add_explicit(&rec_stop_seq, d, memory_order_release); }
"""


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
        ("outcomes", U * 10), ("obs_ticks", U), ("active_seen", U), ("provisional_reject", U), ("last", Attempt),
        ("last_cue", U)] + [(n, U) for n in ("stop_seen", "stops", "stop_submits", "stop_coalesced",
                                             "stop_superseded", "stop_not_idle")]


READ = ctypes.CFUNCTYPE(U, U)
QUERY = ctypes.CFUNCTYPE(I, U)
BEEP = ctypes.CFUNCTYPE(I, U, U, U)
FAULT = dict(NONE=0, GEN=1, DESC=2, TIMEOUT=3, API=4)
START, STOP = 1, 2  # Camera.beeps "which" values (REC_TONE_CUE_* + 1)


def host_config(t, words=None, desc=None, entries=None):
    """The build's own encoders (config, cue definitions), optionally altered.

    words: {config index: value}; desc: {role: {descriptor word: value}};
    entries: {role: C initializer of that rec_tone_cues entry}."""
    w = list(ri.config_words(t, AUDIO))
    for index, value in (words or {}).items():
        w[index] = value
    text = ri.cue_definitions(t, firmware=False)
    for role, alter in (desc or {}).items():
        d = list(struct.unpack("<16I", ri.pending_descriptor(t["cues"][role])))
        for index, value in alter.items():
            d[index] = value
        name = ri.CUE_SYMBOLS[role][2]
        text, n = re.subn(rf"(uint32_t {name}\[REC_TONE_DESC_WORDS\][^\n]*\n    )[^\n]*",
                          lambda m: m.group(1) + ", ".join(hex(v) + "u" for v in d) + ",", text)
        assert n == 1, name
    table = text.index("const struct rec_tone_cue rec_tone_cues")
    head, rows = text[:table], text[table:].splitlines(keepends=True)
    for role, entry in (entries or {}).items():
        rows[1 + ri.ROLES.index(role)] = "    " + entry + ",\n"
    return ("const struct rec_tone_config rec_tone_config = {" + ",".join(hex(v) + "u" for v in w) + "};\n"
            + head + "".join(rows))


def build_step_lib(name, t, **alter):
    d = Path(_TMP.name)
    (d / "observer-source.h").write_text(
        f'#define OBSERVER_SOURCE_ID "{SOURCE}"\n#define OBSERVER_CLASS 0x84u\n#define OBSERVER_LEVEL 3u\n'
        "#define OBSERVER_MAX_NOTIFICATIONS_LOGGED 1u\n#define OBSERVER_MAX_DUMPS 8u\n")
    (d / (name + ".c")).write_text(SHIM.replace("@CONFIG@", host_config(t, **alter)))
    lib = _compile(name, [d / (name + ".c"), XPAN_SRC / "rec_tone.c", XPAN_SRC / "tone_gen.c",
                          XPAN_SRC / "rec_state.c"], extra=("-DREC_STATE_HOST",))
    lib.host_set.argtypes = [READ, QUERY, BEEP]
    for fn in ("host_pcm", "host_desc"):
        getattr(lib, fn).restype = ctypes.c_size_t
        getattr(lib, fn).argtypes = [U]
    lib.host_desc_ptr.restype = U
    lib.host_desc_ptr.argtypes = [U]
    lib.host_sizes.restype = U
    lib.host_press_seq.restype = U
    lib.host_stop_seq.restype = U
    lib.host_add_press.argtypes = [U]
    lib.host_add_stop.argtypes = [U]
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

    def beep(self, se_id, which, ptr):
        self.beeps.append((se_id, which, ptr))
        if self.rc == 0:
            self.mem[self.CTX + 0x48] = 0  # SE_BeepStart: desc[0]==1 -> phase 0
            self.states[self.ABH] = 2
        return self.rc

    def desc(self, cue):
        """The published PCM pointer of rec_tone_cues[cue] (0 until published)."""
        return self.lib.host_desc_ptr(cue)

    def active(self, ptr=None, nbytes=None, cue=0):
        self.mem[self.CTX + 0x48] = 1
        self.mem[self.CTX + 0x28] = self.desc(cue) if ptr is None else ptr
        self.mem[self.CTX + 0x2C] = 24000 if nbytes is None else nbytes
        self.mem[self.CTX + 0x40] = 2

    def complete(self, result=0, state=1):
        self.mem[self.CTX + 0x48] = 3
        self.mem[self.CTX + 0x24] = result
        self.states[self.ABH] = state


class StepMachineTests(unittest.TestCase):
    """--record-indicator ding alone: the start cue on the REC press."""

    @classmethod
    def setUpClass(cls):
        cls.t = ri.facts("ding")
        cls.cue = cls.t["cues"]["start"]
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
        pcm = ctypes.string_at(self.lib.host_pcm(0), self.cue["bytes"])
        self.assertEqual(hashlib.sha256(pcm).hexdigest(), DING_PCM_SHA256)
        self.assertEqual(self.lib.host_pcm(0) % 32, 0)
        self.assertEqual(self.lib.host_desc(0) % 32, 0)
        starts = (ctypes.c_size_t * 8).in_dll(self.lib, "host_clean_start")
        sizes = (U * 8).in_dll(self.lib, "host_clean_bytes")
        self.assertEqual(U.in_dll(self.lib, "host_clean_count").value, 2)
        self.assertEqual([(starts[0], sizes[0]), (starts[1], sizes[1])],
                         [(self.lib.host_pcm(0), 24000), (self.lib.host_desc(0), 64)])
        desc = (U * 16).in_dll(self.lib, "rec_tone_descriptor")
        pending = list(struct.unpack("<16I", ri.pending_descriptor(self.cue)))
        self.assertEqual(list(desc), pending[:1] + [self.lib.host_pcm(0) & 0xFFFFFFFF] + pending[2:])
        self.assertEqual(self.lib.host_desc(1), 0, "no stop cue linked")
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
        self.assertEqual(self.cam.beeps, [(2, START, self.lib.host_pcm(0) & 0xFFFFFFFF)])
        for old, new, lr in ((1, 1, 0xE0B0B735), (1, 2, 0xE0B0BF2B), (2, 3, 0xE0B0BD2D)):
            self.observe(old, new, lr)
            self.step()
        self.cam.active()
        self.step()
        self.cam.complete()
        self.step()
        for old, new, lr in ((3, 5, 0xE0B0B5BF), (5, 5, 0xE0B10977), (5, 0, 0xE0B10905)):
            self.observe(old, new, lr)
            self.step(3)
        st = self.st()
        self.assertEqual((st.presses, st.submits, st.outcomes[OUT["SETTLED"]], st.fault), (1, 1, 1, 0))
        self.assertEqual(len(self.cam.beeps), 1)
        self.assertEqual(self.cam.unowned, [])
        # Without --record-stop-indicator the ended take is counted by the hook but never read.
        self.assertEqual((self.lib.host_stop_seq(), st.stops, st.stop_seen, st.stop_submits), (1, 0, 0, 0))

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
        self.assertEqual([r[0] for r in rows], ["begin", "counts", "drops", "stop", "end"])
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
        self.assertEqual((c["stops"], c["stop_submits"], c["stop_coalesced"], c["stop_superseded"],
                          c["stop_not_idle"], c["stop_seq"]), (0, 0, 0, 0, 0, 0))
        other = record_indicator_log.decode(_pack_kuwa(packed), "fedcba9876543210")
        self.assertEqual(other["groups"], [])

    def test_stop_row_reports_the_hook_count_without_a_stop_cue(self):
        self.step()
        for old, new, lr in TAKE:
            self.observe(old, new, lr)
        self.lib.rec_tone_report(1)
        fmts = (ctypes.c_char_p * 16).in_dll(self.lib, "host_fmt")
        args = ((U * 8) * 16).in_dll(self.lib, "host_args")
        k = [fmts[i].decode() for i in range(U.in_dll(self.lib, "host_rows").value)].index(
            "R5T1 W=%u B=%u K=%u H=%u I=%u A=%u")
        self.assertEqual(list(args[k])[:6], [0, 0, 0, 0, 0, 1])

    def test_faulty_tables_publish_nothing(self):
        start = "rec_tone_segments, rec_tone_pcm, rec_tone_descriptor"
        cases = {"bytes": dict(entries={"start": "{%s, 1u, 12000u, 23968u}" % start}),
                 "segments": dict(entries={"start": "{%s, 0u, 12000u, 24000u}" % start}),
                 "capacity": dict(entries={"start": "{%s, 1u, 11984u, 23968u}" % start}),
                 "absent cue not zero": dict(entries={"stop": "{0, 0, 0, 0u, 0u, 32u}"}),
                 "desc kind": dict(desc={"start": {0: 0}}),
                 "desc bytes": dict(desc={"start": {2: 23968}}), "desc ptr": dict(desc={"start": {1: 0x1000}})}
        for k, (name, alter) in enumerate(cases.items()):
            lib = build_step_lib(f"rt_fault{k}", self.t, **alter)
            cam = Camera(lib)  # fresh library: the linked tables are exactly as altered
            lib.rec_tone_step()
            st = State.in_dll(lib, "rec_tone_state")
            self.assertEqual(st.ready, 0, name)
            self.assertIn(st.fault, (FAULT["GEN"], FAULT["DESC"]), name)
            desc = (U * 16).in_dll(lib, "rec_tone_descriptor")
            self.assertNotEqual(desc[1], lib.host_pcm(0) & 0xFFFFFFFF, name)
            cam.mem[ri.REC_STATE_WORD] = 0
            lib.rec_state_observe((U * 6)(1, 0, 0, 0, 0, ri.REC_PRESS_CALLER))
            cam.mem[ri.REC_STATE_WORD] = 1
            lib.rec_tone_step()
            self.assertEqual(cam.beeps, [], name)


# Every ROM caller of GUI_SetRecordingState(0) (LR = BL site + 5), from the private
# lab's static scan of all 26 setter callers.
IDLE_CALLERS = [a + 5 for a in (0xE05FF824, 0xE05FFD40, 0xE060126E, 0xE09AD88A, 0xE0B0B50E, 0xE0B0B6CC, 0xE0B0B714,
                                0xE0B0C322, 0xE0B10900, 0xE0B10A68, 0xE0B11742, 0xE0B14988, 0xE0B2D9C4, 0xE0B47798)]
# Hardware-recorded take (private lab logs: manual stops and the 1:00 auto-stop share it)
# plus the idle set from LOCAL_MOVIE_RECORD_STOP (E0B10900, ROM).
TAKE = [(0, 1, 0xE0B0B883), (1, 1, 0xE0B0B735), (1, 2, 0xE0B0BF2B), (2, 3, 0xE0B0BD2D), (3, 5, 0xE0B0B5BF),
        (5, 5, 0xE0B10977), (5, 0, 0xE0B10905)]


class StopCueBase(unittest.TestCase):
    START_CUE, STOP_CUE = "ding", "dong"

    @classmethod
    def setUpClass(cls):
        cls.t = ri.facts(cls.START_CUE, cls.STOP_CUE)
        cls.lib = build_step_lib("rt_" + cls.__name__, cls.t)  # one library file per class

    def setUp(self):
        self.lib.host_reset()
        self.cam = Camera(self.lib, actrl_state=4)
        self.lib.rec_tone_step()  # publish

    def st(self):
        return State.in_dll(self.lib, "rec_tone_state")

    def step(self, n=1):
        for _ in range(n):
            self.lib.rec_tone_step()

    def observe(self, old, new, lr):
        """Canon's GUI_SetRecordingState(new) from lr: our hook, then Canon's store."""
        self.cam.mem[ri.REC_STATE_WORD] = old
        self.lib.rec_state_observe((U * 6)(new, 0, 0, 0, 0, lr))
        self.cam.mem[ri.REC_STATE_WORD] = new

    def settle(self, cue):
        self.cam.active(cue=cue)
        self.step()
        self.cam.complete()
        self.step()

    def run_take(self, seq=TAKE):
        """One worker tick per Canon set; a cue we submitted is played to completion at once."""
        per_set = []
        for old, new, lr in seq:
            self.observe(old, new, lr)
            before = len(self.cam.beeps)
            self.step()
            if len(self.cam.beeps) > before:
                self.settle(self.cam.beeps[-1][1] - 1)
            per_set.append([b[1] for b in self.cam.beeps[before:]])
        return per_set

    def cues(self):
        return [b[1] - 1 for b in self.cam.beeps]

    def stop_seq(self):
        return self.lib.host_stop_seq()

    def assert_no_unowned(self):
        self.assertEqual(self.cam.unowned, [], [hex(a) for a in self.cam.unowned])


class StopCueTests(StopCueBase):
    """--record-indicator ding --record-stop-indicator dong: the ding at the REC press, the
    dong once per take when RecState returns to 0 after a confirmed start. One cue at a
    time; nothing is queued or replayed late."""

    def test_facts_link_the_dong_as_the_stop_cue(self):
        t = self.t
        self.assertEqual((t["cue"], t["stop_cue"]), ("ding", "dong"))
        self.assertEqual([(t["cues"][r]["cue"], t["cues"][r]["role"], t["cues"][r]["bytes"]) for r in ri.ROLES],
                         [("ding", "start", 24000), ("dong", "stop", 24000)])
        self.assertEqual((t["stop_state_mask"], t["stop_actrl_mask"], t["stop_retry_mask"], t["rec_confirmed_caller"]),
                         (1, 0x12, 0x84, 0xE0B0BD2D))
        symbols = {n: 0x1000 * (k + 1) for k, n in enumerate(ri.CUE_SYMBOLS["start"] + ri.CUE_SYMBOLS["stop"])}
        words = ri.cue_words(t, symbols)
        self.assertEqual(words, (0x1000, 0x2000, 0x3000, 1, 12000, 24000, 0x4000, 0x5000, 0x6000, 2, 12000, 24000))
        self.assertEqual(ri.cue_words(ri.facts("ding"), symbols)[6:], (0,) * 6)
        self.assertEqual(ri.cue_words(ri.facts("off", "dong"), symbols)[:6], (0,) * 6)

    def test_both_buffers_published_once_bit_exact_and_aligned(self):
        s = self.st()
        self.assertEqual((s.ready, s.fault), (1, 0))
        ding = ctypes.string_at(self.lib.host_pcm(0), 24000)
        dong = ctypes.string_at(self.lib.host_pcm(1), 24000)
        self.assertEqual(hashlib.sha256(ding).hexdigest(), DING_PCM_SHA256)
        self.assertEqual(hashlib.sha256(dong).hexdigest(), DONG_PCM_SHA256)
        for cue in (0, 1):
            self.assertEqual((self.lib.host_pcm(cue) % 32, self.lib.host_desc(cue) % 32), (0, 0))
            self.assertEqual(self.cam.desc(cue), self.lib.host_pcm(cue) & 0xFFFFFFFF)
        lo, hi = sorted((self.lib.host_pcm(0), self.lib.host_pcm(1)))
        self.assertGreaterEqual(hi - lo, 24000, "separate buffers never overlap")
        starts = (ctypes.c_size_t * 8).in_dll(self.lib, "host_clean_start")
        sizes = (U * 8).in_dll(self.lib, "host_clean_bytes")
        self.assertEqual(U.in_dll(self.lib, "host_clean_count").value, 4)
        self.assertEqual([(starts[k], sizes[k]) for k in range(4)],
                         [(self.lib.host_pcm(0), 24000), (self.lib.host_pcm(1), 24000),
                          (self.lib.host_desc(0), 64), (self.lib.host_desc(1), 64)])
        self.run_take()
        self.assertEqual(U.in_dll(self.lib, "host_clean_count").value, 4, "never rewritten")

    def test_full_take_gives_one_ding_at_the_press_and_one_dong_at_idle(self):
        per_set = self.run_take()
        self.assertEqual(per_set, [[START], [], [], [], [], [], [STOP]])
        self.assertEqual(self.cam.beeps, [(2, START, self.cam.desc(0)), (2, STOP, self.cam.desc(1))])
        self.step(30)
        s = self.st()
        self.assertEqual(len(self.cam.beeps), 2, "no replay")
        self.assertEqual((self.stop_seq(), s.stops, s.submits, s.stop_submits, s.last_cue), (1, 1, 2, 1, 1))
        self.assertEqual((s.outcomes[OUT["SETTLED"]], s.stop_coalesced, s.stop_superseded, s.stop_not_idle,
                          s.dropped_not_recording, s.fault), (2, 0, 0, 0, 0, 0))
        self.assertEqual((s.last.actrl_state, s.last.outcome), (4, OUT["SETTLED"]))
        self.assert_no_unowned()

    def test_aborted_or_unconfirmed_starts_give_no_dong(self):
        cases = {
            "uilock failed 1->0": [(0, 1, 0xE0B0B883), (1, 0, 0xE0B0B6D1)],
            "cancel 1->0": [(0, 1, 0xE0B0B883), (1, 1, 0xE0B0B735), (1, 0, 0xE0B0B719)],
            "start error 2->5->0": [(0, 1, 0xE0B0B883), (1, 2, 0xE0B0BF2B), (2, 5, 0xE0B0B5BF), (5, 0, 0xE0B10905)],
            "driving-mode cancel 2->0": [(0, 1, 0xE0B0B883), (1, 2, 0xE0B0BF2B), (2, 0, 0xE0B0B513)],
            "timelapse 2->3 (not the normal confirmed start)": [(0, 1, 0xE0B0C5DB), (1, 2, 0xE0B0C5FB),
                                                               (2, 3, 0xE06011EB), (3, 5, 0xE0B0C497),
                                                               (5, 0, 0xE0B10A6D)],
            "streaming 2->3": [(0, 1, 0xE0B0B883), (1, 2, 0xE0B0BF2B), (2, 3, 0xE0B0C2B5), (3, 0, 0xE0B0C327)],
            "idle while idle": [(0, 0, 0xE0B10905), (0, 0, 0xE09AD88F)],
        }
        for name, seq in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.run_take(seq)
                self.step(5)
                self.assertNotIn(1, self.cues(), "no dong")
                self.assertEqual((self.stop_seq(), self.st().stops), (0, 0))
        # A confirmed take still ends with a dong after any of them (no stale latch either way).
        self.run_take()
        self.assertEqual(self.cues(), [0, 1])

    def test_back_to_back_takes_alternate_ding_dong(self):
        for _ in range(3):
            self.run_take()
            self.step(2)
        s = self.st()
        self.assertEqual(self.cues(), [0, 1, 0, 1, 0, 1])
        self.assertEqual((self.stop_seq(), s.stops, s.submits, s.stop_submits, s.presses), (3, 3, 6, 3, 3))

    def test_every_rom_idle_caller_ends_a_confirmed_take_exactly_once(self):
        self.assertEqual(len(IDLE_CALLERS), 14)
        for lr in IDLE_CALLERS:
            with self.subTest(lr=hex(lr)):
                self.setUp()
                # Auto-stop shape (hardware: same EndMovieRecSequence path as manual), idle set
                # from this caller, then a second idle set from LOCAL_MOVIE_RECORD_STOP.
                self.run_take(TAKE[:-1] + [(5, 0, lr), (0, 0, 0xE0B10905)])
                self.step(5)
                self.assertEqual((self.cues(), self.stop_seq()), ([0, 1], 1))
        # 3 -> 0 directly (an error path without the stopping state) also ends the take once.
        self.setUp()
        self.run_take(TAKE[:4] + [(3, 0, 0xE0B47799)])
        self.assertEqual((self.cues(), self.stop_seq()), ([0, 1], 1))

    def test_stop_while_the_start_cue_is_pending_drops_the_start_and_plays_the_dong(self):
        self.cam.states[self.cam.ABH] = 2   # Canon SE busy for the whole short take
        for old, new, lr in TAKE:
            self.observe(old, new, lr)
            self.step()
        self.assertEqual(self.cam.beeps, [])
        self.cam.states[self.cam.ABH] = 1
        self.step()
        s = self.st()
        self.assertEqual(self.cam.beeps, [(2, STOP, self.cam.desc(1))], "the stale ding is never played late")
        self.assertEqual((s.dropped_not_recording, s.stops, s.presses), (1, 1, 1))

    def test_take_ending_within_one_tick_replaces_the_pending_start_cue(self):
        self.cam.states[self.cam.ABH] = 2   # the ding waits for a busy ABeep ...
        for old, new, lr in TAKE[:4]:
            self.observe(old, new, lr)
            self.step()
        for old, new, lr in TAKE[4:]:       # ... and 3 -> 5 -> 5 -> 0 all land before the next tick
            self.observe(old, new, lr)
        self.step()                          # the end event replaces the stale start cue
        s = self.st()
        self.assertEqual((s.pending, s.dropped_not_recording, s.stops), (2, 1, 1))
        self.cam.states[self.cam.ABH] = 1
        self.step()
        self.assertEqual(self.cam.beeps, [(2, STOP, self.cam.desc(1))])

    def test_stop_while_the_ding_is_in_flight_is_dropped_not_queued(self):
        self.observe(0, 1, 0xE0B0B883)
        self.step()                          # ding submitted, phase 0: observing
        for old, new, lr in TAKE[1:]:
            self.observe(old, new, lr)
            self.step()
        self.settle(0)
        self.step(30)
        s = self.st()
        self.assertEqual(self.cues(), [0])
        self.assertEqual((s.stops, s.stop_coalesced, self.stop_seq(), s.pending), (1, 1, 1, 0))

    def test_new_take_supersedes_a_pending_stop_cue(self):
        self.run_take(TAKE[:-1])
        self.cam.states[self.cam.ABH] = 2   # busy when the take ends
        self.observe(5, 0, 0xE0B10905)
        self.step(2)
        self.assertEqual(self.st().pending, 2)
        self.observe(0, 1, 0xE0B0B883)      # the next take starts before the dong could play
        self.cam.states[self.cam.ABH] = 1
        self.step()
        self.settle(0)
        self.step(30)
        s = self.st()
        self.assertEqual(self.cues(), [0, 0], "ding, then the next take's ding; the old dong never plays")
        self.assertEqual((s.stop_superseded, s.stops, s.presses), (1, 1, 2))

    def test_end_and_new_start_in_one_tick_play_the_new_ding_only(self):
        self.run_take(TAKE[:-1])
        self.observe(5, 0, 0xE0B10905)       # the take ends ...
        self.observe(0, 1, 0xE0B0B883)       # ... and the next starts before the worker tick
        self.step()
        s = self.st()
        self.assertEqual(self.cues(), [0, 0], "ends are absorbed first; the newer start wins")
        self.assertEqual((s.stop_superseded, s.stop_not_idle, s.stops, s.presses), (1, 0, 1, 2))

    def test_stop_cue_only_while_idle(self):
        for state in (1, 2, 3, 5, 4, 0xFFFFFFFF):
            with self.subTest(state=state):
                self.setUp()
                self.cam.mem[ri.REC_STATE_WORD] = state
                self.lib.host_add_stop(1)
                self.step(3)
                self.cam.mem[ri.REC_STATE_WORD] = 0      # back to idle later: no late dong
                self.step(5)
                s = self.st()
                self.assertEqual((self.cam.beeps, s.stop_not_idle, s.pending), ([], 1, 0))

    def test_busy_and_audioctrl_retry_at_most_ten_ticks_then_drop(self):
        # actrl5: AudioCtrl still in s5 when RecState reaches 0 is waited out like any other
        # blocked state (the stop cue's own retry mask always retries ACTRL_STATE).
        for what in ("busy", "actrl", "actrl5"):
            for clear, expect in ((3, 3), (11, 11), (12, None)):
                with self.subTest(what=what, clear=clear):
                    self.setUp()
                    if what == "busy":
                        self.cam.states[self.cam.ABH] = 2
                    else:
                        self.cam.states[self.cam.ACH] = 5 if what == "actrl5" else 2
                    self.lib.host_add_stop(1)
                    got = None
                    for tick in range(1, 20):
                        if tick == clear:
                            self.cam.states[self.cam.ABH] = 1
                            self.cam.states[self.cam.ACH] = 4
                        self.step()
                        if self.cam.beeps:
                            got = tick
                            break
                    self.assertEqual(got, expect)
                    if expect:
                        self.assertEqual(self.cam.beeps, [(2, STOP, self.cam.desc(1))])
                    reason = PF["BUSY"] if what == "busy" else PF["ACTRL_STATE"]
                    self.assertEqual(self.st().preflight_drop[reason], 0 if expect else 1)

    def test_audioctrl_states_after_the_stop(self):
        # s4 (ALV standby, hardware at the press) and s1 are admitted; s5 (RecStop not yet processed)
        # is waited out within the busy budget like any state outside the mask.
        for state, plays in ((1, True), (4, True), (5, False), (0, False), (2, False), (3, False), (6, False)):
            with self.subTest(actrl=state):
                self.setUp()
                self.cam.states[self.cam.ACH] = state
                self.lib.host_add_stop(1)
                self.step(12)
                self.assertEqual(bool(self.cam.beeps), plays)
                if plays:
                    self.assertEqual(self.st().last.actrl_state, state)

    def test_stop_cue_waits_out_audioctrl_s5_then_plays_in_s4(self):
        self.cam.states[self.cam.ACH] = 5   # RecStop not yet processed when RecState reaches 0
        self.lib.host_add_stop(1)
        self.step(4)
        self.assertEqual(self.cam.beeps, [])
        self.assertEqual(self.st().pending, 2, "kept, not dropped at once")
        self.cam.states[self.cam.ACH] = 4   # event 10: s5 -> s4
        self.step()
        self.assertEqual(self.cam.beeps, [(2, STOP, self.cam.desc(1))])
        self.assertEqual(self.st().last.actrl_state, 4)
        # The ding keeps its mask: it still plays in s5.
        self.setUp()
        self.cam.states[self.cam.ACH] = 5
        self.cam.mem[ri.REC_STATE_WORD] = 1
        self.lib.host_add_press(1)
        self.step()
        self.assertEqual(self.cam.beeps, [(2, START, self.cam.desc(0))])

    def test_other_preflight_failures_drop_the_dong_at_once(self):
        a = AUDIO
        for name, mutate, reason in (
                ("stock owned", lambda: self.cam.mem.__setitem__(self.cam.CTX + 0x4C, 1), PF["STOCK_OWNED"]),
                ("audio ic", lambda: self.cam.mem.__setitem__(self.cam.IC + a["audio_ic_init_offset"], 0), PF["AUDIO_IC"]),
                ("actrl invalid", lambda: self.cam.states.pop(self.cam.ACH), PF["ACTRL_INVALID"])):
            with self.subTest(case=name):
                self.setUp()
                mutate()
                self.lib.host_add_stop(1)
                self.step()
                s = self.st()
                self.assertEqual((self.cam.beeps, s.pending, s.preflight_drop[reason]), ([], 0, 1))

    def test_a_replacing_cue_gets_its_own_full_retry_budget(self):
        self.cam.states[self.cam.ABH] = 2   # busy: the ding uses 8 of its 10 retry ticks
        for old, new, lr in TAKE[:4]:
            self.observe(old, new, lr)
            self.step(2)
        for old, new, lr in TAKE[4:]:
            self.observe(old, new, lr)
        got = None
        for tick in range(1, 16):            # the dong must still get ticks 1..11
            if tick == 11:
                self.cam.states[self.cam.ABH] = 1
            self.step()
            if self.cam.beeps:
                got = tick
                break
        self.assertEqual(got, 11)
        self.assertEqual(self.cam.beeps, [(2, STOP, self.cam.desc(1))])
        # And a ding that replaces a pending dong starts its own budget too.
        self.setUp()
        self.run_take(TAKE[:-1])
        self.cam.states[self.cam.ABH] = 2
        self.observe(5, 0, 0xE0B10905)
        self.step(8)
        self.observe(0, 1, 0xE0B0B883)
        got = None
        for tick in range(1, 16):
            if tick == 11:
                self.cam.states[self.cam.ABH] = 1
            self.step()
            if len(self.cam.beeps) > 1:
                got = tick
                break
        self.assertEqual((got, self.cues()), (11, [0, 0]))

    def test_press_while_the_dong_is_in_flight_is_dropped(self):
        self.run_take(TAKE[:-1])
        self.observe(5, 0, 0xE0B10905)
        self.step()                          # dong submitted
        self.observe(0, 1, 0xE0B0B883)       # quick new take while it plays
        self.step(2)
        self.settle(1)
        self.step(10)
        s = self.st()
        self.assertEqual(self.cues(), [0, 1], "no ding over or after the dong")
        self.assertEqual((s.presses, s.coalesced), (2, 1))

    def test_report_rows_carry_the_stop_counters(self):
        self.run_take()
        U.in_dll(self.lib, "host_rows").value = 0
        self.lib.rec_tone_report(5)
        rows = record_indicator_log.report_rows()
        n = U.in_dll(self.lib, "host_rows").value
        fmts = (ctypes.c_char_p * 16).in_dll(self.lib, "host_fmt")
        args = ((U * 8) * 16).in_dll(self.lib, "host_args")
        nargs = (U * 16).in_dll(self.lib, "host_nargs")
        got = {name: (fmts[k].decode(), list(args[k])[: nargs[k]]) for k, (name, _, _) in enumerate(rows[:n])}
        self.assertEqual(got["stop"], ("R5T1 W=%u B=%u K=%u H=%u I=%u A=%u", [1, 1, 0, 0, 0, 1]))
        self.assertEqual(got["counts"][1][:3], [1, 0, 2], "presses, coalesced, submits (both cues)")
        packed = [_pack_record(100 + k, 0, fmts[k].decode(), list(args[k])[: nargs[k]]) for k in range(n)]
        (group,) = record_indicator_log.decode(_pack_kuwa(packed), SOURCE)["groups"]
        self.assertEqual((group["counters"]["stops"], group["counters"]["stop_submits"],
                          group["counters"]["stop_seq"], group["counters"]["settled"]), (1, 1, 1, 2))
        for fmt, values in got.values():
            self.assertGreaterEqual(RECORD_BYTES - 4 * len(values), 11 + len(fmt.encode("ascii")) + 1, fmt)

    def test_role_table_faults_publish_nothing(self):
        start = "rec_tone_segments, rec_tone_pcm, rec_tone_descriptor"
        stop = "rec_tone_stop_segments, rec_tone_stop_pcm, rec_tone_stop_descriptor"
        cases = {
            "stop bytes": dict(entries={"stop": "{%s, 2u, 12000u, 23968u}" % stop}),
            "stop capacity": dict(entries={"stop": "{%s, 2u, 11984u, 23968u}" % stop}),
            "stop segment count": dict(entries={"stop": "{%s, 3u, 12000u, 24000u}" % stop}),
            "stop null pcm": dict(entries={"stop": "{rec_tone_stop_segments, 0, rec_tone_stop_descriptor, "
                                                    "2u, 12000u, 24000u}"}),
            "start entry absent but not zero": dict(entries={"start": "{%s, 0u, 0u, 0u}" % start}),
            "stop descriptor bytes": dict(desc={"stop": {2: 23968}}),
            "stop descriptor kind": dict(desc={"stop": {0: 0}}),
        }
        for k, (name, alter) in enumerate(cases.items()):
            with self.subTest(case=name):
                lib = build_step_lib(f"rt_role{k}", self.t, **alter)
                lib.host_reset()
                cam = Camera(lib)
                lib.rec_tone_step()
                lib.host_add_stop(1)
                lib.host_add_press(1)
                lib.rec_tone_step()
                s = State.in_dll(lib, "rec_tone_state")
                self.assertEqual((s.ready, cam.beeps, cam.desc(0), cam.desc(1)), (0, [], 0, 0))
                self.assertIn(s.fault, (FAULT["GEN"], FAULT["DESC"]))
        # Neither cue linked: nothing to publish.
        lib = build_step_lib("rt_role_none", self.t, entries={"start": "{0, 0, 0, 0u, 0u, 0u}",
                                                              "stop": "{0, 0, 0, 0u, 0u, 0u}"})
        lib.host_reset()
        Camera(lib)
        lib.rec_tone_step()
        self.assertEqual(State.in_dll(lib, "rec_tone_state").fault, FAULT["GEN"])


class StopCueHookTests(StopCueBase):
    """The take driven through the hook entry (rec_state_observe saved registers) with the
    confirmed-start latch in the actual rec_state.c."""

    def test_latch_needs_the_normal_confirmed_start(self):
        # 1 -> 3 from the confirming caller without old 2, then idle: no ended take.
        for old, new, lr in [(0, 1, 0xE0B0B883), (1, 3, 0xE0B0BD2D), (3, 0, 0xE0B10905)]:
            self.cam.mem[ri.REC_STATE_WORD] = old
            self.lib.rec_state_observe((U * 6)(new, 0, 0, 0, 0, lr))
        self.assertEqual(self.stop_seq(), 0)
        # 2 -> 3 from the confirming caller arms it once; every idle set clears it.
        for old, new, lr in [(1, 2, 0xE0B0BF2B), (2, 3, 0xE0B0BD2D), (3, 0, 0xE0B10905), (0, 0, 0xE0B10905)]:
            self.cam.mem[ri.REC_STATE_WORD] = old
            self.lib.rec_state_observe((U * 6)(new, 0, 0, 0, 0, lr))
        self.assertEqual((self.stop_seq(), self.lib.host_press_seq()), (1, 1))


class StopOnlyTests(StopCueBase):
    """--record-stop-indicator dong without --record-indicator: the observer hook and worker
    are linked for the dong alone; REC presses never select a cue."""
    START_CUE = "off"

    def test_only_the_stop_buffer_is_linked_and_published(self):
        s = self.st()
        self.assertEqual((s.ready, s.fault), (1, 0))
        self.assertEqual((self.lib.host_pcm(0), self.lib.host_desc(0)), (0, 0))
        self.assertEqual(hashlib.sha256(ctypes.string_at(self.lib.host_pcm(1), 24000)).hexdigest(), DONG_PCM_SHA256)
        self.assertEqual(U.in_dll(self.lib, "host_clean_count").value, 2)
        self.assertIsNone(self.t["cues"]["start"])
        self.assertEqual((self.t["cue"], self.t["stop_cue"]), ("off", "dong"))

    def test_takes_play_only_the_dong(self):
        for _ in range(2):
            per_set = self.run_take()
            self.assertEqual(per_set, [[], [], [], [], [], [], [STOP]])
        s = self.st()
        self.assertEqual(self.cam.beeps, [(2, STOP, self.cam.desc(1))] * 2)
        self.assertEqual((s.presses, s.coalesced, s.submits, s.stop_submits, s.dropped_not_recording),
                         (0, 0, 2, 2, 0), "presses are counted by the hook but never read")
        self.assertEqual(self.lib.host_press_seq(), 2)
        self.assert_no_unowned()

    def test_press_counter_without_a_start_cue_never_supersedes_the_dong(self):
        self.run_take(TAKE[:-1])
        self.cam.states[self.cam.ABH] = 2
        self.observe(5, 0, 0xE0B10905)
        self.step()
        self.lib.host_add_press(1)          # the counter moves, RecState stays idle
        self.cam.states[self.cam.ABH] = 1
        self.step()
        s = self.st()
        self.assertEqual((self.cues(), s.stop_superseded), ([1], 0))

    def test_new_take_while_the_dong_is_pending_drops_it_as_not_idle(self):
        self.run_take(TAKE[:-1])
        self.cam.states[self.cam.ABH] = 2   # busy when the take ends
        self.observe(5, 0, 0xE0B10905)
        self.step()
        self.observe(0, 1, 0xE0B0B883)      # a real REC press: RecState 1
        self.cam.states[self.cam.ABH] = 1
        self.step(15)
        s = self.st()
        self.assertEqual(self.cam.beeps, [], "never a late dong in the next take")
        self.assertEqual((s.stop_not_idle, s.stop_superseded, s.presses), (1, 0, 0))


# --- build plumbing, contracts and CLI (no toolchain) ----------------------

class BuildPlumbingTests(unittest.TestCase):
    def test_choices_and_facts(self):
        self.assertEqual(ri.CHOICES[0], "off")
        self.assertIn("ding", ri.CHOICES)
        self.assertEqual(ri.STOP_CHOICES, ("off", "dong"))
        self.assertNotIn("dong", ri.CHOICES)
        self.assertEqual(ri.admit_choice(None), "off")
        self.assertEqual(ri.admit_choice(None, "stop"), "off")
        with self.assertRaisesRegex(ValueError, "unknown --record-indicator"):
            ri.admit_choice("loud")
        for bad in ("ding", "loud"):
            with self.assertRaisesRegex(ValueError, "unknown --record-stop-indicator"):
                ri.admit_choice(bad, "stop")
        with self.assertRaisesRegex(ValueError, "unknown --record-indicator"):
            ri.admit_choice("dong")
        with self.assertRaisesRegex(ValueError, "start or stop cue"):
            ri.facts("off", "off")
        self.assertFalse(ri.enabled("off", "off"))
        self.assertTrue(ri.enabled("off", "dong") and ri.enabled("ding", "off") and ri.enabled("ding", "dong"))
        for start in ri.CHOICES:
            for stop in ri.STOP_CHOICES:
                if not ri.enabled(start, stop):
                    continue
                t = ri.facts(start, stop)
                self.assertEqual((t["cue"], t["stop_cue"]), (start, stop))
                self.assertEqual((t["se_id"], t["trigger"], t["actrl_mask"], t["stop_actrl_mask"]), (2, "press", 0x32, 0x12))
                self.assertEqual((t["rec_state_mask"], t["stop_state_mask"]), (0b1110, 0b1))
                self.assertTrue(t["stop_retry_mask"] & (1 << PF["ACTRL_STATE"]), "the stop cue waits out s5")
                for role, name in (("start", start), ("stop", stop)):
                    cue = t["cues"][role]
                    self.assertEqual(cue is None, name == "off")
                    if cue:
                        self.assertEqual((cue["cue"], cue["role"]), (name, role))
                        self.assertLessEqual(cue["duration_ms"], ri.MAX_MS)
                        self.assertEqual(cue["bytes"] % 32, 0)
        self.assertFalse(ri.cue_facts("chirp2", "start")["exceeds_se_patience"])
        with self.assertRaisesRegex(ValueError, "unknown record indicator stop cue"):
            ri.cue_facts("ding", "stop")

    def test_descriptor_config_and_bindings_come_from_firmware_json(self):
        t = ri.facts("ding", "dong")
        for role in ri.ROLES:
            desc = struct.unpack("<16I", ri.pending_descriptor(t["cues"][role]))
            self.assertEqual((desc[0], desc[1], desc[2], desc[9], desc[10], desc[11]), (1, 0, 24000, 48, 16, 1))
        words = ri.config_words(t, AUDIO)
        self.assertEqual((len(words), words[0], words[1]), (14, 0x16DD4, AUDIO["singleton_slot"]))
        self.assertEqual(words, ri.config_words(ri.facts("ding"), AUDIO), "config is independent of the cues")
        src = ri.bindings_source(t, AUDIO)
        self.assertIn(f"(rec_tone_query_fn){hex(AUDIO['state_query'])}u", src)
        self.assertIn(f"(rec_tone_beep_fn){hex(AUDIO['beep_start'])}u", src)
        self.assertIn("int16_t rec_tone_pcm[12000] __attribute__((aligned(32)));", src)
        self.assertIn("int16_t rec_tone_stop_pcm[12000] __attribute__((aligned(32)));", src)
        self.assertEqual(src.count('.resident_data"), aligned(32)'), 2)
        self.assertIn("{rec_tone_stop_segments, rec_tone_stop_pcm, rec_tone_stop_descriptor, 2u, 12000u, 24000u}", src)
        self.assertIn("REC_TONE_STOP_RETRY_MASK == 0x84u", src)
        start_only = ri.bindings_source(ri.facts("ding"), AUDIO)
        stop_only = ri.bindings_source(ri.facts("off", "dong"), AUDIO)
        self.assertNotIn("rec_tone_stop_", start_only)
        self.assertNotIn("rec_tone_pcm", stop_only)
        self.assertIn("    {0, 0, 0, 0u, 0u, 0u},\n", start_only)
        self.assertIn("    {0, 0, 0, 0u, 0u, 0u},\n", stop_only)
        bad = dict(AUDIO, beep_start=AUDIO["beep_start"] & ~1)
        with self.assertRaisesRegex(ValueError, "Thumb"):
            ri.bindings_source(t, bad)
        header = (XPAN_SRC / "rec_tone.h").read_text()
        self.assertIn("_Static_assert(sizeof(struct rec_tone_config) == 56u", header)
        self.assertEqual(ri.CONFIG_WORDS * 4, 56)

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
        wavs = ["--startup-wav", "a.wav", "--shutter-wav", "b.wav"]
        with tempfile.TemporaryDirectory() as tmp:
            for argv, flag in ((["--profile", "xpan-crop", "--record-indicator", "loud"], "--record-indicator"),
                               (["--record-indicator", "ding", *wavs], "--record-indicator"),
                               (["--record-indicator", "off", *wavs], "--record-indicator"),
                               (["--profile", "xpan-crop", "--record-indicator", "dong"], "--record-indicator"),
                               (["--profile", "xpan-crop", "--record-stop-indicator", "ding"], "--record-stop-indicator"),
                               (["--profile", "xpan-crop", "--record-stop-indicator", "loud"], "--record-stop-indicator"),
                               (["--record-stop-indicator", "dong", *wavs], "--record-stop-indicator"),
                               (["--record-stop-indicator", "off", *wavs], "--record-stop-indicator")):
                out = Path(tmp) / "out"
                proc = subprocess.run([sys.executable, str(ROOT / "tools" / "build.py"), "--rom", "r.bin",
                                       "--toolchain", "t", "--output", str(out), *argv], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2, argv)
                self.assertIn(flag, proc.stderr, argv)
                self.assertFalse(out.exists(), argv)
            help_text = subprocess.run([sys.executable, str(ROOT / "tools" / "build.py"), "--help"],
                                       capture_output=True, text=True).stdout
            self.assertIn("--record-stop-indicator {off,dong}", help_text)
        import build as build_mod
        for kwargs, flag in ((dict(record_indicator="ding"), "--record-indicator"),
                             (dict(record_stop_indicator="dong"), "--record-stop-indicator")):
            with self.assertRaisesRegex(ValueError, flag + " requires --profile xpan-crop"):
                build_mod.build_image(rom_path=Path("r.bin"), startup_wav=Path("a"), shutter_wav=Path("b"),
                                      toolchain_dir=Path("t"), output_dir=Path("o"), **kwargs)


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


# Default-off byte identity. Recorded from the mock build of main d0cba72, before the
# stop cue existed: autoexec.bin, the normalized compile/link commands and every
# generated build input except those carrying the source digest or a host path.
# A change here means the default xpan-crop build changed; update it only for a
# deliberate default-build change.
DEFAULT_OFF_MOCK_IMAGE_SHA256 = "d947aeb135c56d02c169d967bb363bc3c48a7fbdfc6b49a47c52d648bbc2241a"
DEFAULT_OFF_MOCK_DIGEST = "3fc07046c693c870270a3abc34e316ee15e74ed27679ab807c8024ac6ab8665a"
# Outputs that carry the source digest (every tracked input change moves it) or
# temporary host paths; everything else must be byte-identical.
DIGEST_OR_PATH_OUTPUTS = {"build.json", "build.log", "source-manifest.json", "observer-source.h",
                          "expected-R5BOOT.TXT", "witness-template.bin", "frame-row/frame_row_profile.json"}


def default_off_digest(out, tmp):
    """sha256 over the default-off mock outputs, normalized for host paths."""
    files = {str(f.relative_to(out)): hashlib.sha256(f.read_bytes()).hexdigest()
             for f in sorted(out.rglob("*")) if f.is_file() and str(f.relative_to(out)) not in DIGEST_OR_PATH_OUTPUTS}
    # Resolved and unresolved forms (macOS /var -> /private/var), longest first.
    subs = sorted({(str(ROOT), "$REPO"), (str(Path(tmp).resolve()), "$TMP"), (str(tmp), "$TMP")},
                  key=lambda sub: -len(sub[0]))
    argv = []
    for line in (out / "build.log").read_text().splitlines():
        if line.startswith("["):
            cmd = json.loads(line)
            for old, new in subs:
                cmd = [a.replace(old, new) for a in cmd]
            argv.append(cmd)
    configuration = json.loads((out / "source-manifest.json").read_text())["configuration"]
    blob = json.dumps({"files": files, "argv": argv, "configuration": configuration}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest(), files, argv


def synthetic_linked(t):
    """A finalized-image stand-in laid out from the build's own encoders, for verify_linked."""
    base, load = 0x1A0000, 0x40
    symbols = {"_resident_start": base, "_resident_load_offset": load}
    data = bytearray(0x8000)
    at = base + 0x100

    def place(name, blob, align=32):
        nonlocal at
        at = (at + align - 1) & ~(align - 1)
        symbols[name] = at
        data[load + at - base : load + at - base + len(blob)] = blob
        at += max(len(blob), 4)
    for name in ri.SYMBOLS:
        place(name, b"")
    for role in ri.ROLES:
        cue = t["cues"][role]
        if cue:
            segs, _, desc = ri.CUE_SYMBOLS[role]
            place(segs, bytes.fromhex(cue["segments_hex"]))
            place(desc, ri.pending_descriptor(cue))
    place("rec_tone_config", struct.pack(f"<{ri.CONFIG_WORDS}I", *ri.config_words(t, AUDIO)))
    symbols["_resident_data_end"] = at + 64
    symbols["_bss_start"] = bss = (at + 4096) & ~4095
    for role in ri.ROLES:
        if t["cues"][role]:
            symbols[ri.CUE_SYMBOLS[role][1]] = bss
            bss += t["cues"][role]["bytes"]
    symbols["_bss_objects_end"] = bss
    place_at = lambda name, blob: data.__setitem__(slice(load + symbols[name] - base,
                                                         load + symbols[name] - base + len(blob)), blob)
    place_at("rec_tone_cues", struct.pack(f"<{2 * ri.CUE_WORDS}I", *ri.cue_words(t, symbols)))
    place_at("rec_tone_api", struct.pack("<4I", symbols["rec_tone_native_read"] | 1, AUDIO["state_query"],
                                         AUDIO["beep_start"], symbols["rec_tone_native_clean"] | 1))
    return data, symbols


class LinkedCheckTests(unittest.TestCase):
    """verify_linked against a synthetic image built from the same encoders (no toolchain)."""

    def check(self, t, data, symbols, remaining=900000):
        return ri.verify_linked(bytes(data), symbols, t, AUDIO, minimum_user_left=MANIFEST["minimum_user_left"],
                                remaining_user_bytes=remaining)

    def test_accepts_each_selection_and_records_both_buffers(self):
        for start, stop in (("ding", "off"), ("off", "dong"), ("ding", "dong"), ("chirp2", "dong")):
            with self.subTest(start=start, stop=stop):
                t = ri.facts(start, stop)
                data, symbols = synthetic_linked(t)
                rec = self.check(t, data, symbols)
                for role in ri.ROLES:
                    self.assertEqual(rec["cues"][role] is None, t["cues"][role] is None)
                    if rec["cues"][role]:
                        self.assertEqual(rec["cues"][role]["pcm_address"], symbols[ri.CUE_SYMBOLS[role][1]])

    def test_rejects_wrong_or_unselected_cues_and_the_heap_floor(self):
        t = ri.facts("ding", "dong")
        data, symbols = synthetic_linked(t)
        start_only = ri.facts("ding")
        one, one_symbols = synthetic_linked(start_only)
        with self.assertRaisesRegex(ValueError, "cue linked without its option"):
            self.check(start_only, one, {**one_symbols, "rec_tone_stop_pcm": 0x300000})
        table = symbols["rec_tone_cues"]
        bad = bytearray(data)
        bad[0x40 + table - symbols["_resident_start"] + 4 * 11] ^= 1   # stop bytes word
        with self.assertRaisesRegex(ValueError, "cue table differs"):
            self.check(t, bad, symbols)
        bad = bytearray(data)
        bad[0x40 + symbols["rec_tone_stop_descriptor"] - symbols["_resident_start"] + 8] ^= 1
        with self.assertRaisesRegex(ValueError, "stop descriptor differs"):
            self.check(t, bad, symbols)
        with self.assertRaisesRegex(ValueError, "stop PCM outside aligned"):
            self.check(t, data, {**symbols, "rec_tone_stop_pcm": symbols["rec_tone_stop_pcm"] + 2})
        with self.assertRaisesRegex(ValueError, "symbols missing"):
            self.check(t, data, {k: v for k, v in symbols.items() if k != "rec_stop_seq"})
        with self.assertRaisesRegex(ValueError, "heap floor"):
            self.check(t, data, symbols, remaining=MANIFEST["minimum_user_left"] - 1)


class MockBuildTests(unittest.TestCase):
    def _build(self, indicator, hooks_key, stop=None):
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
        self.tmp = tmp.name

        def call():
            with mock.patch.dict(os.environ, {}, clear=True):
                return build_mod.build_image(
                    rom_path=root / "rom.bin", toolchain_dir=tc, output_dir=out, profile="xpan-crop",
                    manifest=manifest, target_contract=contract, xpan_contract=xpan_contract,
                    xpan_hooks=indicator_hooks if hooks_key else hooks, record_indicator=indicator,
                    record_stop_indicator=stop)
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

    def test_default_build_refuses_a_retained_indicator_symbol(self):
        from unittest import mock
        call, _ = self._build(None, False)
        # The mock linker emits sound_worker_entry; pretend it were an indicator symbol.
        with mock.patch.object(ri, "ALL_SYMBOLS", (*ri.ALL_SYMBOLS, "sound_worker_entry")):
            with self.assertRaisesRegex(ValueError, "record indicator retained without opt-in: sound_worker_entry"):
                call()

    def test_default_off_build_is_byte_identical_to_the_pre_stop_cue_build(self):
        digests = []
        for indicator, stop in ((None, None), ("off", "off")):
            call, out = self._build(indicator, False, stop)
            record = call()
            self.assertEqual(record["format"]["sha256"], DEFAULT_OFF_MOCK_IMAGE_SHA256)
            self.assertEqual(hashlib.sha256((out / "autoexec.bin").read_bytes()).hexdigest(),
                             DEFAULT_OFF_MOCK_IMAGE_SHA256)
            digest, files, argv = default_off_digest(out, self.tmp)
            self.assertEqual(digest, DEFAULT_OFF_MOCK_DIGEST, json.dumps({"files": files, "argv": argv}, indent=1))
            log = (out / "build.log").read_text()
            for absent in ("R5_RECORD_INDICATOR", "rec_state", "rec_tone", "tone_gen"):
                self.assertNotIn(absent, log)
            digests.append(digest)
        self.assertEqual(digests[0], digests[1], "None and explicit off are the same build")

    def test_indicator_build_admits_compiles_and_fails_closed_at_the_linked_check(self):
        for start, stop in (("ding", None), ("ding", "dong"), (None, "dong")):
            with self.subTest(start=start, stop=stop):
                call, out = self._build(start, True, stop)
                # The mock linker emits no indicator symbols: the linked-image owner must refuse.
                with self.assertRaisesRegex(ValueError, "record indicator symbols missing"):
                    call()
                log = (out / "build.log").read_text()
                self.assertIn("-DR5_RECORD_INDICATOR=1", log)
                for name in ("rec_state.c", "tone_gen.c", "rec_tone.c", "rec-tone-bindings.c", "rec_state_hook.S"):
                    self.assertIn(name, log)
                # One hook inventory for any cue selection: the same capacity and fingerprinted pages.
                feature = json.loads((out / "feature-contract.json").read_text())
                self.assertEqual(len(feature["hooks"]), 35)
                self.assertEqual(len(feature["pages"]), 21)
                self.assertEqual(len(feature["source_guard_pages"]), 139)
                manifest = json.loads((out / "source-manifest.json").read_text())
                config = manifest["configuration"]["record_indicator"]
                self.assertEqual((config["cue"], config["stop_cue"]), (start or "off", stop or "off"))
                self.assertIn("src/experimental/xpan/rec_tone.c", manifest["inputs"])
                bindings = (out / "rec-tone-bindings.c").read_text()
                self.assertEqual("rec_tone_stop_pcm" in bindings, stop is not None)
                self.assertEqual("rec_tone_pcm[" in bindings, start is not None)

    def test_indicator_requires_its_hook_and_fingerprinted_pages(self):
        for start, stop in (("ding", None), (None, "dong"), ("ding", "dong")):
            call, _ = self._build(start, False, stop)
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
            for kwargs in (dict(record_indicator="ding"), dict(record_stop_indicator="dong")):
                with self.assertRaisesRegex(ValueError, "record_indicator_page_e0b0b000"):
                    build_mod.build_image(rom_path=root / "rom.bin", toolchain_dir=root / "tc",
                                          output_dir=root / "out", profile="xpan-crop", manifest=manifest,
                                          target_contract=contract, xpan_contract=xpan_contract,
                                          xpan_hooks=indicator_hooks, **kwargs)
                self.assertFalse((root / "out").exists())


@unittest.skipUnless(os.environ.get("R5_RECORD_INDICATOR_BUILD"), "opt-in real-ROM build verification")
class RealBuildTests(unittest.TestCase):
    def test_linked_indicator_tables(self):
        out = Path(os.environ["R5_RECORD_INDICATOR_BUILD"])
        build = json.loads((out / "build.json").read_text())
        symbols = json.loads((out / "symbols.json").read_text())
        data = (out / "autoexec.bin").read_bytes()
        linked = build["record_indicator"]
        t = ri.facts(linked["cue"], linked["stop_cue"])
        again = ri.verify_linked(data, symbols, t, AUDIO, minimum_user_left=MANIFEST["minimum_user_left"],
                                 remaining_user_bytes=build["reservation"]["remaining_user_bytes"])
        self.assertEqual(again, linked)
        for role in ri.ROLES:
            if linked["cues"][role]:
                self.assertEqual(linked["cues"][role]["pcm_address"] % 32, 0)
                self.assertEqual(linked["cues"][role]["descriptor_address"] % 32, 0)
        self.assertGreaterEqual(build["reservation"]["remaining_user_bytes"], MANIFEST["minimum_user_left"])
        feature = json.loads((out / "feature-contract.json").read_text())
        self.assertIn("rec_state_entry", [h["destination"] for h in feature["hooks"]])
        self.assertEqual((len(feature["hooks"]), len(feature["pages"]), len(feature["source_guard_pages"])),
                         (35, 21, 139))


if __name__ == "__main__":
    unittest.main()
