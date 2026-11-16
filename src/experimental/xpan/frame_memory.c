#include "frame_memory.h"
const struct frame_memory_binding_profile frame_memory_profile={
 0x00004000u,0x40000000u,0xdffc5000u,0xdffc6000u,0xdffc6000u,0xdffc7000u
};
_Static_assert(sizeof(struct frame_memory_binding_profile)==24u,"memory profile ABI");
static uint32_t within(uint32_t p,uint32_t bytes,uint32_t begin,uint32_t end)
{return !(p&3u)&&p>=begin&&p<end&&bytes<=end-p;}
uint32_t frame_memory_data_span(uint32_t p,uint32_t bytes)
{return within(p,bytes,frame_memory_profile.data_begin,frame_memory_profile.data_end);}
uint32_t frame_memory_stack_frame_span(uint32_t sp,uint32_t extent)
{
 if(!extent)return 0u;
 if(sp>=frame_memory_profile.irq0_begin&&sp<frame_memory_profile.irq0_end)
  return within(sp,extent,frame_memory_profile.irq0_begin,frame_memory_profile.irq0_end);
 if(sp>=frame_memory_profile.irq1_begin&&sp<frame_memory_profile.irq1_end)
  return within(sp,extent,frame_memory_profile.irq1_begin,frame_memory_profile.irq1_end);
 return frame_memory_data_span(sp,extent);
}
uint32_t frame_memory_stack_offset_span(uint32_t sp,uint32_t extent,uint32_t offset,uint32_t bytes)
{
 return bytes&&!(offset&3u)&&frame_memory_stack_frame_span(sp,extent)&&
  offset<=extent&&bytes<=extent-offset;
}
