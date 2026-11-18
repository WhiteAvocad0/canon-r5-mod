#include "tone_gen.h"
#ifdef OBS_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif

/* Odd quintic on one quarter turn, x in Q15 [0, 32768]:
 * sin(pi/2 * x) ~= x * (A + x^2 * (B + x^2 * C)), A + B + C == 32768.
 * Chosen by exhaustive search over all 32769 x values (tone_ref.py). */
#define TONE_SIN_A 51452
#define TONE_SIN_B (-21021)
#define TONE_SIN_C 2337
_Static_assert(TONE_SIN_A + TONE_SIN_B + TONE_SIN_C == 32768, "sin(pi/2) == 1.0 in Q15");
_Static_assert(TONE_MAX_AMPLITUDE_SUM * 32768u + 16384u <= 0x7fffffffu, "fade product stays in int32");

CODE static int32_t round_q15(int32_t product)
{
    return (product + 16384) >> 15;
}

CODE int32_t tone_sin_q15(uint32_t phase)
{
    uint32_t quadrant = phase >> 30;
    int32_t x = (int32_t)((phase >> 15) & 0x7fffu);
    if (quadrant & 1u)
        x = 32768 - x;
    /* Every product below is bounded by 2^31 - 1 for x in [0, 32768]. */
    int32_t x2 = round_q15(x * x);
    int32_t t = round_q15(TONE_SIN_C * x2);
    t = round_q15((TONE_SIN_B + t) * x2);
    int32_t y = round_q15((TONE_SIN_A + t) * x);
    if (y > 32767)
        y = 32767;
    return (quadrant & 2u) ? -y : y;
}

CODE int tone_segment_valid(const struct tone_segment *s)
{
    if (!s->samples || s->samples > TONE_MAX_SAMPLES || s->reserved || s->partial_count > TONE_MAX_PARTIALS)
        return 0;
    for (uint32_t i = s->partial_count; i < TONE_MAX_PARTIALS; ++i)
        if (s->partials[i].phase_inc || s->partials[i].amplitude_q15 || s->partials[i].decay)
            return 0;
    if (!s->partial_count)
        return !s->attack_shift && !s->release_shift;
    if (s->attack_shift > TONE_MAX_FADE_SHIFT || s->release_shift > TONE_MAX_FADE_SHIFT)
        return 0;
    uint32_t sum = 0u;
    for (uint32_t i = 0; i < s->partial_count; ++i) {
        const struct tone_partial *p = &s->partials[i];
        if (!p->phase_inc || p->phase_inc >= 0x80000000u || !p->amplitude_q15 || p->amplitude_q15 > 32767u)
            return 0;
        sum += p->amplitude_q15;
    }
    if (sum > TONE_MAX_AMPLITUDE_SUM)
        return 0;
    return s->samples >= (UINT32_C(1) << s->attack_shift) + (UINT32_C(1) << s->release_shift);
}

/* Raised-cosine fade gain sin^2(pi/2 * k / F) in Q15 for k < F = 1 << shift. */
CODE static int32_t fade_gain_q15(uint32_t k, uint32_t shift)
{
    int32_t s = tone_sin_q15(k << (30u - shift));
    return round_q15(s * s);
}

CODE static void fill_segment(int16_t *dst, const struct tone_segment *s)
{
    /* Plain stores: an array initializer may become a .rodata copy, which is
     * not in the resident image. */
    uint32_t phase[TONE_MAX_PARTIALS], env[TONE_MAX_PARTIALS];
    for (uint32_t i = 0; i < TONE_MAX_PARTIALS; ++i) {
        phase[i] = 0u;
        env[i] = TONE_ENV_ONE;
    }
    uint32_t attack = UINT32_C(1) << s->attack_shift, release = UINT32_C(1) << s->release_shift;
    for (uint32_t n = 0; n < s->samples; ++n) {
        int32_t v = 0;
        for (uint32_t i = 0; i < s->partial_count; ++i) {
            const struct tone_partial *p = &s->partials[i];
            int32_t x = round_q15((int32_t)p->amplitude_q15 * tone_sin_q15(phase[i]));
            /* env >> 15 is the Q15 gain (32768 = 1.0 leaves x unchanged). */
            x = round_q15(x * (int32_t)(env[i] >> 15));
            env[i] -= (env[i] >> 16) * p->decay;
            phase[i] += p->phase_inc;
            v += x;
        }
        if (s->partial_count) {
            uint32_t tail = s->samples - 1u - n;
            if (n < attack)
                v = round_q15(v * fade_gain_q15(n, s->attack_shift));
            else if (tail < release)
                v = round_q15(v * fade_gain_q15(tail, s->release_shift));
            if (v > 32767)
                v = 32767;
            else if (v < -32767)
                v = -32767;
        }
        dst[n] = (int16_t)v;
    }
}

CODE uint32_t tone_gen_fill(int16_t *dst, uint32_t capacity_samples, const struct tone_segment *segments,
                            uint32_t count)
{
    if (!dst || !segments || !count || ((uintptr_t)dst & (TONE_DMA_ALIGN_BYTES - 1u)))
        return 0u;
    uint32_t total = 0u;
    for (uint32_t i = 0; i < count; ++i) {
        if (!tone_segment_valid(&segments[i]) || segments[i].samples > TONE_MAX_SAMPLES - total)
            return 0u;
        total += segments[i].samples;
    }
    uint32_t padded = TONE_PADDED_SAMPLES(total);
    if (capacity_samples < padded)
        return 0u;
    uint32_t at = 0u;
    for (uint32_t i = 0; i < count; ++i) {
        fill_segment(dst + at, &segments[i]);
        at += segments[i].samples;
    }
    for (uint32_t n = total; n < padded; ++n)
        dst[n] = 0;
    return padded;
}
