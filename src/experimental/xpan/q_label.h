#ifndef R5_Q_LABEL_H
#define R5_Q_LABEL_H
#include "q_label_profile.h"
#ifndef __ASSEMBLER__
#include <stdint.h>
struct q_label_cpu {uint32_t r[13],lr;};
struct q_label_facts {
 uint32_t id,pointer,entry_lr,native_sp,outer_lr,property,saved_id,item,item_key;
};
struct q_label_binding_profile {
 uint32_t schema,id,english_pointer,text_bytes;
 uint32_t property_caller,property_outer,property_frame,property_return_offset;
 uint32_t property_kind_offset,property_kind,property_id_offset,property_item_offset,property_item;
 uint32_t width_caller,width_outer,width_frame,width_return_offset,width_id_offset;
 uint32_t item_key;
};
extern const struct q_label_binding_profile q_label_profile;
extern const char q_label_text[QL_TEXT_BYTES];
uint32_t q_label_candidate(uint32_t id,uint32_t entry_lr);
uint32_t q_label_admit(const struct q_label_facts *);
/* entry is the private original register image; native_sp is the actual getter
 * entry SP before our prologue. Returned pointer is from one original getter.
 * Unknown clients/ranges/fields return that original pointer unchanged. */
uint32_t q_label_after(const struct q_label_cpu *entry,uint32_t native_sp,uint32_t returned_pointer);
extern void q_label_entry(void);
extern void q_label_original_getter(void);
_Static_assert(sizeof(struct q_label_cpu)==56u,"Q CPU image ABI");
_Static_assert(sizeof(struct q_label_facts)==36u,"Q scalar facts ABI");
_Static_assert(sizeof(struct q_label_binding_profile)==76u,"Q profile ABI");
#endif
#endif
