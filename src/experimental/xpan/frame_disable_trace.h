#ifndef R5_FRAME_DISABLE_TRACE_H
#define R5_FRAME_DISABLE_TRACE_H
#include <stdint.h>
#include <stdatomic.h>

#define FRAME_DISABLE_SCHEMA 1u
#define FRAME_DISABLE_WORDS 16u
#define FD_FMT_BEGIN "R5D1 B=%u V=%u N=%u"
#define FD_FMT_SOURCE "R5D1 S=" OBSERVER_SOURCE_ID
#define FD_FMT_STATE "R5D1 Q=%u"
#define FD_FMT_WORDS "R5D1 W=%u A=%08X B=%08X C=%08X D=%08X"
#define FD_FMT_END "R5D1 E=%u"

enum frame_disable_state {FD_EMPTY=0,FD_WRITING=1,FD_READY=2};
enum frame_disable_site {
 FD_SITE_EXTERNAL=0,FD_SITE_COPY_DEFAULT,FD_SITE_COPY_PREVIOUS,FD_SITE_COPY_REFRESH,
 FD_SITE_PUBLISH_SNAPSHOT,FD_SITE_PUBLISH_REFRESH,FD_SITE_COPY_END,
 FD_SITE_RESET_END,FD_SITE_ROW,FD_SITE_OFFSET,FD_SITE_COUNT
};
enum frame_disable_reason {
 FD_NONE=0,FD_EXTERNAL,FD_SAVED_LR_READ,FD_SAVED_LR_MISMATCH,
 FD_SCOPE_POINTER,FD_SCOPE_MARKER,FD_SCOPE_SELF,FD_SCOPE_NATIVE_SP,
 FD_SCOPE_CONTINUATION,FD_SCOPE_KIND,FD_DUPLICATE_BEGIN,
 FD_BEGIN_CONTROL_REGISTER,FD_BEGIN_SIZE_REGISTER,FD_BEGIN_TABLE_REGISTER,
 FD_BEGIN_INDEX_READ,FD_BEGIN_INDEX_MISMATCH,FD_BEGIN_SLOT_INDEX,
 FD_BEGIN_SLOT_READ,FD_BEGIN_SLOT_POINTER,FD_BEGIN_DESTINATION,FD_BEGIN_SOURCE,
 FD_PUBLISH_WITHOUT_BEGIN,FD_PUBLISH_STARTED,FD_PUBLISH_CONTROL_REGISTER,
 FD_PUBLISH_TABLE_REGISTER,FD_PUBLISH_SLOT_INDEX,FD_PUBLISH_SLOT_READ,
 FD_PUBLISH_SLOT_POINTER,FD_PUBLISH_DESTINATION,FD_PUBLISH_FRAME_READ,
 FD_PUBLISH_PRESERVED_FRAME,FD_PUBLISH_RESTORED_FRAME,
 FD_END_SCOPE_POINTER,FD_END_SCOPE_MARKER,FD_END_SCOPE_SELF,
 FD_RESET_SCOPE_POINTER,FD_RESET_SCOPE_MARKER,FD_RESET_SCOPE_SELF,
 FD_ROW_WRITE,FD_OFFSET_WRITE,FD_REASON_COUNT
};
enum frame_disable_word {
 FD_W_REASON=0,FD_W_SITE,FD_W_NATIVE_SP,FD_W_SCOPE_PTR,FD_W_ACTUAL_SAVED_LR,
 FD_W_EXPECTED_SAVED_LR,FD_W_ARGUMENT_INDEX,FD_W_CURRENT_INDEX,
 FD_W_RECORDED_DESTINATION,FD_W_SELECTED_DESTINATION,FD_W_MARKER,FD_W_SELF,
 FD_W_KIND,FD_W_KNOWN_MASK,FD_W_AUX_A,FD_W_AUX_B
};
#define FD_K_REASON (1u<<FD_W_REASON)
#define FD_K_SITE (1u<<FD_W_SITE)
#define FD_K_NATIVE_SP (1u<<FD_W_NATIVE_SP)
#define FD_K_SCOPE_PTR (1u<<FD_W_SCOPE_PTR)
#define FD_K_ACTUAL_SAVED_LR (1u<<FD_W_ACTUAL_SAVED_LR)
#define FD_K_EXPECTED_SAVED_LR (1u<<FD_W_EXPECTED_SAVED_LR)
#define FD_K_ARGUMENT_INDEX (1u<<FD_W_ARGUMENT_INDEX)
#define FD_K_CURRENT_INDEX (1u<<FD_W_CURRENT_INDEX)
#define FD_K_RECORDED_DESTINATION (1u<<FD_W_RECORDED_DESTINATION)
#define FD_K_SELECTED_DESTINATION (1u<<FD_W_SELECTED_DESTINATION)
#define FD_K_MARKER (1u<<FD_W_MARKER)
#define FD_K_SELF (1u<<FD_W_SELF)
#define FD_K_KIND (1u<<FD_W_KIND)
#define FD_K_AUX_A (1u<<FD_W_AUX_A)
#define FD_K_AUX_B (1u<<FD_W_AUX_B)
#define FD_K_ALL 0xdfffu
/* An unset known bit means unobserved, even if the corresponding word is0.
 * AUX_A/B are actual/expected operands of the named failing predicate. Read
 * failures use AUX_A=attempted address, AUX_B=required byte count. Pointer-range
 * failures use actual pointer and required object size. For PUBLISH_STARTED,
 * AUX_B is a bitmask of admitted started kinds (6 snapshot,8 refresh). Write
 * failures use destination address/attempted value; slot-index failures use
 * actual index/exclusive bound. No pointer is followed by the recorder or
 * reporter. */
struct frame_disable_facts {
 uint32_t reason,site,native_sp,scope_ptr,actual_saved_lr,expected_saved_lr;
 uint32_t argument_index,current_index,recorded_destination,selected_destination;
 uint32_t marker,self,kind,known_mask,aux_a,aux_b;
};
struct frame_disable_record {_Atomic uint32_t state;struct frame_disable_facts facts;};
struct frame_disable_binding_profile {
 uint32_t schema,words,facts_bytes,record_bytes,record_alignment,state_offset,
 facts_offset,ready_state,known_all,reason_count,site_count;
 uint32_t field_words[FRAME_DISABLE_WORDS];
};
extern const struct frame_disable_binding_profile frame_disable_profile;
extern struct frame_disable_record frame_disable_trace;
uint32_t frame_disable_record_claim(struct frame_disable_record *,const struct frame_disable_facts *);
uint32_t frame_disable_record_snapshot(const struct frame_disable_record *,struct frame_disable_facts *);
void frame_disable_claim(const struct frame_disable_facts *);
uint32_t frame_disable_snapshot(struct frame_disable_facts *);
void frame_disable_report(unsigned sequence);
#endif
