#include "heap_span.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
CODE int heap_span_supported(uint32_t raw,uint32_t bytes)
{
 return !(raw&3u) && bytes && raw>=R5_HEAP_BASE+112u && raw<=R5_HEAP_END &&
  bytes<=R5_HEAP_END-raw;
}
CODE int heap_span_mapping_supported(uint32_t raw,uint32_t bytes,
 const uint32_t *groups,unsigned count)
{
 if(!groups||!heap_span_supported(raw,bytes))return 0;
 uint32_t first=raw>>24,last=(raw+bytes-1u)>>24;
 if(count!=(last-first+1u)*16u)return 0;
 for(unsigned n=0;n<count;n++)
  if(groups[n]!=(((first+n/16u)<<24)|0x00054406u))return 0;
 return 1;
}
CODE uint32_t pcm_heap_request(uint32_t xp,uint32_t mambo)
{
 if(!xp||!mambo||((xp|mambo)&31u)||xp>UINT32_MAX-32u ||
  mambo>UINT32_MAX-32u-xp)return 0;
 uint32_t bytes=xp+mambo+32u;
 return bytes<=R5_HEAP_END-R5_HEAP_BASE-112u?bytes:0u;
}
CODE int pcm_heap_layout(uint32_t raw,uint32_t xp,uint32_t mambo,
 struct pcm_heap_extent *out)
{
 uint32_t bytes=pcm_heap_request(xp,mambo);
 if(!out||!heap_span_supported(raw,bytes))return 0;
 struct pcm_heap_extent result={raw,bytes,(raw+31u)&~31u,0,0};
 result.mambo=result.xp+xp;result.end=result.mambo+mambo;
 if(result.end>raw+bytes)return 0;
 *out=result;return 1;
}
