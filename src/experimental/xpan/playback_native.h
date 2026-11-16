#ifndef R5_PLAYBACK_NATIVE_H
#define R5_PLAYBACK_NATIVE_H
#define PN_MAIN_SCOPE_BYTES 80
#include "playback_native_profile.h"
#ifndef __ASSEMBLER__
#include <stdint.h>
#include "playback_tags.h"
#define PLAYBACK_NATIVE_SCHEMA 1u
#define PN_MAX_HOPS 6u
#define PN_MAX_RETURNS 3u
enum playback_native_site {PN_TAG_PRIMARY=1,PN_TAG_ALTERNATE=2};
enum playback_native_copy_role {PN_BACKUP1=1,PN_BACKUP2=2,PN_RESTORE1=3,PN_RESTORE2=4};
struct playback_native_cpu {uint32_t r[13],lr;};
struct playback_native_read_facts {uint32_t entry_lr,context,selector,request,object,r4,r5,r6,r7;};
struct playback_native_tag_facts {
 uint32_t site,status,inner,requested,handle,magic,bytes,tag,type,count,scope_object,scope_known;
};
struct playback_native_hop {uint32_t offset,count,returns[PN_MAX_RETURNS];};
struct playback_native_chain {uint32_t site,hop_count;struct playback_native_hop hops[PN_MAX_HOPS];};
struct playback_native_binding_profile {
 uint32_t schema,metadata_bytes,metadata_count,cache_slot,cache_key,cache_flags;
 uint32_t inner_offset,enum_offset,width_offset,height_offset,header_offset,ifd_magic,ifd_bytes;
 uint32_t primary_tuple,primary_handle,primary_requested,alternate_tuple,alternate_handle,alternate_requested;
 uint32_t main_frame,main_scope_bytes,chain_count,objects[4];
};
_Static_assert(sizeof(struct playback_native_chain)==128u,"chain scalar ABI");
_Static_assert(sizeof(struct playback_native_binding_profile)==104u,"native profile ABI");
_Static_assert(sizeof(struct playback_native_read_facts)==36u,"read scalar ABI");
_Static_assert(sizeof(struct playback_native_tag_facts)==48u,"tag scalar ABI");
extern const struct playback_native_binding_profile playback_native_profile;
extern const struct playback_native_chain playback_native_chains[];
extern struct playback_tags playback_native_tags;
uint32_t playback_native_chain_count(void);
uint32_t playback_native_chain_scope_delta(uint32_t chain);
uint32_t playback_native_chain_match(uint32_t chain,const uint32_t returns[PN_MAX_HOPS]);
uint32_t playback_native_object_index(uint32_t object);
uint32_t playback_native_read_admit(const struct playback_native_read_facts *);
uint32_t playback_native_untracked_admit(uint32_t object);
uint32_t playback_native_tag_admit(const struct playback_native_tag_facts *);
uint32_t playback_native_copy_admit(uint32_t role,uint32_t destination,uint32_t source,uint32_t bytes);
uint32_t playback_native_fields_match(const struct playback_box_facts *,uint32_t aspect,uint32_t header,uint32_t width,uint32_t height);
struct playback_native_main_scope {
 _Alignas(8) uint32_t marker;
 /* admitted:0 proved untracked/no lease,1 tracked,2 unresolved write interval. */
 uint32_t self,native_sp,continuation,object,selector,request,admitted,tag_known;
 struct playback_box_facts facts;
 struct playback_tag_lease lease;
};
#if UINTPTR_MAX==UINT32_MAX
_Static_assert(sizeof(struct playback_native_main_scope)==PN_MAIN_SCOPE_BYTES,"main scope ABI");
#endif
void playback_native_main_scope_init(struct playback_native_main_scope *,const struct playback_native_cpu *,uint32_t native_sp,uint32_t continuation);
void playback_native_main_scope_end(struct playback_native_main_scope *,uint32_t native_return);
void playback_native_tag_observe(const struct playback_native_cpu *,uint32_t native_sp,uint32_t site);
uint32_t playback_native_copy_adapter(uint32_t destination,uint32_t source,uint32_t bytes,uint32_t role);
uint32_t playback_native_cache_adapter(uint32_t object);
/* Private owner AND native enum/dimension/header revalidation. Known stock
 * snapshots remain distinct from playback_box_kind feature eligibility. */
uint32_t playback_native_object_snapshot(uint32_t object,struct playback_tag_snapshot *);
uint32_t playback_native_cache_snapshot(struct playback_tag_snapshot *);
uint32_t playback_native_snapshot_same(const struct playback_tag_snapshot *);
/* Independent relaxed route counters; no Canon reads or coherent time join. */
struct playback_native_observation {
 uint32_t main_entered,main_admitted,tag_entered,tag_captured;
 uint32_t copy_entered,copy_published,cache_entered,cache_published;
};
_Static_assert(sizeof(struct playback_native_observation)==32u,"route counter ABI");
void playback_native_observe(struct playback_native_observation *);
/* Address bindings only; never ordinary C-call these native ASM entries. */
extern void playback_native_main_entry(void),playback_native_tag_primary_entry(void),playback_native_tag_alternate_entry(void);
extern void playback_native_backup1_entry(void),playback_native_backup2_entry(void),playback_native_restore1_entry(void),playback_native_restore2_entry(void);
extern void playback_native_cache_entry(void);
#endif
#endif
