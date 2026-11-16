#ifndef R5_XPAN_JPEG_CROP_H
#define R5_XPAN_JPEG_CROP_H
#include <stdint.h>

enum xpan_jpeg_consumer { XPAN_JPEG_PRIMARY=0, XPAN_JPEG_SECONDARY=1 };
/* Pure facts: the native extractor owns pointer interpretation. No ambient
 * current-aspect value, TLS, new job fields or synthetic ownership inference. */
struct xpan_jpeg_crop_facts {
 uint32_t consumer,entry_lr,native_sp,job,param,outer_lr,descriptor,request;
 uint32_t engine,mode,tier,aspect,raw_aspect;
 uint32_t input_low,input_high,request_input_low,request_input_high;
};
struct xpan_roi_crop_facts {
 uint32_t selected,state,native_sp,argument,caller_lr,request;
 uint32_t raw_aspect,tier,aspect,mode,flag16,flag20,selection,input_record;
 uint32_t callback,callback_arg,request_callback,request_callback_arg;
};
_Static_assert(sizeof(struct xpan_jpeg_crop_facts)==68,"JPEG policy facts ABI");
_Static_assert(sizeof(struct xpan_roi_crop_facts)==72,"ROI policy facts ABI");
uint32_t xpan_jpeg_crop_admit(const struct xpan_jpeg_crop_facts *facts);
uint32_t xpan_roi_crop_admit(const struct xpan_roi_crop_facts *facts);
const uint32_t *xpan_jpeg_crop_parameters(void);
const uint32_t *xpan_roi_crop_record(uint32_t original);

#ifndef XPAN_JPEG_HOST
/* Immutable entry mappings must precede Canon tasks. The entry shims capture
 * native SP/LR before any shim frame. Only the source-proven synchronous
 * dispatcher frames are borrowed; unmatched callers delegate unchanged.
 * Entry/identified/applied observations are recorded by jpeg_crop_trace.h;
 * host policy builds omit native extraction and its trace calls. */
uint32_t xpan_jpeg_primary_entry(uint32_t job,uint32_t parameter);
uint32_t xpan_jpeg_secondary_entry(uint32_t job,uint32_t parameter);
uint32_t xpan_jpeg_crop_primary(uint32_t job,uint32_t parameter,
 uint32_t native_sp,uint32_t native_lr);
uint32_t xpan_jpeg_crop_secondary(uint32_t job,uint32_t parameter,
 uint32_t native_sp,uint32_t native_lr);
uint32_t xpan_jpeg_original_primary(uint32_t job,uint32_t parameter);
uint32_t xpan_jpeg_original_secondary(uint32_t job,uint32_t parameter);
/* Inside E017B564 at B61C, not a function-entry/post-return hook. Assembly
 * preserves all registers/APSR, replacing only selected r0 before native STR.
 * Config-to-later-pixel-submit queue ownership remains unproved: this module
 * does not make a camera eligibility or end-to-end buffer-lifetime claim. */
void xpan_roi_crop_entry(void);
uint32_t xpan_roi_crop_select(uint32_t selected,uint32_t state,
 uint32_t native_sp,uint32_t argument);
#endif
#endif
