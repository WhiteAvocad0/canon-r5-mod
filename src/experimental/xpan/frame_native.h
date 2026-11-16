#ifndef R5_FRAME_NATIVE_H
#define R5_FRAME_NATIVE_H
#define FN_COPY_SCOPE_BYTES 88
#define FN_RESET_SCOPE_BYTES 344
#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stddef.h>
#include "frame_tags.h"
#include "frame_native_profile.h"
#include "frame_disable_trace.h"
#include "frame_memory.h"
#define FN_ROW_FRAME_BYTES (FN_ROW_OUTER_RETURN+4u)
#define FN_OFFSET_FRAME_BYTES (FN_OFFSET_DESCRIPTOR+4u)

/* The ASM-owned CPU image is the original r0..r12,lr. Its end is original SP.
 * Host tests supply scalar parsed facts; native extraction owns every read. */
struct frame_native_cpu {uint32_t r[13],lr;};
enum frame_native_copy_kind {FN_COPY_DEFAULT=1,FN_COPY_PREVIOUS=2,FN_COPY_REFRESH=3};
enum frame_native_role {FN_ROLE_CAPTURE=0,FN_ROLE_DEFAULT=1,FN_ROLE_PREVIOUS=2,
 FN_ROLE_REFRESH=3,FN_ROLE_RESET=4,FN_ROLE_ROW=5,FN_ROLE_OFFSET=6,FN_ROLE_COUNT=7};
#define FRAME_NATIVE_SCHEMA 1u
#define FRAME_NATIVE_FACT_WORDS 24u
#define FRAME_NATIVE_POLICY_WORDS 59u
#define FRAME_NATIVE_CAPTURE_WORDS 12u
#define FRAME_NATIVE_MAX_WORDS 59u

/* Complete caller-owned native stack objects. Leases are never copied. */
struct frame_native_copy_scope {
 _Alignas(8) uint32_t marker;
 uint32_t self,kind,native_sp,continuation,started,source_pointer,source_frame,destination,preserved_frame;
 struct frame_tag_lease lease;
 struct frame_tag_snapshot source;
 uint32_t source_known,destination_index;
};
struct frame_native_reset_scope {uint32_t marker,self;struct frame_tag_reset reset;};
#if UINTPTR_MAX==UINT32_MAX
_Static_assert(sizeof(struct frame_native_copy_scope)==FN_COPY_SCOPE_BYTES,"native copy scope ABI");
_Static_assert(sizeof(struct frame_native_reset_scope)==FN_RESET_SCOPE_BYTES,"native reset scope ABI");
#endif

/* Shared, host-testable admission facts. These describe the original borrowed
 * context, never a configuration-to-image-frame association. */
struct frame_native_facts {
 uint32_t role,entry_lr,native_sp,output_x,output_y,context,display,descriptor,descriptor_index;
 uint32_t descriptor_context,outer_lr,packet,packet_token_low,packet_token_high;
 uint32_t descriptor_token_low,descriptor_token_high,outer_token_low,outer_token_high;
 uint32_t saved_returns[4],prefix,orca_owner,orca_mode,orca_aspect,packet_display;
 uint32_t mode,rich,flags38,flags_eb0,prohibit,quality,flag410,flag418,display_mask;
 uint32_t slot_index,native_frame,tag_pointer,tag_frame,tag_raw,tag_revision,tag_ready,tag_same;
 uint32_t original_return,original_x,original_y,row[12];
};
struct frame_native_capture_facts {
 uint32_t entry_lr,offset,second_offset,source,length,original_r4,index,raw_before,raw_after,writer_count;
 uint32_t index_after,writer_count_after;
};
_Static_assert(sizeof(struct frame_native_facts)==FRAME_NATIVE_POLICY_WORDS*4u,"native policy facts ABI");
_Static_assert(sizeof(struct frame_native_capture_facts)==FRAME_NATIVE_CAPTURE_WORDS*4u,"native capture facts ABI");
struct frame_native_binding_profile {
 uint32_t schema,dts_control,dts_default,dts_table,slot_count,slot_bytes;
 uint32_t descriptors,descriptor_stride,descriptor_count,packet_table,packet_count,orca_slot;
 uint32_t capture_lr_a,capture_lr_b,row_lr,row_outer_lr,offset_lr,mode_base,mode_stride,mode_count,mode_index,raw_offset;
 uint32_t row_output,row_outer_return,offset_x,offset_y,offset_packet,offset_prefix,offset_token,offset_descriptor;
 uint32_t offset_returns[4],offset_return_values[4];
 uint32_t snapshot_frame,refresh_frame,copy_scope_bytes,reset_scope_bytes,role_count,transport_words,policy_words,capture_words;
};
enum frame_native_policy_word {
 FN_P_ROLE=offsetof(struct frame_native_facts,role)/4u,
 FN_P_ENTRY_LR=offsetof(struct frame_native_facts,entry_lr)/4u,
 FN_P_NATIVE_SP=offsetof(struct frame_native_facts,native_sp)/4u,
 FN_P_SAVED_RETURNS=offsetof(struct frame_native_facts,saved_returns)/4u,
 FN_P_TAG_RAW=offsetof(struct frame_native_facts,tag_raw)/4u,
 FN_P_ROW=offsetof(struct frame_native_facts,row)/4u
};
extern const struct frame_native_binding_profile frame_native_profile;
uint32_t frame_native_capture_admit(const struct frame_native_capture_facts *);
uint32_t frame_native_admit(const struct frame_native_facts *);
uint32_t frame_native_project(const struct frame_native_facts *,uint32_t row[12]);
uint32_t frame_native_stack_extent(uint32_t role);
uint32_t frame_native_enabled(void);
void frame_native_disable(void);

void frame_native_copy_scope_init(struct frame_native_copy_scope *,uint32_t native_sp,uint32_t continuation,uint32_t kind);
void frame_native_copy_begin(struct frame_native_copy_scope *,const struct frame_native_cpu *,uint32_t native_sp,uint32_t site);
void frame_native_copy_publish(struct frame_native_copy_scope *,const struct frame_native_cpu *,uint32_t native_sp,uint32_t site);
void frame_native_copy_scope_end(struct frame_native_copy_scope *);
void frame_native_reset_begin(struct frame_native_reset_scope *);
void frame_native_reset_end(struct frame_native_reset_scope *);
uint32_t frame_native_default_adapter(uint32_t,uint32_t,uint32_t,const struct frame_native_cpu *);
uint32_t frame_native_row_adapter(uint32_t,uint32_t,uint32_t,const struct frame_native_cpu *);
uint32_t frame_native_offset_adapter(uint32_t,uint32_t,uint32_t,const struct frame_native_cpu *);
void frame_native_report(unsigned sequence);
/* Address-binding symbols only: never ordinary C-call these native entries. */
extern void frame_native_default_entry(void),frame_native_snapshot_entry(void);
extern void frame_native_copy_default(void),frame_native_copy_previous(void);
extern void frame_native_snapshot_publish(void),frame_native_refresh_entry(void);
extern void frame_native_copy_refresh(void),frame_native_refresh_publish(void);
extern void frame_native_reset_entry(void),frame_native_row_entry(void),frame_native_offset_entry(void);
#endif
#endif
