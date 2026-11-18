"""Host reference for tone_gen.c: bit-exact integer models, a float model and the
record-indicator cue catalog (the host owner of every cue shape the build can select).

Python's >> on negative ints is floor division by a power of two, which is the
arithmetic shift GCC/Clang use for int32 in tone_gen.c.

generate() is an independent single-tone reference: generate_segments() of a
one-segment single-tone cue (2k150loud) must equal it.
"""
import math
import struct

RATE_HZ = 48000
ALIGN_SAMPLES = 16  # 32-byte DMA alignment / 2 bytes per sample
MAX_FADE_SHIFT = 14
MAX_SAMPLES = 0x00FFFFF0
SIN_A, SIN_B, SIN_C = 51452, -21021, 2337


def phase_inc(hz):
    return (int(hz) << 32) // RATE_HZ


def samples_ms(ms):
    return int(ms) * (RATE_HZ // 1000)


def padded(n):
    return (n + ALIGN_SAMPLES - 1) & ~(ALIGN_SAMPLES - 1)


def _round_q15(v):
    return (v + 16384) >> 15


def sin_q15(phase, a=SIN_A, b=SIN_B, c=SIN_C):
    phase &= 0xFFFFFFFF
    quadrant = phase >> 30
    x = (phase >> 15) & 0x7FFF
    if quadrant & 1:
        x = 32768 - x
    x2 = _round_q15(x * x)
    t = _round_q15(c * x2)
    t = _round_q15((b + t) * x2)
    y = min(_round_q15((a + t) * x), 32767)
    return -y if quadrant & 2 else y


def spec_valid(phase_inc_, samples, fade_shift, amplitude_q15):
    return (0 < phase_inc_ < 0x80000000 and 0 <= amplitude_q15 <= 32767
            and 0 <= fade_shift <= MAX_FADE_SHIFT and 0 < samples <= MAX_SAMPLES
            and samples >= (2 << fade_shift))


def generate(phase_inc_, samples, fade_shift, amplitude_q15):
    """Return the padded int16 list tone_gen_fill must produce."""
    if not spec_valid(phase_inc_, samples, fade_shift, amplitude_q15):
        raise ValueError("invalid tone spec")
    fade = 1 << fade_shift
    out = []
    phase = 0
    for n in range(samples):
        v = _round_q15(amplitude_q15 * sin_q15(phase))
        edge = min(n, samples - 1 - n)
        if edge < fade:
            s = sin_q15(edge << (30 - fade_shift))
            v = _round_q15(v * _round_q15(s * s))
        out.append(v)
        phase = (phase + phase_inc_) & 0xFFFFFFFF
    out.extend([0] * (padded(samples) - samples))
    return out


def generate_float(phase_inc_, samples, fade_shift, amplitude_q15):
    """Ideal model with the same (truncated) phase increment, in Q15 units."""
    fade = 1 << fade_shift
    out = []
    for n in range(samples):
        v = amplitude_q15 * math.sin(2 * math.pi * ((n * phase_inc_) & 0xFFFFFFFF) / 2**32)
        edge = min(n, samples - 1 - n)
        if edge < fade:
            v *= math.sin(math.pi / 2 * edge / fade) ** 2
        out.append(v)
    out.extend([0.0] * (padded(samples) - samples))
    return out


# --- Multi-segment cue shapes (tone_gen.h struct tone_segment) -------------
# A segment is (samples, attack_shift, release_shift, partials), partials a tuple
# of (phase_inc, amplitude_q15, decay). partials == () is exact silence.
MAX_PARTIALS = 3
MAX_AMPLITUDE_SUM = 65535
ENV_ONE = 1 << 30


def segment(samples, partials=(), attack_shift=0, release_shift=0):
    return (int(samples), int(attack_shift), int(release_shift),
            tuple((int(i), int(a), int(d)) for i, a, d in partials))


def tone(hz, ms, fade_shift, amplitude_q15):
    """A single-tone cue as one segment (attack == release == fade_shift)."""
    return segment(samples_ms(ms), ((phase_inc(hz), amplitude_q15, 0),), fade_shift, fade_shift)


def silence(ms):
    return segment(samples_ms(ms))


def segment_valid(seg):
    samples, attack, release, partials = seg
    if not 0 < samples <= MAX_SAMPLES or len(partials) > MAX_PARTIALS:
        return False
    if not partials:
        return attack == 0 and release == 0
    if not (0 <= attack <= MAX_FADE_SHIFT and 0 <= release <= MAX_FADE_SHIFT):
        return False
    if not all(0 < i < 0x80000000 and 0 < a <= 32767 and 0 <= d <= 0xFFFF for i, a, d in partials):
        return False
    if sum(a for _, a, _ in partials) > MAX_AMPLITUDE_SUM:
        return False
    return samples >= (1 << attack) + (1 << release)


def shape_samples(segments):
    return sum(s[0] for s in segments)


def pack_segment(seg):
    """The exact 32-byte struct tone_segment image (little endian)."""
    samples, attack, release, partials = seg
    words = list(partials) + [(0, 0, 0)] * (MAX_PARTIALS - len(partials))
    return struct.pack("<IBBBx", samples, len(partials), attack, release) + b"".join(
        struct.pack("<IHH", i, a, d) for i, a, d in words)


def _fade_gain(k, shift):
    s = sin_q15(k << (30 - shift))
    return _round_q15(s * s)


def _segment_values(seg, clamp=True):
    samples, attack_shift, release_shift, partials = seg
    if not partials:
        return [0] * samples
    attack, release = 1 << attack_shift, 1 << release_shift
    phase = [0] * len(partials)
    env = [ENV_ONE] * len(partials)
    out = []
    for n in range(samples):
        v = 0
        for k, (inc, amp, decay) in enumerate(partials):
            x = _round_q15(amp * sin_q15(phase[k]))
            x = _round_q15(x * (env[k] >> 15))
            env[k] -= (env[k] >> 16) * decay
            phase[k] = (phase[k] + inc) & 0xFFFFFFFF
            v += x
        tail = samples - 1 - n
        if n < attack:
            v = _round_q15(v * _fade_gain(n, attack_shift))
        elif tail < release:
            v = _round_q15(v * _fade_gain(tail, release_shift))
        out.append(max(-32767, min(32767, v)) if clamp else v)
    return out


def generate_segments(segments, clamp=True):
    """Return the padded int16 list tone_gen_fill(dst, cap, segments, count) must produce.

    clamp=False returns the values before the +-32767 saturation (to prove it never engages)."""
    if not segments or not all(segment_valid(s) for s in segments) or shape_samples(segments) > MAX_SAMPLES:
        raise ValueError("invalid tone shape")
    out = []
    for seg in segments:
        out.extend(_segment_values(seg, clamp))
    out.extend([0] * (padded(len(out)) - len(out)))
    return out


def effective_hz(inc):
    """The frequency a truncated phase increment actually plays."""
    return inc * RATE_HZ / 2**32


# --- Cue catalog ----------------------------------------------------------
# Raised-cosine fades of 128 samples (2.67 ms) on every new note.
NOTE_FADE_SHIFT = 7
PEAK_Q15 = 31000  # about -0.48 dBFS

# ding: an original struck-bar chime (synthesized here; no recorded or sampled
# sound). Fundamental C7, 2093 Hz. The two upper partials sit at the first two
# overtone ratios of an ideal free-free bar (Euler-Bernoulli modes 2.756 and
# 5.404 x the fundamental: the inharmonic series of glockenspiel/chime bars),
# rounded to whole hertz: 5768 Hz and 11311 Hz. Levels -9 dB and -18 dB
# relative to the fundamental. Each upper partial decays twice as fast as the
# one below it (damping rising with frequency): decay 20/40/80, i.e. time
# constants of about 65536/decay samples = 68.3/34.1/17.1 ms. 23140 is the
# largest round fundamental amplitude whose summed peak stays <= PEAK_Q15
# (tested); 8210 = 23140 * 10^(-9/20) and 2913 = 23140 * 10^(-18/20), rounded.
DING_HZ = (2093, 5768, 11311)
DING_AMPLITUDES = (23140, 8210, 2913)
DING_DECAY = (20, 40, 80)
DING_MS = 250
DING_ATTACK_SHIFT = 7   # 128 samples, 2.67 ms
DING_RELEASE_SHIFT = 8  # 256 samples, 5.33 ms final fade to exactly 0

# dong: the record-stop counterpart of the ding (original, synthesized here; no
# recorded or sampled sound). Two struck-bar notes falling a perfect fifth, G6
# 1568 Hz then C6 1047 Hz: the second resolves to C an octave below the ding's
# C7, so the pair reads as "start" (high, bright) and "stop" (lower, closing).
# Each note has two partials, the fundamental and the first free-free bar mode
# (2.756 x, rounded to whole hertz: 4321 Hz and 2886 Hz) at -9 dB, decaying twice
# as fast as its fundamental. The ding's third (5.404 x) partial is left out, so
# the dong is darker. Note 1 is a short strike (90 ms, decay 40/80: time
# constants about 34/17 ms); note 2 rings longer (160 ms, decay 24/48: about
# 57/28 ms). Same attack/release as the ding (2.67 ms / 5.33 ms per note), so
# each note starts and ends at exactly 0 and the pair is 250 ms like the ding.
# 25290 is the largest round (x10) shared fundamental amplitude whose peak stays
# <= PEAK_Q15 (tested); 8973 = 25290 * 10^(-9/20), rounded.
DONG_NOTES = ((1568, 90, 40), (1047, 160, 24))   # (fundamental Hz, ms, decay)
DONG_OVERTONE_RATIO = 2.756
DONG_AMPLITUDES = (25290, 8973)
DONG_ATTACK_SHIFT = DING_ATTACK_SHIFT
DONG_RELEASE_SHIFT = DING_RELEASE_SHIFT


def _dong_note(hz, ms, decay):
    upper = round(hz * DONG_OVERTONE_RATIO)
    return segment(samples_ms(ms), ((phase_inc(hz), DONG_AMPLITUDES[0], decay),
                                    (phase_inc(upper), DONG_AMPLITUDES[1], 2 * decay)),
                   DONG_ATTACK_SHIFT, DONG_RELEASE_SHIFT)


# --record-indicator (start) cues and --record-stop-indicator (stop) cues are
# separate catalogs: a stop cue is never offered as a start cue, or the reverse.
VARIANTS = {
    # Default record indicator: the struck-bar chime above (250 ms).
    "ding": (segment(samples_ms(DING_MS),
                     tuple((phase_inc(h), a, d) for h, a, d in zip(DING_HZ, DING_AMPLITUDES, DING_DECAY)),
                     DING_ATTACK_SHIFT, DING_RELEASE_SHIFT),),
    # Rising two-note chirp: A6 1760 Hz 45 ms, 20 ms silence, E7 2637 Hz 55 ms.
    "chirp2": (tone(1760, 45, NOTE_FADE_SHIFT, PEAK_Q15), silence(20),
               tone(2637, 55, NOTE_FADE_SHIFT, PEAK_Q15)),
    # Single 2 kHz tone, 150 ms, about -0.48 dBFS.
    "2k150loud": (tone(2000, 150, 8, PEAK_Q15),),
}

STOP_VARIANTS = {
    # Record-stop cue: the falling G6 -> C6 pair above (250 ms).
    "dong": tuple(_dong_note(h, ms, d) for h, ms, d in DONG_NOTES),
}


def to_bytes(values):
    return struct.pack("<%dh" % len(values), *values)


def sin_max_error_lsb(a=SIN_A, b=SIN_B, c=SIN_C):
    """Exhaustive over every quarter-wave input; the other quadrants mirror it."""
    return max(abs(sin_q15(x << 15, a, b, c) - 32768 * math.sin(math.pi / 2 * x / 32768))
               for x in range(32768))


def search_coefficients(a_range=range(51440, 51462), c_range=range(2325, 2352)):
    """Reproduce the coefficient choice (B is fixed by A + B + C == 32768).

    Includes x == 32768 (the mirrored quadrant boundary)."""
    def err(a, b, c):
        worst = 0.0
        for x in range(32769):
            x2 = _round_q15(x * x)
            t = _round_q15(c * x2)
            t = _round_q15((b + t) * x2)
            y = min(_round_q15((a + t) * x), 32767)
            worst = max(worst, abs(y - 32768 * math.sin(math.pi / 2 * x / 32768)))
        return worst
    best = None
    for a in a_range:
        for c in c_range:
            e = err(a, 32768 - a - c, c)
            if best is None or e < best[0]:
                best = (e, a, 32768 - a - c, c)
    return best
