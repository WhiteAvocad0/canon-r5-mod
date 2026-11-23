#include "xpan_review_gate.h"
#include "xpan_authority.h"
#include "frame_memory.h"
#include <stdatomic.h>
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
const struct xpan_review_gate_profile xpan_review_gate_profile = {
    XPAN_OUTLINE_HOOK_VA, XPAN_OUTLINE_RESUME, XPAN_OUTLINE_CALLER_LR, XPAN_OUTLINE_UNDISP_LR, XPAN_OUTLINE_H_OFFSET,
    XPAN_OUTLINE_V_OFFSET, XPAN_OUTLINE_DISPLAY_OFFSET, XPAN_OUTLINE_CALLER_OFFSET, XPAN_OUTLINE_BD_CALLER_OFFSET,
    XPAN_OUTLINE_FRAME_BYTES, XPAN_GATE_SR_FLAG, XPAN_GATE_ASPECT_SLOT, XPAN_GATE_ASPECT_OFFSET, XPAN_GATE_ASPECT_XPAN,
    XPAN_GATE_LARGE_JPEG};
_Static_assert(sizeof(xpan_review_gate_profile) == 60u, "review gate profile ABI");
_Static_assert(XPAN_OUTLINE_BD_CALLER_OFFSET + 4u == XPAN_OUTLINE_FRAME_BYTES && XPAN_OUTLINE_H_OFFSET < XPAN_OUTLINE_FRAME_BYTES &&
                   XPAN_OUTLINE_V_OFFSET < XPAN_OUTLINE_FRAME_BYTES,
               "every stack read lies inside the admitted frame");
/* claim: written by the review start and closes (GUI task, _DrawAspectLine);
 * revoked by either consumer with a CAS. open_reason: the claim-open result of
 * the current review (0 after a close). seen: events count at the previous
 * review start (GUI task only). */
static _Atomic uint32_t claim, open_reason, seen;
static _Atomic uint32_t reviews, outline_calls, outline_decisions, outline_hidden, crop_decisions, crop_applied, closes;
static _Atomic uint32_t open_counts[XPAN_GATE_R_COUNT], crop_counts[XPAN_GATE_R_COUNT],
    outline_counts[XPAN_OUTLINE_R_COUNT], last_gate;
#ifdef XPAN_REVIEW_GATE_HOST
extern uint32_t xpan_review_gate_host_read(uint32_t);
extern void xpan_review_gate_host_write(uint32_t, uint32_t);
#endif
CODE static uint32_t read_word(uint32_t a)
{
#ifdef XPAN_REVIEW_GATE_HOST
    return xpan_review_gate_host_read(a);
#else
    return *(volatile const uint32_t *)(uintptr_t)a;
#endif
}
CODE static void write_word(uint32_t a, uint32_t v)
{
#ifdef XPAN_REVIEW_GATE_HOST
    xpan_review_gate_host_write(a, v);
#else
    *(volatile uint32_t *)(uintptr_t)a = v;
#endif
}
CODE static void bump(_Atomic uint32_t *c)
{
    atomic_fetch_add_explicit(c, 1u, memory_order_relaxed);
}
CODE void xpan_review_gate_counts(struct xpan_review_gate_counts *out)
{
    out->reviews = atomic_load_explicit(&reviews, memory_order_relaxed);
    out->outline_calls = atomic_load_explicit(&outline_calls, memory_order_relaxed);
    out->outline_decisions = atomic_load_explicit(&outline_decisions, memory_order_relaxed);
    out->outline_hidden = atomic_load_explicit(&outline_hidden, memory_order_relaxed);
    out->crop_decisions = atomic_load_explicit(&crop_decisions, memory_order_relaxed);
    out->crop_applied = atomic_load_explicit(&crop_applied, memory_order_relaxed);
    out->closes = atomic_load_explicit(&closes, memory_order_relaxed);
    for (unsigned i = 0; i < XPAN_GATE_R_COUNT; ++i) {
        out->open[i] = atomic_load_explicit(&open_counts[i], memory_order_relaxed);
        out->crop[i] = atomic_load_explicit(&crop_counts[i], memory_order_relaxed);
    }
    for (unsigned i = 0; i < XPAN_OUTLINE_R_COUNT; ++i)
        out->outline[i] = atomic_load_explicit(&outline_counts[i], memory_order_relaxed);
    out->last_gate = atomic_load_explicit(&last_gate, memory_order_relaxed);
}
/* One snapshot per decision: the authority event count and the latest record. */
struct snapshot {
    uint32_t events, stable;
    struct xpan_authority_latest latest;
};
CODE static void take(struct snapshot *s)
{
    struct xpan_authority_counts n;
    xpan_authority_counts(&n);
    s->events = n.events;
    s->stable = xpan_authority_latest(&s->latest);
}
CODE static uint32_t is_xpan(const struct xpan_authority_latest *l)
{
    return l->aspect == XPAN_GATE_ASPECT_XPAN && (l->flags & XPAN_GATE_LARGE_JPEG) != 0u;
}
/* Review start: claim the newest event only if it is the single new event
 * since the previous review start and the record describes it. */
CODE static uint32_t open_claim(const struct snapshot *s)
{
    bump(&reviews);
    const uint32_t previous = atomic_exchange_explicit(&seen, s->events, memory_order_relaxed), e = s->events;
    uint32_t why, word = 0u;
    if (e == previous)
        why = XPAN_GATE_R_NO_NEW_EVENT;
    else if (e - previous != 1u)
        why = XPAN_GATE_R_MULTIPLE;
    else if (!s->stable)
        why = XPAN_GATE_R_UNSTABLE;
    else if (s->latest.ordinal != e)
        why = XPAN_GATE_R_UNPUBLISHED;
    else if (s->latest.reason != XPAN_AUTH_R_JOB)
        why = XPAN_GATE_R_INVALID;
    else {
        const uint32_t x = is_xpan(&s->latest);
        why = x ? XPAN_GATE_R_CLAIMED : XPAN_GATE_R_NOT_XPAN;
        word = ((e & XPAN_CLAIM_ORDINAL_MASK) << 2) | (x ? XPAN_CLAIM_XPAN : 0u);
    }
    bump(&open_counts[why]);
    atomic_store_explicit(&open_reason, why, memory_order_relaxed);
    atomic_store_explicit(&claim, word, memory_order_release);
    return why;
}
CODE static void close_claim(void)
{
    if (atomic_load_explicit(&claim, memory_order_relaxed) || atomic_load_explicit(&open_reason, memory_order_relaxed)) {
        atomic_store_explicit(&claim, 0u, memory_order_release);
        atomic_store_explicit(&open_reason, 0u, memory_order_relaxed);
        bump(&closes);
    }
}
/* Shared by both consumers, so one review's outline and crop agree. */
CODE static uint32_t admit(uint32_t word, const struct snapshot *s)
{
    if (!word) {
        const uint32_t why = atomic_load_explicit(&open_reason, memory_order_relaxed);
        return why && why != XPAN_GATE_R_CLAIMED && why != XPAN_GATE_R_NOT_XPAN ? why : XPAN_GATE_R_NO_CLAIM;
    }
    if (!(word & XPAN_CLAIM_XPAN))
        return XPAN_GATE_R_NOT_XPAN;
    if (word & XPAN_CLAIM_REVOKED)
        return XPAN_GATE_R_REVOKED;
    if (!s->stable)
        return XPAN_GATE_R_UNSTABLE;
    const uint32_t ordinal = word >> 2;
    if ((s->latest.ordinal & XPAN_CLAIM_ORDINAL_MASK) != ordinal || (s->events & XPAN_CLAIM_ORDINAL_MASK) != ordinal) {
        /* A newer event exists: this review may now show another frame. Revoke for
         * the rest of the review (a failed CAS means the claim already changed). */
        uint32_t expected = word;
        atomic_compare_exchange_strong_explicit(&claim, &expected, word | XPAN_CLAIM_REVOKED, memory_order_acq_rel,
                                                memory_order_relaxed);
        return XPAN_GATE_R_STALE;
    }
    if (s->latest.reason != XPAN_AUTH_R_JOB)
        return XPAN_GATE_R_INVALID;
    if (!is_xpan(&s->latest))
        return XPAN_GATE_R_NOT_XPAN;
    return XPAN_GATE_R_NONE;
}
CODE uint32_t xpan_review_gate_crop(uint32_t caller)
{
    (void)caller;
    bump(&crop_decisions);
    const uint32_t word = atomic_load_explicit(&claim, memory_order_acquire);
    struct snapshot s;
    take(&s);
    const uint32_t why = admit(word, &s);
    bump(&crop_counts[why]);
    if (why != XPAN_GATE_R_NONE) {
        atomic_store_explicit(&last_gate, why, memory_order_relaxed);
        return 0u;
    }
    bump(&crop_applied);
    return 1u;
}
CODE uint32_t xpan_review_gate_claim_ordinal(void)
{
    const uint32_t word = atomic_load_explicit(&claim, memory_order_acquire);
    return (word & XPAN_CLAIM_XPAN) && !(word & XPAN_CLAIM_REVOKED) ? word >> 2 : 0u;
}
CODE uint32_t xpan_review_gate_event_current(uint32_t ordinal)
{
    if (!ordinal)
        return XPAN_GATE_R_NO_CLAIM;
    struct snapshot s;
    take(&s);
    if (!s.stable)
        return XPAN_GATE_R_UNSTABLE;
    if ((s.latest.ordinal & XPAN_CLAIM_ORDINAL_MASK) != ordinal || (s.events & XPAN_CLAIM_ORDINAL_MASK) != ordinal)
        return XPAN_GATE_R_STALE;
    if (s.latest.reason != XPAN_AUTH_R_JOB)
        return XPAN_GATE_R_INVALID;
    if (!is_xpan(&s.latest) || (s.latest.flags & XPAN_GATE_RAW_MASK))
        return XPAN_GATE_R_NOT_XPAN;
    return XPAN_GATE_R_NONE;
}
/* Byte [[0x171DC]+0x18C0] (the GUI aspect getter E07D6B68), read as its
 * aligned word. 0 when the address is not an admitted RAM word. */
CODE static uint32_t gui_aspect(uint32_t *value)
{
    const uint32_t base = read_word(XPAN_GATE_ASPECT_SLOT);
    if (base > UINT32_MAX - XPAN_GATE_ASPECT_OFFSET)
        return 0u;
    const uint32_t at = base + XPAN_GATE_ASPECT_OFFSET, word = at & ~3u;
    if (!frame_memory_data_span(word, 4u))
        return 0u;
    *value = (read_word(word) >> (8u * (at & 3u))) & 0xffu;
    return 1u;
}
CODE void xpan_review_outline_decide(uint32_t sp, uint32_t force)
{
    bump(&outline_calls);
    /* Outside SR the hook is passive and only ends the current review's claim. */
    if (read_word(XPAN_GATE_SR_FLAG) != 1u) {
        close_claim();
        return;
    }
    bump(&outline_decisions);
    uint32_t why;
    if (!frame_memory_stack_frame_span(sp, XPAN_OUTLINE_FRAME_BYTES)) {
        why = XPAN_OUTLINE_R_FRAME;
    } else {
        const uint32_t caller = read_word(sp + XPAN_OUTLINE_CALLER_OFFSET);
        const uint32_t bd_caller = read_word(sp + XPAN_OUTLINE_BD_CALLER_OFFSET);
        const uint32_t display = read_word(sp + XPAN_OUTLINE_DISPLAY_OFFSET);
        const uint32_t h = read_word(sp + XPAN_OUTLINE_H_OFFSET), v = read_word(sp + XPAN_OUTLINE_V_OFFSET);
        struct snapshot s;
        take(&s);
        /* The SR-start Undisp redraw claims for this review, whatever the guards
         * below decide: the crop of the same review consumes the same claim. */
        if (caller == XPAN_OUTLINE_CALLER_LR && bd_caller == XPAN_OUTLINE_UNDISP_LR && display == 0u && force == 1u)
            (void)open_claim(&s);
        uint32_t aspect = 0u;
        const uint32_t aspect_known = gui_aspect(&aspect);
        if (caller != XPAN_OUTLINE_CALLER_LR)
            why = XPAN_OUTLINE_R_CALLER;
        else if (display != 0u)
            why = XPAN_OUTLINE_R_DISPLAY;
        else if (!aspect_known || aspect != XPAN_GATE_ASPECT_XPAN)
            why = XPAN_OUTLINE_R_GUI_ASPECT;
        else if (h != 1u || v != 0u)
            why = XPAN_OUTLINE_R_DECISION;
        else {
            const uint32_t gate = admit(atomic_load_explicit(&claim, memory_order_acquire), &s);
            if (gate == XPAN_GATE_R_NONE) {
                /* This call only: Canon then takes its own no-line path. */
                write_word(sp + XPAN_OUTLINE_H_OFFSET, 0u);
                write_word(sp + XPAN_OUTLINE_V_OFFSET, 0u);
                bump(&outline_hidden);
                why = XPAN_OUTLINE_R_HIDDEN;
            } else {
                atomic_store_explicit(&last_gate, gate, memory_order_relaxed);
                why = XPAN_OUTLINE_R_GATE;
            }
        }
    }
    bump(&outline_counts[why]);
}
