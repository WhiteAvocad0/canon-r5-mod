#ifndef XPAN_FRAME_ROW_H
#define XPAN_FRAME_ROW_H
#include <stdint.h>
#define XPAN_FRAME_ROW_WORDS 12u
#define XPAN_FRAME_ROW_BYTES 48u
/* Caller must supply separately established raw-photo authority. This core
 * does not identify a frame, bind a final scaler, or authorize camera use. */
uint32_t xpan_frame_row_project(uint32_t raw_aspect,uint32_t out[12]);
#endif
