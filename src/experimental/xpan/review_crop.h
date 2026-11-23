#ifndef R5_REVIEW_CROP_H
#define R5_REVIEW_CROP_H
#include <stdint.h>
/* XPan automatic post-shot review: owner of the review-descriptor crop at the
 * completed E063FBEE call (the copy of the 152-byte rear-LCD review descriptor
 * made for caller LR E063BECF; the common E063BECE path is never hooked).
 *
 * Exact match only: the descriptor must be active (word 0 == 1), kind 0, carry
 * a non-zero id, a 1024x682 source, and at +0x1C..+0x30 either the full-source
 * tuple {0,0,0,0,1024,682} (RAW + JPEG review) or Canon's stock 16:9 letterbox
 * tuple {0,53,0,53,1024,576} (JPEG-only review, issue #31). Then, and only if
 * the review gate (xpan_review_gate_crop) admits this review's fresh XPan
 * claim, the three words that differ are stored so the tuple becomes
 * {0,152,0,152,1024,378}: the centred 512:189 band (1024*3024/8192 = 378 rows,
 * (682-378)/2 = 152). Everything else, the source and the controller, is
 * untouched.
 *
 * JPEG review token (issue #31): every applied crop records which review it
 * was for. A letterbox (JPEG-only) crop keeps the gate claim's ordinal
 * (xpan_review_gate_claim_ordinal), a full-source crop clears it to 0; gate
 * refusals and descriptor rejects leave it. review_crop_jpeg_review() then
 * tells the playback fit (Quick Review, which Canon draws after the SR flag has
 * closed the claim) whether that shot is still the newest JPEG-only XPan
 * event. */
#define REVIEW_CROP_DESCRIPTOR_BYTES 152u
#define REVIEW_CROP_DESCRIPTOR_WORDS 38u
#define REVIEW_CROP_CALLER_FRAME_BYTES 216u
#define REVIEW_CROP_CALLER_LR UINT32_C(0xe063becf)
#define REVIEW_CROP_ORIGINAL_RESUME UINT32_C(0xe063fbf9)
#define REVIEW_CROP_COMPLETED UINT32_C(0x53524331)
#define REVIEW_CROP_SOURCE_SLOT_OFFSET 0x10a0u

enum review_crop_reason {
    REVIEW_CROP_R_APPLIED = 0,
    REVIEW_CROP_R_COMPLETION = 1,   /* entered without the completion marker */
    REVIEW_CROP_R_CALLER = 2,       /* not the E063BECF review copy */
    REVIEW_CROP_R_SLOT = 3,         /* slot != 0 */
    REVIEW_CROP_R_FRAME = 4,        /* caller frame not an admitted 8-aligned stack span */
    REVIEW_CROP_R_DESTINATION = 5,  /* destination != caller_sp + 16 */
    REVIEW_CROP_R_SOURCE_SLOT = 6,  /* source != controller + 0x10A0 */
    REVIEW_CROP_R_ACTIVE = 7,       /* descriptor word 0 != 1 */
    REVIEW_CROP_R_KIND = 8,         /* descriptor word 1 != 0 */
    REVIEW_CROP_R_ID = 9,           /* descriptor words 2..3 both 0 */
    REVIEW_CROP_R_SOURCE_DIMS = 10, /* source not 1024x682 */
    REVIEW_CROP_R_TUPLE = 11,       /* not the full-source tuple (already cropped or other) */
    REVIEW_CROP_R_GATE = 12,        /* the review gate refused (no fresh XPan claim) */
    REVIEW_CROP_R_LETTERBOX = 13,   /* applied to the 16:9 letterbox tuple (JPEG-only review) */
    REVIEW_CROP_R_COUNT = 14
};

struct review_crop_profile {
    uint32_t caller_lr, descriptor_bytes, caller_frame_bytes, source_slot_offset, completed_marker, source_width,
        source_height, before[6], after[6], letterbox_before[6];
};
extern const struct review_crop_profile review_crop_profile;
struct review_crop_call {
    uint32_t caller, caller_sp, destination, source, controller, slot, completed, original_result;
};
struct review_crop_cpu {
    uint32_t r[13], lr;
};
_Static_assert(sizeof(struct review_crop_cpu) == 56u, "review crop saved CPU ABI");
/* Own .bss decision counts, one per enum review_crop_reason. */
struct review_crop_counts {
    uint32_t reasons[REVIEW_CROP_R_COUNT];
};
void review_crop_counts(struct review_crop_counts *out);
/* Returns c->original_result unchanged; *reason (optional) gets the decision
 * (REVIEW_CROP_R_APPLIED or REVIEW_CROP_R_LETTERBOX when the band was stored). */
uint32_t review_crop_apply(const struct review_crop_call *c, uint32_t *reason);
/* XPAN_GATE_R_NONE while the latest letterbox crop's shot is still the newest
 * JPEG-only XPan event (xpan_review_gate_event_current), else its reason. */
uint32_t review_crop_jpeg_review(void);
void review_crop_native_post(const struct review_crop_cpu *entry, uint32_t caller_sp,
                             const struct review_crop_cpu *after, uint32_t completed);
void review_crop_entry(void);
void review_crop_original(void);
#endif
