#include "jpeg_crop_trace.h"
#include "observer.h"
#include <stdatomic.h>
#include <stddef.h>

#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif
_Static_assert(sizeof(uint32_t)==4 && sizeof(unsigned)==4 && ATOMIC_INT_LOCK_FREE==2,
 "trace requires lock-free32-bit atomics");
_Static_assert(sizeof(_Atomic uint32_t)==4,"atomic state storage");
_Static_assert(sizeof(struct xpan_jpeg_crop_facts)==JPEG_CROP_TRACE_JPEG_WORDS*4u,
 "JPEG snapshot schema");
_Static_assert(sizeof(struct xpan_roi_crop_facts)==JPEG_CROP_TRACE_ROI_WORDS*4u,
 "ROI snapshot schema");
_Static_assert(offsetof(struct xpan_jpeg_crop_facts,request_input_high)==JT_REQUEST_INPUT_HIGH*4u &&
 offsetof(struct xpan_roi_crop_facts,request_callback_arg)==RT_REQUEST_CALLBACK_ARG*4u,
 "snapshot field order");

struct trace_count_state {_Atomic uint32_t entered,identified,applied;};
struct trace_snapshot {
 _Atomic uint32_t state;
 unsigned char bytes[JPEG_CROP_TRACE_MAX_WORDS*4u];
};
static struct trace_count_state trace_counts[JPEG_CROP_TRACE_ROLES];
static struct trace_snapshot trace_snapshots[JPEG_CROP_TRACE_ROLES][JPEG_CROP_TRACE_KINDS];

CODE uint32_t jpeg_crop_trace_word_count(uint32_t role)
{return role<2u?JPEG_CROP_TRACE_JPEG_WORDS:(role==JPEG_TRACE_ROI?JPEG_CROP_TRACE_ROI_WORDS:0u);}

CODE void jpeg_crop_trace_enter(uint32_t role)
{
 if(role<JPEG_CROP_TRACE_ROLES)
  atomic_fetch_add_explicit(&trace_counts[role].entered,1u,memory_order_relaxed);
}
CODE static void freeze(uint32_t role,uint32_t kind,const void *facts,uint32_t bytes)
{
 struct trace_snapshot *slot=&trace_snapshots[role][kind];
 uint32_t expected=JPEG_TRACE_EMPTY;
 if(!atomic_compare_exchange_strong_explicit(&slot->state,&expected,JPEG_TRACE_WRITING,
  memory_order_relaxed,memory_order_relaxed))return;
 /* Copy character representations, not a u32 alias of an unrelated struct.
  * The winning publisher owns this slot until release publication. */
 const unsigned char *source=(const unsigned char *)facts;
 for(uint32_t i=0;i<bytes;i++)slot->bytes[i]=source[i];
 atomic_store_explicit(&slot->state,JPEG_TRACE_READY,memory_order_release);
}
CODE static void identified(uint32_t role,const void *facts,uint32_t bytes,uint32_t applied)
{
 atomic_fetch_add_explicit(&trace_counts[role].identified,1u,memory_order_relaxed);
 freeze(role,JPEG_TRACE_IDENTIFIED,facts,bytes);
 if(applied) {
  atomic_fetch_add_explicit(&trace_counts[role].applied,1u,memory_order_relaxed);
  freeze(role,JPEG_TRACE_APPLIED,facts,bytes);
 }
}
CODE void jpeg_crop_trace_jpeg(uint32_t role,const struct xpan_jpeg_crop_facts *facts,uint32_t applied)
{
 if(role<2u && facts)identified(role,facts,sizeof(*facts),applied);
}
CODE void jpeg_crop_trace_roi(const struct xpan_roi_crop_facts *facts,uint32_t applied)
{
 if(facts)identified(JPEG_TRACE_ROI,facts,sizeof(*facts),applied);
}
CODE uint32_t jpeg_crop_trace_counters(uint32_t role,struct jpeg_crop_trace_counts *out)
{
 if(role>=JPEG_CROP_TRACE_ROLES || !out)return 0u;
 out->entered=atomic_load_explicit(&trace_counts[role].entered,memory_order_relaxed);
 out->identified=atomic_load_explicit(&trace_counts[role].identified,memory_order_relaxed);
 out->applied=atomic_load_explicit(&trace_counts[role].applied,memory_order_relaxed);
 return 1u;
}
CODE uint32_t jpeg_crop_trace_snapshot(uint32_t role,uint32_t kind,uint32_t *out)
{
 if(role>=JPEG_CROP_TRACE_ROLES || kind>=JPEG_CROP_TRACE_KINDS)return JPEG_TRACE_EMPTY;
 const struct trace_snapshot *slot=&trace_snapshots[role][kind];
 uint32_t state=atomic_load_explicit(&slot->state,memory_order_acquire);
 if(state==JPEG_TRACE_READY && out) {
  unsigned char *destination=(unsigned char *)out;
  uint32_t bytes=jpeg_crop_trace_word_count(role)*4u;
  /* Immutable after state2: acquire publication makes each byte available. */
  for(uint32_t i=0;i<bytes;i++)destination[i]=slot->bytes[i];
 }
 return state;
}

static const char begin_format[] DATA="R5J1 B=%u V=%u";
static const char source_format[] DATA="R5J1 S=" OBSERVER_SOURCE_ID;
static const char counts_format[] DATA="R5J1 C=%u N=%u I=%u A=%u";
static const char ready_format[] DATA="R5J1 R=%u K=%u S=%u N=%u";
static const char pair_format[] DATA="R5J1 F=%u K=%u W=%u A=%08X B=%08X";
static const char single_format[] DATA="R5J1 F=%u K=%u W=%u A=%08X";
static const char end_format[] DATA="R5J1 E=%u";
CODE void jpeg_crop_trace_report(uint32_t sequence)
{
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,begin_format,sequence,JPEG_CROP_TRACE_SCHEMA);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,source_format);
 for(uint32_t role=0;role<JPEG_CROP_TRACE_ROLES;role++) {
  struct jpeg_crop_trace_counts counts;
  jpeg_crop_trace_counters(role,&counts);
  observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,counts_format,
   role,counts.entered,counts.identified,counts.applied);
  for(uint32_t kind=0;kind<JPEG_CROP_TRACE_KINDS;kind++) {
   uint32_t words[JPEG_CROP_TRACE_MAX_WORDS];
   uint32_t state=jpeg_crop_trace_snapshot(role,kind,words);
   uint32_t count=jpeg_crop_trace_word_count(role);
   observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,ready_format,role,kind,state,count);
   if(state!=JPEG_TRACE_READY)continue;
   for(uint32_t i=0;i<count;i+=2u) {
    if(i+1u<count)
     observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,pair_format,role,kind,i,words[i],words[i+1u]);
    else
     observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,single_format,role,kind,i,words[i]);
   }
  }
 }
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,end_format,sequence);
}
