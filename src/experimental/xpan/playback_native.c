#include "playback_native.h"
#include "frame_memory.h"
#include "ifd_memory.h"
#include <stddef.h>

const struct playback_native_chain playback_native_chains[]={
 {1u,3u,{{0x42cu,1u,{0xe03362f3u}},{0x564u,2u,{0xe0333911u,0xe0333a55u}},{0x654u,1u,{0xe033519du}}}},
 {1u,3u,{{0x42cu,1u,{0xe03170b1u}},{0x57cu,2u,{0xe0332d9fu,0xe03334b7u}},{0x774u,1u,{0xe0335233u}}}},
 {1u,4u,{{0x42cu,1u,{0xe03170b1u}},{0x57cu,2u,{0xe0332d9fu,0xe03334b7u}},{0x774u,1u,{0xe0333ca3u}},{0x864u,1u,{0xe033519du}}}},
 {1u,3u,{{0x42cu,1u,{0xe02e7ce9u}},{0x574u,1u,{0xe0332f09u}},{0x76cu,1u,{0xe0335233u}}}},
 {1u,4u,{{0x42cu,1u,{0xe02e7ce9u}},{0x574u,1u,{0xe0332f09u}},{0x76cu,1u,{0xe0333ca3u}},{0x85cu,1u,{0xe033519du}}}},
 {1u,4u,{{0x42cu,1u,{0xe02e7ce9u}},{0x574u,1u,{0xe03323fbu}},{0x5ecu,3u,{0xe0332d83u,0xe0332e5bu,0xe0332eddu}},{0x7e4u,1u,{0xe0335233u}}}},
 {1u,5u,{{0x42cu,1u,{0xe02e7ce9u}},{0x574u,1u,{0xe03323fbu}},{0x5ecu,3u,{0xe0332d83u,0xe0332e5bu,0xe0332eddu}},{0x7e4u,1u,{0xe0333ca3u}},{0x8d4u,1u,{0xe033519du}}}},
 {1u,3u,{{0x42cu,1u,{0xe0315743u}},{0x56cu,1u,{0xe033293fu}},{0x70cu,1u,{0xe0335283u}}}},
 {1u,4u,{{0x42cu,1u,{0xe032196fu}},{0x574u,1u,{0xe03230a7u}},{0x594u,2u,{0xe0332e7fu,0xe0333547u}},{0x78cu,1u,{0xe0335233u}}}},
 {1u,5u,{{0x42cu,1u,{0xe032196fu}},{0x574u,1u,{0xe03230a7u}},{0x594u,2u,{0xe0332e7fu,0xe0333547u}},{0x78cu,1u,{0xe0333ca3u}},{0x87cu,1u,{0xe033519du}}}},
 {1u,4u,{{0x42cu,1u,{0xe0324949u}},{0x57cu,1u,{0xe0326a53u}},{0x5dcu,1u,{0xe0333517u}},{0x7d4u,1u,{0xe0335233u}}}},
 {1u,5u,{{0x42cu,1u,{0xe0324949u}},{0x57cu,1u,{0xe0326a53u}},{0x5dcu,1u,{0xe0333517u}},{0x7d4u,1u,{0xe0333ca3u}},{0x8c4u,1u,{0xe033519du}}}},
 {1u,5u,{{0x42cu,1u,{0xe0324949u}},{0x57cu,1u,{0xe0326a53u}},{0x5dcu,1u,{0xe03323c7u}},{0x654u,3u,{0xe0332d83u,0xe0332e5bu,0xe0332eddu}},{0x84cu,1u,{0xe0335233u}}}},
 {1u,6u,{{0x42cu,1u,{0xe0324949u}},{0x57cu,1u,{0xe0326a53u}},{0x5dcu,1u,{0xe03323c7u}},{0x654u,3u,{0xe0332d83u,0xe0332e5bu,0xe0332eddu}},{0x84cu,1u,{0xe0333ca3u}},{0x93cu,1u,{0xe033519du}}}},
 {2u,3u,{{0x504u,1u,{0xe03367f3u}},{0x64cu,1u,{0xe0332fdfu}},{0x844u,1u,{0xe0335233u}}}},
 {2u,4u,{{0x504u,1u,{0xe03367f3u}},{0x64cu,1u,{0xe0332fdfu}},{0x844u,1u,{0xe0333ca3u}},{0x934u,1u,{0xe033519du}}}},
 {2u,3u,{{0x504u,1u,{0xe03367f3u}},{0x64cu,1u,{0xe03329b3u}},{0x7ecu,1u,{0xe0335283u}}}},
 {2u,3u,{{0x504u,1u,{0xe02e835fu}},{0x664u,1u,{0xe0333365u}},{0x85cu,1u,{0xe0335233u}}}},
 {2u,4u,{{0x504u,1u,{0xe02e835fu}},{0x664u,1u,{0xe0333365u}},{0x85cu,1u,{0xe0333ca3u}},{0x94cu,1u,{0xe033519du}}}},
};
uint32_t playback_native_chain_count(void){return sizeof(playback_native_chains)/sizeof(playback_native_chains[0]);}
uint32_t playback_native_chain_scope_delta(uint32_t index)
{
 if(index>=playback_native_chain_count())return 0u;
 const struct playback_native_chain *c=&playback_native_chains[index];
 return c->hops[c->hop_count-1u].offset+4u+PN_MAIN_FRAME;
}
const struct playback_native_binding_profile playback_native_profile={
 PLAYBACK_NATIVE_SCHEMA,PN_METADATA_BYTES,PN_METADATA_COUNT,PN_CACHE_SLOT,PN_CACHE_KEY,PN_CACHE_FLAGS,
 PN_INNER_OFFSET,PN_ENUM_OFFSET,PN_WIDTH_OFFSET,PN_HEIGHT_OFFSET,PN_HEADER_OFFSET,PN_IFD_MAGIC,PN_IFD_BYTES,
 PN_PRIMARY_TUPLE,PN_PRIMARY_HANDLE,PN_PRIMARY_REQUESTED,PN_ALTERNATE_TUPLE,PN_ALTERNATE_HANDLE,PN_ALTERNATE_REQUESTED,
 PN_MAIN_FRAME,PN_MAIN_SCOPE_BYTES,sizeof(playback_native_chains)/sizeof(playback_native_chains[0]),
 {PN_OBJECT0,PN_OBJECT1,PN_OBJECT2,PN_OBJECT3}
};
uint32_t playback_native_object_index(uint32_t object)
{for(uint32_t i=0;i<PN_METADATA_COUNT;i++)if(playback_native_profile.objects[i]==object)return i;return UINT32_MAX;}
static uint32_t read_frame(const struct playback_native_read_facts *f)
{return f&&f->entry_lr==PN_MAIN_CALLER&&frame_memory_data_span(f->context,PN_CLASS_OFFSET+4u)&&
 frame_memory_data_span(f->request,32u)&&f->context==f->r4&&f->selector==f->r5&&f->request==f->r6&&f->r7==f->context+PN_CLASS_OFFSET;}
uint32_t playback_native_read_admit(const struct playback_native_read_facts *f)
{return read_frame(f)&&playback_native_object_index(f->object)<PN_METADATA_COUNT;}
uint32_t playback_native_untracked_admit(uint32_t object)
{
 if(!frame_memory_data_span(object,PN_METADATA_BYTES))return 0u;
 uint32_t end=object+PN_METADATA_BYTES;
 for(uint32_t i=0;i<PN_METADATA_COUNT;i++){
  uint32_t start=playback_native_profile.objects[i];
  if(object<start+PN_METADATA_BYTES&&start<end)return 0u;
 }
 /* Conservative page envelope of the canonical cache/flags objects, not an
  * assertion that their allocated struct has this exact size. */
 return !(object<PN_CACHE_GUARD_END&&PN_CACHE_GUARD_BEGIN<end);
}
uint32_t playback_native_tag_admit(const struct playback_native_tag_facts *f)
{return f&&(f->site==PN_TAG_PRIMARY||f->site==PN_TAG_ALTERNATE)&&f->status==0u&&
 f->inner>=PN_INNER_OFFSET&&playback_native_object_index(f->inner-PN_INNER_OFFSET)<PN_METADATA_COUNT&&
 f->requested==20u&&ifd_memory_span(f->handle,PN_IFD_BYTES)&&f->magic==PN_IFD_MAGIC&&f->bytes==PN_IFD_BYTES&&
 f->tag==0x9au&&f->type==4u&&f->count==5u&&f->scope_known==1u&&f->scope_object==f->inner-PN_INNER_OFFSET;}
uint32_t playback_native_copy_admit(uint32_t role,uint32_t d,uint32_t s,uint32_t bytes)
{
 if(bytes!=PN_METADATA_BYTES)return 0u;
 return (role==PN_BACKUP1&&d==PN_OBJECT1&&s==PN_OBJECT0)||(role==PN_BACKUP2&&d==PN_OBJECT2&&s==PN_OBJECT0)||
  (role==PN_RESTORE1&&d==PN_OBJECT0&&s==PN_OBJECT1)||(role==PN_RESTORE2&&d==PN_OBJECT0&&s==PN_OBJECT2);
}
uint32_t playback_native_result_admit(uint32_t result);
uint32_t playback_native_result_admit(uint32_t result)
{
 /* 0xE0334E4C returns 0, a 4B-aligned non-sentinel CBR handle on synchronous completion, or an odd/0x80000000 error code. */
 return result==0u||((result&3u)==0u&&result!=0x80000000u);
}
uint32_t playback_native_fields_match(const struct playback_box_facts *f,uint32_t aspect,uint32_t header,uint32_t w,uint32_t h)
{
 if(!f||!w||!h||w>0xffffu||h>0xffffu||w!=f->current_width||h!=f->current_height)return 0u;
 uint32_t expected=(f->words[0]>>8)|((f->words[0]&0x80000000u)?0xff000000u:0u);
 return header==expected&&(aspect==(f->words[0]&15u)||(f->words[0]==0x7fffu&&aspect==0xffu));
}
static uint32_t hop_match(const struct playback_native_hop *hop,uint32_t value)
{for(uint32_t i=0;i<hop->count;i++)if(hop->returns[i]==value)return 1u;return 0u;}
uint32_t playback_native_chain_match(uint32_t index,const uint32_t values[PN_MAX_HOPS])
{
 if(index>=playback_native_chain_count()||!values)return 0u;
 const struct playback_native_chain *c=&playback_native_chains[index];
 for(uint32_t i=0;i<c->hop_count;i++)if(!hop_match(&c->hops[i],values[i]))return 0u;
 return 1u;
}
#ifndef PLAYBACK_NATIVE_POLICY_ONLY
#include "playback_reject.h"
#include "playback_first_view.h"
#include "root_native.h"
#define PN_SCOPE_MARKER 0x50424e31u
struct playback_tags playback_native_tags;
_Static_assert(ATOMIC_INT_LOCK_FREE==2,"native counters require lock-free u32");
enum native_count {C_MAIN_ENTERED,C_MAIN_ADMITTED,C_TAG_ENTERED,C_TAG_CAPTURED,C_COPY_ENTERED,C_COPY_PUBLISHED,C_CACHE_ENTERED,C_CACHE_PUBLISHED};
static _Atomic uint32_t observations[8];
/* An unmatched full-reader entry still owns a native write interval, but its
 * destination cannot safely be followed. Suppress knowledge through that
 * interval, then invalidate private records before retiring it. */
static _Atomic uint32_t unresolved_reads;
static uint32_t resolved_reads(void)
{return atomic_load_explicit(&unresolved_reads,memory_order_acquire)==0u;}
static void count(uint32_t index){atomic_fetch_add_explicit(&observations[index],1u,memory_order_relaxed);}
void playback_native_observe(struct playback_native_observation *out)
{
 if(!out)return;
 out->main_entered=atomic_load_explicit(&observations[C_MAIN_ENTERED],memory_order_relaxed);
 out->main_admitted=atomic_load_explicit(&observations[C_MAIN_ADMITTED],memory_order_relaxed);
 out->tag_entered=atomic_load_explicit(&observations[C_TAG_ENTERED],memory_order_relaxed);
 out->tag_captured=atomic_load_explicit(&observations[C_TAG_CAPTURED],memory_order_relaxed);
 out->copy_entered=atomic_load_explicit(&observations[C_COPY_ENTERED],memory_order_relaxed);
 out->copy_published=atomic_load_explicit(&observations[C_COPY_PUBLISHED],memory_order_relaxed);
 out->cache_entered=atomic_load_explicit(&observations[C_CACHE_ENTERED],memory_order_relaxed);
 out->cache_published=atomic_load_explicit(&observations[C_CACHE_PUBLISHED],memory_order_relaxed);
}
static void count_publication(uint32_t index,uint32_t object,uint32_t revision,uint32_t counter)
{struct playback_tag_snapshot s;if(playback_tags_read(&playback_native_tags,index,object,&s)&&s.revision==revision)count(counter);}
extern void playback_native_main_continue(void);
extern uint32_t playback_native_original_cache(uint32_t);
#ifdef PLAYBACK_NATIVE_HOST
extern uint32_t playback_native_host_read(uint32_t,uint32_t *),playback_native_host_address(const void *);
extern struct playback_native_main_scope *playback_native_host_scope(uint32_t);
extern uint32_t playback_native_host_memcpy(uint32_t,uint32_t,uint32_t),playback_native_host_cache(uint32_t);
#endif
static __attribute__((noinline)) void zero_bytes(void *object,uint32_t bytes)
{volatile unsigned char *p=(volatile unsigned char *)object;for(uint32_t i=0;i<bytes;i++)p[i]=0u;}
static uint32_t address_of(const void *p)
{
#ifdef PLAYBACK_NATIVE_HOST
 return playback_native_host_address(p);
#else
 return (uint32_t)(uintptr_t)p;
#endif
}
static uint32_t data_word(uint32_t a,uint32_t *out)
{
 if(!out||!frame_memory_data_span(a,4u))return 0u;
#ifdef PLAYBACK_NATIVE_HOST
 return playback_native_host_read(a,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)a;return 1u;
#endif
}
/* This relative44B reader is used only after the scoped typed borrow and its
 * once-per-borrow high mapping proof. Generic DATA authority is unchanged. */
static uint32_t ifd_word(uint32_t handle,uint32_t offset,uint32_t *out)
{
 if(!out||!ifd_memory_word_span(handle,offset))return 0u;
#ifdef PLAYBACK_NATIVE_HOST
 return playback_native_host_read(handle+offset,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)(handle+offset);return 1u;
#endif
}
static uint32_t half_word(uint32_t a,uint32_t *out)
{uint32_t w;if((a&1u)||!data_word(a&~3u,&w))return 0u;*out=(w>>((a&3u)*8u))&0xffffu;return 1u;}
static uint32_t object_fields(uint32_t object,uint32_t *aspect,uint32_t *header,uint32_t *w,uint32_t *h)
{return playback_native_object_index(object)<PN_METADATA_COUNT&&half_word(object+PN_ENUM_OFFSET,aspect)&&
 data_word(object+PN_HEADER_OFFSET,header)&&half_word(object+PN_WIDTH_OFFSET,w)&&half_word(object+PN_HEIGHT_OFFSET,h);}
static uint32_t object_image_status(uint32_t object,uint32_t *status_out)
{
 uint32_t w;
 if(!status_out||playback_native_object_index(object)>=PN_METADATA_COUNT||!frame_memory_data_span(object,PN_METADATA_BYTES)||
  !data_word((object+PN_INNER_OFFSET)&~3u,&w))return 0u;
 *status_out=(w>>((PN_INNER_OFFSET&3u)*8u))&0xffu;return 1u;
}
static uint32_t cache_fields(uint32_t *aspect,uint32_t *header,uint32_t *w,uint32_t *h)
{return half_word(PN_CACHE_KEY+0x64u,aspect)&&data_word(PN_CACHE_FLAGS+0x18u,header)&&
  data_word(PN_CACHE_KEY+PN_CACHE_WIDTH_OFFSET,w)&&data_word(PN_CACHE_KEY+PN_CACHE_HEIGHT_OFFSET,h);}
static uint32_t native_fields(uint32_t object,const struct playback_box_facts *facts,uint32_t cache)
{
 uint32_t aspect,header,w,h;
 if(cache)return cache_fields(&aspect,&header,&w,&h)&&playback_native_fields_match(facts,aspect,header,w,h);
 return object_fields(object,&aspect,&header,&w,&h)&&
  playback_native_fields_match(facts,aspect,header,w,h);
}
static uint32_t pack_dims(uint32_t w,uint32_t h,uint32_t *out)
{if(w>0xffffu||h>0xffffu)return 0u;*out=(w<<16)|h;return 1u;}
static __attribute__((noinline)) void record_producer_decline(uint32_t reason,uint32_t site,uint32_t caller,
 uint32_t object,uint32_t selector,uint32_t actual,uint32_t expected,
 uint32_t obj_w,uint32_t obj_h,uint32_t cache_w,uint32_t cache_h,
 uint32_t have_obj_dims,uint32_t have_cache_dims,uint32_t aux_a,uint32_t aux_b,uint32_t aux_c)
{
 struct playback_first_view_facts pf;zero_bytes(&pf,sizeof(pf));
 pf.reason=reason;pf.site=site;pf.caller=caller;pf.object=object;pf.selector=selector;
 pf.actual=actual;pf.expected=expected;pf.aux_a=aux_a;pf.aux_b=aux_b;pf.aux_c=aux_c;
 pf.known_mask=PFV_K(reason)|PFV_K(site)|PFV_K(actual)|PFV_K(expected)|PFV_K(aux_a)|PFV_K(aux_b)|PFV_K(aux_c);
 if(caller)pf.known_mask|=PFV_K(caller);
 if(object)pf.known_mask|=PFV_K(object);
 if(site!=PFV_SITE_CACHE)pf.known_mask|=PFV_K(selector);
 if(have_obj_dims&&pack_dims(obj_w,obj_h,&pf.obj_dims))pf.known_mask|=PFV_K(obj_dims);
 if(have_cache_dims&&pack_dims(cache_w,cache_h,&pf.cache_dims))pf.known_mask|=PFV_K(cache_dims);
 playback_first_view_claim(PFV_PRODUCER_DECLINE,&pf);
}
void playback_native_slot_diag(uint32_t index,uint32_t object,struct playback_slot_diag *out)
{
 if(!out)return;
 zero_bytes(out,sizeof(*out));
 out->resolved=resolved_reads();
 struct playback_tag_snapshot s;
 if(out->resolved&&playback_tags_read(&playback_native_tags,index,object,&s)){
  out->slot_state=1u;out->slot_object=s.object;out->slot_revision=s.revision;
  out->slot_kind=playback_box_kind(&s.facts);
  out->field_ok=1u;
  out->aspect=s.facts.words[0]&0xfu;
  out->header=s.facts.words[0]>>8;
  out->width=s.facts.current_width;
  out->height=s.facts.current_height;
 }
}
uint32_t playback_native_object_snapshot(uint32_t object,struct playback_tag_snapshot *out)
{
 uint32_t index=playback_native_object_index(object);struct playback_tag_snapshot s;
 if(index>=PN_METADATA_COUNT||!out||!resolved_reads()||!playback_tags_read(&playback_native_tags,index,object,&s)||!native_fields(object,&s.facts,0u)||!playback_tags_same(&playback_native_tags,&s)||!resolved_reads())return 0u;
 *out=s;return 1u;
}
uint32_t playback_native_cache_snapshot(struct playback_tag_snapshot *out)
{
 struct playback_tag_snapshot s;if(!out||!resolved_reads()||!playback_tags_read(&playback_native_tags,PN_CACHE_SLOT,PN_CACHE_KEY,&s)||!native_fields(PN_CACHE_KEY,&s.facts,1u)||!playback_tags_same(&playback_native_tags,&s)||!resolved_reads())return 0u;
 *out=s;return 1u;
}
uint32_t playback_native_snapshot_same(const struct playback_tag_snapshot *s)
{return s&&resolved_reads()&&s->owner==&playback_native_tags&&((s->index<PN_METADATA_COUNT&&playback_native_object_index(s->object)==s->index)||
 (s->index==PN_CACHE_SLOT&&s->object==PN_CACHE_KEY))&&playback_tags_same(&playback_native_tags,s)&&native_fields(s->object,&s->facts,s->index==PN_CACHE_SLOT)&&playback_tags_same(&playback_native_tags,s)&&resolved_reads();}
void playback_native_main_scope_init(struct playback_native_main_scope *s,const struct playback_native_cpu *cpu,uint32_t sp,uint32_t continuation)
{
 count(C_MAIN_ENTERED);
 zero_bytes(s,sizeof(*s));s->marker=PN_SCOPE_MARKER;s->self=address_of(s);s->native_sp=sp;s->continuation=continuation;
 uint32_t why_step=1u;
 struct playback_native_read_facts f;zero_bytes(&f,sizeof(f));
 if(!cpu)goto unresolved;
 f=(struct playback_native_read_facts){cpu->lr,cpu->r[0],cpu->r[1],cpu->r[2],0u,cpu->r[4],cpu->r[5],cpu->r[6],cpu->r[7]};
 why_step=2u;if(!read_frame(&f))goto unresolved;
 why_step=3u;if(!data_word(f.request+0x14u,&f.object))goto unresolved;
 if(!playback_native_read_admit(&f)){
  if(playback_native_untracked_admit(f.object))return;
  why_step=4u;goto unresolved;
 }
 s->object=f.object;s->selector=f.selector;s->request=f.request;s->admitted=1u;
 count(C_MAIN_ADMITTED);
 s->lease=playback_tags_begin(&playback_native_tags,playback_native_object_index(s->object));
 return;
unresolved:
 s->admitted=2u;atomic_fetch_add_explicit(&unresolved_reads,1u,memory_order_acq_rel);
 if(cpu&&f.entry_lr==PN_MAIN_CALLER)
  record_producer_decline(PFV_R_MAIN_UNRESOLVED,PFV_SITE_MAIN_INIT,f.entry_lr,f.object,f.selector,why_step,0u,0u,0u,0u,0u,0u,0u,sp,f.request,f.context);
}
void playback_native_main_scope_end(struct playback_native_main_scope *s,uint32_t result)
{
 if(!s||s->marker!=PN_SCOPE_MARKER||s->self!=address_of(s))return;
 if(s->admitted==2u){
  for(uint32_t i=0;i<PLAYBACK_TAG_SLOT_COUNT;i++)playback_tags_invalidate(&playback_native_tags,i);
  s->admitted=0u;s->marker=0u;atomic_fetch_sub_explicit(&unresolved_reads,1u,memory_order_release);return;
 }
 uint32_t index=playback_native_object_index(s->object);
 if(s->lease.owner!=&playback_native_tags||s->lease.index!=index){
  if(index<PN_METADATA_COUNT)playback_tags_invalidate(&playback_native_tags,index);
  s->marker=0u;return;
 }
 uint32_t aspect=0u,header=0u,w=0u,h=0u,known=0u,res_ok=resolved_reads(),result_ok=playback_native_result_admit(result),fields_ok=0u,fields_match=0u,status_ok=0u,image_status=0u,post_ok=0u;
 if(s->admitted==1u&&res_ok&&s->tag_known&&result_ok&&s->native_sp==address_of(s)-PN_MAIN_FRAME&&
  s->continuation==(uint32_t)(uintptr_t)playback_native_main_continue&&s->lease.active){
  fields_ok=object_fields(s->object,&aspect,&header,&w,&h);
  if(fields_ok){
   s->facts.current_width=w;s->facts.current_height=h;
   fields_match=playback_native_fields_match(&s->facts,aspect,header,w,h);
   if(fields_match){
    status_ok=object_image_status(s->object,&image_status);
    post_ok=resolved_reads()&&s->lease.active;
    known=status_ok&&image_status==0u&&post_ok;
   }
  }
 }
 if(s->admitted==1u&&!known){
  if(s->tag_known&&playback_box_kind(&s->facts)==PB_BOX_RAW_L){
   uint32_t why=PFV_R_MAIN_END_FIELDS_MISMATCH,actual=aspect,expected=s->facts.words[0];
   if(!result_ok){why=PFV_R_MAIN_END_RESULT;actual=result;}
   else if(!res_ok||(fields_match&&status_ok&&image_status==0u&&!post_ok)){why=PFV_R_MAIN_UNRESOLVED;actual=0u;}
   else if(!fields_ok||(fields_match&&!status_ok)){why=PFV_R_MAIN_END_OBJECT_FIELDS;}
   else if(fields_match&&image_status!=0u){why=PFV_R_MAIN_END_IMAGE_STATUS;actual=image_status;expected=0u;}
   record_producer_decline(why,PFV_SITE_MAIN_END,0u,s->object,s->selector,
    actual,expected,w,h,0u,0u,fields_ok,0u,header,s->facts.words[1],s->facts.words[2]);
  }
 }
 playback_tags_end(&s->lease,s->object,&s->facts,known);s->marker=0u;
}
static uint32_t stack_word(uint32_t sp,uint32_t extent,uint32_t offset,uint32_t *out)
{
 if(!out||!frame_memory_stack_offset_span(sp,extent,offset,4u))return 0u;
#ifdef PLAYBACK_NATIVE_HOST
 return playback_native_host_read(sp+offset,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)(sp+offset);return 1u;
#endif
}
/* Diagnostics retain only observed arguments and the final best candidate.
 * They never choose admission or add a Canon read. */
static void reject_detail(struct playback_reject_facts *f,uint32_t reason,uint32_t offset,uint32_t have_actual,
 uint32_t actual,uint32_t expected,uint32_t have_aux,uint32_t aux_a,uint32_t aux_b)
{
 uint32_t keep=PNR_K(site)|PNR_K(native_sp)|PNR_K(inner)|PNR_K(object)|PNR_K(status)|PNR_K(chain)|PNR_K(prefix);
 f->known_mask&=keep;f->reason=reason;f->offset=offset;f->actual=have_actual?actual:0u;
 f->expected0=expected;f->expected1=0u;f->expected2=0u;f->aux_a=have_aux?aux_a:0u;f->aux_b=have_aux?aux_b:0u;
 f->known_mask|=PNR_K(reason)|PNR_K(offset)|PNR_K(expected0);
 if(have_actual)f->known_mask|=PNR_K(actual);
 if(have_aux)f->known_mask|=PNR_K(aux_a)|PNR_K(aux_b);
}
static void reject_callback(struct playback_reject_facts *f,uint32_t scoped)
{
 playback_reject_claim(PNR_KNOWN_OBJECT,f);
 if(scoped)playback_reject_claim(PNR_SCOPED,f);
}
static struct playback_native_main_scope *find_scope(uint32_t sp,uint32_t site,uint32_t object,struct playback_reject_facts *f)
{
 uint32_t continuation=(uint32_t)(uintptr_t)playback_native_main_continue;
 uint32_t have_best=0u,best_prefix=0u,best_progress=0u;
 for(uint32_t i=0;i<playback_native_chain_count();i++){
  const struct playback_native_chain *c=&playback_native_chains[i];
  uint32_t delta=playback_native_chain_scope_delta(i),extent=delta+PN_MAIN_SCOPE_BYTES+64u;
  if(c->site!=site)continue;
  uint32_t why=PNR_STACK,prefix=0u,progress=0u,offset=extent,have_actual=0u,actual=0u;
  uint32_t expected0=extent,expected1=0u,expected2=0u,expected_count=1u,aux_a=delta,aux_b=c->hop_count;
  if(!frame_memory_stack_frame_span(sp,extent))goto candidate;
  for(uint32_t h=0;h<c->hop_count;h++){
   const struct playback_native_hop *hop=&c->hops[h];offset=hop->offset;
   expected_count=hop->count;expected0=hop->returns[0];expected1=hop->count>1u?hop->returns[1]:0u;expected2=hop->count>2u?hop->returns[2]:0u;
   aux_a=hop->count;aux_b=delta;have_actual=0u;
   if(!stack_word(sp,extent,offset,&actual)){why=PNR_HOP_READ;goto candidate;}
   have_actual=1u;if(!hop_match(hop,actual)){why=PNR_HOP_MISMATCH;goto candidate;}
   prefix++;
  }
  progress=1u;offset=delta-4u;have_actual=0u;expected_count=1u;expected0=continuation;expected1=expected2=0u;aux_a=sp+delta;aux_b=delta;
  if(!stack_word(sp,extent,offset,&actual)){why=PNR_CONT_READ;goto candidate;}
  have_actual=1u;progress=2u;if(actual!=continuation){why=PNR_CONT_MISMATCH;goto candidate;}
  progress=3u;
  uint32_t address=sp+delta;
#ifdef PLAYBACK_NATIVE_HOST
  struct playback_native_main_scope *scope=playback_native_host_scope(address);
#else
  struct playback_native_main_scope *scope=(struct playback_native_main_scope *)(uintptr_t)address;
#endif
  offset=0u;have_actual=0u;expected0=PN_SCOPE_MARKER;aux_a=address;aux_b=0u;
  if(!scope){why=PNR_SCOPE_MARKER;goto candidate;}
#define CHECK_SCOPE(member,reason,want,stage) do { \
 offset=offsetof(struct playback_native_main_scope,member);actual=scope->member;have_actual=1u;expected0=(want); \
 if(actual!=expected0){why=(reason);goto candidate;}progress=(stage); \
} while(0)
  CHECK_SCOPE(marker,PNR_SCOPE_MARKER,PN_SCOPE_MARKER,4u);
  CHECK_SCOPE(self,PNR_SCOPE_SELF,address,5u);
  CHECK_SCOPE(native_sp,PNR_SCOPE_SP,address-PN_MAIN_FRAME,6u);
  CHECK_SCOPE(continuation,PNR_SCOPE_CONT,continuation,7u);
  CHECK_SCOPE(admitted,PNR_SCOPE_ADMITTED,1u,8u);
  CHECK_SCOPE(object,PNR_SCOPE_OBJECT,object,9u);
  offset=offsetof(struct playback_native_main_scope,lease.owner);
  const struct playback_tags *owner=scope->lease.owner;actual=(uint32_t)(uintptr_t)owner;expected0=(uint32_t)(uintptr_t)&playback_native_tags;have_actual=1u;
  if(owner!=&playback_native_tags){why=PNR_SCOPE_OWNER;goto candidate;}progress=10u;
  CHECK_SCOPE(lease.index,PNR_SCOPE_INDEX,playback_native_object_index(object),11u);
  offset=offsetof(struct playback_native_main_scope,lease.active);actual=scope->lease.active;expected0=1u;have_actual=1u;
  if(!actual){why=PNR_SCOPE_ACTIVE;goto candidate;}
#undef CHECK_SCOPE
  /* A successful later alternative cancels every earlier candidate miss. */
  f->chain=i;f->prefix=c->hop_count;f->known_mask&=PNR_K(site)|PNR_K(native_sp)|PNR_K(inner)|PNR_K(object)|PNR_K(status);
  f->known_mask|=PNR_K(chain)|PNR_K(prefix);return scope;
candidate:
  if(!have_best||prefix>best_prefix||(prefix==best_prefix&&progress>best_progress)){
   have_best=1u;best_prefix=prefix;best_progress=progress;
   f->chain=i;f->prefix=prefix;f->known_mask|=PNR_K(chain)|PNR_K(prefix);
   if(why>=PNR_SCOPE_MARKER&&why<=PNR_SCOPE_ACTIVE)aux_b=progress;
   reject_detail(f,why,offset,have_actual,actual,expected0,1u,aux_a,aux_b);
   f->expected1=expected1;f->expected2=expected2;
   if(expected_count>1u)f->known_mask|=PNR_K(expected1);
   if(expected_count>2u)f->known_mask|=PNR_K(expected2);
  }
 }
 return NULL;
}
void playback_native_tag_observe(const struct playback_native_cpu *cpu,uint32_t sp,uint32_t site)
{
 count(C_TAG_ENTERED);
 struct playback_reject_facts rejection;zero_bytes(&rejection,sizeof(rejection));rejection.site=site;rejection.native_sp=sp;rejection.known_mask=PNR_K(site)|PNR_K(native_sp);
 if(!cpu){reject_detail(&rejection,PNR_INPUT,0u,1u,0u,1u,0u,0u,0u);playback_reject_claim(PNR_EARLY,&rejection);return;}
 if(site!=PN_TAG_PRIMARY&&site!=PN_TAG_ALTERNATE){
  reject_detail(&rejection,PNR_INPUT,1u,1u,site,PN_TAG_PRIMARY,0u,0u,0u);rejection.expected1=PN_TAG_ALTERNATE;rejection.known_mask|=PNR_K(expected1);playback_reject_claim(PNR_EARLY,&rejection);return;
 }
 rejection.inner=cpu->r[4];rejection.known_mask|=PNR_K(inner);
 if(rejection.inner<PN_INNER_OFFSET){reject_detail(&rejection,PNR_INPUT,2u,1u,rejection.inner,PN_INNER_OFFSET,0u,0u,0u);playback_reject_claim(PNR_EARLY,&rejection);return;}
 uint32_t object=rejection.inner-PN_INNER_OFFSET,index=playback_native_object_index(object);
 rejection.object=object;rejection.status=cpu->r[0];rejection.known_mask|=PNR_K(object)|PNR_K(status);
 if(index>=PN_METADATA_COUNT){reject_detail(&rejection,PNR_OBJECT,0u,1u,object,PN_OBJECT0,0u,0u,0u);rejection.aux_a=PN_OBJECT3;rejection.known_mask|=PNR_K(aux_a);rejection.expected1=PN_OBJECT1;rejection.expected2=PN_OBJECT2;rejection.known_mask|=PNR_K(expected1)|PNR_K(expected2);playback_reject_claim(PNR_EARLY,&rejection);return;}
 struct playback_native_main_scope *scope=find_scope(sp,site,object,&rejection);
 if(!scope){reject_callback(&rejection,0u);playback_tags_invalidate(&playback_native_tags,index);return;}
 scope->tag_known=0u;
 if(rejection.status!=0u){reject_detail(&rejection,PNR_STATUS,0u,1u,rejection.status,0u,0u,0u,0u);rejection.aux_a=address_of(scope);rejection.known_mask|=PNR_K(aux_a);reject_callback(&rejection,1u);return;}
 uint32_t tuple=site==PN_TAG_PRIMARY?PN_PRIMARY_TUPLE:PN_ALTERNATE_TUPLE;
 uint32_t handle=site==PN_TAG_PRIMARY?PN_PRIMARY_HANDLE:PN_ALTERNATE_HANDLE;
 uint32_t requested=site==PN_TAG_PRIMARY?PN_PRIMARY_REQUESTED:PN_ALTERNATE_REQUESTED;
 struct playback_native_tag_facts f;zero_bytes(&f,sizeof(f));f.site=site;f.status=rejection.status;f.inner=rejection.inner;f.scope_object=scope->object;f.scope_known=1u;
 uint32_t tagtype,why,offset,actual=0u,expected=0u,have_actual=0u,handle_seen=0u,request_seen=0u;
 if(!stack_word(sp,handle+4u,handle,&f.handle)){why=PNR_HANDLE_READ;offset=handle;expected=4u;goto typed_failure;}
 handle_seen=1u;
 if(!stack_word(sp,handle+4u,requested,&f.requested)){why=PNR_REQUEST_READ;offset=requested;expected=4u;goto typed_failure;}
 request_seen=1u;
 if(!ifd_memory_span(f.handle,PN_IFD_BYTES)){why=PNR_HANDLE_RANGE;offset=handle;expected=PN_IFD_BYTES;actual=f.handle;have_actual=1u;goto typed_failure;}
 if(ifd_memory_high_span(f.handle,PN_IFD_BYTES)&&!root_ifd_memory_admitted(f.handle,PN_IFD_BYTES)){
  why=PNR_HANDLE_MAPPING;offset=handle;expected=1u;actual=0u;have_actual=1u;goto typed_failure;
 }
 if(!ifd_word(f.handle,0u,&f.magic)){why=PNR_MAGIC_READ;offset=0u;expected=PN_IFD_MAGIC;goto typed_failure;}
 if(!ifd_word(f.handle,4u,&f.bytes)){why=PNR_SIZE_READ;offset=4u;expected=PN_IFD_BYTES;goto typed_failure;}
 if(!ifd_word(f.handle,12u,&tagtype)){why=PNR_TAGTYPE_READ;offset=12u;expected=0x0004009au;goto typed_failure;}
 if(!ifd_word(f.handle,16u,&f.count)){why=PNR_COUNT_READ;offset=16u;expected=5u;goto typed_failure;}
 f.tag=tagtype&0xffffu;f.type=tagtype>>16;
 if(!playback_native_tag_admit(&f)){
  have_actual=1u;
  if(f.requested!=20u){why=PNR_REQUEST;offset=requested;actual=f.requested;expected=20u;}
  else if(f.magic!=PN_IFD_MAGIC){why=PNR_MAGIC;offset=0u;actual=f.magic;expected=PN_IFD_MAGIC;}
  else if(f.bytes!=PN_IFD_BYTES){why=PNR_SIZE;offset=4u;actual=f.bytes;expected=PN_IFD_BYTES;}
  else if(f.tag!=0x9au){why=PNR_TAG;offset=12u;actual=f.tag;expected=0x9au;}
  else if(f.type!=4u){why=PNR_TYPE;offset=14u;actual=f.type;expected=4u;}
  else if(f.count!=5u){why=PNR_COUNT;offset=16u;actual=f.count;expected=5u;}
  else{why=PNR_INPUT;offset=0u;actual=0u;expected=1u;}
  goto typed_failure;
 }
 for(uint32_t i=0;i<5u;i++)if(!stack_word(sp,handle+4u,tuple+i*4u,&scope->facts.words[i])){
  why=PNR_TUPLE_READ;offset=tuple+i*4u;expected=4u;goto typed_failure;
 }
 scope->tag_known=1u;count(C_TAG_CAPTURED);return;
typed_failure:
 reject_detail(&rejection,why,offset,have_actual,actual,expected,0u,0u,0u);
 if(handle_seen){rejection.aux_a=f.handle;rejection.known_mask|=PNR_K(aux_a);}
 if(request_seen){rejection.aux_b=why==PNR_HANDLE_MAPPING?PN_IFD_BYTES:f.requested;rejection.known_mask|=PNR_K(aux_b);}
 reject_callback(&rejection,1u);
}
static uint32_t original_copy(uint32_t d,uint32_t s,uint32_t n)
{
#ifdef PLAYBACK_NATIVE_HOST
 return playback_native_host_memcpy(d,s,n);
#else
 return ((uint32_t (*)(uint32_t,uint32_t,uint32_t))(uintptr_t)PN_NATIVE_MEMCPY)(d,s,n);
#endif
}
uint32_t playback_native_copy_adapter(uint32_t d,uint32_t s,uint32_t bytes,uint32_t role)
{
 count(C_COPY_ENTERED);
 uint32_t index=playback_native_object_index(d),admitted=playback_native_copy_admit(role,d,s,bytes);
 struct playback_tag_snapshot source;uint32_t known=admitted&&playback_native_object_snapshot(s,&source);
 struct playback_tag_lease lease=playback_tags_begin(&playback_native_tags,index);
 uint32_t result=original_copy(d,s,bytes);
 known=known&&playback_native_snapshot_same(&source);
 playback_tags_end(&lease,d,known?&source.facts:NULL,known);
 if(known)count_publication(index,d,lease.revision,C_COPY_PUBLISHED);
 return result;
}
uint32_t playback_native_cache_adapter(uint32_t object)
{
 count(C_CACHE_ENTERED);
 struct playback_tag_lease lease=playback_tags_begin(&playback_native_tags,PN_CACHE_SLOT);
 struct playback_tag_snapshot source;uint32_t source_ok=playback_native_object_snapshot(object,&source);
#ifdef PLAYBACK_NATIVE_HOST
 uint32_t result=playback_native_host_cache(object);
#else
 uint32_t result=playback_native_original_cache(object);
#endif
 uint32_t same_ok=source_ok&&playback_native_snapshot_same(&source);
 uint32_t aspect=0u,header=0u,w=0u,h=0u;
 uint32_t c_ok=same_ok?cache_fields(&aspect,&header,&w,&h):0u;
 uint32_t cache_ok=c_ok&&playback_native_fields_match(&source.facts,aspect,header,w,h);
 uint32_t status_ok=0u,image_status=0u,post_ok=0u;
 if(cache_ok){
  status_ok=object_image_status(object,&image_status);
  post_ok=resolved_reads()&&playback_tags_same(&playback_native_tags,&source);
 }
 uint32_t known=cache_ok&&status_ok&&image_status==0u&&post_ok;
 if(!known&&source_ok&&playback_box_kind(&source.facts)==PB_BOX_RAW_L){
  uint32_t why=PFV_R_CACHE_SNAPSHOT_RACED,actual=aspect,expected=source.facts.words[0];
  if(same_ok&&!cache_ok)why=PFV_R_CACHE_FIELDS_MISMATCH;
  else if(cache_ok&&(!status_ok||image_status!=0u)){
   why=PFV_R_CACHE_IMAGE_STATUS;actual=status_ok?image_status:0xffffffffu;expected=0u;
  }
  record_producer_decline(why,PFV_SITE_CACHE,0u,object,0u,actual,expected,
   source.facts.current_width,source.facts.current_height,w,h,1u,c_ok,header,result,lease.revision);
 }
 playback_tags_end(&lease,PN_CACHE_KEY,known?&source.facts:NULL,known);
 if(known)count_publication(PN_CACHE_SLOT,PN_CACHE_KEY,lease.revision,C_CACHE_PUBLISHED);
 return result;
}
#endif
