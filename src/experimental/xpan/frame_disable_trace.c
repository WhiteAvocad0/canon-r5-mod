#include "frame_disable_trace.h"
#include <stddef.h>
#if !defined(FRAME_NATIVE_HOST)||defined(FRAME_NATIVE_HOST_REPORT)
#include "observer.h"
#endif
_Static_assert(ATOMIC_INT_LOCK_FREE==2,"disable trace requires lock-free u32");
_Static_assert(sizeof(struct frame_disable_facts)==64u,"disable facts ABI");
_Static_assert(offsetof(struct frame_disable_facts,known_mask)==52u,"disable known-mask ABI");
#define FIELD_ABI(member,word) _Static_assert(offsetof(struct frame_disable_facts,member)==4u*(word),"disable field ABI: " #member)
FIELD_ABI(reason,FD_W_REASON);FIELD_ABI(site,FD_W_SITE);FIELD_ABI(native_sp,FD_W_NATIVE_SP);
FIELD_ABI(scope_ptr,FD_W_SCOPE_PTR);FIELD_ABI(actual_saved_lr,FD_W_ACTUAL_SAVED_LR);
FIELD_ABI(expected_saved_lr,FD_W_EXPECTED_SAVED_LR);FIELD_ABI(argument_index,FD_W_ARGUMENT_INDEX);
FIELD_ABI(current_index,FD_W_CURRENT_INDEX);FIELD_ABI(recorded_destination,FD_W_RECORDED_DESTINATION);
FIELD_ABI(selected_destination,FD_W_SELECTED_DESTINATION);FIELD_ABI(marker,FD_W_MARKER);
FIELD_ABI(self,FD_W_SELF);FIELD_ABI(kind,FD_W_KIND);FIELD_ABI(known_mask,FD_W_KNOWN_MASK);
FIELD_ABI(aux_a,FD_W_AUX_A);FIELD_ABI(aux_b,FD_W_AUX_B);
#undef FIELD_ABI
const struct frame_disable_binding_profile frame_disable_profile={
 FRAME_DISABLE_SCHEMA,FRAME_DISABLE_WORDS,sizeof(struct frame_disable_facts),
 sizeof(struct frame_disable_record),_Alignof(struct frame_disable_record),
 offsetof(struct frame_disable_record,state),offsetof(struct frame_disable_record,facts),
 FD_READY,FD_K_ALL,FD_REASON_COUNT,FD_SITE_COUNT,
 {FD_W_REASON,FD_W_SITE,FD_W_NATIVE_SP,FD_W_SCOPE_PTR,FD_W_ACTUAL_SAVED_LR,
 FD_W_EXPECTED_SAVED_LR,FD_W_ARGUMENT_INDEX,FD_W_CURRENT_INDEX,
 FD_W_RECORDED_DESTINATION,FD_W_SELECTED_DESTINATION,FD_W_MARKER,FD_W_SELF,
 FD_W_KIND,FD_W_KNOWN_MASK,FD_W_AUX_A,FD_W_AUX_B}
};
struct frame_disable_record frame_disable_trace;
uint32_t frame_disable_record_claim(struct frame_disable_record *record,const struct frame_disable_facts *facts)
{
 if(!record||!facts)return 0u;
 uint32_t empty=FD_EMPTY;
 if(!atomic_compare_exchange_strong_explicit(&record->state,&empty,FD_WRITING,memory_order_relaxed,memory_order_relaxed))return 0u;
 const unsigned char *s=(const unsigned char *)facts;unsigned char *d=(unsigned char *)&record->facts;
 for(uint32_t i=0;i<sizeof(*facts);i++)d[i]=s[i];
 atomic_store_explicit(&record->state,FD_READY,memory_order_release);return 1u;
}
uint32_t frame_disable_record_snapshot(const struct frame_disable_record *record,struct frame_disable_facts *out)
{
 if(!record)return FD_EMPTY;
 uint32_t state=atomic_load_explicit(&record->state,memory_order_acquire);
 if(state==FD_READY&&out){
  const unsigned char *s=(const unsigned char *)&record->facts;unsigned char *d=(unsigned char *)out;
  for(uint32_t i=0;i<sizeof(*out);i++)d[i]=s[i];
 }
 return state;
}
void frame_disable_claim(const struct frame_disable_facts *facts)
{(void)frame_disable_record_claim(&frame_disable_trace,facts);}
uint32_t frame_disable_snapshot(struct frame_disable_facts *out)
{return frame_disable_record_snapshot(&frame_disable_trace,out);}
#if !defined(FRAME_NATIVE_HOST)||defined(FRAME_NATIVE_HOST_REPORT)
static const char begin_format[]=FD_FMT_BEGIN,source_format[]=FD_FMT_SOURCE;
static const char state_format[]=FD_FMT_STATE,words_format[]=FD_FMT_WORDS,end_format[]=FD_FMT_END;
void frame_disable_report(unsigned sequence)
{
 struct frame_disable_facts facts;uint32_t state=frame_disable_snapshot(&facts);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,begin_format,sequence,FRAME_DISABLE_SCHEMA,FRAME_DISABLE_WORDS);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,source_format);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,state_format,state);
 if(state==FD_READY){
  /* Copy representation into a real u32 array, avoiding a struct/u32 alias. */
  uint32_t words[FRAME_DISABLE_WORDS];unsigned char *d=(unsigned char *)words;
  const unsigned char *s=(const unsigned char *)&facts;
  for(uint32_t i=0;i<sizeof(facts);i++)d[i]=s[i];
  for(uint32_t i=0;i<FRAME_DISABLE_WORDS;i+=4u)
   observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,words_format,i,words[i],words[i+1u],words[i+2u],words[i+3u]);
 }
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,end_format,sequence);
}
#else
void frame_disable_report(unsigned sequence){(void)sequence;}
#endif
