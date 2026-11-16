#include "playback_tags.h"

#ifdef PLAYBACK_TAGS_TEST_HOOK
/* Bounded scheduling seam; production state operations remain unchanged. */
extern void playback_tags_test_point(struct playback_tags *,unsigned,unsigned);
#endif
static uint32_t revision(uint64_t control)
{return (uint32_t)(control>>PLAYBACK_TAG_REV_SHIFT);}
static uint64_t count(uint64_t control)
{return control&PLAYBACK_TAG_ACTIVE_MASK;}
static unsigned ready_control(uint64_t control)
{return revision(control)!=0u && count(control)==0u && !(control&PLAYBACK_TAG_COLLISION);}
/* Use the actual facts members, avoiding a struct/u32 alias or crop-policy
 * interpretation. A complete legacy box is knowledge too. */
static uint32_t fact_word(const struct playback_box_facts *facts,unsigned word)
{return word<5u?facts->words[word]:word==5u?facts->current_width:facts->current_height;}
static void set_fact_word(struct playback_box_facts *facts,unsigned word,uint32_t value)
{
 if(word<5u)facts->words[word]=value;
 else if(word==5u)facts->current_width=value;
 else facts->current_height=value;
}

struct playback_tag_lease playback_tags_begin(struct playback_tags *owner,unsigned index)
{
 struct playback_tag_lease lease;
 lease.owner=0;lease.index=index;lease.revision=0u;lease.active=0u;
 if(!owner || index>=PLAYBACK_TAG_SLOT_COUNT)return lease;
 struct playback_tag_record *record=&owner->records[index];
 uint64_t old=atomic_load_explicit(&record->control,memory_order_seq_cst);
 for(;;) {
  uint32_t rev=revision(old);uint64_t active=count(old),next;
  unsigned exhausted=rev==UINT32_MAX || active==PLAYBACK_TAG_ACTIVE_MASK;
  if(exhausted)
   next=((uint64_t)UINT32_MAX<<PLAYBACK_TAG_REV_SHIFT)|PLAYBACK_TAG_COLLISION|active;
  else next=((uint64_t)(rev+1u)<<PLAYBACK_TAG_REV_SHIFT)|(active+1u)|
   (active?PLAYBACK_TAG_COLLISION:0u);
  if(atomic_compare_exchange_weak_explicit(&record->control,&old,next,
    memory_order_seq_cst,memory_order_seq_cst)) {
   /* Before any caller-native write. Exhaustion never issues a reusable
    * revision or active unit; permanent collision keeps all reads unknown. */
   atomic_thread_fence(memory_order_seq_cst);
   if(!exhausted){lease.owner=owner;lease.index=index;lease.revision=rev+1u;lease.active=1u;}
   return lease;
  }
 }
}

void playback_tags_end(struct playback_tag_lease *lease,uint32_t object,
 const struct playback_box_facts *facts,unsigned known)
{
 if(!lease)return;
 struct playback_tag_lease current=*lease;
 lease->owner=0;lease->active=0u;
 if(!current.active || !current.owner || current.index>=PLAYBACK_TAG_SLOT_COUNT || !current.revision)return;
 struct playback_tag_record *record=&current.owner->records[current.index];
 uint64_t control=atomic_load_explicit(&record->control,memory_order_seq_cst);
 if(!count(control))return;
 if(known && facts && object && revision(control)==current.revision &&
  count(control)==1u && !(control&PLAYBACK_TAG_COLLISION)) {
#ifdef PLAYBACK_TAGS_TEST_HOOK
  playback_tags_test_point(current.owner,current.index,1u);
#endif
  atomic_store_explicit(&record->object,object,memory_order_seq_cst);
  for(unsigned i=0u;i<PLAYBACK_TAG_FACT_WORDS;i++)
   atomic_store_explicit(&record->facts[i],fact_word(facts,i),memory_order_seq_cst);
  atomic_store_explicit(&record->ready_revision,current.revision,memory_order_seq_cst);
#ifdef PLAYBACK_TAGS_TEST_HOOK
  playback_tags_test_point(current.owner,current.index,2u);
#endif
 }
 atomic_thread_fence(memory_order_seq_cst);
 /* LAST shared record write: no payload, revision or log store follows. */
 (void)atomic_fetch_sub_explicit(&record->control,UINT64_C(1),memory_order_seq_cst);
}

unsigned playback_tags_read(const struct playback_tags *owner,unsigned index,uint32_t object,
 struct playback_tag_snapshot *out)
{
 if(!owner || !out || index>=PLAYBACK_TAG_SLOT_COUNT || !object)return 0u;
 const struct playback_tag_record *record=&owner->records[index];
 uint64_t before=atomic_load_explicit(&record->control,memory_order_seq_cst);
 if(!ready_control(before))return 0u;
 struct playback_tag_snapshot candidate;
 candidate.owner=owner;candidate.index=index;
 candidate.object=atomic_load_explicit(&record->object,memory_order_seq_cst);
 for(unsigned i=0u;i<PLAYBACK_TAG_FACT_WORDS;i++)
  set_fact_word(&candidate.facts,i,atomic_load_explicit(&record->facts[i],memory_order_seq_cst));
 candidate.revision=atomic_load_explicit(&record->ready_revision,memory_order_seq_cst);
#ifdef PLAYBACK_TAGS_TEST_HOOK
 playback_tags_test_point((struct playback_tags *)owner,index,3u);
#endif
 atomic_thread_fence(memory_order_seq_cst);
 uint64_t after=atomic_load_explicit(&record->control,memory_order_seq_cst);
 if(after!=before || candidate.revision!=revision(before) || candidate.object!=object)return 0u;
 /* Caller output is first written ONLY after the complete read is admitted. */
 *out=candidate;return 1u;
}

unsigned playback_tags_same(const struct playback_tags *owner,const struct playback_tag_snapshot *snapshot)
{
 if(!owner || !snapshot || snapshot->owner!=owner || !snapshot->revision)return 0u;
 struct playback_tag_snapshot canonical;
 if(!playback_tags_read(owner,snapshot->index,snapshot->object,&canonical) ||
  canonical.revision!=snapshot->revision)return 0u;
 for(unsigned i=0u;i<PLAYBACK_TAG_FACT_WORDS;i++)
  if(fact_word(&canonical.facts,i)!=fact_word(&snapshot->facts,i))return 0u;
 return 1u;
}

void playback_tags_invalidate(struct playback_tags *owner,unsigned index)
{
 struct playback_tag_lease lease=playback_tags_begin(owner,index);
 playback_tags_end(&lease,0u,0,0u);
}
