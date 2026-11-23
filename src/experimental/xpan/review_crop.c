#include "review_crop.h"
#include "frame_memory.h"
#include "xpan_review_gate.h"
#include <stdatomic.h>
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
/* before: the full-source tuple observed on hardware for both XPan and
 * full-frame RAW + JPEG review. after: the derived centred XPan band.
 * letterbox_before: Canon's stock 16:9 tuple of the JPEG-only review (RAW off,
 * observed on hardware for XPan), which gets the same band. */
const struct review_crop_profile review_crop_profile = {
    REVIEW_CROP_CALLER_LR, REVIEW_CROP_DESCRIPTOR_BYTES, REVIEW_CROP_CALLER_FRAME_BYTES, REVIEW_CROP_SOURCE_SLOT_OFFSET,
    REVIEW_CROP_COMPLETED, 1024u, 682u, {0u, 0u, 0u, 0u, 1024u, 682u}, {0u, 152u, 0u, 152u, 1024u, 378u},
    {0u, 53u, 0u, 53u, 1024u, 576u}};
_Static_assert(sizeof(review_crop_profile) == 100u, "review crop profile ABI");
static _Atomic uint32_t counts[REVIEW_CROP_R_COUNT];
/* The JPEG review token (review_crop.h): the gate claim ordinal of the latest
 * applied letterbox crop, 0 after an applied full-source crop. Written by the
 * review hook (display task), read by the playback fit (playback task). */
static _Atomic uint32_t jpeg_review_event;
#ifdef REVIEW_CROP_HOST
extern uint32_t review_crop_host_read(uint32_t);
extern void review_crop_host_write(uint32_t, uint32_t);
#endif
CODE static uint32_t read_word(uint32_t a)
{
#ifdef REVIEW_CROP_HOST
    return review_crop_host_read(a);
#else
    return *(volatile const uint32_t *)(uintptr_t)a;
#endif
}
CODE static void write_word(uint32_t a, uint32_t v)
{
#ifdef REVIEW_CROP_HOST
    review_crop_host_write(a, v);
#else
    *(volatile uint32_t *)(uintptr_t)a = v;
#endif
}
CODE void review_crop_counts(struct review_crop_counts *out)
{
    for (unsigned i = 0; i < REVIEW_CROP_R_COUNT; ++i)
        out->reasons[i] = atomic_load_explicit(&counts[i], memory_order_relaxed);
}
CODE static uint32_t scope(const struct review_crop_call *c)
{
    if (c->completed != REVIEW_CROP_COMPLETED)
        return REVIEW_CROP_R_COMPLETION;
    if (c->caller != REVIEW_CROP_CALLER_LR)
        return REVIEW_CROP_R_CALLER;
    if (c->slot != 0u)
        return REVIEW_CROP_R_SLOT;
    if ((c->caller_sp & 7u) || !frame_memory_stack_frame_span(c->caller_sp, REVIEW_CROP_CALLER_FRAME_BYTES))
        return REVIEW_CROP_R_FRAME;
    if (c->destination != c->caller_sp + 16u)
        return REVIEW_CROP_R_DESTINATION;
    if (c->controller > UINT32_MAX - REVIEW_CROP_SOURCE_SLOT_OFFSET ||
        c->source != c->controller + REVIEW_CROP_SOURCE_SLOT_OFFSET)
        return REVIEW_CROP_R_SOURCE_SLOT;
    return REVIEW_CROP_R_APPLIED;
}
CODE static uint32_t descriptor(const struct review_crop_call *c)
{
    uint32_t v[13];
    for (unsigned i = 0; i < 13u; i++)
        v[i] = read_word(c->destination + 4u * i);
    if (v[0] != 1u)
        return REVIEW_CROP_R_ACTIVE;
    if (v[1] != 0u)
        return REVIEW_CROP_R_KIND;
    if (!(v[2] | v[3]))
        return REVIEW_CROP_R_ID;
    if (v[5] != review_crop_profile.source_width || v[6] != review_crop_profile.source_height)
        return REVIEW_CROP_R_SOURCE_DIMS;
    uint32_t full = 1u, letterbox = 1u;
    for (unsigned i = 0; i < 6u; i++) {
        full &= v[7u + i] == review_crop_profile.before[i];
        letterbox &= v[7u + i] == review_crop_profile.letterbox_before[i];
    }
    return full ? REVIEW_CROP_R_APPLIED : letterbox ? REVIEW_CROP_R_LETTERBOX : REVIEW_CROP_R_TUPLE;
}
CODE static uint32_t applied(uint32_t why)
{
    return why == REVIEW_CROP_R_APPLIED || why == REVIEW_CROP_R_LETTERBOX;
}
CODE uint32_t review_crop_apply(const struct review_crop_call *c, uint32_t *reason)
{
    uint32_t why = REVIEW_CROP_R_COMPLETION;
    if (c) {
        why = scope(c);
        if (why == REVIEW_CROP_R_APPLIED)
            why = descriptor(c);
        /* Authority gate after the unchanged scope and descriptor checks: store
         * only for this review's unused, still-latest XPan claim; else stock. */
        if (applied(why) && !xpan_review_gate_crop(c->caller))
            why = REVIEW_CROP_R_GATE;
        if (applied(why)) {
            /* The gate admitted this review's claim just now (open, unrevoked). */
            atomic_store_explicit(&jpeg_review_event,
                                  why == REVIEW_CROP_R_LETTERBOX ? xpan_review_gate_claim_ordinal() : 0u,
                                  memory_order_release);
            /* Change only the three words that differ (+0x20, +0x28, +0x30). */
            const uint32_t *to = review_crop_profile.after;
            write_word(c->destination + 32u, to[1]);
            write_word(c->destination + 40u, to[3]);
            write_word(c->destination + 48u, to[5]);
        }
        atomic_fetch_add_explicit(&counts[why], 1u, memory_order_relaxed);
    }
    if (reason)
        *reason = why;
    return c ? c->original_result : 0u;
}
CODE uint32_t review_crop_jpeg_review(void)
{
    return xpan_review_gate_event_current(atomic_load_explicit(&jpeg_review_event, memory_order_acquire));
}
CODE void review_crop_native_post(const struct review_crop_cpu *entry, uint32_t caller_sp,
                                  const struct review_crop_cpu *after, uint32_t completed)
{
    struct review_crop_call c = {entry->lr, caller_sp, entry->r[0], entry->r[1], entry->r[4], entry->r[5],
                                 completed, after->r[0]};
    (void)review_crop_apply(&c, 0);
}
