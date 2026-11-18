#ifndef R5_XPAN_REVIEW_GATE_H
#define R5_XPAN_REVIEW_GATE_H
#include <stdint.h>
/* Authority-gated XPan automatic post-shot review. Owner of the per-review
 * claim on the latest authority event (xpan_authority_latest) and of its two
 * consumers, which must agree for one review:
 *   - outline: the _DrawAspectLine hook at E0A956FE (10 bytes
 *     089a1e21d8f814004446, replayed exactly, resume E0A95709) zeroes the
 *     caller-local H [sp+28] and V [sp+32] decision words for that call only;
 *   - crop: review_crop_apply asks xpan_review_gate_crop() before its
 *     three-word store at E063FBEE.
 *
 * Claim protocol (fail-closed):
 *   - The review start is the SR Undisp redraw: caller Background_Draw
 *     ([sp+84]==E0A957CB), Background_Draw called from SetDisplayDlgLiveView's
 *     Undisp path ([sp+108]==E05FDF6D), SR flag [0x17268]==1, display
 *     [sp+68]==0, force (sl)==1. It claims the newest event only if exactly one
 *     authority event arrived since the previous review start and the latest
 *     record describes it; otherwise the review has no claim (stock review;
 *     a burst gives MULTIPLE).
 *   - A claim is XPan only for aspect 7 with a Large JPEG ((flags & 0x101) != 0).
 *   - Each consumer admits only an open, unrevoked XPan claim whose event is
 *     still the latest event (stable snapshot). A newer event revokes the claim
 *     for the rest of that review.
 *   - Any _DrawAspectLine call with the SR flag != 1 closes the claim (the
 *     review is over), so a later review cannot reuse it.
 * Writes: only its own .bss and (outline, admitted only) the two stack words. */
#define XPAN_OUTLINE_HOOK_VA UINT32_C(0xe0a956fe)
#define XPAN_OUTLINE_RESUME UINT32_C(0xe0a95709)     /* Thumb E0A95708 bl SetVisible(30) */
#define XPAN_OUTLINE_CALLER_LR UINT32_C(0xe0a957cb)  /* Background_Draw E0A957C6 bl _DrawAspectLine */
#define XPAN_OUTLINE_UNDISP_LR UINT32_C(0xe05fdf6d)  /* SetDisplayDlgLiveView E05FDF68 bl Background_Draw(sp,0,1) */
#define XPAN_OUTLINE_H_OFFSET 28u                     /* E0A95698 str r0,[sp,#28]; E0A9575A ldr r2,[sp,#28] */
#define XPAN_OUTLINE_V_OFFSET 32u                     /* E0A9568C str r0,[sp,#32]; E0A956FE ldr r2,[sp,#32] */
#define XPAN_OUTLINE_DISPLAY_OFFSET 68u               /* saved Background_Draw r7 (E0A9577A mov r7,r1) */
#define XPAN_OUTLINE_CALLER_OFFSET 84u                /* _DrawAspectLine saved LR (push r4-sl,lr; sub sp,#56) */
#define XPAN_OUTLINE_BD_CALLER_OFFSET 108u            /* Background_Draw saved LR (push r4-r8,lr) */
#define XPAN_OUTLINE_FRAME_BYTES 112u
#define XPAN_GATE_SR_FLAG UINT32_C(0x00017268)        /* E0B20CA2: [0x17258+16]; set E0B20DEC, cleared E0B20DB4 */
#define XPAN_GATE_ASPECT_SLOT UINT32_C(0x000171dc)    /* E07D6B68: [0x171D8+4] */
#define XPAN_GATE_ASPECT_OFFSET 0x18c0u               /* +0x1000 then ldrb +0x8C0 */
#define XPAN_GATE_ASPECT_XPAN 7u
#define XPAN_GATE_LARGE_JPEG 0x101u                   /* image-type flags & 0x101: Large JPEG present */
/* Claim word: 0 = no claim; else ordinal<<2 | REVOKED | XPAN. */
#define XPAN_CLAIM_XPAN 1u
#define XPAN_CLAIM_REVOKED 2u
#define XPAN_CLAIM_ORDINAL_MASK UINT32_C(0x3fffffff)

enum xpan_gate_reason {
    XPAN_GATE_R_NONE = 0,         /* admitted */
    XPAN_GATE_R_CLAIMED = 1,      /* review start: claimed a fresh XPan event */
    XPAN_GATE_R_NOT_XPAN = 2,     /* the claimed event is not XPan (full frame etc.) */
    XPAN_GATE_R_NO_NEW_EVENT = 3, /* review start without a new authority event */
    XPAN_GATE_R_MULTIPLE = 4,     /* more than one new event (burst): stock review */
    XPAN_GATE_R_UNSTABLE = 5,     /* no stable latest-authority snapshot */
    XPAN_GATE_R_UNPUBLISHED = 6,  /* the latest record lags the event count */
    XPAN_GATE_R_INVALID = 7,      /* the latest event is not a validated Job */
    XPAN_GATE_R_NO_CLAIM = 8,     /* no open claim */
    XPAN_GATE_R_REVOKED = 9,      /* claim revoked by a newer event */
    XPAN_GATE_R_STALE = 10,       /* a newer event exists now: revoke */
    XPAN_GATE_R_COUNT = 11
};
enum xpan_outline_reason {
    XPAN_OUTLINE_R_HIDDEN = 0,     /* this call's outline hidden */
    XPAN_OUTLINE_R_FRAME = 1,      /* native frame not an admitted stack span */
    XPAN_OUTLINE_R_CALLER = 2,     /* not the Background_Draw call */
    XPAN_OUTLINE_R_DISPLAY = 3,    /* not the rear-LCD display */
    XPAN_OUTLINE_R_GUI_ASPECT = 4, /* GUI aspect unknown or not XPan */
    XPAN_OUTLINE_R_DECISION = 5,   /* Canon's own H/V decision is not the 16:9 outline */
    XPAN_OUTLINE_R_GATE = 6,       /* the gate refused (see xpan_review_gate_counts.last_gate) */
    XPAN_OUTLINE_R_COUNT = 7
};

struct xpan_review_gate_profile {
    uint32_t hook_va, resume, caller_lr, undisp_lr, h_offset, v_offset, display_offset, caller_offset,
        bd_caller_offset, frame_bytes, sr_flag, aspect_slot, aspect_offset, aspect_xpan, large_jpeg_mask;
};
extern const struct xpan_review_gate_profile xpan_review_gate_profile;

/* Own .bss counters: totals, per-reason decisions and the latest gate refusal. */
struct xpan_review_gate_counts {
    uint32_t reviews, outline_calls, outline_decisions, outline_hidden, crop_decisions, crop_applied, closes;
    uint32_t open[XPAN_GATE_R_COUNT], crop[XPAN_GATE_R_COUNT], outline[XPAN_OUTLINE_R_COUNT];
    uint32_t last_gate;
};
void xpan_review_gate_counts(struct xpan_review_gate_counts *out);
/* Crop consumer: 1 = this review holds a fresh XPan claim, write allowed. */
uint32_t xpan_review_gate_crop(uint32_t caller);
/* Outline consumer, called by the wrapper with the native _DrawAspectLine SP
 * and its force argument (sl). May write only [sp+28] and [sp+32] (both 0). */
void xpan_review_outline_decide(uint32_t native_sp, uint32_t force);
void xpan_review_outline_entry(void);
#endif
