/* Issue #14 movie record indicator. Host-testable through rec_tone_api. */
#include "rec_tone.h"
#include "rec_state.h"
#include "observer.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

struct rec_tone_state rec_tone_state;
#define RT rec_tone_state
#define CFG rec_tone_config

CODE static uint32_t sat_add(uint32_t a, uint32_t b)
{
    return a > UINT32_MAX - b ? UINT32_MAX : a + b;
}

/* Field-wise clear: no memset/memcpy libcalls in the freestanding link. */
CODE static void clear_attempt(struct rec_tone_attempt *a)
{
    volatile uint32_t *w = (volatile uint32_t *)a;
    for (unsigned i = 0; i < sizeof(*a) / 4u; ++i)
        w[i] = 0u;
}

CODE static uint32_t rd(uint32_t address)
{
    return rec_tone_api.read(address);
}

/* rec_state.c is the only writer of the press counter. */
CODE static uint32_t press_seq(void)
{
    return atomic_load_explicit(&rec_press_seq, memory_order_acquire);
}

CODE static uint32_t in_mask(uint32_t value, uint32_t mask)
{
    return value < 32u && ((UINT32_C(1) << value) & mask) != 0u;
}

/* Worker only, once. The buffer is filled and cleaned before the descriptor
 * pointer is set, and it is never written again, so a timed-out or interrupted
 * DMA cannot race a rewrite. A fault leaves the pointer 0 (nothing published). */
CODE static void publish(void)
{
    RT.publish_attempted = 1u;
    if (!rec_tone_api.read || !rec_tone_api.query || !rec_tone_api.beep_start || !rec_tone_api.clean) {
        RT.fault = REC_TONE_F_API;
        return;
    }
    if (!CFG.segment_count || CFG.bytes != 2u * CFG.padded_samples ||
        tone_gen_fill(rec_tone_pcm, CFG.padded_samples, rec_tone_segments, CFG.segment_count) !=
            CFG.padded_samples) {
        RT.fault = REC_TONE_F_GEN;
        return;
    }
    rec_tone_api.clean(rec_tone_pcm, CFG.bytes);
    volatile uint32_t *d = rec_tone_descriptor;
    if (((uintptr_t)d & 31u) || ((uintptr_t)rec_tone_pcm & 31u) || (CFG.bytes & 31u) || d[0] != 1u ||
        d[1] != 0u || d[2] != CFG.bytes) {
        RT.fault = REC_TONE_F_DESC;
        return;
    }
    d[1] = (uint32_t)(uintptr_t)rec_tone_pcm;
    rec_tone_api.clean(rec_tone_descriptor, REC_TONE_DESC_BYTES);
#ifdef OBSERVER_FIRMWARE
    __asm__ volatile("dsb sy" ::: "memory");
#endif
    __atomic_store_n(&RT.ready, 1u, __ATOMIC_RELEASE);
}

/* Port of audio.S:50-86 (ABeep readiness) and :308-384
 * (audio_check_dependencies) with one change: the AudioCtrl state mask. */
CODE uint32_t rec_tone_preflight(struct rec_tone_attempt *a, uint32_t actrl_mask)
{
    uint32_t ctx = rd(CFG.singleton_slot);
    a->context = ctx;
    if (!ctx || !rd(ctx))
        return REC_TONE_PF_NOT_READY;
    uint32_t handle = rd(ctx + REC_TONE_ABEEP_HANDLE);
    a->handle = handle;
    if (!handle)
        return REC_TONE_PF_NOT_READY;
    int32_t state = rec_tone_api.query(handle);
    a->state_before = state;
    if (state == -1)
        return REC_TONE_PF_STATE_INVALID;
    a->phase_before = rd(ctx + REC_TONE_ABEEP_PHASE);
    if (rd(ctx + REC_TONE_ABEEP_OWNER))
        return REC_TONE_PF_STOCK_OWNED;
    if (state != 1 || a->phase_before != REC_TONE_PHASE_COMPLETE)
        return REC_TONE_PF_BUSY;
    uint32_t actrl = rd(CFG.audioctrl_slot);
    if (!actrl)
        return REC_TONE_PF_ACTRL_MISSING;
    uint32_t actrl_handle = rd(actrl + CFG.audioctrl_handle_offset);
    if (!actrl_handle)
        return REC_TONE_PF_ACTRL_MISSING;
    int32_t actrl_state = rec_tone_api.query(actrl_handle);
    a->actrl_state = (uint32_t)actrl_state;
    if (actrl_state == -1)
        return REC_TONE_PF_ACTRL_INVALID;
    if ((uint32_t)actrl_state >= 32u || !((UINT32_C(1) << actrl_state) & actrl_mask))
        return REC_TONE_PF_ACTRL_STATE;
    uint32_t ic = rd(CFG.audio_ic_slot);
    if (!ic || rd(ic + CFG.audio_ic_init_offset) != 1u)
        return REC_TONE_PF_AUDIO_IC;
    uint32_t output = rd(CFG.output_context_slot);
    if (!output)
        return REC_TONE_PF_OUTPUT_CTX;
    uint32_t init = rd(CFG.icu_cache_base + CFG.icu_cache_init_offset);
    uint32_t kind = rd(CFG.icu_cache_base + CFG.icu_cache_kind_offset);
    if (!init)
        return REC_TONE_PF_ICU_CACHE;
    uint32_t table;
    if (kind == CFG.icu_kind_ak)
        table = CFG.ak_param_slot;
    else if (kind == CFG.icu_kind_vaif)
        table = CFG.vaif_param_slot;
    else
        return REC_TONE_PF_ICU_CACHE;
    if (!rd(table))
        return REC_TONE_PF_PARAM_TABLE;
    /* PlayVolume/route are applied only when these are 0 (E08BC390/E08BC3A2). */
    a->route[0] = rd(output + 4u);
    a->route[1] = rd(output + 8u);
    a->route[2] = rd(output + 12u);
    return REC_TONE_PF_OK;
}

/* Presses that arrive while a cue is pending or observed join that cue; they
 * are counted, never replayed late. */
CODE static void absorb(uint32_t seq)
{
    if (seq != RT.seen) {
        uint32_t d = seq - RT.seen;
        RT.seen = seq;
        RT.presses = sat_add(RT.presses, d);
        RT.coalesced = sat_add(RT.coalesced, d);
    }
}

CODE static void finish(uint32_t outcome)
{
    RT.outcomes[outcome] = sat_add(RT.outcomes[outcome], 1u);
    RT.last.outcome = outcome;
    RT.mode = REC_TONE_IDLE;
    if (outcome == REC_TONE_O_TIMEOUT && !RT.fault)
        RT.fault = REC_TONE_F_TIMEOUT;
    absorb(press_seq());
}

CODE static void observe(void)
{
    struct rec_tone_attempt *a = &RT.last;
    if (++RT.obs_ticks > REC_TONE_OBSERVE_TICKS) {
        finish(REC_TONE_O_TIMEOUT);
        return;
    }
    uint32_t ctx = rd(CFG.singleton_slot);
    if (ctx != a->context || rd(ctx + REC_TONE_ABEEP_HANDLE) != a->handle) {
        finish(REC_TONE_O_CONTEXT_CHANGED);
        return;
    }
    int32_t state = rec_tone_api.query(a->handle);
    uint32_t phase = rd(ctx + REC_TONE_ABEEP_PHASE);
    uint32_t result = rd(ctx + REC_TONE_ABEEP_RESULT);
    uint32_t ptr = rd(ctx + REC_TONE_ABEEP_PCM_PTR);
    uint32_t bytes = rd(ctx + REC_TONE_ABEEP_PCM_BYTES);
    a->state_after = state;
    a->phase_after = phase;
    a->result_after = result;
    a->last_id_after = rd(ctx + REC_TONE_ABEEP_LAST_ID);
    if (state == -1) {
        finish(REC_TONE_O_STATE_INVALID);
        return;
    }
    if (phase > REC_TONE_PHASE_COMPLETE) {
        finish(REC_TONE_O_UNEXPECTED_PHASE);
        return;
    }
    if (phase == REC_TONE_PHASE_STOP) {
        finish(REC_TONE_O_STOPPED);
        return;
    }
    if (phase == REC_TONE_PHASE_ACTIVE) {
        RT.provisional_reject = 0u;
        if (ptr == rec_tone_descriptor[1] && bytes == CFG.bytes) {
            if (!RT.active_seen) {
                RT.active_seen = 1u;
                a->ticks_to_active = RT.obs_ticks;
            }
        } else if (RT.active_seen) {
            finish(REC_TONE_O_BUFFER_CHANGED);
        }
        return;
    }
    if (phase == REC_TONE_PHASE_COMPLETE) {
        if (RT.active_seen) {
            if (state == 1) {
                a->ticks_to_settle = RT.obs_ticks;
                finish(REC_TONE_O_SETTLED);
            }
            return;
        }
        /* +0x24 is written after phase 3; confirm on a second tick. */
        if (RT.provisional_reject) {
            a->rejection = result;
            finish(REC_TONE_O_REJECTED);
            return;
        }
        RT.provisional_reject = 1u;
        return;
    }
    RT.provisional_reject = 0u; /* phase 0: queued, not yet picked up */
}

CODE void rec_tone_step(void)
{
    RT.steps = sat_add(RT.steps, 1u);
    if (!RT.publish_attempted)
        publish();
    uint32_t seq = press_seq();
    if (RT.mode == REC_TONE_OBSERVE) { /* single-flight */
        observe();
        return;
    }
    if (seq == RT.seen && !RT.pending)
        return;
    if (seq != RT.seen) {
        if (RT.pending) {
            absorb(seq);
        } else {
            uint32_t d = seq - RT.seen;
            RT.seen = seq;
            RT.presses = sat_add(RT.presses, d);
            if (d > 1u)
                RT.coalesced = sat_add(RT.coalesced, d - 1u);
            RT.pending = 1u;
            RT.pending_ticks = 0u;
        }
    }
    if (!__atomic_load_n(&RT.ready, __ATOMIC_ACQUIRE) || RT.fault) {
        RT.dropped_unready = sat_add(RT.dropped_unready, 1u);
        RT.pending = 0u;
        return;
    }
    /* Never beep outside the admitted record states: a press whose start was
     * refused or a take that already ended drops the pending cue. */
    if (!in_mask(rd(CFG.rec_state_word), REC_TONE_REC_STATE_MASK)) {
        RT.dropped_not_recording = sat_add(RT.dropped_not_recording, 1u);
        RT.pending = 0u;
        return;
    }
    /* The last-attempt record is rewritten only here, never while observing. */
    struct rec_tone_attempt *a = &RT.last;
    clear_attempt(a);
    uint32_t why = rec_tone_preflight(a, REC_TONE_ACTRL_MASK);
    a->preflight = why;
    if (in_mask(why, REC_TONE_RETRY_MASK) && ++RT.pending_ticks <= REC_TONE_BUSY_TICKS)
        return;
    if (why) {
        RT.preflight_drop[why] = sat_add(RT.preflight_drop[why], 1u);
        RT.pending = 0u;
        return;
    }
    a->se_id = REC_TONE_SE_ID;
    a->result_before = rd(a->context + REC_TONE_ABEEP_RESULT);
    a->last_id_before = rd(a->context + REC_TONE_ABEEP_LAST_ID);
    int32_t rc = rec_tone_api.beep_start(REC_TONE_SE_ID, rec_tone_descriptor);
    RT.pending = 0u;
    RT.submits = sat_add(RT.submits, 1u);
    RT.last.submit_rc = rc;
    if (rc != 0) { /* 5 = descriptor-copy allocation failure (E08B19B4); no retry */
        RT.submit_nonzero = sat_add(RT.submit_nonzero, 1u);
        RT.last_rc = rc;
        RT.outcomes[REC_TONE_O_SUBMIT_NONZERO] = sat_add(RT.outcomes[REC_TONE_O_SUBMIT_NONZERO], 1u);
        RT.last.outcome = REC_TONE_O_SUBMIT_NONZERO;
        return;
    }
    RT.mode = REC_TONE_OBSERVE;
    RT.obs_ticks = 0u;
    RT.active_seen = 0u;
    RT.provisional_reject = 0u;
}

static const char begin_fmt[] DATA = RT_FMT_BEGIN;
static const char counts_fmt[] DATA = RT_FMT_COUNTS;
static const char drops_fmt[] DATA = RT_FMT_DROPS;
static const char end_fmt[] DATA = RT_FMT_END;

CODE void rec_tone_report(unsigned reports)
{
    observer_debug_fn debug = observer_api.debug;
    if (!debug)
        return;
#define RT_ADD_OUTCOME(o) +RT.outcomes[o]
    uint32_t other = 0u REC_TONE_OTHER_OUTCOMES(RT_ADD_OUTCOME);
#undef RT_ADD_OUTCOME
    uint32_t dropped = 0u;
    for (unsigned i = 1u; i < REC_TONE_PF_COUNT; ++i)
        dropped = sat_add(dropped, RT.preflight_drop[i]);
    debug(OBSERVER_CLASS, OBSERVER_LEVEL, begin_fmt, reports, RT.ready, RT.fault);
    debug(OBSERVER_CLASS, OBSERVER_LEVEL, counts_fmt, RT.presses, RT.coalesced, RT.submits,
          RT.outcomes[REC_TONE_O_SETTLED], RT.outcomes[REC_TONE_O_STOPPED], RT.outcomes[REC_TONE_O_REJECTED]);
    debug(OBSERVER_CLASS, OBSERVER_LEVEL, drops_fmt, RT.outcomes[REC_TONE_O_TIMEOUT], other, RT.dropped_unready,
          RT.dropped_not_recording, dropped, RT.last.preflight, RT.last_rc);
    debug(OBSERVER_CLASS, OBSERVER_LEVEL, end_fmt, reports);
}
