#include "rect_crop.h"
#include "geometry.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif

CODE uint32_t xpan_rect_is_low_large(uint32_t low,uint32_t high,uint32_t aspect)
{
 (void)high;
 /* Exact E1732210 row3 precedes all other aspect7 rows. High-only row67
  * has an unbound output role and is deliberately outside initial scope. */
 return aspect==7u && (low&UINT32_C(0x101))?1u:0u;
}
CODE static uint32_t word(const uint32_t *p,uint32_t byte_offset)
{return *(const volatile uint32_t *)((uintptr_t)p+byte_offset);}

CODE enum xpan_rect_apply_result xpan_rect_project(uint32_t low,uint32_t high,
 uint32_t aspect,uint32_t *output)
{
 /* The original API owns parsing the borrowed source. Only its completed
  * caller-owned output is interpreted here; the source kind is not guessed. */
 if(!xpan_rect_is_low_large(low,high,aspect) || !output)return XPAN_RECT_UNCHANGED;
 uintptr_t o=(uintptr_t)output;
 if((o&3u) || o>UINTPTR_MAX-XPAN_RECT_OUTPUT_BYTES)return XPAN_RECT_UNCHANGED;
 /* All three actual row3 aliases are cropped dimensions. out16/20 is
  * an opaque8B copy. out32/36 is the FULL sensor pair and must stay full.
  * Reject partial or foreign views before changing any caller-owned word. */
 uint32_t height=word(output,4u);
 if((height!=XPAN_STOCK_LARGE_HEIGHT && height!=XPAN_LARGE_HEIGHT) ||
  word(output,0u)!=XPAN_LARGE_WIDTH || word(output,8u)!=XPAN_LARGE_WIDTH ||
  word(output,12u)!=height || word(output,24u)!=XPAN_LARGE_WIDTH ||
  word(output,28u)!=height || word(output,32u)!=XPAN_LARGE_WIDTH ||
  word(output,36u)!=XPAN_LARGE_FULL_HEIGHT)return XPAN_RECT_UNCHANGED;
 if(height==XPAN_LARGE_HEIGHT)return XPAN_RECT_ALREADY_PROJECTED;
 *(volatile uint32_t *)((uintptr_t)output+4u)=XPAN_LARGE_HEIGHT;
 *(volatile uint32_t *)((uintptr_t)output+12u)=XPAN_LARGE_HEIGHT;
 *(volatile uint32_t *)((uintptr_t)output+28u)=XPAN_LARGE_HEIGHT;
 return XPAN_RECT_PROJECTED;
}

#ifndef XPAN_RECT_HOST
_Static_assert(sizeof(uintptr_t)==4,"native DsDefine pointer ABI");
CODE __attribute__((noinline)) uint32_t xpan_get_aspect_rect_entry(uint32_t low,
 uint32_t high,uint32_t aspect,const uint32_t *spec,uint32_t *output)
{
 /* Five formal arguments preserve the exact stack[0] output pointer. The
  * original has no status return: retain its actualr0 even after projection.
  * No SF write, retained borrowed pointer, shared scratch, IRQ or kernel call. */
 uint32_t original=xpan_original_get_aspect_rect(low,high,aspect,spec,output);
 (void)xpan_rect_project(low,high,aspect,output);
 return original;
}
#endif
