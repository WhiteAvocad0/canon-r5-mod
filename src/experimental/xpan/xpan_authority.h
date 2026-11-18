#ifndef R5_XPAN_AUTHORITY_H
#define R5_XPAN_AUTHORITY_H
#include <stdint.h>
/* XPan review-authority observer (passive, part of the XPan automatic-review
 * fix). Owner of the entry hook at E054C530: the scs handler that logs
 * scsVramDevelopComplete(JobID) (E054C548 bl GetJobID) and then permits
 * SIMPLE_REVIEW_REQ (E054C55A bl E0594B38). Reached only through the dispatch
 * words E0E35C40..E0E35C78 (all = E054C531); no BL callers. At entry r0 = scs
 * context, r1 = Job handle {signature word, object pointer}, r2 = argument
 * passed to E0594B38.
 *
 * Read-only on Canon state; no Canon call. Writes only its own .bss counters
 * and its latest-authority seqlock record. Canon memory is read only inside
 * spans admitted by frame_memory_data_span, and the object only after the
 * handle's signature word matched. */
#define XPAN_AUTH_HOOK_VA UINT32_C(0xe054c530)
#define XPAN_AUTH_RESUME UINT32_C(0xe054c539)       /* Thumb E054C538 bne.n */
#define XPAN_AUTH_JOB_SIGNATURE UINT32_C(0xe2083738) /* ROM word [E0F28178], compared by E0803ED2 */
#define XPAN_AUTH_HANDLE_BYTES 8u                    /* {signature, object} */
#define XPAN_AUTH_OBJECT_OFFSET 4u                   /* E07FA138 ldr r0,[r4,#4] */
#define XPAN_AUTH_JOBID_OFFSET 0x3c0u                /* E07FA13A ldr.w r0,[r0,#0x3c0] (GetJobID) */
#define XPAN_AUTH_ASPECT_OFFSET 0x14c0u              /* CF0 block obj+0xCF0 (E07FD884/E07FD88C), +0x7D0 (E0840D34) */
#define XPAN_AUTH_PICTTYPE_OFFSET 0x19f0u            /* CF0+0xD00 (E0840D84): image-type flags word */
#define XPAN_AUTH_OBJECT_BYTES (XPAN_AUTH_PICTTYPE_OFFSET + 4u)

/* Event classification (xpan_authority_latest.reason). */
enum xpan_auth_reason {
    XPAN_AUTH_R_NONE = 0,
    XPAN_AUTH_R_JOB = 1,       /* validated Job: jobid/aspect/flags were read */
    XPAN_AUTH_R_HANDLE = 2,    /* handle not an admitted data span */
    XPAN_AUTH_R_SIGNATURE = 3, /* handle signature != XPAN_AUTH_JOB_SIGNATURE */
    XPAN_AUTH_R_OBJECT = 4     /* object not an admitted data span */
};

struct xpan_authority_profile {
    uint32_t hook_va, resume, signature, handle_bytes, object_offset, jobid_offset, aspect_offset,
        picttype_offset, object_bytes;
};
extern const struct xpan_authority_profile xpan_authority_profile;

/* Own .bss: total entries and entries whose Job handle validated. */
struct xpan_authority_counts {
    uint32_t events, valid;
};
void xpan_authority_counts(struct xpan_authority_counts *out);
/* The single "latest authority" state, published last in every event (valid or
 * not). ordinal = that event's 1-based number, the same count as
 * xpan_authority_counts.events, so a reader can tell whether the record
 * describes the newest event. jobid/aspect/flags are meaningful only when
 * reason == XPAN_AUTH_R_JOB (else 0). Single writer assumed (the scs handler):
 * a publication that finds another in progress is skipped and counted, so the
 * record then lags the event count (consumers fail closed). Readers take a
 * seqlock snapshot and never wait. */
struct xpan_authority_latest {
    uint32_t ordinal, reason, jobid, aspect, flags;
};
/* 1: stable snapshot of a published record. 0: nothing published yet, or a
 * publication was in progress or raced the read (*out then unspecified). */
uint32_t xpan_authority_latest(struct xpan_authority_latest *out);
/* Publications skipped because another publication was in progress. */
uint32_t xpan_authority_contended(void);
/* saved[0..5] = caller r0,r1,r2,r3,r12,lr at handler entry. */
void xpan_review_authority_observe(const uint32_t *saved);
void xpan_review_authority_entry(void);
#endif
