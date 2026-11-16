#ifndef R5_FRAME_TAGS_H
#define R5_FRAME_TAGS_H
#include <stdint.h>
#include <stdatomic.h>

#define FRAME_TAG_SLOT_COUNT 20u
#define FRAME_TAG_DEFAULT 20u
#define FRAME_TAG_COUNT 21u
#define FRAME_TAG_ACTIVE_MASK UINT64_C(0x7fffffff)
#define FRAME_TAG_COLLISION UINT64_C(0x80000000)
#define FRAME_TAG_REV_SHIFT 32u

struct frame_tag_record {
 _Alignas(8) _Atomic uint64_t control;
 _Atomic uint32_t pointer;
 _Atomic uint32_t frame;
 _Atomic uint32_t raw;
 _Atomic uint32_t ready_revision;
};
struct frame_tags { struct frame_tag_record records[FRAME_TAG_COUNT]; };
/* Internal per-invocation leases: begin initializes, one end consumes.
 * Never copy/reissue an active lease or initialize the owner during a borrow. */
struct frame_tag_lease {
 struct frame_tags *owner;
 uint32_t index;
 uint32_t revision;
 uint32_t active;
};
struct frame_tag_snapshot {
 const struct frame_tags *owner;
 uint32_t index;
 uint32_t pointer;
 uint32_t frame;
 uint32_t raw;
 uint32_t revision;
};
struct frame_tag_reset { struct frame_tag_lease leases[FRAME_TAG_COUNT]; };

_Static_assert(sizeof(struct frame_tag_record)==24,"frame tag record ABI");
_Static_assert(_Alignof(struct frame_tag_record)==8,"frame tag alignment");
_Static_assert(sizeof(struct frame_tags)==504,"frame tag owner ABI");
#if UINTPTR_MAX==UINT32_MAX
_Static_assert(sizeof(struct frame_tag_lease)==16,"native frame tag lease ABI");
_Static_assert(sizeof(struct frame_tag_snapshot)==24,"native frame tag snapshot ABI");
_Static_assert(sizeof(struct frame_tag_reset)==336,"native frame tag reset ABI");
#endif

/* The owner is zero initialized once, before native producers/borrowers.
 * Begin fences before Canon writes. Begin failure quarantines exhaustion;
 * original native behavior still delegates and must not acquire raw authority. */
unsigned frame_tags_begin(struct frame_tags *,unsigned index,struct frame_tag_lease *);
void frame_tags_end_unknown(struct frame_tag_lease *);
/* Only default leases may capture. Stored default frame is always0.
 * Native adapter owns the recognized original GmtState/outer-writer proof. */
void frame_tags_end_capture(struct frame_tag_lease *,uint32_t pointer,uint32_t raw,unsigned known);
/* Only slot leases may copy. Native adapter supplies the actual destination
 * frame after copy/refresh restoration and source-native stability separately.
 * Source must be a complete canonical snapshot of this SAME owner. */
void frame_tags_end_copy(struct frame_tag_lease *,uint32_t pointer,uint32_t frame,
 const struct frame_tag_snapshot *source,unsigned native_source_stable);
/* Default native_frame argument is ignored; its snapshot frame remains0.
 * Slots require exact actual native pointer/frame. No address is dereferenced.
 * Snapshots include owner identity; same validates every canonical field. */
unsigned frame_tags_read(const struct frame_tags *,unsigned index,uint32_t pointer,
 uint32_t native_frame,struct frame_tag_snapshot *);
unsigned frame_tags_same(const struct frame_tags *,const struct frame_tag_snapshot *);
/* Reset lease spans ALL Canon lifecycle writes. Never memset/reset revision
 * history or outstanding counts during a Canon reset/reallocation. */
void frame_tags_reset_begin(struct frame_tags *,struct frame_tag_reset *);
void frame_tags_reset_end(struct frame_tag_reset *);
#endif
