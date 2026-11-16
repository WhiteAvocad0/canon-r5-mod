#include "playback_reject.h"
const struct playback_reject_binding_profile playback_reject_profile={PLAYBACK_REJECT_SCHEMA,PNR_ROLE_COUNT,PLAYBACK_REJECT_WORDS,sizeof(struct playback_reject_record),_Alignof(struct playback_reject_record),PNR_REASON_COUNT,PNR_ALL_KNOWN};
struct playback_reject_record playback_reject_records[PNR_ROLE_COUNT];
static _Atomic uint32_t hits[PNR_ROLE_COUNT];
void playback_reject_claim(uint32_t role,const struct playback_reject_facts *facts)
{
 if(role>=PNR_ROLE_COUNT||!facts||facts->reason==PNR_NONE||facts->reason>=PNR_REASON_COUNT||
    (facts->known_mask&~PNR_ALL_KNOWN))return;
 atomic_fetch_add_explicit(&hits[role],1u,memory_order_relaxed);
 uint32_t expected=PNR_STATE_EMPTY;
 struct playback_reject_record *record=&playback_reject_records[role];
 if(!atomic_compare_exchange_strong_explicit(&record->state,&expected,PNR_STATE_WRITING,memory_order_acq_rel,memory_order_relaxed))return;
#define PNR_COPY(name) record->facts.name=facts->name;
 PLAYBACK_REJECT_FIELDS(PNR_COPY)
#undef PNR_COPY
 atomic_store_explicit(&record->state,PNR_STATE_READY,memory_order_release);
}
uint32_t playback_reject_snapshot(uint32_t role,struct playback_reject_facts *out)
{
 if(role>=PNR_ROLE_COUNT)return PNR_STATE_EMPTY;
 const struct playback_reject_record *record=&playback_reject_records[role];
 uint32_t state=atomic_load_explicit(&record->state,memory_order_acquire);
 if(state==PNR_STATE_READY&&out){
#define PNR_READ(name) out->name=record->facts.name;
  PLAYBACK_REJECT_FIELDS(PNR_READ)
#undef PNR_READ
 }
 return state;
}
uint32_t playback_reject_hits(uint32_t role)
{return role<PNR_ROLE_COUNT?atomic_load_explicit(&hits[role],memory_order_relaxed):0u;}
