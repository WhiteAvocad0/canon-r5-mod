#include "playback_mask_native.h"
#include "frame_memory.h"
#include <stddef.h>
#define PM_MARKER 0x504D5332u
#define PM_COMBINED (PM_FIT_FRAME+PM_FIT_SCOPE_BYTES+PM_CPU_IMAGE+PM_CALLER_BYTES)
const struct playback_mask_container_profile playback_mask_containers[3]={
 {0u,0x2cu,0x3eu,0x40u},{1u,0x24u,0x36u,0x38u},{2u,0x28u,0x3au,0x3cu}
};
const struct playback_mask_binding_profile playback_mask_profile={
 PLAYBACK_MASK_SCHEMA,PM_FIT_SCOPE_BYTES,PM_FIT_FRAME,PM_CPU_IMAGE,PM_CALLER_BYTES,PM_COMBINED,
 PM_FIT_CALLER,PM_OUTPUT_OFFSET,PM_GUIDE_CALLER,PM_GUIDE_SPAN,PM_GUIDE_GEOMETRY,PM_GUIDE_RETURN,PM_GUIDE_SAVED_GEOMETRY,PM_GUIDE_HANDLE,PM_GUIDE_RESOURCE,
 PM_DISPLAY_WORD,PM_ORIENTATION_WORD,PM_CATEGORY_WORD,PM_CLEAR_COLOR,
 PM_NATIVE_ASPECT_VALID,PM_NATIVE_HEADER_SET,PM_NATIVE_MODEL_MATCH,PM_THUMB_ROI_OFFSET,
 sizeof(struct playback_mask_fit_facts),sizeof(struct playback_mask_guide_facts),sizeof(struct playback_mask_view_owner),10u,
 sizeof(struct playback_mask_geometry),sizeof(struct playback_mask_projection_facts),3u
};
static uint32_t dimensions(uint32_t w,uint32_t h)
{return w&&h&&w<=0xffffu&&h<=0xffffu;}
static uint32_t facts_same(const struct playback_box_facts *a,const struct playback_box_facts *b)
{
 for(unsigned i=0;i<5u;i++)if(a->words[i]!=b->words[i])return 0u;
 return a->current_width==b->current_width&&a->current_height==b->current_height;
}
static uint32_t view_device_admit(uint32_t device)
{return device<=1u;}
static uint32_t view_orientation_admit(uint32_t orientation)
{return orientation==0u||orientation==2u;}
static uint32_t view_admit(const struct playback_mask_view_facts *v)
{return view_device_admit(v->device)&&view_orientation_admit(v->orientation)&&v->category!=13u&&!(v->color[0]|v->color[1]|v->color[2]);}
uint32_t playback_mask_fit_admit(const struct playback_mask_fit_facts *f)
{
 return f&&f->entry_lr==PM_FIT_CALLER&&!(f->original_sp&7u)&&frame_memory_stack_frame_span(f->original_sp,PM_CALLER_BYTES)&&
  f->output==f->original_sp+PM_OUTPUT_OFFSET&&f->object==PN_OBJECT0&&f->saved_object==f->object&&
  f->selector<3u&&f->saved_selector==f->selector&&f->drawmode==0u&&f->rotation==0u&&f->category==f->view.category&&
  view_admit(&f->view)&&f->object_known==1u&&f->cache_known==1u&&
  dimensions(f->object_facts.current_width,f->object_facts.current_height)&&
  playback_box_kind(&f->object_facts)==PB_BOX_RAW_L&&playback_box_kind(&f->cache_facts)==PB_BOX_RAW_L&&facts_same(&f->object_facts,&f->cache_facts);
}
uint32_t playback_mask_guide_admit(const struct playback_mask_guide_facts *f)
{
 return f&&!(f->native_sp&7u)&&frame_memory_stack_frame_span(f->native_sp,PM_GUIDE_SPAN)&&
  f->saved_lr==PM_GUIDE_CALLER&&f->geometry==f->native_sp+PM_GUIDE_GEOMETRY&&f->saved_geometry==f->geometry&&
  f->resources==PM_GUIDE_RESOURCE&&f->handle!=0u&&view_admit(&f->view);
}
static uint32_t rectangle(const uint32_t r[4],uint32_t w,uint32_t h)
{return r[2]>1u&&r[3]>1u&&r[0]<w&&r[1]<h&&r[2]<=w-r[0]&&r[3]<=h-r[1];}
/* Ratio operands are bounded to u16 below; remainder<<1 cannot overflow.
 * A bounded integer divide avoids adding an ARM ABI/runtime dependency. */
static uint32_t divide(uint32_t n,uint32_t d)
{
 uint32_t q=0u,r=0u;
 for(unsigned bit=32u;bit;){--bit;r=(r<<1)|((n>>bit)&1u);if(r>=d){r-=d;q|=UINT32_C(1)<<bit;}}
 return q;
}
uint32_t playback_mask_project(const struct playback_mask_projection_facts *f,uint32_t output[4])
{
 if(!f||!output||f->selector>2u||!dimensions(f->width,f->height)||
  !dimensions(f->numerator,f->denominator))return 0u;
 uint32_t box[4]={0u,0u,f->width,f->height};
 if(f->selector==1u){
  if(f->stock_result!=1u||!rectangle(f->stock,f->width,f->height)||(f->stock[0]&1u)||(f->stock[2]&1u))return 0u;
  for(unsigned i=0;i<4u;i++)box[i]=f->stock[i];
 }else if(f->stock_result||f->stock[0]||f->stock[1]||f->stock[2]||f->stock[3])return 0u;
 uint32_t row[4],wd=box[2]*f->denominator,hn=box[3]*f->numerator;
 if(wd>hn){row[2]=divide(hn,f->denominator);row[3]=box[3];}
 else{row[2]=box[2];row[3]=divide(wd,f->numerator);}
 if(row[2]>box[2]||row[3]>box[3])return 0u;
 row[0]=box[0]+((box[2]-row[2])>>1);row[1]=box[1]+((box[3]-row[3])>>1);
 if(row[0]&1u){row[0]--;row[2]++;}if(row[2]&1u)row[2]++;
 if(!rectangle(row,f->width,f->height)||row[0]<box[0]||row[1]<box[1]||
  row[2]>box[2]-(row[0]-box[0])||row[3]>box[3]-(row[1]-box[1]))return 0u;
 for(unsigned i=0;i<4u;i++)output[i]=row[i];
 return 1u;
}
#ifndef PLAYBACK_MASK_POLICY_ONLY
#include "playback_first_view.h"
static uint32_t row_same(const uint32_t a[4],const uint32_t b[4])
{for(unsigned i=0;i<4u;i++)if(a[i]!=b[i])return 0u;return 1u;}
struct playback_mask_view_owner playback_mask_view;
struct mask_counts {_Atomic uint32_t entered,admitted,projected,crop_candidate,guide_hidden,rejected;};
static struct mask_counts counts;
extern void playback_mask_fit_continue(void);
#ifdef PLAYBACK_MASK_HOST
extern uint32_t playback_mask_host_address(const void *),playback_mask_host_read(uint32_t,uint32_t *),playback_mask_host_native_get(uint32_t);
extern void playback_mask_host_write(uint32_t,uint32_t);
#endif
static __attribute__((noinline)) void clear_bytes(void *p,uint32_t n)
{volatile unsigned char *b=p;for(uint32_t i=0;i<n;i++)b[i]=0u;}
static uint32_t address_of(const void *p)
{
#ifdef PLAYBACK_MASK_HOST
 return playback_mask_host_address(p);
#else
 return (uint32_t)(uintptr_t)p;
#endif
}
static uint32_t original_sp(const struct playback_mask_cpu *cpu)
{uint32_t p=address_of(cpu);return p>UINT32_MAX-sizeof(*cpu)?0u:p+sizeof(*cpu);}
static uint32_t data_word(uint32_t a,uint32_t *out)
{
 if(!out||!frame_memory_data_span(a,4u))return 0u;
#ifdef PLAYBACK_MASK_HOST
 return playback_mask_host_read(a,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)a;return 1u;
#endif
}
static uint32_t half_word(uint32_t a,uint32_t *out)
{uint32_t value;if((a&1u)||!data_word(a&~3u,&value))return 0u;*out=(value>>((a&3u)*8u))&0xffffu;return 1u;}
static uint32_t stack_word(uint32_t sp,uint32_t extent,uint32_t offset,uint32_t *out)
{
 if(!out||!frame_memory_stack_offset_span(sp,extent,offset,4u))return 0u;
#ifdef PLAYBACK_MASK_HOST
 return playback_mask_host_read(sp+offset,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)(sp+offset);return 1u;
#endif
}
static void output_word(uint32_t address,uint32_t word)
{
#ifdef PLAYBACK_MASK_HOST
 playback_mask_host_write(address,word);
#else
 *(volatile uint32_t *)(uintptr_t)address=word;
#endif
}
static uint32_t read_view(struct playback_mask_view_facts *v)
{
 uint32_t orientation;
 if(!data_word(PM_DISPLAY_WORD,&v->device)||!data_word(PM_ORIENTATION_WORD,&orientation)||!data_word(PM_CATEGORY_WORD,&v->category))return 0u;
 v->orientation=orientation&0xffffu;
 for(unsigned i=0;i<3u;i++)if(!data_word(PM_CLEAR_COLOR+4u*i,&v->color[i]))return 0u;
 return 1u;
}
static uint32_t native_get(uint32_t target)
{
#ifdef PLAYBACK_MASK_HOST
 return playback_mask_host_native_get(target);
#else
 return ((uint32_t (*)(void))(uintptr_t)target)();
#endif
}
static uint32_t read_geometry(uint32_t object,uint32_t selector,struct playback_mask_geometry *g)
{
 if(!g||object!=PN_OBJECT0||selector>=3u||!frame_memory_data_span(object,PN_METADATA_BYTES))return 0u;
 const struct playback_mask_container_profile *p=&playback_mask_containers[selector];
 if(!data_word(object+p->token_offset,&g->pixel_token)||!half_word(object+p->width_offset,&g->width)||
  !half_word(object+p->height_offset,&g->height)||!dimensions(g->width,g->height))return 0u;
 for(unsigned i=0;i<4u;i++)g->stock[i]=0u;
 if(selector==1u){
  uint32_t bounds[4];for(unsigned i=0;i<4u;i++)if(!half_word(object+PM_THUMB_ROI_OFFSET+2u*i,&bounds[i]))return 0u;
  if(bounds[0]>bounds[1]||bounds[2]>bounds[3])return 0u;
  g->stock[0]=bounds[0];g->stock[1]=bounds[2];g->stock[2]=bounds[1]-bounds[0]+1u;g->stock[3]=bounds[3]-bounds[2]+1u;
  if(g->stock[0]&1u){g->stock[0]--;g->stock[2]++;}if(g->stock[2]&1u)g->stock[2]++;
  if(!rectangle(g->stock,g->width,g->height))return 0u;
 }
 return 1u;
}
static uint32_t geometry_same(const struct playback_mask_geometry *a,const struct playback_mask_geometry *b)
{return a->pixel_token==b->pixel_token&&a->width==b->width&&a->height==b->height&&row_same(a->stock,b->stock);}
static void rejected(void){atomic_fetch_add_explicit(&counts.rejected,1u,memory_order_relaxed);}
static uint32_t revision(uint64_t c){return (uint32_t)(c>>PLAYBACK_TAG_REV_SHIFT);}
static uint32_t active(uint64_t c){return (uint32_t)(c&PLAYBACK_TAG_ACTIVE_MASK);}
static uint32_t idle(uint64_t c){return revision(c)&&!active(c)&&!(c&PLAYBACK_TAG_COLLISION);}
static void quarantine(void)
{
 uint64_t old=atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst),next;
 do{next=(UINT64_C(0xffffffff)<<32)|PLAYBACK_TAG_COLLISION|(old&PLAYBACK_TAG_ACTIVE_MASK);}
 while(!atomic_compare_exchange_weak_explicit(&playback_mask_view.control,&old,next,memory_order_seq_cst,memory_order_seq_cst));
}
static void view_begin(struct playback_mask_fit_scope *s)
{
 uint64_t old=atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst);
 for(;;){
  uint32_t rev=revision(old),n=active(old);
  if(rev==UINT32_MAX||n==PLAYBACK_TAG_ACTIVE_MASK){quarantine();return;}
  uint64_t next=((uint64_t)(rev+1u)<<32)|(n+1u)|(n?PLAYBACK_TAG_COLLISION:0u);
  if(atomic_compare_exchange_weak_explicit(&playback_mask_view.control,&old,next,memory_order_seq_cst,memory_order_seq_cst)){
   s->view_revision=rev+1u;s->view_active=1u;return;
  }
 }
}
static uint32_t view_sole(const struct playback_mask_fit_scope *s)
{return s->view_active&&s->view_revision&&atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst)==(((uint64_t)s->view_revision<<32)|1u);}
static uint32_t view_matches(const struct playback_mask_view_facts *v,uint32_t device,uint32_t orientation,uint32_t category)
{return view_admit(v)&&v->device==device&&v->orientation==orientation&&v->category==category;}
static uint32_t view_end(struct playback_mask_fit_scope *s,uint32_t known)
{
 if(!s->view_active)return 0u;
 uint32_t ticket=s->view_revision;s->view_active=0u;
 uint64_t c=atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst);
 if(!ticket||!active(c)){quarantine();return 0u;}
 if(known&&c==(((uint64_t)ticket<<32)|1u)){
#ifdef PLAYBACK_MASK_TEST_AFTER_CANDIDATE_CHECK
  /* Scheduling seam only: production atomic owner operations stay identical. */
  extern void playback_mask_test_after_candidate_check(void);
  playback_mask_test_after_candidate_check();
#endif
  /* A newer Begin can race these atomic payload stores. Its revision/count/
   * collision excludes readers until this invocation's final decrement. */
  atomic_store_explicit(&playback_mask_view.object_revision,s->object_snapshot.revision,memory_order_seq_cst);
  atomic_store_explicit(&playback_mask_view.cache_revision,s->cache_snapshot.revision,memory_order_seq_cst);
  atomic_store_explicit(&playback_mask_view.category,s->original_category,memory_order_seq_cst);
  atomic_store_explicit(&playback_mask_view.device,s->fitted_device,memory_order_seq_cst);
  atomic_store_explicit(&playback_mask_view.orientation,s->fitted_orientation,memory_order_seq_cst);
  atomic_store_explicit(&playback_mask_view.rotation,s->fitted_rotation,memory_order_seq_cst);
  atomic_store_explicit(&playback_mask_view.ready_revision,ticket,memory_order_seq_cst);
 }
 /* Last shared owner write. No payload store is permitted after release. */
 (void)atomic_fetch_sub_explicit(&playback_mask_view.control,UINT64_C(1),memory_order_seq_cst);
 c=atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst);
 return known&&c==((uint64_t)ticket<<32)&&atomic_load_explicit(&playback_mask_view.ready_revision,memory_order_seq_cst)==ticket;
}

static uint32_t snapshots_current(const struct playback_mask_fit_scope *s)
{
 struct playback_mask_view_facts v;
 return view_sole(s)&&playback_native_snapshot_same(&s->object_snapshot)&&playback_native_snapshot_same(&s->cache_snapshot)&&
  read_view(&v)&&view_matches(&v,s->fitted_device,s->fitted_orientation,s->original_category)&&s->fitted_rotation==0u&&view_sole(s);
}
static uint32_t native_eligible_check(uint32_t *flags_out)
{
 uint32_t flags=0u;
 if(native_get(PM_NATIVE_ASPECT_VALID)!=0u){
  flags|=PFV_FLAG_NATIVE_ASPECT;
  if(native_get(PM_NATIVE_HEADER_SET)==0u){
   flags|=PFV_FLAG_NATIVE_HEADER_OK;
   if(native_get(PM_NATIVE_MODEL_MATCH)!=0u){
    flags|=PFV_FLAG_NATIVE_MODEL;
    if(flags_out)*flags_out=flags;
    return 1u;
   }
  }
 }
 if(flags_out)*flags_out=flags;
 return 0u;
}
static uint32_t authority_current_diag(const struct playback_mask_fit_scope *s,uint32_t *why,uint32_t *native_flags)
{
 struct playback_mask_geometry g;
 *why=PFV_R_FIT_END_AUTHORITY;
 *native_flags=0u;
 if(!snapshots_current(s)||!read_geometry(s->object,s->selector,&g)||!geometry_same(&g,&s->geometry))
  return 0u;
 if(!native_eligible_check(native_flags)){
  *why=PFV_R_FIT_END_NATIVE_ELIGIBLE;
  return 0u;
 }
 return snapshots_current(s);
}
static uint32_t pfv_pack(uint32_t w,uint32_t h,uint32_t *out)
{if(w>0xffffu||h>0xffffu)return 0u;*out=(w<<16)|h;return 1u;}
static uint32_t pfv_native_bits(uint32_t obj_known,uint32_t obj_kind,uint32_t cache_known,uint32_t cache_kind,
 uint32_t view_ready,uint32_t admitted,uint32_t unresolved,uint32_t native_flags)
{
 uint32_t f=((obj_kind&3u)<<PFV_FLAG_OBJ_KIND_SHIFT)|((cache_kind&3u)<<PFV_FLAG_CACHE_KIND_SHIFT)|(native_flags&(PFV_FLAG_NATIVE_ASPECT|PFV_FLAG_NATIVE_HEADER_OK|PFV_FLAG_NATIVE_MODEL));
 if(obj_known)f|=PFV_FLAG_OBJ_KNOWN;
 if(cache_known)f|=PFV_FLAG_CACHE_KNOWN;
 if(view_ready)f|=PFV_FLAG_VIEW_READY;
 if(admitted)f|=PFV_FLAG_ADMITTED;
 if(unresolved)f|=PFV_FLAG_UNRESOLVED;
 return f;
}
struct pfv_fit_ctx {
 uint32_t site,reason,bucket,caller,object,selector,category,actual,expected;
 uint32_t have_call,have_geom,geom_w,geom_h,have_obj,obj_w,obj_h,obj_kind;
 uint32_t have_cache,cache_w,cache_h,cache_kind,aux_a,aux_b,aux_c,aux_mask;
 uint32_t view_ready,admitted,unresolved,native_flags;
};
static __attribute__((noinline)) void pfv_emit_fit(const struct pfv_fit_ctx *c)
{
 struct playback_first_view_facts pf;clear_bytes(&pf,sizeof(pf));
 pf.reason=c->reason;pf.site=c->site;pf.caller=c->caller;pf.object=c->object;
 pf.selector=c->selector;pf.category=c->category;
 pf.flags=pfv_native_bits(c->have_obj,c->obj_kind,c->have_cache,c->cache_kind,c->view_ready,c->admitted,c->unresolved,c->native_flags);
 pf.actual=c->actual;pf.expected=c->expected;
 pf.aux_a=c->aux_a;pf.aux_b=c->aux_b;pf.aux_c=c->aux_c;
 pf.known_mask=PFV_K(reason)|PFV_K(site)|PFV_K(flags)|PFV_K(actual)|PFV_K(expected)|(c->aux_mask);
 if(c->have_call)pf.known_mask|=PFV_K(caller)|PFV_K(object)|PFV_K(selector)|PFV_K(category);
 if(c->have_geom&&pfv_pack(c->geom_w,c->geom_h,&pf.geom_dims))pf.known_mask|=PFV_K(geom_dims);
 if(c->have_obj&&pfv_pack(c->obj_w,c->obj_h,&pf.obj_dims))pf.known_mask|=PFV_K(obj_dims);
 if(c->have_cache&&pfv_pack(c->cache_w,c->cache_h,&pf.cache_dims))pf.known_mask|=PFV_K(cache_dims);
 playback_first_view_claim(PFV_FIT_FIRST_CALL,&pf);
 if(c->reason==PFV_R_FIT_OK){
  if(c->obj_kind==PB_BOX_RAW_L)playback_first_view_claim(PFV_FIT_RAW_L_SUCCESS,&pf);
  return;
 }
 if(c->bucket==PFV_FIT_ENTRY_REJECT)playback_first_view_claim(PFV_FIT_ENTRY_REJECT,&pf);
 else if(c->bucket==PFV_FIT_SNAPSHOT_REJECT)playback_first_view_claim(PFV_FIT_SNAPSHOT_REJECT,&pf);
 if(c->obj_kind==PB_BOX_RAW_L||c->cache_kind==PB_BOX_RAW_L)playback_first_view_claim(PFV_FIT_RAW_L_REJECT,&pf);
}
static __attribute__((noinline)) void pfv_diag_fit_begin(const struct playback_mask_fit_scope *s,
 const struct playback_mask_fit_facts *f,uint32_t reason,uint32_t bucket,uint32_t actual,uint32_t expected,
 const struct playback_mask_geometry *geom)
{
 struct pfv_fit_ctx c;clear_bytes(&c,sizeof(c));
 c.site=PFV_SITE_FIT_BEGIN;c.reason=reason;c.bucket=bucket;
 c.have_call=(reason!=PFV_R_FIT_CPU_NULL);
 c.caller=s->caller_lr;c.object=s->object;c.selector=s->selector;c.category=s->original_category;
 c.actual=actual;c.expected=expected;c.view_ready=s->view_active;
 if(f){
  c.have_obj=f->object_known;
  if(f->object_known){c.obj_kind=playback_box_kind(&f->object_facts);c.obj_w=f->object_facts.current_width;c.obj_h=f->object_facts.current_height;}
  c.have_cache=f->cache_known;
  if(f->cache_known){c.cache_kind=playback_box_kind(&f->cache_facts);c.cache_w=f->cache_facts.current_width;c.cache_h=f->cache_facts.current_height;}
 }
 if(geom){c.have_geom=1u;c.geom_w=geom->width;c.geom_h=geom->height;}
 if(reason==PFV_R_FIT_OBJECT_UNKNOWN||reason==PFV_R_FIT_CACHE_UNKNOWN){
  struct playback_slot_diag od,cd;
  playback_native_slot_diag(0u,PN_OBJECT0,&od);
  playback_native_slot_diag(PN_CACHE_SLOT,PN_CACHE_KEY,&cd);
  if(!c.have_obj){c.obj_kind=od.slot_kind;if(od.field_ok){c.obj_w=od.width;c.obj_h=od.height;c.have_obj=0u;}}
  if(!c.have_cache){c.cache_kind=cd.slot_kind;if(cd.field_ok){c.cache_w=cd.width;c.cache_h=cd.height;c.have_cache=0u;}}
  c.unresolved=!od.resolved||!cd.resolved;
  c.aux_a=(od.slot_state<<24)|(od.field_ok<<16)|(cd.slot_state<<8)|cd.field_ok;
  c.aux_b=reason==PFV_R_FIT_OBJECT_UNKNOWN?od.aspect:cd.aspect;
  c.aux_c=reason==PFV_R_FIT_OBJECT_UNKNOWN?od.slot_revision:cd.slot_revision;
  c.aux_mask=PFV_K(aux_a)|PFV_K(aux_b)|PFV_K(aux_c);
  if(od.field_ok)pfv_pack(od.width,od.height,&c.actual);
  if(cd.field_ok)pfv_pack(cd.width,cd.height,&c.expected);
 }else if(f&&reason==PFV_R_FIT_CATEGORY_MISMATCH){
  c.aux_a=f->view.category;c.aux_mask=PFV_K(aux_a);
 }else if(f&&(reason==PFV_R_FIT_VIEW_DEVICE||reason==PFV_R_FIT_VIEW_ORIENTATION||
  reason==PFV_R_FIT_VIEW_CAT13||reason==PFV_R_FIT_VIEW_COLOR)){
  c.aux_a=f->view.device;c.aux_b=f->view.orientation;c.aux_c=f->rotation;
  c.aux_mask=PFV_K(aux_a)|PFV_K(aux_b)|PFV_K(aux_c);
 }else{
  c.aux_a=s->object_snapshot.revision;c.aux_b=s->cache_snapshot.revision;
  c.aux_mask=PFV_K(aux_a)|PFV_K(aux_b);
 }
 pfv_emit_fit(&c);
}
static uint32_t entry_reject_reason(const struct playback_mask_fit_scope *s,const struct playback_mask_cpu *cpu,
 uint32_t sp,uint32_t continuation,uint32_t *actual,uint32_t *expected)
{
 if(!cpu){*actual=0u;*expected=1u;return PFV_R_FIT_CPU_NULL;}
 if(cpu->lr!=PM_FIT_CALLER){*actual=cpu->lr;*expected=PM_FIT_CALLER;return PFV_R_FIT_CALLER_LR;}
 if(cpu->r[1]!=PN_OBJECT0){*actual=cpu->r[1];*expected=PN_OBJECT0;return PFV_R_FIT_OBJECT;}
 if(cpu->r[4]!=cpu->r[1]){*actual=cpu->r[4];*expected=cpu->r[1];return PFV_R_FIT_SAVED_OBJECT;}
 if(cpu->r[2]>=3u){*actual=cpu->r[2];*expected=2u;return PFV_R_FIT_SELECTOR;}
 if(cpu->r[5]!=cpu->r[2]){*actual=cpu->r[5];*expected=cpu->r[2];return PFV_R_FIT_SAVED_SELECTOR;}
 if(cpu->r[6]){*actual=cpu->r[6];*expected=0u;return PFV_R_FIT_DRAWMODE;}
 if(cpu->r[7]){*actual=cpu->r[7];*expected=0u;return PFV_R_FIT_ROTATION;}
 if((sp&7u)||!frame_memory_stack_frame_span(sp,PM_COMBINED)||address_of(s)!=sp+PM_FIT_FRAME||
  s->original_sp!=sp+PM_FIT_FRAME+PM_FIT_SCOPE_BYTES+PM_CPU_IMAGE||s->output!=s->original_sp+PM_OUTPUT_OFFSET||
  continuation!=(uint32_t)(uintptr_t)playback_mask_fit_continue){*actual=sp;*expected=s->original_sp;return PFV_R_FIT_FRAME_SPAN;}
 if(!view_sole(s)){*actual=(uint32_t)atomic_load_explicit(&playback_mask_view.control,memory_order_relaxed);*expected=s->view_revision;return PFV_R_FIT_VIEW_SOLE;}
 return PFV_R_NONE;
}
void playback_mask_fit_begin(struct playback_mask_fit_scope *s,const struct playback_mask_cpu *cpu,uint32_t sp,uint32_t continuation)
{
 clear_bytes(s,sizeof(*s));s->marker=PM_MARKER;s->self=address_of(s);s->native_sp=sp;s->continuation=continuation;
 atomic_fetch_add_explicit(&counts.entered,1u,memory_order_relaxed);view_begin(s);
 uint32_t actual=0u,expected=0u;
 if(!cpu){pfv_diag_fit_begin(s,NULL,PFV_R_FIT_CPU_NULL,PFV_FIT_ENTRY_REJECT,0u,1u,NULL);return;}
 s->original_category=cpu->r[3];s->original_sp=original_sp(cpu);s->output=cpu->r[0];s->object=cpu->r[1];s->caller_lr=cpu->lr;s->selector=cpu->r[2];
 uint32_t why=entry_reject_reason(s,cpu,sp,continuation,&actual,&expected);
 if(why!=PFV_R_NONE){pfv_diag_fit_begin(s,NULL,why,PFV_FIT_ENTRY_REJECT,actual,expected,NULL);return;}
 struct playback_mask_fit_facts f;clear_bytes(&f,sizeof(f));
 f.entry_lr=cpu->lr;f.original_sp=s->original_sp;f.output=s->output;f.object=s->object;f.saved_object=cpu->r[4];
 f.selector=cpu->r[2];f.saved_selector=cpu->r[5];f.drawmode=cpu->r[6];f.rotation=cpu->r[7];f.category=cpu->r[3];
 if(!read_view(&f.view)){pfv_diag_fit_begin(s,&f,PFV_R_FIT_VIEW_READ,PFV_FIT_ENTRY_REJECT,0u,1u,NULL);return;}
 if(!view_admit(&f.view)){
  uint32_t expected_view=0u;
  if(!view_device_admit(f.view.device)){why=PFV_R_FIT_VIEW_DEVICE;actual=f.view.device;expected_view=1u;}
  else if(!view_orientation_admit(f.view.orientation)){why=PFV_R_FIT_VIEW_ORIENTATION;actual=f.view.orientation;}
  else if(f.view.category==13u){why=PFV_R_FIT_VIEW_CAT13;actual=13u;}
  else{why=PFV_R_FIT_VIEW_COLOR;actual=f.view.color[0]|f.view.color[1]|f.view.color[2];}
  pfv_diag_fit_begin(s,&f,why,PFV_FIT_ENTRY_REJECT,actual,expected_view,NULL);return;
 }
 if(f.category!=f.view.category){pfv_diag_fit_begin(s,&f,PFV_R_FIT_CATEGORY_MISMATCH,PFV_FIT_ENTRY_REJECT,f.category,f.view.category,NULL);return;}
 s->fitted_device=f.view.device;s->fitted_orientation=f.view.orientation;s->fitted_rotation=f.rotation;
 f.object_known=playback_native_object_snapshot(s->object,&s->object_snapshot);
 f.cache_known=playback_native_cache_snapshot(&s->cache_snapshot);
 if(f.object_known)f.object_facts=s->object_snapshot.facts;
 if(f.cache_known)f.cache_facts=s->cache_snapshot.facts;
 if(!f.object_known){pfv_diag_fit_begin(s,&f,PFV_R_FIT_OBJECT_UNKNOWN,PFV_FIT_SNAPSHOT_REJECT,0u,1u,NULL);return;}
 if(!f.cache_known){pfv_diag_fit_begin(s,&f,PFV_R_FIT_CACHE_UNKNOWN,PFV_FIT_SNAPSHOT_REJECT,0u,1u,NULL);return;}
 if(!playback_mask_fit_admit(&f)){
  if(playback_box_kind(&f.object_facts)!=PB_BOX_RAW_L){why=PFV_R_FIT_OBJECT_NOT_RAW_L;actual=playback_box_kind(&f.object_facts);expected=PB_BOX_RAW_L;}
  else if(playback_box_kind(&f.cache_facts)!=PB_BOX_RAW_L){why=PFV_R_FIT_CACHE_NOT_RAW_L;actual=playback_box_kind(&f.cache_facts);expected=PB_BOX_RAW_L;}
  else if(!facts_same(&f.object_facts,&f.cache_facts)){why=PFV_R_FIT_FACTS_MISMATCH;actual=f.object_facts.current_width;expected=f.cache_facts.current_width;}
  else{why=PFV_R_FIT_MAIN_DIMS_MISMATCH;actual=f.object_facts.current_width;expected=f.object_facts.current_height;}
  pfv_diag_fit_begin(s,&f,why,PFV_FIT_RAW_L_REJECT,actual,expected,NULL);return;
 }
 if(!read_geometry(s->object,s->selector,&s->geometry)){pfv_diag_fit_begin(s,&f,PFV_R_FIT_GEOMETRY_FAIL,PFV_FIT_RAW_L_REJECT,s->selector,1u,&s->geometry);return;}
 if(!snapshots_current(s)){pfv_diag_fit_begin(s,&f,PFV_R_FIT_SNAPSHOTS_FAIL,PFV_FIT_SNAPSHOT_REJECT,0u,1u,&s->geometry);return;}
 if(!s->selector&&(s->geometry.width!=f.object_facts.current_width||s->geometry.height!=f.object_facts.current_height)){
  pfv_diag_fit_begin(s,&f,PFV_R_FIT_MAIN_DIMS_MISMATCH,PFV_FIT_RAW_L_REJECT,s->geometry.width,f.object_facts.current_width,&s->geometry);return;
 }
 s->admitted=1u;atomic_fetch_add_explicit(&counts.admitted,1u,memory_order_relaxed);
}
static __attribute__((noinline)) void pfv_diag_fit_end(const struct playback_mask_fit_scope *s,
 uint32_t reason,uint32_t bucket,uint32_t actual,uint32_t expected,uint32_t aux_a,uint32_t aux_b,uint32_t aux_c,uint32_t native_flags)
{
 struct pfv_fit_ctx c;clear_bytes(&c,sizeof(c));
 c.site=PFV_SITE_FIT_END;c.reason=reason;c.bucket=bucket;c.native_flags=native_flags;
 if(s){
  c.have_call=1u;
  c.caller=s->caller_lr;c.object=s->object;c.selector=s->selector;c.category=s->original_category;
  c.admitted=s->admitted;c.view_ready=(reason==PFV_R_FIT_OK);
  c.have_geom=1u;c.geom_w=s->geometry.width;c.geom_h=s->geometry.height;
  c.have_obj=1u;c.obj_kind=playback_box_kind(&s->object_snapshot.facts);
  c.obj_w=s->object_snapshot.facts.current_width;c.obj_h=s->object_snapshot.facts.current_height;
  c.have_cache=1u;c.cache_kind=playback_box_kind(&s->cache_snapshot.facts);
  c.cache_w=s->cache_snapshot.facts.current_width;c.cache_h=s->cache_snapshot.facts.current_height;
 }
 c.actual=actual;c.expected=expected;c.aux_a=aux_a;c.aux_b=aux_b;c.aux_c=aux_c;
 c.aux_mask=PFV_K(aux_a)|PFV_K(aux_b)|PFV_K(aux_c);
 pfv_emit_fit(&c);
}
uint32_t playback_mask_fit_end(struct playback_mask_fit_scope *s,uint32_t result)
{
 if(!s||s->marker!=PM_MARKER||s->self!=address_of(s)||
  (s->native_sp&7u)||!frame_memory_stack_frame_span(s->native_sp,PM_COMBINED)||s->self!=s->native_sp+PM_FIT_FRAME||
  s->original_sp!=s->self+PM_FIT_SCOPE_BYTES+PM_CPU_IMAGE||s->continuation!=(uint32_t)(uintptr_t)playback_mask_fit_continue){
  pfv_diag_fit_end(NULL,PFV_R_FIT_END_FRAME,PFV_FIT_ENTRY_REJECT,s?s->marker:0u,PM_MARKER,result,0u,0u,0u);
  quarantine();rejected();return result;
 }
 uint32_t owned=s->output==s->original_sp+PM_OUTPUT_OFFSET&&s->object==PN_OBJECT0&&s->caller_lr==PM_FIT_CALLER&&s->selector<3u&&s->original_category!=13u&&
  view_device_admit(s->fitted_device)&&view_orientation_admit(s->fitted_orientation)&&s->fitted_rotation==0u;
 if(s->admitted&&!owned){
  pfv_diag_fit_end(s,PFV_R_FIT_END_OWNED,PFV_FIT_ENTRY_REJECT,s->object,PN_OBJECT0,s->caller_lr,result,0u,0u);
  quarantine();rejected();return result;
 }
 uint32_t stock[4],row[4],known=0u,wrote=0u,fail_reason=PFV_R_NONE,actual=0u,expected=0u,native_flags=0u;clear_bytes(row,sizeof(row));
 if(s->admitted&&owned){
  uint32_t read=1u;for(unsigned i=0;read&&i<4u;i++)read=stack_word(s->original_sp,PM_CALLER_BYTES,PM_OUTPUT_OFFSET+4u*i,&stock[i]);
  if(!read){fail_reason=PFV_R_FIT_END_STOCK_READ;}
  else if(!row_same(stock,s->geometry.stock)){fail_reason=PFV_R_FIT_END_STOCK_MISMATCH;actual=stock[2];expected=s->geometry.stock[2];}
  else{
   uint32_t ratio[2];struct playback_mask_projection_facts f;
   f.selector=s->selector;f.width=s->geometry.width;f.height=s->geometry.height;f.stock_result=result;
   for(unsigned i=0;i<4u;i++)f.stock[i]=stock[i];
   if(!playback_box_ratio(&s->object_snapshot.facts,ratio)){fail_reason=PFV_R_FIT_END_RATIO;}
   else{
    f.numerator=ratio[0];f.denominator=ratio[1];
    if(!playback_mask_project(&f,row)){fail_reason=PFV_R_FIT_END_PROJECT;actual=result;expected=s->selector;}
    else if(!authority_current_diag(s,&fail_reason,&native_flags)){
     actual=0u;expected=1u;
    }else{
     for(unsigned i=0;i<4u;i++){
      output_word(s->output+4u*i,row[i]);
#ifdef PLAYBACK_MASK_TEST_AFTER_OUTPUT_STORE
      extern void playback_mask_test_after_output_store(unsigned);playback_mask_test_after_output_store(i+1u);
#endif
     }
     wrote=1u;atomic_fetch_add_explicit(&counts.projected,1u,memory_order_relaxed);known=authority_current_diag(s,&fail_reason,&native_flags);
     if(!known){
      actual=0u;expected=1u;
     }
    }
   }
  }
 }
 uint32_t ready=view_end(s,known);
 if(wrote&&!ready)for(unsigned i=0;i<4u;i++)output_word(s->output+4u*i,stock[i]);
 if(s->admitted&&owned){
  if(ready){
   pfv_diag_fit_end(s,PFV_R_FIT_OK,PFV_FIT_RAW_L_SUCCESS,result,1u,s->view_revision,s->object_snapshot.revision,s->cache_snapshot.revision,native_flags);
  }else{
   if(fail_reason==PFV_R_NONE){fail_reason=PFV_R_FIT_END_VIEW_COLLISION;actual=s->view_revision;expected=ready;}
   pfv_diag_fit_end(s,fail_reason,PFV_FIT_RAW_L_REJECT,actual,expected,result,s->object_snapshot.revision,s->cache_snapshot.revision,native_flags);
  }
 }
 s->marker=0u;
 if(ready){atomic_fetch_add_explicit(&counts.crop_candidate,1u,memory_order_relaxed);return 1u;}
 rejected();return result;
}
static uint32_t candidate(uint32_t *view,uint32_t *object,uint32_t *cache,uint32_t *category,uint32_t *device,uint32_t *orientation,uint32_t *rotation)
{
 uint64_t before=atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst);
 if(!idle(before))return 0u;
 uint32_t ready=atomic_load_explicit(&playback_mask_view.ready_revision,memory_order_seq_cst);
 uint32_t o=atomic_load_explicit(&playback_mask_view.object_revision,memory_order_seq_cst),c=atomic_load_explicit(&playback_mask_view.cache_revision,memory_order_seq_cst);
 uint32_t cat=atomic_load_explicit(&playback_mask_view.category,memory_order_seq_cst);
 uint32_t dev=atomic_load_explicit(&playback_mask_view.device,memory_order_seq_cst);
 uint32_t ori=atomic_load_explicit(&playback_mask_view.orientation,memory_order_seq_cst);
 uint32_t rot=atomic_load_explicit(&playback_mask_view.rotation,memory_order_seq_cst);
 if(!o||!c||cat==13u||!view_device_admit(dev)||!view_orientation_admit(ori)||rot!=0u||
  ready!=revision(before)||before!=atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst))return 0u;
 *view=ready;*object=o;*cache=c;*category=cat;*device=dev;*orientation=ori;*rotation=rot;return 1u;
}
static __attribute__((noinline)) void pfv_diag_guide(uint32_t role,uint32_t reason,uint32_t caller,
 uint32_t vr,uint32_t category,uint32_t view_ready,uint32_t actual,uint32_t expected,
 uint32_t o_ok,uint32_t obj_kind,uint32_t o_w,uint32_t o_h,
 uint32_t c_ok,uint32_t cache_kind,uint32_t c_w,uint32_t c_h,
 uint32_t unresolved,uint32_t native_flags,
 uint32_t aux_a,uint32_t aux_b,uint32_t aux_c)
{
 struct playback_first_view_facts pf;clear_bytes(&pf,sizeof(pf));
 pf.reason=reason;pf.site=PFV_SITE_GUIDE;pf.caller=caller;pf.object=PN_OBJECT0;
 pf.selector=vr;pf.category=category;
 pf.flags=pfv_native_bits(o_ok,obj_kind,c_ok,cache_kind,view_ready,role==PFV_GUIDE_FIRST_HIDDEN,unresolved,native_flags);
 pf.actual=actual;pf.expected=expected;pf.aux_a=aux_a;pf.aux_b=aux_b;pf.aux_c=aux_c;
 pf.known_mask=PFV_K(reason)|PFV_K(site)|PFV_K(object)|PFV_K(flags)|PFV_K(actual)|PFV_K(expected)|PFV_K(aux_a)|PFV_K(aux_b)|PFV_K(aux_c);
 if(caller)pf.known_mask|=PFV_K(caller);
 if(reason!=PFV_R_GUIDE_CPU_NULL)pf.known_mask|=PFV_K(selector)|PFV_K(category);
 if(o_ok&&pfv_pack(o_w,o_h,&pf.obj_dims))pf.known_mask|=PFV_K(obj_dims);
 if(c_ok&&pfv_pack(c_w,c_h,&pf.cache_dims))pf.known_mask|=PFV_K(cache_dims);
 playback_first_view_claim(role,&pf);
}
static __attribute__((noinline)) void pfv_guide_reject_if_relevant(uint32_t reason,uint32_t caller,
 uint32_t vr,uint32_t category,uint32_t view_ready,uint32_t actual,uint32_t expected,
 const struct playback_tag_snapshot *o,uint32_t o_ok,const struct playback_tag_snapshot *c,uint32_t c_ok,
 uint32_t native_flags,uint32_t aux_a,uint32_t aux_b,uint32_t aux_c)
{
 uint32_t obj_kind=0u,o_w=0u,o_h=0u,cache_kind=0u,c_w=0u,c_h=0u,unresolved=0u;
 if(o_ok&&o){obj_kind=playback_box_kind(&o->facts);o_w=o->facts.current_width;o_h=o->facts.current_height;}
 else{
  struct playback_slot_diag od;playback_native_slot_diag(0u,PN_OBJECT0,&od);
  unresolved|=!od.resolved;obj_kind=od.slot_kind;
  if(od.field_ok){o_ok=1u;o_w=od.width;o_h=od.height;}
 }
 if(c_ok&&c){cache_kind=playback_box_kind(&c->facts);c_w=c->facts.current_width;c_h=c->facts.current_height;}
 else{
  struct playback_slot_diag cd;playback_native_slot_diag(PN_CACHE_SLOT,PN_CACHE_KEY,&cd);
  unresolved|=!cd.resolved;cache_kind=cd.slot_kind;
  if(cd.field_ok){c_ok=1u;c_w=cd.width;c_h=cd.height;}
 }
 uint32_t raw_l=(o_ok&&obj_kind==PB_BOX_RAW_L)||(c_ok&&cache_kind==PB_BOX_RAW_L);
 if(!view_ready&&!raw_l)return;
 pfv_diag_guide(PFV_GUIDE_RELEVANT_REJECT,reason,caller,vr,category,view_ready,actual,expected,
  o_ok,obj_kind,o_w,o_h,c_ok,cache_kind,c_w,c_h,unresolved,native_flags,aux_a,aux_b,aux_c);
}
uint32_t playback_mask_guide_decide(const struct playback_mask_cpu *cpu)
{
 uint32_t vr=0u,orr=0u,cr=0u,cat=0u,dev=0u,ori=0u,rot=0u;
 if(!cpu){pfv_guide_reject_if_relevant(PFV_R_GUIDE_CPU_NULL,0u,0u,0u,0u,0u,1u,NULL,0u,NULL,0u,0u,0u,0u,0u);return 0u;}
 if(!candidate(&vr,&orr,&cr,&cat,&dev,&ori,&rot)){
  pfv_guide_reject_if_relevant(PFV_R_GUIDE_NOT_CANDIDATE,0u,vr,cat,0u,
   atomic_load_explicit(&playback_mask_view.ready_revision,memory_order_relaxed),
   (uint32_t)(atomic_load_explicit(&playback_mask_view.control,memory_order_relaxed)>>32),
   NULL,0u,NULL,0u,0u,
   atomic_load_explicit(&playback_mask_view.object_revision,memory_order_relaxed),
   atomic_load_explicit(&playback_mask_view.cache_revision,memory_order_relaxed),
   atomic_load_explicit(&playback_mask_view.category,memory_order_relaxed));
  return 0u;
 }
 struct playback_mask_guide_facts f;clear_bytes(&f,sizeof(f));f.native_sp=original_sp(cpu);f.geometry=cpu->r[5];f.resources=cpu->r[4];
 uint32_t why=PFV_R_NONE,actual=0u,expected=0u;
 if((f.native_sp&7u)||!frame_memory_stack_frame_span(f.native_sp,PM_GUIDE_SPAN)){why=PFV_R_GUIDE_FRAME_SPAN;actual=f.native_sp;expected=PM_GUIDE_SPAN;}
 else if(f.geometry!=f.native_sp+PM_GUIDE_GEOMETRY){why=PFV_R_GUIDE_GEOMETRY_PTR;actual=f.geometry;expected=f.native_sp+PM_GUIDE_GEOMETRY;}
 else if(f.resources!=PM_GUIDE_RESOURCE){why=PFV_R_GUIDE_RESOURCES;actual=f.resources;expected=PM_GUIDE_RESOURCE;}
 else if(!stack_word(f.native_sp,PM_GUIDE_SPAN,PM_GUIDE_RETURN,&f.saved_lr)||f.saved_lr!=PM_GUIDE_CALLER){why=PFV_R_GUIDE_SAVED_LR;actual=f.saved_lr;expected=PM_GUIDE_CALLER;}
 else if(!stack_word(f.native_sp,PM_GUIDE_SPAN,PM_GUIDE_SAVED_GEOMETRY,&f.saved_geometry)||f.saved_geometry!=f.geometry){why=PFV_R_GUIDE_SAVED_GEOM;actual=f.saved_geometry;expected=f.geometry;}
 else if(!stack_word(f.native_sp,PM_GUIDE_SPAN,PM_GUIDE_HANDLE,&f.handle)||f.handle==0u){why=PFV_R_GUIDE_HANDLE_ZERO;actual=f.handle;expected=1u;}
 else if(!read_view(&f.view)){why=PFV_R_GUIDE_VIEW_READ;actual=0u;expected=1u;}
 else if(!playback_mask_guide_admit(&f)){why=PFV_R_GUIDE_VIEW_ADMIT;actual=(f.view.device<<16)|(f.view.orientation&0xffffu);expected=f.view.category;}
 else if(f.view.device!=dev){why=PFV_R_GUIDE_DEVICE_MISMATCH;actual=f.view.device;expected=dev;}
 else if(f.view.orientation!=ori){why=PFV_R_GUIDE_ORIENTATION_MISMATCH;actual=f.view.orientation;expected=ori;}
 else if(f.view.category!=cat){why=PFV_R_GUIDE_CAT_MISMATCH;actual=f.view.category;expected=cat;}
 if(why!=PFV_R_NONE){
  pfv_guide_reject_if_relevant(why,f.saved_lr,vr,f.view.category,1u,actual,expected,NULL,0u,NULL,0u,0u,orr,cr,cat);
  return 0u;
 }
 struct playback_tag_snapshot o,c;
 uint32_t o_ok=playback_native_object_snapshot(PN_OBJECT0,&o),c_ok=o_ok?playback_native_cache_snapshot(&c):0u,native_flags=0u;
 if(!o_ok){why=PFV_R_GUIDE_OBJECT_UNKNOWN;actual=0u;expected=orr;}
 else if(!c_ok){why=PFV_R_GUIDE_CACHE_UNKNOWN;actual=0u;expected=cr;}
 else if(o.revision!=orr){why=PFV_R_GUIDE_OBJECT_REV;actual=o.revision;expected=orr;}
 else if(c.revision!=cr){why=PFV_R_GUIDE_CACHE_REV;actual=c.revision;expected=cr;}
 else if(playback_box_kind(&o.facts)!=PB_BOX_RAW_L){why=PFV_R_GUIDE_NOT_RAW_L;actual=playback_box_kind(&o.facts);expected=PB_BOX_RAW_L;}
 else if(!facts_same(&o.facts,&c.facts)){why=PFV_R_GUIDE_FACTS_MISMATCH;actual=o.facts.current_width;expected=c.facts.current_width;}
 else if(!playback_native_snapshot_same(&o)||!playback_native_snapshot_same(&c)||
  !read_view(&f.view)||!view_matches(&f.view,dev,ori,cat)){why=PFV_R_GUIDE_LATE_RACE;actual=vr;expected=1u;}
 else if(!native_eligible_check(&native_flags)){why=PFV_R_GUIDE_NATIVE_ELIGIBLE;actual=0u;expected=1u;}
 else if(!playback_native_snapshot_same(&o)||!playback_native_snapshot_same(&c)||
  !read_view(&f.view)||!view_matches(&f.view,dev,ori,cat)||
  atomic_load_explicit(&playback_mask_view.control,memory_order_seq_cst)!=((uint64_t)vr<<32)){why=PFV_R_GUIDE_LATE_RACE;actual=vr;expected=1u;}
 if(why!=PFV_R_NONE){
  pfv_guide_reject_if_relevant(why,f.saved_lr,vr,f.view.category,1u,actual,expected,&o,o_ok,&c,c_ok,native_flags,orr,cr,f.handle);
  return 0u;
 }
 pfv_diag_guide(PFV_GUIDE_FIRST_HIDDEN,PFV_R_GUIDE_HIDDEN,f.saved_lr,vr,f.view.category,1u,1u,1u,
  o_ok,playback_box_kind(&o.facts),o.facts.current_width,o.facts.current_height,
  c_ok,playback_box_kind(&c.facts),c.facts.current_width,c.facts.current_height,
  0u,native_flags,orr,cr,f.handle);
 atomic_fetch_add_explicit(&counts.guide_hidden,1u,memory_order_relaxed);return 1u;
}
void playback_mask_observe(struct playback_mask_observation *out)
{
 if(!out)return;
 out->entered=atomic_load_explicit(&counts.entered,memory_order_relaxed);out->admitted=atomic_load_explicit(&counts.admitted,memory_order_relaxed);
 out->projected=atomic_load_explicit(&counts.projected,memory_order_relaxed);out->crop_candidate=atomic_load_explicit(&counts.crop_candidate,memory_order_relaxed);
 out->guide_hidden=atomic_load_explicit(&counts.guide_hidden,memory_order_relaxed);out->rejected=atomic_load_explicit(&counts.rejected,memory_order_relaxed);
 out->view_ready=0u;out->view_revision=0u;out->object_revision=0u;out->cache_revision=0u;
 uint32_t category,dev,ori,rot;out->view_ready=candidate(&out->view_revision,&out->object_revision,&out->cache_revision,&category,&dev,&ori,&rot);
}
#endif
