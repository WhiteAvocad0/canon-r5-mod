#ifndef R5_PLAYBACK_MASK_NATIVE_H
#define R5_PLAYBACK_MASK_NATIVE_H
#define PM_FIT_SCOPE_BYTES 184
#include "playback_mask_profile.h"
#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stdatomic.h>
#include "playback_native.h"
#define PLAYBACK_MASK_SCHEMA 2u
struct playback_mask_cpu {uint32_t r[13],lr;};
struct playback_mask_view_facts {uint32_t device,orientation,category,color[3];};
struct playback_mask_fit_facts {
 uint32_t entry_lr,original_sp,output,object,saved_object,selector,saved_selector,drawmode,rotation,category;
 struct playback_mask_view_facts view;
 uint32_t object_known,cache_known;
 struct playback_box_facts object_facts,cache_facts;
};
struct playback_mask_guide_facts {
 uint32_t native_sp,saved_lr,geometry,saved_geometry,resources,handle;
 struct playback_mask_view_facts view;
};
/* Native pixel handle is an opaque identity token. It is never dereferenced
 * here or admitted through the generic DATA pointer domain. */
struct playback_mask_geometry {uint32_t pixel_token,width,height,stock[4];};
struct playback_mask_projection_facts {
 uint32_t selector,width,height,stock_result,stock[4],numerator,denominator;
};
struct playback_mask_container_profile {uint32_t selector,token_offset,width_offset,height_offset;};
extern const struct playback_mask_container_profile playback_mask_containers[3];
struct playback_mask_fit_scope {
 _Alignas(8) uint32_t marker;
 uint32_t self,native_sp,continuation,original_sp,output,object,caller_lr;
 uint32_t selector,original_category,admitted,view_revision,view_active;
 uint32_t fitted_device,fitted_orientation,fitted_rotation,fitted_reserved;
 struct playback_mask_geometry geometry;
 struct playback_tag_snapshot object_snapshot,cache_snapshot;
};
struct playback_mask_view_owner {
 _Alignas(8) _Atomic uint64_t control;
 _Atomic uint32_t ready_revision,object_revision,cache_revision,category,device,orientation,rotation,reserved;
};
extern struct playback_mask_view_owner playback_mask_view;
struct playback_mask_observation {
 uint32_t entered,admitted,projected,crop_candidate,guide_hidden,rejected;
 uint32_t view_ready,view_revision,object_revision,cache_revision;
};
struct playback_mask_binding_profile {
 uint32_t schema,scope_bytes,native_frame,cpu_image,caller_bytes,combined_span;
 uint32_t fit_lr,output_offset,guide_lr,guide_span,guide_geometry,guide_return,guide_saved_geometry,guide_handle,guide_resource;
 uint32_t display_word,orientation_word,category_word,clear_color;
 uint32_t native_aspect_valid,native_header_set,native_model_match,thumb_roi_offset;
 uint32_t fit_facts_bytes,guide_facts_bytes,view_owner_bytes,observation_words;
 uint32_t geometry_bytes,projection_facts_bytes,container_count;
};
extern const struct playback_mask_binding_profile playback_mask_profile;
uint32_t playback_mask_fit_admit(const struct playback_mask_fit_facts *);
uint32_t playback_mask_guide_admit(const struct playback_mask_guide_facts *);
/* Pure source-equivalent integer geometry. No pointer or file authority is
 * granted by this function; output remains untouched on rejection. */
uint32_t playback_mask_project(const struct playback_mask_projection_facts *,uint32_t output[4]);
void playback_mask_fit_begin(struct playback_mask_fit_scope *,const struct playback_mask_cpu *,uint32_t native_sp,uint32_t continuation);
uint32_t playback_mask_fit_end(struct playback_mask_fit_scope *,uint32_t native_return);
uint32_t playback_mask_guide_decide(const struct playback_mask_cpu *);
/* Independent counters; the last4 words describe only a coherent private
 * candidate when view_ready==1, never Canon/GPU completion. */
void playback_mask_observe(struct playback_mask_observation *);
extern void playback_mask_fit_entry(void),playback_mask_guide_entry(void);
#if UINTPTR_MAX==UINT32_MAX
_Static_assert(sizeof(struct playback_mask_fit_scope)==PM_FIT_SCOPE_BYTES,"fit scope ABI");
#endif
_Static_assert(sizeof(struct playback_mask_fit_facts)==128u,"fit scalar facts ABI");
_Static_assert(sizeof(struct playback_mask_guide_facts)==48u,"guide scalar facts ABI");
_Static_assert(sizeof(struct playback_mask_geometry)==28u,"selected geometry ABI");
_Static_assert(sizeof(struct playback_mask_projection_facts)==40u,"projection facts ABI");
_Static_assert(sizeof(struct playback_mask_container_profile)==16u,"container profile ABI");
_Static_assert(sizeof(struct playback_mask_binding_profile)==120u,"mask profile ABI");
_Static_assert(sizeof(struct playback_mask_view_owner)==40u,"private view owner ABI");
_Static_assert(sizeof(struct playback_mask_observation)==40u,"mask observation ABI");
#endif
#endif
