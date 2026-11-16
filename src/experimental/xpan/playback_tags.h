#ifndef R5_PLAYBACK_TAGS_H
#define R5_PLAYBACK_TAGS_H
#include <stdint.h>
#include <stdatomic.h>
#include "playback_box.h"
#define PLAYBACK_TAG_SLOT_COUNT 5u
#define PLAYBACK_TAG_FACT_WORDS 7u
#define PLAYBACK_TAG_ACTIVE_MASK UINT64_C(0x7fffffff)
#define PLAYBACK_TAG_COLLISION UINT64_C(0x80000000)
#define PLAYBACK_TAG_REV_SHIFT 32u
struct playback_tag_record {
 _Alignas(8) _Atomic uint64_t control;
 _Atomic uint32_t object;
 _Atomic uint32_t facts[PLAYBACK_TAG_FACT_WORDS];
 _Atomic uint32_t ready_revision;
};
struct playback_tags {struct playback_tag_record records[PLAYBACK_TAG_SLOT_COUNT];};
struct playback_tag_lease {
 struct playback_tags *owner;
 uint32_t index,revision,active;
};
struct playback_tag_snapshot {
 const struct playback_tags *owner;
 uint32_t index,object,revision;
 struct playback_box_facts facts;
};
_Static_assert(sizeof(struct playback_box_facts)==28u,"playback facts ABI");
_Static_assert(sizeof(struct playback_tag_record)==48u,"playback tag record ABI");
_Static_assert(_Alignof(struct playback_tag_record)==8u,"playback atomic alignment");
_Static_assert(sizeof(struct playback_tags)==240u,"playback tag owner ABI");
#if UINTPTR_MAX==UINT32_MAX
_Static_assert(sizeof(struct playback_tag_lease)==16u,"native playback lease ABI");
_Static_assert(sizeof(struct playback_tag_snapshot)==44u,"native playback snapshot ABI");
#endif
/* Zero initialize owner once before concurrency. A lease belongs to one call,
 * is not copied/reissued while active, and exactly one End consumes it. Begin
 * returns by value (native AAPCS hidden result pointer), with active0 on reject.
 * Object is a native identity value only; this core never dereferences it. */
struct playback_tag_lease playback_tags_begin(struct playback_tags *,unsigned index);
/* Known means seven complete, source-validated stable tuple/current-view facts, including stock
 * or legacy files. It is NOT playback_box_kind eligibility. Source-copy callers
 * must revalidate their source snapshot and pass known0 when it changed. */
void playback_tags_end(struct playback_tag_lease *,uint32_t object,
 const struct playback_box_facts *,unsigned known);
/* Output stays entirely untouched on every failed read. Snapshots include
 * owner identity; same revalidates every fact, object and private revision. */
unsigned playback_tags_read(const struct playback_tags *,unsigned index,uint32_t object,
 struct playback_tag_snapshot *);
unsigned playback_tags_same(const struct playback_tags *,const struct playback_tag_snapshot *);
/* Instant knowledge invalidation. Actual native writes/reads/copies require
 * Begin/End spanning the operation; do not substitute this for that lease. */
void playback_tags_invalidate(struct playback_tags *,unsigned index);
#endif
