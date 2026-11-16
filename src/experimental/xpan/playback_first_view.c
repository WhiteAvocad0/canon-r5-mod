#include "playback_first_view.h"

const struct playback_first_view_binding_profile playback_first_view_profile={
 PLAYBACK_FIRST_VIEW_SCHEMA,
 PFV_ROLE_COUNT,
 PLAYBACK_FIRST_VIEW_WORDS,
 sizeof(struct playback_first_view_record),
 _Alignof(struct playback_first_view_record),
 PFV_R_COUNT,
 PFV_ALL_KNOWN
};

struct playback_first_view_record playback_first_view_records[PFV_ROLE_COUNT];
static _Atomic uint32_t hits[PFV_ROLE_COUNT];

void playback_first_view_claim(uint32_t role,const struct playback_first_view_facts *facts)
{
 if(role>=PFV_ROLE_COUNT||!facts||facts->reason==PFV_R_NONE||facts->reason>=PFV_R_COUNT||
    facts->site>=PFV_SITE_COUNT||(facts->known_mask&~PFV_ALL_KNOWN))return;
 atomic_fetch_add_explicit(&hits[role],1u,memory_order_relaxed);
 uint32_t expected=PFV_STATE_EMPTY;
 struct playback_first_view_record *record=&playback_first_view_records[role];
 if(!atomic_compare_exchange_strong_explicit(&record->state,&expected,PFV_STATE_WRITING,
     memory_order_acq_rel,memory_order_relaxed))return;
#define PFV_COPY(name) record->facts.name=facts->name;
 PLAYBACK_FIRST_VIEW_FIELDS(PFV_COPY)
#undef PFV_COPY
 atomic_store_explicit(&record->state,PFV_STATE_READY,memory_order_release);
}

uint32_t playback_first_view_snapshot(uint32_t role,struct playback_first_view_facts *out)
{
 if(role>=PFV_ROLE_COUNT)return PFV_STATE_EMPTY;
 const struct playback_first_view_record *record=&playback_first_view_records[role];
 uint32_t state=atomic_load_explicit(&record->state,memory_order_acquire);
 if(state==PFV_STATE_READY&&out){
#define PFV_READ(name) out->name=record->facts.name;
  PLAYBACK_FIRST_VIEW_FIELDS(PFV_READ)
#undef PFV_READ
 }
 return state;
}

uint32_t playback_first_view_hits(uint32_t role)
{
 return role<PFV_ROLE_COUNT?atomic_load_explicit(&hits[role],memory_order_relaxed):0u;
}
