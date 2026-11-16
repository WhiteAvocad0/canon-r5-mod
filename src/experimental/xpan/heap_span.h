#ifndef R5_HEAP_SPAN_H
#define R5_HEAP_SPAN_H
#include <stdint.h>
#define R5_HEAP_BASE 0x012c9000u
#define R5_HEAP_END 0x02140000u
struct pcm_heap_extent {uint32_t raw,request,xp,mambo,end;};
/* Geometry/mapping validity only. Allocation receipt and lifetime remain
 * owned by the native caller which invoked the source-bound allocator. */
int heap_span_supported(uint32_t raw,uint32_t bytes);
int heap_span_mapping_supported(uint32_t raw,uint32_t bytes,
 const uint32_t *groups,unsigned count);
uint32_t pcm_heap_request(uint32_t xp_bytes,uint32_t mambo_bytes);
int pcm_heap_layout(uint32_t raw,uint32_t xp_bytes,uint32_t mambo_bytes,
 struct pcm_heap_extent *out);
#endif
