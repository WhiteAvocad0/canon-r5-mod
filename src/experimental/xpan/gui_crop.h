#ifndef R5_XPAN_GUI_CROP_H
#define R5_XPAN_GUI_CROP_H
#include <stdint.h>

enum xpan_gui_height_status {
 XPAN_GUI_UNCHANGED=0,
 XPAN_GUI_APPLIED=1,
 XPAN_GUI_ALREADY_APPLIED=2
};
struct xpan_gui_height_result {uint32_t height,status;};
_Static_assert(sizeof(struct xpan_gui_height_result)==8,"GUI height result ABI");

/* Canonical SF remains8192x4608. Only the produced Large height changes;
 * already3024 is idempotent, and every unmatched tuple returns its old height. */
struct xpan_gui_height_result xpan_gui_large_height(uint32_t source_width,
 uint32_t source_height,uint32_t destination_width,uint32_t destination_height);

#ifndef XPAN_GUI_HOST
/* Source-bound E07D030C adapter. r0 is its sole argument; the original r0
 * return is preserved. Only incomingLR E07D2A7D admits the post-adjustment.
 * The immutable entry hook must be installed before Canon tasks start.
 * This borrows the original producer's source/owner call contract and natural
 * serialization; it does not authorize a new task/ISR call or validate a
 * synthetic pointer. No SF mutation, retained scratch, IF or kernel calls. */
uint32_t xpan_gui_crop_gather(uint32_t source);
/* Replays complete8-byte prefix with absolute171D8 load; resumesE07D0315. */
uint32_t xpan_gui_original_gather(uint32_t source);
#endif
#endif
