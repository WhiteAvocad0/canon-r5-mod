#include "frame_tags.h"

#ifdef FRAME_TAGS_TEST_HOOK
/* Test scheduling seam only; the production owner algorithm is unchanged. */
extern void frame_tags_test_after_candidate_check(struct frame_tags *,unsigned);
#endif

static uint32_t revision(uint64_t control)
{return (uint32_t)(control>>FRAME_TAG_REV_SHIFT);}
static uint64_t count(uint64_t control)
{return control&FRAME_TAG_ACTIVE_MASK;}
static unsigned ready_control(uint64_t control)
{return revision(control)!=0u && count(control)==0u && !(control&FRAME_TAG_COLLISION);}

unsigned frame_tags_begin(struct frame_tags *owner,unsigned index,struct frame_tag_lease *lease)
{
 if(!lease)return 0u;
 lease->owner=0;lease->index=0u;lease->revision=0u;lease->active=0u;
 if(!owner || index>=FRAME_TAG_COUNT)return 0u;
 struct frame_tag_record *record=&owner->records[index];
 uint64_t old=atomic_load_explicit(&record->control,memory_order_seq_cst);
 for(;;) {
  uint32_t rev=revision(old);uint64_t active=count(old),next;
  unsigned exhausted=rev==UINT32_MAX || active==FRAME_TAG_ACTIVE_MASK;
  if(exhausted) {
   /* Permanent unknown quarantine: never wrap a revision/count or clear its
    * collision at a later idle Begin. No lease/count unit is issued. */
   next=((uint64_t)UINT32_MAX<<FRAME_TAG_REV_SHIFT)|FRAME_TAG_COLLISION|active;
  } else {
   next=((uint64_t)(rev+1u)<<FRAME_TAG_REV_SHIFT)|(active+1u)|
    (active?FRAME_TAG_COLLISION:0u);
  }
  if(atomic_compare_exchange_weak_explicit(&record->control,&old,next,
    memory_order_seq_cst,memory_order_seq_cst)) {
   atomic_thread_fence(memory_order_seq_cst);
   if(exhausted)return 0u;
   lease->owner=owner;lease->index=index;lease->revision=rev+1u;lease->active=1u;
   return 1u;
  }
 }
}

static void finish(struct frame_tag_lease *lease,uint32_t pointer,uint32_t frame,
 uint32_t raw,unsigned known)
{
 if(!lease)return;
 /* Consume the caller-owned lease before any final shared counter operation.
  * Leases are internal per-native-invocation state, not external handles. */
 struct frame_tag_lease current=*lease;
 lease->owner=0;lease->active=0u;
 if(!current.active || !current.owner || current.index>=FRAME_TAG_COUNT || !current.revision)return;
 struct frame_tag_record *record=&current.owner->records[current.index];
 uint64_t control=atomic_load_explicit(&record->control,memory_order_seq_cst);
 if(!count(control))return; /* Invalid/unregistered local lease: no underflow. */
 if(known && pointer && revision(control)==current.revision && count(control)==1u &&
  !(control&FRAME_TAG_COLLISION)) {
#ifdef FRAME_TAGS_TEST_HOOK
  frame_tags_test_after_candidate_check(current.owner,current.index);
#endif
  /* A newer Begin may intervene after this check. Its revision/active/collision
   * excludes all readers until old stores finish, and it cannot become ready
   * during the overlap. This old ready_revision cannot match its newer ticket. */
  atomic_store_explicit(&record->pointer,pointer,memory_order_seq_cst);
  atomic_store_explicit(&record->frame,frame,memory_order_seq_cst);
  atomic_store_explicit(&record->raw,raw,memory_order_seq_cst);
  atomic_store_explicit(&record->ready_revision,current.revision,memory_order_seq_cst);
 }
 atomic_thread_fence(memory_order_seq_cst);
 /* LAST shared owner write: no payload/log/state stores may follow this. */
 (void)atomic_fetch_sub_explicit(&record->control,UINT64_C(1),memory_order_seq_cst);
}

void frame_tags_end_unknown(struct frame_tag_lease *lease)
{finish(lease,0u,0u,0u,0u);}

void frame_tags_end_capture(struct frame_tag_lease *lease,uint32_t pointer,uint32_t raw,unsigned known)
{
 unsigned allowed=lease && lease->index==FRAME_TAG_DEFAULT;
 finish(lease,pointer,0u,raw,allowed && known);
}

unsigned frame_tags_read(const struct frame_tags *owner,unsigned index,uint32_t pointer,
 uint32_t native_frame,struct frame_tag_snapshot *out)
{
 if(!out)return 0u;
 out->owner=0;out->index=0u;out->pointer=0u;out->frame=0u;out->raw=0u;out->revision=0u;
 if(!owner || index>=FRAME_TAG_COUNT || !pointer)return 0u;
 const struct frame_tag_record *record=&owner->records[index];
 uint64_t before=atomic_load_explicit(&record->control,memory_order_seq_cst);
 if(!ready_control(before))return 0u;
 uint32_t p=atomic_load_explicit(&record->pointer,memory_order_seq_cst);
 uint32_t f=atomic_load_explicit(&record->frame,memory_order_seq_cst);
 uint32_t raw=atomic_load_explicit(&record->raw,memory_order_seq_cst);
 uint32_t ready=atomic_load_explicit(&record->ready_revision,memory_order_seq_cst);
 atomic_thread_fence(memory_order_seq_cst);
 uint64_t after=atomic_load_explicit(&record->control,memory_order_seq_cst);
 if(after!=before || ready!=revision(before) || p!=pointer ||
  f!=(index==FRAME_TAG_DEFAULT?0u:native_frame))return 0u;
 out->owner=owner;out->index=index;out->pointer=p;out->frame=f;out->raw=raw;out->revision=ready;
 return 1u;
}

unsigned frame_tags_same(const struct frame_tags *owner,const struct frame_tag_snapshot *source)
{
 if(!source || source->owner!=owner || !owner || !source->revision)return 0u;
 struct frame_tag_snapshot canonical;
 if(!frame_tags_read(owner,source->index,source->pointer,source->frame,&canonical))return 0u;
 return canonical.owner==source->owner && canonical.index==source->index &&
  canonical.pointer==source->pointer && canonical.frame==source->frame &&
  canonical.raw==source->raw && canonical.revision==source->revision;
}

void frame_tags_end_copy(struct frame_tag_lease *lease,uint32_t pointer,uint32_t frame,
 const struct frame_tag_snapshot *source,unsigned native_source_stable)
{
 uint32_t raw=0u;unsigned known=0u;
 if(lease && lease->owner && lease->index<FRAME_TAG_SLOT_COUNT && source && native_source_stable) {
  /* Copy local caller facts before validation; never trust a modified field
   * merely because its revision matches. Cross-owner snapshots are rejected. */
  struct frame_tag_snapshot candidate=*source;
  if(frame_tags_same(lease->owner,&candidate)) {raw=candidate.raw;known=1u;}
 }
 finish(lease,pointer,frame,raw,known);
}

void frame_tags_reset_begin(struct frame_tags *owner,struct frame_tag_reset *reset)
{
 if(!reset)return;
 for(unsigned index=0u;index<FRAME_TAG_COUNT;index++)
  (void)frame_tags_begin(owner,index,&reset->leases[index]);
}

void frame_tags_reset_end(struct frame_tag_reset *reset)
{
 if(!reset)return;
 for(unsigned index=0u;index<FRAME_TAG_COUNT;index++)
  frame_tags_end_unknown(&reset->leases[index]);
}
