#include "q_label.h"
#include "frame_memory.h"
const char q_label_text[QL_TEXT_BYTES]="XPan";
const struct q_label_binding_profile q_label_profile={
 QL_SCHEMA,QL_STRING_ID,QL_ENGLISH_POINTER,QL_TEXT_BYTES,
 QL_PROPERTY_CALLER,QL_PROPERTY_OUTER,QL_PROPERTY_FRAME,QL_PROPERTY_RETURN_OFFSET,
 QL_PROPERTY_KIND_OFFSET,QL_PROPERTY_KIND,QL_PROPERTY_ID_OFFSET,QL_PROPERTY_ITEM_OFFSET,QL_PROPERTY_ITEM,
 QL_WIDTH_CALLER,QL_WIDTH_OUTER,QL_WIDTH_FRAME,QL_WIDTH_RETURN_OFFSET,QL_WIDTH_ID_OFFSET,QL_ITEM_KEY
};
uint32_t q_label_candidate(uint32_t id,uint32_t lr)
{return id==QL_STRING_ID&&(lr==QL_PROPERTY_CALLER||lr==QL_WIDTH_CALLER);}
uint32_t q_label_admit(const struct q_label_facts *f)
{
 if(!f||!q_label_candidate(f->id,f->entry_lr)||f->pointer!=QL_ENGLISH_POINTER||
  (f->native_sp&7u)||f->saved_id!=QL_STRING_ID||f->item_key!=QL_ITEM_KEY)return 0u;
 if(f->entry_lr==QL_PROPERTY_CALLER)
  return frame_memory_stack_frame_span(f->native_sp,QL_PROPERTY_FRAME)&&
   f->outer_lr==QL_PROPERTY_OUTER&&f->property==QL_PROPERTY_KIND&&f->item==QL_PROPERTY_ITEM;
 return frame_memory_stack_frame_span(f->native_sp,QL_WIDTH_FRAME)&&f->outer_lr==QL_WIDTH_OUTER;
}
#ifndef Q_LABEL_POLICY_ONLY
#ifndef Q_LABEL_HOST
_Static_assert(sizeof(uintptr_t)==4u,"native Q string pointers");
#endif
static uint32_t stack_word(uint32_t sp,uint32_t extent,uint32_t offset,uint32_t *out)
{
 if(!out||!frame_memory_stack_offset_span(sp,extent,offset,4u))return 0u;
#ifdef Q_LABEL_HOST
 extern uint32_t q_label_host_read32(uint32_t address,uint32_t *out);
 return q_label_host_read32(sp+offset,out);
#else
 *out=*(const volatile uint32_t *)(uintptr_t)(sp+offset);return 1u;
#endif
}
uint32_t q_label_after(const struct q_label_cpu *e,uint32_t sp,uint32_t p)
{
 /* The register image belongs to this entry wrapper. Prove the immediate
  * client and complete actual-SP frame before following any Canon stack. */
 if(!e||!q_label_candidate(e->r[0],e->lr)||e->r[8]!=QL_ITEM_KEY||p!=QL_ENGLISH_POINTER||(sp&7u))return p;
 uint32_t property=e->lr==QL_PROPERTY_CALLER;
 uint32_t extent=property?QL_PROPERTY_FRAME:QL_WIDTH_FRAME;
 if(!frame_memory_stack_frame_span(sp,extent))return p;
 struct q_label_facts f={e->r[0],p,e->lr,sp,0u,0u,0u,0u,e->r[8]};
 uint32_t return_offset=property?QL_PROPERTY_RETURN_OFFSET:QL_WIDTH_RETURN_OFFSET;
 if(!stack_word(sp,extent,return_offset,&f.outer_lr)||
  f.outer_lr!=(property?QL_PROPERTY_OUTER:QL_WIDTH_OUTER))return p;
 if(property){
  if(!stack_word(sp,extent,QL_PROPERTY_KIND_OFFSET,&f.property)||
   !stack_word(sp,extent,QL_PROPERTY_ID_OFFSET,&f.saved_id)||
   !stack_word(sp,extent,QL_PROPERTY_ITEM_OFFSET,&f.item))return p;
 }else if(!stack_word(sp,extent,QL_WIDTH_ID_OFFSET,&f.saved_id))return p;
 return q_label_admit(&f)?(uint32_t)(uintptr_t)q_label_text:p;
}
#endif
