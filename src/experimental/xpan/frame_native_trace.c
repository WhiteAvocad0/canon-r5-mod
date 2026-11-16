#include "frame_native_trace.h"
#include <stdatomic.h>
#if !defined(FRAME_NATIVE_HOST)||defined(FRAME_NATIVE_HOST_REPORT)
#include "observer.h"
#endif
_Static_assert(ATOMIC_INT_LOCK_FREE==2,"private trace requires lock-free u32");
struct private_counts {_Atomic uint32_t entered,identified,applied,rejected;};
struct private_snapshot {_Atomic uint32_t state;unsigned char facts[FRAME_NATIVE_MAX_WORDS*4u];};
static struct private_counts counts[FN_ROLE_COUNT];
static struct private_snapshot snapshots[FN_ROLE_COUNT][2];
void frame_native_trace_enter(uint32_t role)
{if(role<FN_ROLE_COUNT)atomic_fetch_add_explicit(&counts[role].entered,1u,memory_order_relaxed);}
void frame_native_trace_reject(uint32_t role)
{if(role<FN_ROLE_COUNT)atomic_fetch_add_explicit(&counts[role].rejected,1u,memory_order_relaxed);}
uint32_t frame_native_trace_word_count(uint32_t role)
{return role>=FN_ROLE_COUNT?0u:role==FN_ROLE_CAPTURE?FRAME_NATIVE_CAPTURE_WORDS:role>=FN_ROLE_ROW?FRAME_NATIVE_POLICY_WORDS:FRAME_NATIVE_FACT_WORDS;}
static void freeze(uint32_t role,uint32_t kind,const void *facts)
{
 struct private_snapshot *slot=&snapshots[role][kind];uint32_t empty=0u;
 if(!atomic_compare_exchange_strong_explicit(&slot->state,&empty,1u,memory_order_relaxed,memory_order_relaxed))return;
 const unsigned char *source=(const unsigned char *)facts;
 for(uint32_t i=0;i<frame_native_trace_word_count(role)*4u;i++)slot->facts[i]=source[i];
 atomic_store_explicit(&slot->state,2u,memory_order_release);
}
void frame_native_trace_observe(uint32_t role,const void *facts,uint32_t applied)
{
 if(role>=FN_ROLE_COUNT||!facts)return;
 atomic_fetch_add_explicit(&counts[role].identified,1u,memory_order_relaxed);freeze(role,0u,facts);
 if(applied){atomic_fetch_add_explicit(&counts[role].applied,1u,memory_order_relaxed);freeze(role,1u,facts);}
}
uint32_t frame_native_trace_counts(uint32_t role,struct frame_native_counts *out)
{
 if(role>=FN_ROLE_COUNT||!out)return 0u;
 out->entered=atomic_load_explicit(&counts[role].entered,memory_order_relaxed);
 out->identified=atomic_load_explicit(&counts[role].identified,memory_order_relaxed);
 out->applied=atomic_load_explicit(&counts[role].applied,memory_order_relaxed);
 out->rejected=atomic_load_explicit(&counts[role].rejected,memory_order_relaxed);return 1u;
}
uint32_t frame_native_trace_snapshot(uint32_t role,uint32_t kind,uint32_t out[FRAME_NATIVE_MAX_WORDS])
{
 if(role>=FN_ROLE_COUNT||kind>1u)return 0u;
 const struct private_snapshot *slot=&snapshots[role][kind];
 uint32_t state=atomic_load_explicit(&slot->state,memory_order_acquire);
 if(state==2u&&out){unsigned char *d=(unsigned char *)out;for(uint32_t i=0;i<frame_native_trace_word_count(role)*4u;i++)d[i]=slot->facts[i];}
 return state;
}
#if !defined(FRAME_NATIVE_HOST)||defined(FRAME_NATIVE_HOST_REPORT)
static const char begin_format[]=FN_FMT_BEGIN;
static const char source_format[]=FN_FMT_SOURCE;
static const char disabled_format[]=FN_FMT_DISABLED;
static const char counts_format[]=FN_FMT_COUNTS;
static const char ready_format[]=FN_FMT_READY;
static const char facts_format[]=FN_FMT_WORDS;
static const char tail_format[]=FN_FMT_TAIL;
static const char end_format[]=FN_FMT_END;
void frame_native_report(unsigned sequence)
{
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,begin_format,sequence,FRAME_NATIVE_SCHEMA);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,source_format);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,disabled_format,!frame_native_enabled());
 for(uint32_t role=0;role<FN_ROLE_COUNT;role++){
  struct frame_native_counts c;frame_native_trace_counts(role,&c);
  observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,counts_format,role,c.entered,c.identified,c.applied,c.rejected);
  for(uint32_t kind=0;kind<2u;kind++){
   uint32_t words[FRAME_NATIVE_MAX_WORDS];uint32_t state=frame_native_trace_snapshot(role,kind,words),count=frame_native_trace_word_count(role);
   observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,ready_format,role,kind,state,count);
   if(state!=2u)continue;
   for(uint32_t i=0;i<count;i+=4u){
    if(i+3u<count)observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,facts_format,i,words[i],words[i+1u],words[i+2u],words[i+3u]);
    else observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,tail_format,i,words[i],words[i+1u],words[i+2u]);
   }
  }
 }
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,end_format,sequence);
}
#else
void frame_native_report(unsigned sequence){(void)sequence;}
#endif
