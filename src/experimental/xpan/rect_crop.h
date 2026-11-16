#ifndef R5_XPAN_RECT_CROP_H
#define R5_XPAN_RECT_CROP_H
#include <stdint.h>

/* R5 GetAspectRectDsDefine takes caller-owned48B output storage. The actual
 * routine writes40B; its initializer owns the final8B. SF2 is borrowed and
 * unchanged. This module only projects the source-bound Large aspect7 view. */
#define XPAN_RECT_OUTPUT_BYTES 48u

enum xpan_rect_apply_result {
 XPAN_RECT_UNCHANGED=0,
 XPAN_RECT_PROJECTED=1,
 XPAN_RECT_ALREADY_PROJECTED=2
};
/* True only for source row3/low bank. High-only bank is excluded. */
uint32_t xpan_rect_is_low_large(uint32_t quality_low,uint32_t quality_high,uint32_t aspect);
enum xpan_rect_apply_result xpan_rect_project(uint32_t quality_low,
 uint32_t quality_high,uint32_t aspect,uint32_t *output);
#ifndef XPAN_RECT_HOST
uint32_t xpan_get_aspect_rect_entry(uint32_t quality_low,uint32_t quality_high,
 uint32_t aspect,const uint32_t *spec,uint32_t *output);
uint32_t xpan_original_get_aspect_rect(uint32_t quality_low,uint32_t quality_high,
 uint32_t aspect,const uint32_t *spec,uint32_t *output);
#endif
#endif
