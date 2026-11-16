#ifndef R5_PLAYBACK_FIRST_VIEW_H
#define R5_PLAYBACK_FIRST_VIEW_H
#include <stdint.h>
#include <stdatomic.h>

#define PLAYBACK_FIRST_VIEW_SCHEMA 1u
#define PLAYBACK_FIRST_VIEW_WORDS 16u

enum playback_first_view_state { PFV_STATE_EMPTY=0, PFV_STATE_WRITING=1, PFV_STATE_READY=2 };

#define PLAYBACK_FIRST_VIEW_FIELDS(X) \
 X(reason) X(site) X(caller) X(object) \
 X(selector) X(category) X(flags) X(actual) \
 X(expected) X(geom_dims) X(obj_dims) X(cache_dims) \
 X(aux_a) X(aux_b) X(aux_c) X(known_mask)

enum playback_first_view_role {
 PFV_FIT_FIRST_CALL=0,
 PFV_FIT_ENTRY_REJECT=1,
 PFV_FIT_SNAPSHOT_REJECT=2,
 PFV_FIT_RAW_L_REJECT=3,
 PFV_FIT_RAW_L_SUCCESS=4,
 PFV_GUIDE_RELEVANT_REJECT=5,
 PFV_GUIDE_FIRST_HIDDEN=6,
 PFV_PRODUCER_DECLINE=7,
 PFV_ROLE_COUNT=8
};

enum playback_first_view_site {
 PFV_SITE_NONE=0,
 PFV_SITE_FIT_BEGIN=1,
 PFV_SITE_FIT_END=2,
 PFV_SITE_GUIDE=3,
 PFV_SITE_MAIN_INIT=4,
 PFV_SITE_MAIN_END=5,
 PFV_SITE_CACHE=6,
 PFV_SITE_COUNT=7
};

enum playback_first_view_reason {
 PFV_R_NONE=0,
 PFV_R_FIT_OK,
 PFV_R_FIT_CPU_NULL,
 PFV_R_FIT_CALLER_LR,
 PFV_R_FIT_OBJECT,
 PFV_R_FIT_SAVED_OBJECT,
 PFV_R_FIT_SELECTOR,
 PFV_R_FIT_SAVED_SELECTOR,
 PFV_R_FIT_DRAWMODE,
 PFV_R_FIT_ROTATION,
 PFV_R_FIT_FRAME_SPAN,
 PFV_R_FIT_VIEW_SOLE,
 PFV_R_FIT_VIEW_READ,
 PFV_R_FIT_VIEW_DEVICE,
 PFV_R_FIT_VIEW_ORIENTATION,
 PFV_R_FIT_VIEW_CAT13,
 PFV_R_FIT_VIEW_COLOR,
 PFV_R_FIT_CATEGORY_MISMATCH,
 PFV_R_FIT_OBJECT_UNKNOWN,
 PFV_R_FIT_CACHE_UNKNOWN,
 PFV_R_FIT_OBJECT_NOT_RAW_L,
 PFV_R_FIT_CACHE_NOT_RAW_L,
 PFV_R_FIT_FACTS_MISMATCH,
 PFV_R_FIT_MAIN_DIMS_MISMATCH,
 PFV_R_FIT_GEOMETRY_FAIL,
 PFV_R_FIT_SNAPSHOTS_FAIL,
 PFV_R_FIT_END_FRAME,
 PFV_R_FIT_END_OWNED,
 PFV_R_FIT_END_STOCK_READ,
 PFV_R_FIT_END_STOCK_MISMATCH,
 PFV_R_FIT_END_RATIO,
 PFV_R_FIT_END_PROJECT,
 PFV_R_FIT_END_NATIVE_ELIGIBLE,
 PFV_R_FIT_END_AUTHORITY,
 PFV_R_FIT_END_VIEW_COLLISION,
 PFV_R_GUIDE_HIDDEN,
 PFV_R_GUIDE_CPU_NULL,
 PFV_R_GUIDE_NOT_CANDIDATE,
 PFV_R_GUIDE_FRAME_SPAN,
 PFV_R_GUIDE_GEOMETRY_PTR,
 PFV_R_GUIDE_RESOURCES,
 PFV_R_GUIDE_SAVED_LR,
 PFV_R_GUIDE_SAVED_GEOM,
 PFV_R_GUIDE_HANDLE_ZERO,
 PFV_R_GUIDE_VIEW_READ,
 PFV_R_GUIDE_VIEW_ADMIT,
 PFV_R_GUIDE_CAT_MISMATCH,
 PFV_R_GUIDE_DEVICE_MISMATCH,
 PFV_R_GUIDE_ORIENTATION_MISMATCH,
 PFV_R_GUIDE_OBJECT_UNKNOWN,
 PFV_R_GUIDE_CACHE_UNKNOWN,
 PFV_R_GUIDE_OBJECT_REV,
 PFV_R_GUIDE_CACHE_REV,
 PFV_R_GUIDE_NOT_RAW_L,
 PFV_R_GUIDE_FACTS_MISMATCH,
 PFV_R_GUIDE_NATIVE_ELIGIBLE,
 PFV_R_GUIDE_LATE_RACE,
 PFV_R_MAIN_UNRESOLVED,
 PFV_R_MAIN_END_TAG_MISSING,
 PFV_R_MAIN_END_RESULT,
 PFV_R_MAIN_END_OBJECT_FIELDS,
 PFV_R_MAIN_END_FIELDS_MISMATCH,
 PFV_R_CACHE_OBJECT_UNKNOWN,
 PFV_R_CACHE_SNAPSHOT_RACED,
 PFV_R_CACHE_FIELDS_MISMATCH,
 PFV_R_MAIN_END_IMAGE_STATUS,
 PFV_R_CACHE_IMAGE_STATUS,
 PFV_R_COUNT
};

enum playback_first_view_field {
#define PFV_ENUM(name) PFV_F_##name,
 PLAYBACK_FIRST_VIEW_FIELDS(PFV_ENUM)
#undef PFV_ENUM
 PFV_FIELD_COUNT
};

#define PFV_K(field) (UINT32_C(1)<<PFV_F_##field)
#define PFV_ALL_KNOWN UINT32_C(0x7fff)

#define PFV_FLAG_OBJ_KIND_SHIFT   0u
#define PFV_FLAG_CACHE_KIND_SHIFT 2u
#define PFV_FLAG_OBJ_KNOWN        (UINT32_C(1)<<4)
#define PFV_FLAG_CACHE_KNOWN      (UINT32_C(1)<<5)
#define PFV_FLAG_VIEW_READY       (UINT32_C(1)<<6)
#define PFV_FLAG_NATIVE_ASPECT    (UINT32_C(1)<<7)
#define PFV_FLAG_NATIVE_HEADER_OK (UINT32_C(1)<<8)
#define PFV_FLAG_NATIVE_MODEL     (UINT32_C(1)<<9)
#define PFV_FLAG_ADMITTED         (UINT32_C(1)<<10)
#define PFV_FLAG_UNRESOLVED       (UINT32_C(1)<<11)

struct playback_first_view_facts {
#define PFV_MEMBER(name) uint32_t name;
 PLAYBACK_FIRST_VIEW_FIELDS(PFV_MEMBER)
#undef PFV_MEMBER
};

struct playback_first_view_record {
 _Atomic uint32_t state;
 struct playback_first_view_facts facts;
};

struct playback_first_view_binding_profile {
 uint32_t schema,roles,words,record_bytes,record_alignment,reason_count,known_mask;
};

extern const struct playback_first_view_binding_profile playback_first_view_profile;
extern struct playback_first_view_record playback_first_view_records[PFV_ROLE_COUNT];

/* First immutable observation in each cohort. Later failures/calls increment
 * hits without overwriting the first captured facts. */
void playback_first_view_claim(uint32_t role,const struct playback_first_view_facts *);
uint32_t playback_first_view_snapshot(uint32_t role,struct playback_first_view_facts *);
uint32_t playback_first_view_hits(uint32_t role);

struct playback_slot_diag {
 uint32_t resolved,slot_state,slot_object,slot_revision,slot_kind,field_ok,aspect,header,width,height;
};
void playback_native_slot_diag(uint32_t index,uint32_t object,struct playback_slot_diag *out);

_Static_assert(PFV_FIELD_COUNT==PLAYBACK_FIRST_VIEW_WORDS,"first_view words ABI");
_Static_assert(sizeof(struct playback_first_view_facts)==64u,"first_view facts ABI");
_Static_assert(sizeof(struct playback_first_view_record)==68u,"first_view record ABI");
#endif
