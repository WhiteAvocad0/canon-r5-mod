#include "xpan_authority.h"
#include "frame_memory.h"
#include <stdatomic.h>
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
const struct xpan_authority_profile xpan_authority_profile = {
    XPAN_AUTH_HOOK_VA, XPAN_AUTH_RESUME, XPAN_AUTH_JOB_SIGNATURE, XPAN_AUTH_HANDLE_BYTES, XPAN_AUTH_OBJECT_OFFSET,
    XPAN_AUTH_JOBID_OFFSET, XPAN_AUTH_ASPECT_OFFSET, XPAN_AUTH_PICTTYPE_OFFSET, XPAN_AUTH_OBJECT_BYTES};
_Static_assert(sizeof(xpan_authority_profile) == 36u, "authority profile ABI");
_Static_assert(XPAN_AUTH_OBJECT_OFFSET + 4u <= XPAN_AUTH_HANDLE_BYTES && XPAN_AUTH_JOBID_OFFSET + 4u <= XPAN_AUTH_OBJECT_BYTES &&
                   XPAN_AUTH_ASPECT_OFFSET + 4u <= XPAN_AUTH_OBJECT_BYTES,
               "every read lies inside its admitted span");
static _Atomic uint32_t events, valid;
/* Latest-authority seqlock: latest_seq is even when stable and 0 before the
 * first publication. One writer (the scs handler); readers never wait. */
static _Atomic uint32_t latest_seq, latest_contended;
static _Atomic uint32_t latest_words[5];
#ifdef XPAN_AUTHORITY_HOST
extern uint32_t xpan_authority_host_read(uint32_t);
#endif
CODE static uint32_t read_word(uint32_t a)
{
#ifdef XPAN_AUTHORITY_HOST
    return xpan_authority_host_read(a);
#else
    return *(volatile const uint32_t *)(uintptr_t)a;
#endif
}
CODE void xpan_authority_counts(struct xpan_authority_counts *out)
{
    out->events = atomic_load_explicit(&events, memory_order_relaxed);
    out->valid = atomic_load_explicit(&valid, memory_order_relaxed);
}
CODE __attribute__((noinline)) static void publish_latest(const struct xpan_authority_latest *l)
{
    uint32_t s = atomic_load_explicit(&latest_seq, memory_order_relaxed);
    if ((s & 1u) || !atomic_compare_exchange_strong_explicit(&latest_seq, &s, s + 1u, memory_order_relaxed,
                                                             memory_order_relaxed)) {
        atomic_fetch_add_explicit(&latest_contended, 1u, memory_order_relaxed);
        return;
    }
    atomic_thread_fence(memory_order_release);
    const uint32_t job = l->reason == XPAN_AUTH_R_JOB;
    atomic_store_explicit(&latest_words[0], l->ordinal, memory_order_relaxed);
    atomic_store_explicit(&latest_words[1], l->reason, memory_order_relaxed);
    atomic_store_explicit(&latest_words[2], job ? l->jobid : 0u, memory_order_relaxed);
    atomic_store_explicit(&latest_words[3], job ? l->aspect : 0u, memory_order_relaxed);
    atomic_store_explicit(&latest_words[4], job ? l->flags : 0u, memory_order_relaxed);
    atomic_store_explicit(&latest_seq, s + 2u, memory_order_release);
}
CODE uint32_t xpan_authority_latest(struct xpan_authority_latest *out)
{
    uint32_t s = atomic_load_explicit(&latest_seq, memory_order_acquire);
    if (!s || (s & 1u))
        return 0u;
    out->ordinal = atomic_load_explicit(&latest_words[0], memory_order_relaxed);
    out->reason = atomic_load_explicit(&latest_words[1], memory_order_relaxed);
    out->jobid = atomic_load_explicit(&latest_words[2], memory_order_relaxed);
    out->aspect = atomic_load_explicit(&latest_words[3], memory_order_relaxed);
    out->flags = atomic_load_explicit(&latest_words[4], memory_order_relaxed);
    atomic_thread_fence(memory_order_acquire);
    return atomic_load_explicit(&latest_seq, memory_order_relaxed) == s;
}
CODE uint32_t xpan_authority_contended(void)
{
    return atomic_load_explicit(&latest_contended, memory_order_relaxed);
}
CODE static uint32_t validate_and_read(uint32_t handle, struct xpan_authority_latest *l)
{
    if (!frame_memory_data_span(handle, XPAN_AUTH_HANDLE_BYTES))
        return XPAN_AUTH_R_HANDLE;
    if (read_word(handle) != XPAN_AUTH_JOB_SIGNATURE)
        return XPAN_AUTH_R_SIGNATURE;
    const uint32_t object = read_word(handle + XPAN_AUTH_OBJECT_OFFSET);
    if (!frame_memory_data_span(object, XPAN_AUTH_OBJECT_BYTES))
        return XPAN_AUTH_R_OBJECT;
    l->jobid = read_word(object + XPAN_AUTH_JOBID_OFFSET);
    l->aspect = read_word(object + XPAN_AUTH_ASPECT_OFFSET);
    l->flags = read_word(object + XPAN_AUTH_PICTTYPE_OFFSET);
    atomic_fetch_add_explicit(&valid, 1u, memory_order_relaxed);
    return XPAN_AUTH_R_JOB;
}
CODE void xpan_review_authority_observe(const uint32_t *saved)
{
    /* Passive: reads only; registers are observed as passed. */
    uint32_t n = atomic_fetch_add_explicit(&events, 1u, memory_order_relaxed);
    struct xpan_authority_latest l = {n + 1u, XPAN_AUTH_R_NONE, 0u, 0u, 0u};
    l.reason = validate_and_read(saved[1], &l);
    /* Last, after the record is complete: the single latest-authority state the
     * review gate consumes. */
    publish_latest(&l);
}
