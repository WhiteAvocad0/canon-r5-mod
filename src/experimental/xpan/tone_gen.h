#ifndef R5_TONE_GEN_H
#define R5_TONE_GEN_H
#include <stdint.h>

/* Issue #14: procedural record-indicator cue. 48 kHz mono s16le only, matching the
 * canonical ABeep PCM descriptor (+0x24 rate code 48, +0x28 16 bits, +0x2C 1
 * channel; tools/audio_profile.py descriptor()).
 *
 * Owner of the cue shape: a cue is a sequence of segments; a segment is
 * silence or the sum of up to TONE_MAX_PARTIALS sine partials, each with its
 * own exponential decay, under one raised-cosine attack and one raised-cosine
 * release. A single-tone cue (2k150loud) is one segment with one undecayed
 * partial and attack == release; tone_ref.generate() is its independent
 * single-tone oracle (tests/test_record_indicator.py).
 *
 * Integer-only: no libm, no division, no 64-bit runtime arithmetic, so the
 * freestanding Cortex-A9 object needs no libgcc helper (the resident link uses
 * ld --no-undefined without libgcc). Right shifts of negative int32 values are
 * arithmetic (GCC/Clang documented behaviour); tone_ref.py mirrors that. */

#define TONE_RATE_HZ 48000u
#define TONE_DMA_ALIGN_BYTES 32u
#define TONE_ALIGN_SAMPLES (TONE_DMA_ALIGN_BYTES / 2u)
#define TONE_MAX_FADE_SHIFT 14u
#define TONE_MAX_SAMPLES 0x00fffff0u
#define TONE_MAX_PARTIALS 3u
/* Sum of a segment's partial amplitudes. Keeps every intermediate product in
 * int32 (65535 * 32768 < 2^31); the output saturates at +-32767. */
#define TONE_MAX_AMPLITUDE_SUM 65535u
#define TONE_ENV_ONE (UINT32_C(1) << 30)

/* Constant expressions only: the division happens at compile time. */
#define TONE_PHASE_INC(hz) ((uint32_t)((((uint64_t)(hz)) << 32) / TONE_RATE_HZ))
#define TONE_SAMPLES_MS(ms) ((uint32_t)(ms) * (TONE_RATE_HZ / 1000u))
#define TONE_PADDED_SAMPLES(n) ((((uint32_t)(n)) + (TONE_ALIGN_SAMPLES - 1u)) & ~(TONE_ALIGN_SAMPLES - 1u))

struct tone_partial {
    uint32_t phase_inc;     /* 2^32 per cycle per sample; 0 < phase_inc < 2^31 (below Nyquist) */
    uint16_t amplitude_q15; /* 1..32767 */
    /* Per-sample envelope step: env -= (env >> 16) * decay, env starting at
     * 2^30, i.e. a multiplier of about 1 - decay/65536 per sample
     * (time constant about 65536/decay samples). 0 = constant. */
    uint16_t decay;
};
struct tone_segment {
    uint32_t samples;       /* 1..TONE_MAX_SAMPLES */
    uint8_t partial_count;  /* 0 = silence (exact zeros), 1..TONE_MAX_PARTIALS */
    uint8_t attack_shift;   /* raised-cosine fade-in of 1 << attack_shift samples */
    uint8_t release_shift;  /* raised-cosine fade-out of 1 << release_shift samples */
    uint8_t reserved;       /* 0 */
    struct tone_partial partials[TONE_MAX_PARTIALS]; /* unused entries all zero */
};
_Static_assert(sizeof(struct tone_partial) == 8u, "tone partial ABI");
_Static_assert(sizeof(struct tone_segment) == 32u, "tone segment ABI");

/* sin(2*pi*phase/2^32) in Q15, range [-32767, 32767]. Quarter-wave odd
 * quintic; coefficients sum to 32768 so sin(pi/2) saturates to 32767.
 * Maximum absolute error 3.72 LSB (1.14e-4 FS), exhaustively checked. */
int32_t tone_sin_q15(uint32_t phase);

/* 1 when the segment is canonical and admitted (see the field comments; a
 * silent segment has zero shifts and all-zero partials; a tonal segment has
 * samples >= (1 << attack_shift) + (1 << release_shift), so the first and last
 * samples have zero fade gain and the segment starts and ends at 0). */
int tone_segment_valid(const struct tone_segment *segment);

/* Writes the segments back to back, then zero padding up to
 * TONE_PADDED_SAMPLES(total), and returns that padded count. Each partial's
 * phase starts at 0 and its envelope at 1.0 at the start of its segment.
 * Returns 0 without writing on any invalid argument: null pointers, dst not
 * 32-byte aligned, count 0, an invalid segment, a total above
 * TONE_MAX_SAMPLES, or capacity_samples too small.
 * No cache maintenance and no publication: the caller owns both. */
uint32_t tone_gen_fill(int16_t *dst, uint32_t capacity_samples, const struct tone_segment *segments,
                       uint32_t count);

#endif
