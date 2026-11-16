#include "frame_row.h"
#include "frame_row_constants.h"
_Static_assert(sizeof(uint32_t)==4,"frame-row word ABI");
_Static_assert(sizeof(struct xpan_frame_row_case)==96,"source/projected row ABI");

static uint32_t row_equal(const volatile uint32_t *out,const uint32_t expected[12])
{
 for(uint32_t word=0u;word<XPAN_FRAME_ROW_WORDS;word++)
  if(out[word]!=expected[word])return 0u;
 return 1u;
}

uint32_t xpan_frame_row_project(uint32_t raw_aspect,uint32_t out[12])
{
 if(raw_aspect!=7u || !out || ((uintptr_t)out&3u) ||
  (uintptr_t)out>UINTPTR_MAX-XPAN_FRAME_ROW_BYTES)return 0u;
 volatile uint32_t *owned=out;
 for(uint32_t index=0u;index<XPAN_FRAME_ROW_CASE_COUNT;index++) {
  const struct xpan_frame_row_case *profile=&xpan_frame_row_cases[index];
  if(row_equal(owned,profile->original)) {
   /* Preserve every other word, including backing dimensions/pitch and X.
    * Authority for this caller/frame and final scaler remains external. */
   owned[7]=profile->projected[7];
   owned[9]=profile->projected[9];
   owned[11]=profile->projected[11];
   return 1u;
  }
  if(row_equal(owned,profile->projected))return 2u;
 }
 return 0u;
}
