#include "gui_crop.h"
#include "geometry.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif

CODE struct xpan_gui_height_result xpan_gui_large_height(uint32_t source_width,
 uint32_t source_height,uint32_t destination_width,uint32_t destination_height)
{
 struct xpan_gui_height_result result={destination_height,XPAN_GUI_UNCHANGED};
 if(source_width!=XPAN_LARGE_WIDTH || source_height!=XPAN_STOCK_LARGE_HEIGHT ||
  destination_width!=XPAN_LARGE_WIDTH)return result;
 if(destination_height==XPAN_LARGE_HEIGHT)result.status=XPAN_GUI_ALREADY_APPLIED;
 else if(destination_height==XPAN_STOCK_LARGE_HEIGHT) {
  result.height=XPAN_LARGE_HEIGHT;result.status=XPAN_GUI_APPLIED;
 }
 return result;
}

#ifndef XPAN_GUI_HOST
_Static_assert(sizeof(uintptr_t)==4,"native pointer ABI");
#define GUI_OWNER_SLOT UINT32_C(0x000171dc)
#define GUI_CALLER UINT32_C(0xe07d2a7d)
#define GUI_SOURCE_WIDTH 0x68u
#define GUI_SOURCE_HEIGHT 0x6cu
#define GUI_DESTINATION_WIDTH 0x22b4u
#define GUI_DESTINATION_HEIGHT 0x22b8u

CODE static uint32_t read_word(uint32_t address)
{return *(const volatile uint32_t *)(uintptr_t)address;}

CODE __attribute__((noinline)) uint32_t xpan_gui_crop_gather(uint32_t source)
{
 /* Preserve an unmatched caller's original behavior without new pointer reads.
  * GCC's native Thumb listing must retain the exact incomingLR comparison. */
 if((uintptr_t)__builtin_return_address(0)!=GUI_CALLER)
  return xpan_gui_original_gather(source);
 uint32_t owner=read_word(GUI_OWNER_SLOT),sw=0u,sh=0u;
 unsigned eligible=owner && !(owner&3u) &&
  owner<=UINT32_MAX-GUI_DESTINATION_HEIGHT-4u && source && !(source&3u) &&
  source<=UINT32_MAX-GUI_SOURCE_HEIGHT-4u;
 if(eligible) {
  /* The original gather reads these same fields and the rest of its0x798-byte
   * input. This adapter neither clones nor retains that borrowed source. */
  sw=read_word(source+GUI_SOURCE_WIDTH);sh=read_word(source+GUI_SOURCE_HEIGHT);
  eligible=sw==XPAN_LARGE_WIDTH && sh==XPAN_STOCK_LARGE_HEIGHT;
 }
 uint32_t original=xpan_gui_original_gather(source);
 if(!eligible || read_word(GUI_OWNER_SLOT)!=owner)return original;
 /* Never follow a changed owner. The completed original gather produced these
  * two scalar fields in this same Canon-owned object before our post-step. */
 uint32_t dw=read_word(owner+GUI_DESTINATION_WIDTH);
 uint32_t dh=read_word(owner+GUI_DESTINATION_HEIGHT);
 struct xpan_gui_height_result result=xpan_gui_large_height(sw,sh,dw,dh);
 if(result.status==XPAN_GUI_APPLIED)
  *(volatile uint32_t *)(uintptr_t)(owner+GUI_DESTINATION_HEIGHT)=result.height;
 return original;
}
#endif
