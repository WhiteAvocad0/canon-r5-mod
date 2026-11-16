#include "frame_native.h"
#include "frame-row/frame_row.h"
#include "frame-row/frame_row_constants.h"
#include <stddef.h>
#include <stdatomic.h>

const struct frame_native_binding_profile frame_native_profile={
 FRAME_NATIVE_SCHEMA,FN_DTS_CONTROL,FN_DTS_DEFAULT,FN_DTS_TABLE,FN_DTS_SLOTS,FN_DTS_BYTES,
 FN_DESCRIPTORS,FN_DESCRIPTOR_STRIDE,FN_DESCRIPTOR_COUNT,FN_PACKET_TABLE,FN_PACKET_COUNT,FN_ORCA_SLOT,
 FN_CAPTURE_LR_A,FN_CAPTURE_LR_B,FN_ROW_LR,FN_ROW_OUTER_LR,FN_OFFSET_LR,FN_MODE_BASE,FN_MODE_STRIDE,
 FN_MODE_COUNT,FN_MODE_INDEX,FN_RAW_OFFSET,
 FN_ROW_OUTPUT,FN_ROW_OUTER_RETURN,FN_OFFSET_X,FN_OFFSET_Y,FN_OFFSET_PACKET,FN_OFFSET_PREFIX,FN_OFFSET_TOKEN,FN_OFFSET_DESCRIPTOR,
 {FN_OFFSET_RET0,FN_OFFSET_RET1,FN_OFFSET_RET2,FN_OFFSET_RET3},
 {FN_OFFSET_LR0,FN_OFFSET_LR1,FN_OFFSET_LR2,FN_OFFSET_LR3},
 FN_SNAPSHOT_FRAME,FN_REFRESH_FRAME,FN_COPY_SCOPE_BYTES,FN_RESET_SCOPE_BYTES,FN_ROLE_COUNT,
 FRAME_NATIVE_FACT_WORDS,FRAME_NATIVE_POLICY_WORDS,FRAME_NATIVE_CAPTURE_WORDS
};

static uint32_t words_equal(const uint32_t *a,const uint32_t *b)
{for(uint32_t i=0;i<12u;i++)if(a[i]!=b[i])return 0u;return 1u;}
/* Keys 4001/4021/5001/5021 are cases 2..5. Reduced rows are the odd pair. */
static uint32_t evf_row_case(const uint32_t *row,uint32_t reduced,uint32_t projected_ok)
{
 uint32_t found=0xffffffffu,count=0u;
 for(uint32_t i=2u;i<6u;i++){
  uint32_t case_reduced=(i==3u||i==5u)?1u:0u;
  if(case_reduced!=reduced)continue;
  const struct xpan_frame_row_case *c=&xpan_frame_row_cases[i];
  if(!words_equal(row,c->original)&&(!projected_ok||!words_equal(row,c->projected)))continue;
  found=i;count++;
 }
 return count==1u?found:0xffffffffu;
}
static uint32_t descriptor_index(uint32_t pointer,uint32_t *index)
{
 if(pointer<FN_DESCRIPTORS)return 0u;
 uint32_t delta=pointer-FN_DESCRIPTORS;
 /* Six explicit addresses avoid adding a divide runtime to this hot guard. */
 for(uint32_t i=0;i<FN_DESCRIPTOR_COUNT;i++)if(delta==i*FN_DESCRIPTOR_STRIDE){*index=i;return 1u;}
 return 0u;
}
uint32_t frame_native_stack_extent(uint32_t role)
{return role==FN_ROLE_ROW?FN_ROW_FRAME_BYTES:role==FN_ROLE_OFFSET?FN_OFFSET_FRAME_BYTES:0u;}
uint32_t frame_native_capture_admit(const struct frame_native_capture_facts *f)
{
 if(!f||(f->entry_lr!=FN_CAPTURE_LR_A&&f->entry_lr!=FN_CAPTURE_LR_B)||
  f->offset!=4u||f->second_offset!=4u||f->length!=FN_MODE_STRIDE||!f->writer_count||!f->writer_count_after||
  f->index>=FN_MODE_COUNT||!frame_memory_data_span(f->original_r4,FN_RAW_OFFSET+4u)||
  f->index_after!=f->index||f->raw_before!=f->raw_after||f->raw_before>255u)return 0u;
 return f->source==f->original_r4+FN_MODE_BASE+f->index*FN_MODE_STRIDE;
}
uint32_t frame_native_admit(const struct frame_native_facts *f)
{
 uint32_t index;
 /* Display 1 is the rear LCD. Display 0x10 is the still EVF path: DevPath
  * takes it only when context+0xAC bit 0 is clear and bit 4 is set, and the
  * Orca sibling E037C926 passes that same type from the packet bit. */
 if(!f||(f->role!=FN_ROLE_ROW&&f->role!=FN_ROLE_OFFSET)||
  (f->native_sp&7u)||!frame_memory_stack_frame_span(f->native_sp,frame_native_stack_extent(f->role))||!frame_memory_data_span(f->context,FN_DTS_BYTES)||
  (f->display!=1u&&f->display!=0x10u)||!descriptor_index(f->descriptor,&index)||f->descriptor_index!=index||
  f->descriptor_context!=f->context||f->mode!=0u||(f->rich!=1u&&f->rich!=8u)||
  (f->flags38==0u&&f->flags_eb0==1u)||f->prohibit||
  f->slot_index>=FRAME_TAG_SLOT_COUNT||f->tag_pointer!=f->context||f->tag_frame!=f->native_frame||
  f->tag_raw!=7u||!f->tag_revision||f->tag_ready!=1u||f->tag_same!=1u)return 0u;
 if(f->display==1u){if((f->display_mask&15u)!=1u)return 0u;}
 else if((f->display_mask&0x1Fu)!=0x10u)return 0u;
 uint32_t reduced=(f->quality==23u||f->quality==24u||
  (f->quality>=36u&&f->quality<=39u)||(f->flag410&&f->flag418))?1u:0u;
 uint32_t case_index=reduced;
 if(f->display==1u){
  const struct xpan_frame_row_case *row=&xpan_frame_row_cases[reduced];
  if(f->role==FN_ROLE_ROW) {
   if(f->entry_lr!=FN_ROW_LR||f->outer_lr!=FN_ROW_OUTER_LR||f->output_x!=f->native_sp+FN_ROW_OUTPUT||
    (!words_equal(f->row,row->original)&&!words_equal(f->row,row->projected)))return 0u;
  } else {
   if(f->entry_lr!=FN_OFFSET_LR||f->output_x!=f->native_sp+FN_OFFSET_X||f->output_y!=f->native_sp+FN_OFFSET_Y||
    f->saved_returns[0]!=FN_OFFSET_LR0||f->saved_returns[1]!=FN_OFFSET_LR1||
    f->saved_returns[2]!=FN_OFFSET_LR2||f->saved_returns[3]!=FN_OFFSET_LR3||
    !frame_memory_data_span(f->packet,0x2C8u)||!frame_memory_data_span(f->orca_owner,0x1D4u)||f->prefix!=f->orca_owner+FN_OFFSET_LCD_PREFIX||
    f->orca_mode!=(reduced?2u:0u)||f->orca_aspect!=1u||f->packet_display!=1u||
    f->packet_token_low!=f->descriptor_token_low||f->packet_token_high!=f->descriptor_token_high||
    f->outer_token_low!=f->descriptor_token_low||f->outer_token_high!=f->descriptor_token_high||
    !words_equal(f->row,row->original)||f->original_x!=row->original[10]||
    f->original_y!=row->original[11]||f->original_return!=row->original[11])return 0u;
  }
 }else if(f->role==FN_ROLE_ROW){
  if(f->entry_lr!=FN_ROW_LR||f->outer_lr!=FN_ROW_OUTER_LR||f->output_x!=f->native_sp+FN_ROW_OUTPUT)return 0u;
  case_index=evf_row_case(f->row,reduced,1u);
  if(case_index>5u)return 0u;
 }else{
  /* E037C97F/E037CD3D are the C926/CCBC returns. The outer two match LCD. */
  if(f->entry_lr!=FN_OFFSET_LR||f->output_x!=f->native_sp+FN_OFFSET_X||f->output_y!=f->native_sp+FN_OFFSET_Y||
   f->saved_returns[0]!=FN_OFFSET_EVF_LR0||f->saved_returns[1]!=FN_OFFSET_EVF_LR1||
   f->saved_returns[2]!=FN_OFFSET_LR2||f->saved_returns[3]!=FN_OFFSET_LR3||
   !frame_memory_data_span(f->packet,0x2C8u)||!frame_memory_data_span(f->orca_owner,0x1D4u)||f->prefix!=f->orca_owner+FN_OFFSET_EVF_PREFIX||
   f->orca_mode!=(reduced?2u:0u)||f->orca_aspect!=1u||f->packet_display!=0x10u||
   f->packet_token_low!=f->descriptor_token_low||f->packet_token_high!=f->descriptor_token_high||
   f->outer_token_low!=f->descriptor_token_low||f->outer_token_high!=f->descriptor_token_high)return 0u;
  case_index=evf_row_case(f->row,reduced,0u);
  if(case_index>5u)return 0u;
  const struct xpan_frame_row_case *row=&xpan_frame_row_cases[case_index];
  if(f->original_x!=row->original[10]||f->original_y!=row->original[11]||f->original_return!=row->original[11])return 0u;
 }
 return case_index+1u;
}
uint32_t frame_native_project(const struct frame_native_facts *f,uint32_t out[12])
{
 uint32_t row=frame_native_admit(f);
 if(!row||!out)return 0u;
 const struct xpan_frame_row_case *profile=&xpan_frame_row_cases[row-1u];
 if(!words_equal(out,profile->original)&&!words_equal(out,profile->projected))return 0u;
 return xpan_frame_row_project(7u,out);
}

#ifndef FRAME_NATIVE_POLICY_ONLY
#include "frame_native_trace.h"
#include "frame_disable_trace.h"
#define COPY_MARKER 0x46534331u
#define RESET_MARKER 0x46535231u
/* Private, coherent retained state. No Canon pointer is followed by reporting. */
struct frame_tags frame_native_tags;
static _Atomic uint32_t framing_disabled;
/* Character writes avoid both struct-alias UB and a freestanding memset call. */
static __attribute__((noinline)) void zero_bytes(void *object,uint32_t bytes)
{volatile unsigned char *p=(volatile unsigned char *)object;for(uint32_t i=0;i<bytes;i++)p[i]=0u;}
extern void frame_native_snapshot_continue(void),frame_native_refresh_continue(void);
extern uint32_t frame_native_original_default(uint32_t,uint32_t,uint32_t,uint32_t);
extern uint32_t frame_native_original_row(uint32_t,uint32_t,uint32_t);
extern uint32_t frame_native_original_offset(uint32_t,uint32_t,uint32_t);
#ifdef FRAME_NATIVE_HOST
extern uint32_t frame_native_host_read(uint32_t,uint32_t *);
extern uint32_t frame_native_host_write(uint32_t,uint32_t);
extern uint32_t frame_native_host_address(const void *);
#endif
static uint32_t private_address(const void *pointer)
{
#ifdef FRAME_NATIVE_HOST
 return frame_native_host_address(pointer);
#else
 return (uint32_t)(uintptr_t)pointer;
#endif
}
uint32_t frame_native_enabled(void)
{return atomic_load_explicit(&framing_disabled,memory_order_acquire)==0u;}
static void disable_facts(struct frame_disable_facts *f,uint32_t reason)
{
 f->reason=reason;f->known_mask|=FD_K_REASON;
 frame_disable_claim(f);
 atomic_store_explicit(&framing_disabled,1u,memory_order_release);
}
static void disable_operands(struct frame_disable_facts *f,uint32_t reason,uint32_t actual,uint32_t expected)
{f->aux_a=actual;f->aux_b=expected;f->known_mask|=FD_K_AUX_A|FD_K_AUX_B;disable_facts(f,reason);}
static void disable_init(struct frame_disable_facts *f,uint32_t site)
{zero_bytes(f,sizeof(*f));f->site=site;f->known_mask=FD_K_SITE;}
void frame_native_disable(void)
{struct frame_disable_facts f;disable_init(&f,FD_SITE_EXTERNAL);disable_facts(&f,FD_EXTERNAL);}
static uint32_t read_data32(uint32_t address,uint32_t *out)
{
 if(!out||!frame_memory_data_span(address,4u))return 0u;
#ifdef FRAME_NATIVE_HOST
 return frame_native_host_read(address,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)address;return 1u;
#endif
}
static uint32_t read_stack32(uint32_t sp,uint32_t extent,uint32_t offset,uint32_t *out)
{
 if(!out||!frame_memory_stack_offset_span(sp,extent,offset,4u))return 0u;
 uint32_t address=sp+offset;
#ifdef FRAME_NATIVE_HOST
 return frame_native_host_read(address,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)address;return 1u;
#endif
}
static uint32_t write_stack32(uint32_t sp,uint32_t extent,uint32_t offset,uint32_t value)
{
 if(!frame_memory_stack_offset_span(sp,extent,offset,4u))return 0u;
 uint32_t address=sp+offset;
#ifdef FRAME_NATIVE_HOST
 return frame_native_host_write(address,value);
#else
 *(volatile uint32_t *)(uintptr_t)address=value;return 1u;
#endif
}
static uint32_t cpu_sp(const struct frame_native_cpu *cpu)
{uint32_t p=private_address(cpu);return p>UINT32_MAX-(uint32_t)sizeof(*cpu)?0u:p+(uint32_t)sizeof(*cpu);}
static uint32_t slot_pointer(uint32_t index,uint32_t *pointer)
{return index<FRAME_TAG_SLOT_COUNT&&read_data32(FN_DTS_TABLE+index*4u,pointer)&&frame_memory_data_span(*pointer,FN_DTS_BYTES);}
static uint32_t resolve_slot(uint32_t pointer,uint32_t *index)
{
 uint32_t matches=0u,found=0u;
 if(pointer==FN_DTS_DEFAULT||!frame_memory_data_span(pointer,FN_DTS_BYTES))return 0u;
 for(uint32_t i=0;i<FRAME_TAG_SLOT_COUNT;i++){
  uint32_t p;if(!read_data32(FN_DTS_TABLE+i*4u,&p))return 0u;
  if(p==pointer){matches++;found=i;}
 }
 if(matches!=1u)return 0u;
 *index=found;return 1u;
}
static uint32_t context_tag(uint32_t pointer,struct frame_tag_snapshot *tag,uint32_t *frame)
{
 uint32_t index;
 return resolve_slot(pointer,&index)&&read_data32(pointer,frame)&&
  frame_tags_read(&frame_native_tags,index,pointer,*frame,tag);
}
static uint32_t source_stable(const struct frame_native_copy_scope *s)
{
 uint32_t now;
 if(!s->source_known)return 0u;
 if(s->source.index!=FRAME_TAG_DEFAULT&&
  (!read_data32(s->source_pointer,&now)||now!=s->source_frame))return 0u;
 return frame_tags_same(&frame_native_tags,&s->source);
}
void frame_native_copy_scope_init(struct frame_native_copy_scope *s,uint32_t native_sp,uint32_t continuation,uint32_t kind)
{
 s->marker=COPY_MARKER;s->self=private_address(s);s->kind=kind;s->native_sp=native_sp;
 s->continuation=continuation;s->started=0u;s->source_known=0u;s->lease.active=0u;
}
static uint32_t copy_scope_valid(struct frame_native_copy_scope *s,uint32_t native_sp,uint32_t site,struct frame_disable_facts *f)
{
 uint32_t refresh=site==FN_COPY_REFRESH;
 uint32_t frame=refresh?FN_REFRESH_FRAME:FN_SNAPSHOT_FRAME,saved;
 uint32_t continuation=(uint32_t)(uintptr_t)(refresh?frame_native_refresh_continue:frame_native_snapshot_continue);
 /* The original saved native return is checked BEFORE following a possible
  * private-scope pointer above Canon's unwrapped stack frame. */
 f->native_sp=native_sp;f->scope_ptr=private_address(s);f->expected_saved_lr=continuation;
 f->known_mask|=FD_K_NATIVE_SP|FD_K_SCOPE_PTR|FD_K_EXPECTED_SAVED_LR;
 if(!read_stack32(native_sp,frame+FN_COPY_SCOPE_BYTES+64u,frame-4u,&saved)){disable_operands(f,FD_SAVED_LR_READ,native_sp+frame-4u,4u);return 0u;}
 f->actual_saved_lr=saved;f->known_mask|=FD_K_ACTUAL_SAVED_LR;
 if(saved!=continuation){disable_operands(f,FD_SAVED_LR_MISMATCH,saved,continuation);return 0u;}
 if(!s||private_address(s)!=native_sp+frame){disable_operands(f,FD_SCOPE_POINTER,private_address(s),native_sp+frame);return 0u;}
 f->marker=s->marker;f->known_mask|=FD_K_MARKER;
 if(f->marker!=COPY_MARKER){disable_operands(f,FD_SCOPE_MARKER,f->marker,COPY_MARKER);return 0u;}
 f->self=s->self;f->known_mask|=FD_K_SELF;
 if(f->self!=private_address(s)){disable_operands(f,FD_SCOPE_SELF,f->self,private_address(s));return 0u;}
 if(s->native_sp!=native_sp){disable_operands(f,FD_SCOPE_NATIVE_SP,s->native_sp,native_sp);return 0u;}
 if(s->continuation!=continuation){disable_operands(f,FD_SCOPE_CONTINUATION,s->continuation,continuation);return 0u;}
 f->kind=s->kind;f->known_mask|=FD_K_KIND;
 if(f->kind!=(refresh?FN_COPY_REFRESH:FN_COPY_DEFAULT)){
  disable_operands(f,FD_SCOPE_KIND,f->kind,refresh?FN_COPY_REFRESH:FN_COPY_DEFAULT);return 0u;
 }
 return 1u;
}
/* Same short-circuit reads as slot_pointer, retaining only observed operands.
 * Three consecutive reason IDs distinguish domain/read/range failures. */
static uint32_t observed_slot(uint32_t index,uint32_t *pointer,struct frame_disable_facts *f,uint32_t first)
{
 if(index>=FRAME_TAG_SLOT_COUNT){f->aux_a=index;f->aux_b=FRAME_TAG_SLOT_COUNT;goto failed;}
 if(!read_data32(FN_DTS_TABLE+index*4u,pointer)){
  f->aux_a=FN_DTS_TABLE+index*4u;f->aux_b=4u;first++;goto failed;
 }
 f->selected_destination=*pointer;f->known_mask|=FD_K_SELECTED_DESTINATION;
 if(!frame_memory_data_span(*pointer,FN_DTS_BYTES)){f->aux_a=*pointer;f->aux_b=FN_DTS_BYTES;first+=2u;goto failed;}
 return 0u;
failed:f->known_mask|=FD_K_AUX_A|FD_K_AUX_B;return first;
}
void frame_native_copy_begin(struct frame_native_copy_scope *s,const struct frame_native_cpu *cpu,uint32_t native_sp,uint32_t site)
{
 uint32_t role=site==FN_COPY_REFRESH?FN_ROLE_REFRESH:(site==FN_COPY_PREVIOUS?FN_ROLE_PREVIOUS:FN_ROLE_DEFAULT);
 frame_native_trace_enter(role);
 struct frame_disable_facts f;disable_init(&f,site==FN_COPY_REFRESH?FD_SITE_COPY_REFRESH:(site==FN_COPY_PREVIOUS?FD_SITE_COPY_PREVIOUS:FD_SITE_COPY_DEFAULT));
 if(!copy_scope_valid(s,native_sp,site,&f)||!cpu)return;
 if(s->started){disable_operands(&f,FD_DUPLICATE_BEGIN,s->started,0u);return;}
 if(!frame_native_enabled())return;
 uint32_t index,pointer,current,source_index,source_frame=0u,reason;
 if(cpu->r[4]!=FN_DTS_CONTROL){disable_operands(&f,FD_BEGIN_CONTROL_REGISTER,cpu->r[4],FN_DTS_CONTROL);return;}
 if(cpu->r[2]!=FN_DTS_BYTES){disable_operands(&f,FD_BEGIN_SIZE_REGISTER,cpu->r[2],FN_DTS_BYTES);return;}
 if(site==FN_COPY_REFRESH){
  if(cpu->r[6]!=FN_DTS_TABLE){disable_operands(&f,FD_BEGIN_TABLE_REGISTER,cpu->r[6],FN_DTS_TABLE);return;}
  if(!read_data32(FN_DTS_CONTROL+0x1Cu,&index)){disable_operands(&f,FD_BEGIN_INDEX_READ,FN_DTS_CONTROL+0x1Cu,4u);return;}
  f.current_index=index;f.known_mask|=FD_K_CURRENT_INDEX;
  if((reason=observed_slot(index,&pointer,&f,FD_BEGIN_SLOT_INDEX))){disable_facts(&f,reason);return;}
  if(pointer!=cpu->r[0]){disable_operands(&f,FD_BEGIN_DESTINATION,cpu->r[0],pointer);return;}
  if(cpu->r[1]!=FN_DTS_DEFAULT){disable_operands(&f,FD_BEGIN_SOURCE,cpu->r[1],FN_DTS_DEFAULT);return;}
 }else{
  index=cpu->r[0];f.argument_index=index;f.known_mask|=FD_K_ARGUMENT_INDEX;
  if(cpu->r[5]!=FN_DTS_TABLE){disable_operands(&f,FD_BEGIN_TABLE_REGISTER,cpu->r[5],FN_DTS_TABLE);return;}
  if(!read_data32(FN_DTS_CONTROL+0x18u,&current)){disable_operands(&f,FD_BEGIN_INDEX_READ,FN_DTS_CONTROL+0x18u,4u);return;}
  f.current_index=current;f.known_mask|=FD_K_CURRENT_INDEX;
  if(current!=index){disable_operands(&f,FD_BEGIN_INDEX_MISMATCH,current,index);return;}
  if((reason=observed_slot(index,&pointer,&f,FD_BEGIN_SLOT_INDEX))){disable_facts(&f,reason);return;}
 }
 /* Own the destination interval even when source identity is unknown. */
 s->started=site;s->destination=pointer;s->destination_index=index;s->source_pointer=cpu->r[1];s->source_known=0u;
 s->preserved_frame=site==FN_COPY_REFRESH?cpu->r[5]:0u;
 if(site==FN_COPY_PREVIOUS){
  uint32_t previous;
  if(read_data32(FN_DTS_CONTROL+0x1Cu,&previous)&&slot_pointer(previous,&current)&&current==cpu->r[1]&&
   resolve_slot(current,&source_index)&&source_index==previous&&read_data32(current,&source_frame))
   s->source_known=frame_tags_read(&frame_native_tags,source_index,current,source_frame,&s->source);
 }else if(cpu->r[1]==FN_DTS_DEFAULT)
  s->source_known=frame_tags_read(&frame_native_tags,FRAME_TAG_DEFAULT,FN_DTS_DEFAULT,0u,&s->source);
 s->source_frame=source_frame;
 /* Begin's barrier precedes every canonical write in the displaced memcpy. */
 (void)frame_tags_begin(&frame_native_tags,index,&s->lease);
}
void frame_native_copy_publish(struct frame_native_copy_scope *s,const struct frame_native_cpu *cpu,uint32_t native_sp,uint32_t site)
{
 struct frame_disable_facts f;disable_init(&f,site==FN_COPY_REFRESH?FD_SITE_PUBLISH_REFRESH:FD_SITE_PUBLISH_SNAPSHOT);
 if(!copy_scope_valid(s,native_sp,site,&f)||!cpu)return;
 if(!s->started){if(frame_native_enabled())disable_operands(&f,FD_PUBLISH_WITHOUT_BEGIN,0u,1u);return;}
 uint32_t pointer=0u,index=0u,frame=0u,reason=0u,actual=0u,expected=0u;
 if(site==FN_COPY_REFRESH){
  uint32_t restored;
  if(s->started!=FN_COPY_REFRESH){reason=FD_PUBLISH_STARTED;actual=s->started;expected=1u<<FN_COPY_REFRESH;goto invalid;}
  if(cpu->r[4]!=FN_DTS_CONTROL){reason=FD_PUBLISH_CONTROL_REGISTER;actual=cpu->r[4];expected=FN_DTS_CONTROL;goto invalid;}
  f.recorded_destination=s->destination;f.selected_destination=cpu->r[0];f.known_mask|=FD_K_RECORDED_DESTINATION|FD_K_SELECTED_DESTINATION;
  if(cpu->r[0]!=s->destination){reason=FD_PUBLISH_DESTINATION;actual=cpu->r[0];expected=s->destination;goto invalid;}
  if(cpu->r[5]!=s->preserved_frame){reason=FD_PUBLISH_PRESERVED_FRAME;actual=cpu->r[5];expected=s->preserved_frame;goto invalid;}
  if(!read_data32(s->destination,&restored)){reason=FD_PUBLISH_FRAME_READ;actual=s->destination;expected=4u;goto invalid;}
  if(restored!=s->preserved_frame){reason=FD_PUBLISH_RESTORED_FRAME;actual=restored;expected=s->preserved_frame;goto invalid;}
  pointer=cpu->r[0];frame=cpu->r[5];
  /* The displaced STR has already restored r5 under the active lease. */
 }else{
  index=cpu->r[0];f.argument_index=index;f.known_mask|=FD_K_ARGUMENT_INDEX;
  if(s->started!=FN_COPY_DEFAULT&&s->started!=FN_COPY_PREVIOUS){reason=FD_PUBLISH_STARTED;actual=s->started;expected=(1u<<FN_COPY_DEFAULT)|(1u<<FN_COPY_PREVIOUS);goto invalid;}
  if(cpu->r[4]!=FN_DTS_CONTROL){reason=FD_PUBLISH_CONTROL_REGISTER;actual=cpu->r[4];expected=FN_DTS_CONTROL;goto invalid;}
  if(cpu->r[5]!=FN_DTS_TABLE){reason=FD_PUBLISH_TABLE_REGISTER;actual=cpu->r[5];expected=FN_DTS_TABLE;goto invalid;}
  if((reason=observed_slot(index,&pointer,&f,FD_PUBLISH_SLOT_INDEX))){actual=f.aux_a;expected=f.aux_b;goto invalid;}
  f.recorded_destination=s->destination;f.known_mask|=FD_K_RECORDED_DESTINATION;
  if(pointer!=s->destination){reason=FD_PUBLISH_DESTINATION;actual=pointer;expected=s->destination;goto invalid;}
  if(!read_data32(pointer,&frame)){reason=FD_PUBLISH_FRAME_READ;actual=pointer;expected=4u;goto invalid;}
 }
 uint32_t revision=s->lease.revision,known=frame_native_enabled()&&source_stable(s);
 frame_tags_end_copy(&s->lease,pointer,frame,s->source_known?&s->source:NULL,known);
 uint32_t facts[FRAME_NATIVE_FACT_WORDS];zero_bytes(facts,sizeof(facts));
 uint32_t role=s->started==FN_COPY_REFRESH?FN_ROLE_REFRESH:(s->started==FN_COPY_PREVIOUS?FN_ROLE_PREVIOUS:FN_ROLE_DEFAULT);
 facts[FN_T_ROLE]=role;facts[FN_T_SITE]=s->started;facts[FN_T_NATIVE_SP]=native_sp;
 facts[FN_T_SOURCE]=s->source_pointer;facts[FN_T_DESTINATION]=pointer;facts[FN_T_FRAME]=frame;
 facts[FN_T_RAW]=s->source_known?s->source.raw:0u;facts[FN_T_REVISION]=revision;
 facts[FN_T_INDEX]=s->destination_index;facts[FN_T_KNOWN]=known;facts[FN_T_SOURCE_FRAME]=s->source_frame;
 facts[FN_T_SOURCE_REVISION]=s->source_known?s->source.revision:0u;facts[FN_T_ENABLED]=frame_native_enabled();
 struct frame_tag_snapshot check;
 uint32_t ready=frame_tags_read(&frame_native_tags,s->destination_index,pointer,frame,&check)&&check.revision==revision;
 frame_native_trace_observe(role,facts,ready);if(!ready)frame_native_trace_reject(role);
 s->started=0u;
 return;
invalid:
 disable_operands(&f,reason,actual,expected);frame_tags_end_unknown(&s->lease);s->started=0u;
}
void frame_native_copy_scope_end(struct frame_native_copy_scope *s)
{
 struct frame_disable_facts f;disable_init(&f,FD_SITE_COPY_END);f.scope_ptr=private_address(s);f.known_mask|=FD_K_SCOPE_PTR;
 if(!s){disable_operands(&f,FD_END_SCOPE_POINTER,0u,1u);return;}
 f.marker=s->marker;f.known_mask|=FD_K_MARKER;
 if(f.marker!=COPY_MARKER){disable_operands(&f,FD_END_SCOPE_MARKER,f.marker,COPY_MARKER);return;}
 f.self=s->self;f.known_mask|=FD_K_SELF;
 if(f.self!=private_address(s)){disable_operands(&f,FD_END_SCOPE_SELF,f.self,private_address(s));return;}
 if(s->started){frame_tags_end_unknown(&s->lease);s->started=0u;}
 s->marker=0u;
}
void frame_native_reset_begin(struct frame_native_reset_scope *s)
{
 frame_native_trace_enter(FN_ROLE_RESET);s->marker=RESET_MARKER;s->self=private_address(s);
 frame_tags_reset_begin(&frame_native_tags,&s->reset);
}
void frame_native_reset_end(struct frame_native_reset_scope *s)
{
 struct frame_disable_facts facts;disable_init(&facts,FD_SITE_RESET_END);facts.scope_ptr=private_address(s);facts.known_mask|=FD_K_SCOPE_PTR;
 if(!s){disable_operands(&facts,FD_RESET_SCOPE_POINTER,0u,1u);return;}
 facts.marker=s->marker;facts.known_mask|=FD_K_MARKER;
 if(facts.marker!=RESET_MARKER){disable_operands(&facts,FD_RESET_SCOPE_MARKER,facts.marker,RESET_MARKER);return;}
 facts.self=s->self;facts.known_mask|=FD_K_SELF;
 if(facts.self!=private_address(s)){disable_operands(&facts,FD_RESET_SCOPE_SELF,facts.self,private_address(s));return;}
 frame_tags_reset_end(&s->reset);s->marker=0u;
 uint32_t f[FRAME_NATIVE_FACT_WORDS];zero_bytes(f,sizeof(f));f[FN_T_ROLE]=FN_ROLE_RESET;f[FN_T_ENABLED]=frame_native_enabled();
 frame_native_trace_observe(FN_ROLE_RESET,f,1u);
}
uint32_t frame_native_default_adapter(uint32_t offset,uint32_t second,uint32_t source,const struct frame_native_cpu *cpu)
{
 frame_native_trace_enter(FN_ROLE_CAPTURE);
 uint32_t length=cpu->r[3],end=offset+length;
 uint32_t overlaps=length&&((end<offset)||(offset<0xA8u&&end>4u));
 if(!overlaps||!frame_native_enabled())return frame_native_original_default(offset,second,source,length);
 struct frame_tag_lease lease;(void)frame_tags_begin(&frame_native_tags,FRAME_TAG_DEFAULT,&lease);
 struct frame_native_capture_facts f;zero_bytes(&f,sizeof(f));f.entry_lr=cpu->lr;f.offset=offset;f.second_offset=second;
 f.source=source;f.length=length;f.original_r4=cpu->r[4];
 uint32_t identified=(f.entry_lr==FN_CAPTURE_LR_A||f.entry_lr==FN_CAPTURE_LR_B)&&offset==4u&&second==4u&&length==FN_MODE_STRIDE&&
  frame_memory_data_span(f.original_r4,FN_RAW_OFFSET+4u)&&read_data32(f.original_r4+FN_MODE_INDEX,&f.index)&&f.index<FN_MODE_COUNT&&
  source==f.original_r4+FN_MODE_BASE+f.index*FN_MODE_STRIDE&&read_data32(f.original_r4+FN_RAW_OFFSET,&f.raw_before)&&
  read_data32(FN_DTS_CONTROL+0x20u,&f.writer_count)&&f.writer_count;
 uint32_t original=frame_native_original_default(offset,second,source,length);
 uint32_t known=identified&&read_data32(f.original_r4+FN_RAW_OFFSET,&f.raw_after)&&
  read_data32(f.original_r4+FN_MODE_INDEX,&f.index_after)&&
  read_data32(FN_DTS_CONTROL+0x20u,&f.writer_count_after)&&frame_native_capture_admit(&f);
 uint32_t revision=lease.revision;
 frame_tags_end_capture(&lease,FN_DTS_DEFAULT,f.raw_before,known);
 if(identified){
  struct frame_tag_snapshot check;
  uint32_t ready=frame_tags_read(&frame_native_tags,FRAME_TAG_DEFAULT,FN_DTS_DEFAULT,0u,&check)&&check.revision==revision;
  frame_native_trace_observe(FN_ROLE_CAPTURE,&f,ready);if(!ready)frame_native_trace_reject(FN_ROLE_CAPTURE);
 }else frame_native_trace_reject(FN_ROLE_CAPTURE);
 return original;
}
static uint32_t read_data_words(uint32_t pointer,uint32_t *out,uint32_t count)
{
 if(!frame_memory_data_span(pointer,count*4u))return 0u;
 for(uint32_t i=0;i<count;i++)if(!read_data32(pointer+i*4u,&out[i]))return 0u;
 return 1u;
}
static uint32_t read_stack_words(uint32_t sp,uint32_t extent,uint32_t offset,uint32_t *out,uint32_t count)
{
 if(count>UINT32_MAX/4u||!frame_memory_stack_offset_span(sp,extent,offset,count*4u))return 0u;
 for(uint32_t i=0;i<count;i++)if(!read_stack32(sp,extent,offset+i*4u,&out[i]))return 0u;
 return 1u;
}
static uint32_t descriptor_context(uint32_t pointer,uint32_t *index,uint32_t *context)
{
 uint32_t observed;
 return descriptor_index(pointer,index)&&read_data32(pointer+4u,&observed)&&observed==*index&&
  read_data32(pointer+0x21Cu,context);
}
static uint32_t common_context(struct frame_native_facts *f,struct frame_tag_snapshot *tag)
{
 if(!context_tag(f->context,tag,&f->native_frame))return 0u;
 f->slot_index=tag->index;f->tag_pointer=tag->pointer;f->tag_frame=tag->frame;f->tag_raw=tag->raw;
 f->tag_revision=tag->revision;f->tag_ready=1u;f->tag_same=1u;
 uint32_t p0,p1;
 return read_data32(f->context+4u,&f->mode)&&read_data32(f->context+0x20u,&f->rich)&&
  read_data32(f->context+0x38u,&f->flags38)&&read_data32(f->context+0xEB0u,&f->flags_eb0)&&
  read_data32(f->context+0x424u,&f->quality)&&read_data32(f->context+0x410u,&f->flag410)&&
  read_data32(f->context+0x418u,&f->flag418)&&read_data32(f->context+0xACu,&f->display_mask)&&
  read_data32(FN_PROFILE_FLAGS+0x4Cu,&p0)&&read_data32(FN_PROFILE_FLAGS+0x50u,&p1)&&
  ((f->prohibit=p0|p1),1u);
}
static uint32_t row_context(uint32_t output,uint32_t context,uint32_t display,const struct frame_native_cpu *cpu,
 struct frame_native_facts *f,struct frame_tag_snapshot *tag)
{
 uint32_t sp=cpu_sp(cpu);
 /* Source LR/output/callee-saved arguments identify the original DevPath
  * frame before descriptor/context pointers are followed. */
 if(cpu->lr!=FN_ROW_LR||(display!=1u&&display!=0x10u)||(sp&7u)||!frame_memory_stack_frame_span(sp,FN_ROW_FRAME_BYTES)||
  output!=sp+FN_ROW_OUTPUT||cpu->r[5]!=context||cpu->r[8]!=display)return 0u;
 f->role=FN_ROLE_ROW;f->entry_lr=cpu->lr;f->native_sp=sp;f->output_x=output;f->context=context;f->display=display;
 if(!read_stack32(sp,FN_ROW_FRAME_BYTES,FN_ROW_OUTER_RETURN,&f->outer_lr)||f->outer_lr!=FN_ROW_OUTER_LR)return 0u;
 f->descriptor=cpu->r[4];
 if(!descriptor_context(f->descriptor,&f->descriptor_index,&f->descriptor_context)||f->descriptor_context!=context)return 0u;
 return common_context(f,tag);
}
static uint32_t registered_packet(uint32_t pointer)
{
 uint32_t matches=0u;
 if(!frame_memory_data_span(pointer,0x2C8u))return 0u;
 for(uint32_t i=0;i<FN_PACKET_COUNT;i++){uint32_t p;if(!read_data32(FN_PACKET_TABLE+i*4u,&p))return 0u;if(p==pointer)matches++;}
 return matches==1u;
}
static uint32_t offset_context(uint32_t x,uint32_t y,uint32_t display,const struct frame_native_cpu *cpu,
 struct frame_native_facts *f,struct frame_tag_snapshot *tag)
{
 uint32_t sp=cpu_sp(cpu);
 if(cpu->lr!=FN_OFFSET_LR||(display!=1u&&display!=0x10u)||(sp&7u)||!frame_memory_stack_frame_span(sp,FN_OFFSET_FRAME_BYTES)||
  x!=sp+FN_OFFSET_X||y!=sp+FN_OFFSET_Y)return 0u;
 f->role=FN_ROLE_OFFSET;f->entry_lr=cpu->lr;f->native_sp=sp;f->output_x=x;f->output_y=y;f->display=display;
 static const uint32_t offsets[4]={FN_OFFSET_RET0,FN_OFFSET_RET1,FN_OFFSET_RET2,FN_OFFSET_RET3};
 static const uint32_t lcd_returns[4]={FN_OFFSET_LR0,FN_OFFSET_LR1,FN_OFFSET_LR2,FN_OFFSET_LR3};
 static const uint32_t evf_returns[4]={FN_OFFSET_EVF_LR0,FN_OFFSET_EVF_LR1,FN_OFFSET_LR2,FN_OFFSET_LR3};
 const uint32_t *returns=display==0x10u?evf_returns:lcd_returns;
 for(uint32_t i=0;i<4u;i++)if(!read_stack32(sp,FN_OFFSET_FRAME_BYTES,offsets[i],&f->saved_returns[i])||f->saved_returns[i]!=returns[i])return 0u;
 if(!read_stack32(sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_DESCRIPTOR,&f->descriptor)||
  !descriptor_context(f->descriptor,&f->descriptor_index,&f->descriptor_context))return 0u;
 if(!read_stack32(sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_PACKET,&f->packet)||!registered_packet(f->packet)||
  !read_stack32(sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_PREFIX,&f->prefix)||!read_data32(FN_ORCA_SLOT,&f->orca_owner)||
  !frame_memory_data_span(f->orca_owner,0x1D4u)||
  f->prefix!=f->orca_owner+(display==0x10u?FN_OFFSET_EVF_PREFIX:FN_OFFSET_LCD_PREFIX))return 0u;
 uint32_t owner_mode;
 if(!read_data32(f->orca_owner+0x10u,&owner_mode)||owner_mode!=0u||
  !read_data32(f->packet+8u,&f->packet_token_low)||!read_data32(f->packet+12u,&f->packet_token_high)||
  !read_data32(f->descriptor+0x20u,&f->descriptor_token_low)||!read_data32(f->descriptor+0x24u,&f->descriptor_token_high)||
  !read_stack32(sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_TOKEN,&f->outer_token_low)||!read_stack32(sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_TOKEN+4u,&f->outer_token_high)||
  f->packet_token_low!=f->descriptor_token_low||f->packet_token_high!=f->descriptor_token_high||
  f->outer_token_low!=f->descriptor_token_low||f->outer_token_high!=f->descriptor_token_high)return 0u;
 uint32_t packet_mask;
 if(!read_data32(f->packet+0x10u,&packet_mask)||!read_data32(f->orca_owner+0x1C8u,&f->orca_mode)||
  !read_data32(f->orca_owner+0x1CCu,&f->orca_aspect))return 0u;
 if(display==0x10u){if((packet_mask&0x10u)!=0x10u)return 0u;f->packet_display=0x10u;}
 else f->packet_display=packet_mask&15u;
 f->context=f->descriptor_context;
 return common_context(f,tag)&&read_data_words(f->orca_owner+(display==0x10u?FN_OFFSET_EVF_ROW:FN_OFFSET_LCD_ROW),f->row,12u);
}
static void revalidate_tag(struct frame_native_facts *f,const struct frame_tag_snapshot *tag)
{
 uint32_t frame;
 f->tag_same=frame_native_enabled()&&frame_tags_same(&frame_native_tags,tag)&&
  read_data32(f->context,&frame)&&frame==f->native_frame;
}
static void observe_binding(const struct frame_native_facts *f,uint32_t applied)
{
 frame_native_trace_observe(f->role,f,applied);if(!applied)frame_native_trace_reject(f->role);
}
static void disable_write(uint32_t site,uint32_t native_sp,uint32_t address,uint32_t value,uint32_t reason)
{
 struct frame_disable_facts f;disable_init(&f,site);f.native_sp=native_sp;f.known_mask|=FD_K_NATIVE_SP;
 disable_operands(&f,reason,address,value);
}
uint32_t frame_native_row_adapter(uint32_t output,uint32_t context,uint32_t display,const struct frame_native_cpu *cpu)
{
 frame_native_trace_enter(FN_ROLE_ROW);struct frame_native_facts f;zero_bytes(&f,sizeof(f));struct frame_tag_snapshot tag;
 uint32_t identified=frame_native_enabled()&&cpu&&row_context(output,context,display,cpu,&f,&tag);
 uint32_t original=frame_native_original_row(output,context,display);
 if(!identified){frame_native_trace_reject(FN_ROLE_ROW);return original;}
 f.original_return=original;
 if(!read_stack_words(f.native_sp,FN_ROW_FRAME_BYTES,FN_ROW_OUTPUT,f.row,12u)){frame_native_trace_reject(FN_ROLE_ROW);return original;}
 revalidate_tag(&f,&tag);uint32_t out[12];
 for(uint32_t i=0;i<12u;i++)out[i]=f.row[i];
 uint32_t status=frame_native_project(&f,out),applied=0u;
 if(status==1u){
  /* Only caller-owned row fields are changed; native r0 is row word+0x14. */
  uint32_t address=output+0x1Cu,value=out[7];applied=write_stack32(f.native_sp,FN_ROW_FRAME_BYTES,address-f.native_sp,value);
  if(applied){address=output+0x24u;value=out[9];applied=write_stack32(f.native_sp,FN_ROW_FRAME_BYTES,address-f.native_sp,value);}
  if(applied){address=output+0x2Cu;value=out[11];applied=write_stack32(f.native_sp,FN_ROW_FRAME_BYTES,address-f.native_sp,value);}
  if(!applied)disable_write(FD_SITE_ROW,f.native_sp,address,value,FD_ROW_WRITE);
 }
 observe_binding(&f,applied);return original;
}
uint32_t frame_native_offset_adapter(uint32_t x,uint32_t y,uint32_t display,const struct frame_native_cpu *cpu)
{
 frame_native_trace_enter(FN_ROLE_OFFSET);struct frame_native_facts f;zero_bytes(&f,sizeof(f));struct frame_tag_snapshot tag;
 uint32_t identified=frame_native_enabled()&&cpu&&offset_context(x,y,display,cpu,&f,&tag);
 uint32_t original=frame_native_original_offset(x,y,display);
 if(!identified){frame_native_trace_reject(FN_ROLE_OFFSET);return original;}
 f.original_return=original;
 if(!read_stack32(f.native_sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_X,&f.original_x)||!read_stack32(f.native_sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_Y,&f.original_y)){frame_native_trace_reject(FN_ROLE_OFFSET);return original;}
 /* The original getter's cached row is sampled again; no cache word is written. */
 uint32_t row[12];
 if(!read_data_words(f.orca_owner+(f.display==0x10u?FN_OFFSET_EVF_ROW:FN_OFFSET_LCD_ROW),row,12u)||!words_equal(row,f.row)){
  frame_native_trace_reject(FN_ROLE_OFFSET);return original;
 }
 revalidate_tag(&f,&tag);uint32_t status=frame_native_project(&f,row),applied=0u;
 if(status==1u){applied=write_stack32(f.native_sp,FN_OFFSET_FRAME_BYTES,FN_OFFSET_Y,row[11]);if(!applied)disable_write(FD_SITE_OFFSET,f.native_sp,y,row[11],FD_OFFSET_WRITE);}
 observe_binding(&f,applied);
 return applied?row[11]:original;
}
#endif
