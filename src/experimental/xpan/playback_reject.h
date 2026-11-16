#ifndef R5_PLAYBACK_REJECT_H
#define R5_PLAYBACK_REJECT_H
#include <stdint.h>
#include <stdatomic.h>

#define PLAYBACK_REJECT_SCHEMA 1u
#define PLAYBACK_REJECT_WORDS 16u
enum playback_reject_state { PNR_STATE_EMPTY=0, PNR_STATE_WRITING=1, PNR_STATE_READY=2 };
#define PLAYBACK_REJECT_FIELDS(X) \
 X(reason) X(site) X(native_sp) X(inner) X(object) X(status) X(chain) X(prefix) \
 X(offset) X(actual) X(expected0) X(expected1) X(expected2) X(aux_a) X(aux_b) X(known_mask)
enum playback_reject_role { PNR_EARLY=0, PNR_KNOWN_OBJECT=1, PNR_SCOPED=2, PNR_ROLE_COUNT=3 };
enum playback_reject_reason {
 PNR_NONE=0, PNR_INPUT, PNR_OBJECT, PNR_STACK, PNR_HOP_READ, PNR_HOP_MISMATCH,
 PNR_CONT_READ, PNR_CONT_MISMATCH, PNR_SCOPE_MARKER, PNR_SCOPE_SELF, PNR_SCOPE_SP,
 PNR_SCOPE_CONT, PNR_SCOPE_ADMITTED, PNR_SCOPE_OBJECT, PNR_SCOPE_OWNER,
 PNR_SCOPE_INDEX, PNR_SCOPE_ACTIVE, PNR_STATUS, PNR_HANDLE_READ, PNR_REQUEST_READ,
 PNR_HANDLE_RANGE, PNR_MAGIC_READ, PNR_MAGIC, PNR_SIZE_READ, PNR_SIZE,
 PNR_TAGTYPE_READ, PNR_TAG, PNR_TYPE, PNR_COUNT_READ, PNR_COUNT, PNR_REQUEST,
 PNR_TUPLE_READ, PNR_HANDLE_MAPPING, PNR_REASON_COUNT
};
enum playback_reject_field {
#define PNR_ENUM(name) PNR_F_##name,
 PLAYBACK_REJECT_FIELDS(PNR_ENUM)
#undef PNR_ENUM
 PNR_FIELD_COUNT
};
#define PNR_K(field) (UINT32_C(1)<<PNR_F_##field)
#define PNR_ALL_KNOWN UINT32_C(0x7fff)
struct playback_reject_facts {
#define PNR_MEMBER(name) uint32_t name;
 PLAYBACK_REJECT_FIELDS(PNR_MEMBER)
#undef PNR_MEMBER
};
struct playback_reject_record {
 _Atomic uint32_t state;
 struct playback_reject_facts facts;
};
struct playback_reject_binding_profile {
 uint32_t schema,roles,words,record_bytes,record_alignment,reason_count,known_mask;
};
extern const struct playback_reject_binding_profile playback_reject_profile;
extern struct playback_reject_record playback_reject_records[PNR_ROLE_COUNT];
/* First immutable rejection in each cohort. Fields are observations only when
 * their bit is set. A best source-chain candidate is not a proven caller. */
void playback_reject_claim(uint32_t role,const struct playback_reject_facts *);
uint32_t playback_reject_snapshot(uint32_t role,struct playback_reject_facts *);
uint32_t playback_reject_hits(uint32_t role);
_Static_assert(sizeof(struct playback_reject_facts)==64u,"rejection facts ABI");
_Static_assert(sizeof(struct playback_reject_record)==68u,"rejection record ABI");
#endif
