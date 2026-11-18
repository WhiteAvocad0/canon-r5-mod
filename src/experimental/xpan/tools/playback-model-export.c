/* Host-only export from actual typed native/policy owners. */
#include <stdio.h>
#include <stddef.h>
#include <inttypes.h>
#include "playback_report.h"
#include "playback_reject.h"
#include "playback_first_view.h"
#include "playback_native.h"
#include "playback_mask_native.h"
#include "export_refresh.h"
#include "ifd_memory.h"
static unsigned comma;
static void number(const char*n,size_t v){printf("%s\"%s\":%zu",comma++?",":"",n,v);}
static void hexbytes(const void*p,size_t n){const unsigned char*b=p;for(size_t i=0;i<n;i++)printf("%02x",b[i]);}
static void field(const char*n,size_t at,size_t bytes){printf("%s\"%s\":{\"word\":%zu,\"count\":%zu}",comma++?",":"",n,at/4u,bytes/4u);}
static void tuple(const uint32_t *v,size_t n){putchar('[');for(size_t i=0;i<n;i++)printf("%s%" PRIu32,i?",":"",v[i]);putchar(']');}
#define FIELD(type,member) field(#member,offsetof(struct type,member),sizeof(((struct type*)0)->member))
static void rejection_model(void){
 printf(",\"rejection\":{\"profile_bytes\":%zu,\"profile_hex\":\"",sizeof playback_reject_profile);hexbytes(&playback_reject_profile,sizeof playback_reject_profile);
 printf("\",\"records_bytes\":%zu,\"facts_bytes\":%zu,\"state_offset\":%zu,\"facts_offset\":%zu,\"profile\":{",sizeof playback_reject_records,sizeof(struct playback_reject_facts),offsetof(struct playback_reject_record,state),offsetof(struct playback_reject_record,facts));comma=0;
#define REJECT_PROFILE(name) number(#name,playback_reject_profile.name)
 REJECT_PROFILE(schema);REJECT_PROFILE(roles);REJECT_PROFILE(words);REJECT_PROFILE(record_bytes);REJECT_PROFILE(record_alignment);REJECT_PROFILE(reason_count);REJECT_PROFILE(known_mask);
#undef REJECT_PROFILE
 printf("},\"fields\":{");comma=0;
#define REJECT_FIELD(name) printf("%s\"" #name "\":{\"word\":%u,\"known_bit\":%u}",comma++?",":"",PNR_F_##name,PNR_K(name)&PNR_ALL_KNOWN);
 PLAYBACK_REJECT_FIELDS(REJECT_FIELD)
#undef REJECT_FIELD
 printf("},\"roles\":{");comma=0;
#define REJECT_ENUM(name) number(#name,name)
 REJECT_ENUM(PNR_EARLY);REJECT_ENUM(PNR_KNOWN_OBJECT);REJECT_ENUM(PNR_SCOPED);
 printf("},\"states\":{");comma=0;REJECT_ENUM(PNR_STATE_EMPTY);REJECT_ENUM(PNR_STATE_WRITING);REJECT_ENUM(PNR_STATE_READY);
 printf("},\"reasons\":{");comma=0;
 REJECT_ENUM(PNR_NONE);
 REJECT_ENUM(PNR_INPUT);
 REJECT_ENUM(PNR_OBJECT);
 REJECT_ENUM(PNR_STACK);
 REJECT_ENUM(PNR_HOP_READ);
 REJECT_ENUM(PNR_HOP_MISMATCH);
 REJECT_ENUM(PNR_CONT_READ);
 REJECT_ENUM(PNR_CONT_MISMATCH);
 REJECT_ENUM(PNR_SCOPE_MARKER);
 REJECT_ENUM(PNR_SCOPE_SELF);
 REJECT_ENUM(PNR_SCOPE_SP);
 REJECT_ENUM(PNR_SCOPE_CONT);
 REJECT_ENUM(PNR_SCOPE_ADMITTED);
 REJECT_ENUM(PNR_SCOPE_OBJECT);
 REJECT_ENUM(PNR_SCOPE_OWNER);
 REJECT_ENUM(PNR_SCOPE_INDEX);
 REJECT_ENUM(PNR_SCOPE_ACTIVE);
 REJECT_ENUM(PNR_STATUS);
 REJECT_ENUM(PNR_HANDLE_READ);
 REJECT_ENUM(PNR_REQUEST_READ);
 REJECT_ENUM(PNR_HANDLE_RANGE);
 REJECT_ENUM(PNR_MAGIC_READ);
 REJECT_ENUM(PNR_MAGIC);
 REJECT_ENUM(PNR_SIZE_READ);
 REJECT_ENUM(PNR_SIZE);
 REJECT_ENUM(PNR_TAGTYPE_READ);
 REJECT_ENUM(PNR_TAG);
 REJECT_ENUM(PNR_TYPE);
 REJECT_ENUM(PNR_COUNT_READ);
 REJECT_ENUM(PNR_COUNT);
 REJECT_ENUM(PNR_REQUEST);
 REJECT_ENUM(PNR_TUPLE_READ);
 REJECT_ENUM(PNR_HANDLE_MAPPING);
#undef REJECT_ENUM
 printf("},\"max_records\":%u,\"candidate_chain_count\":%u,\"formats\":{\"begin\":\"%s\",\"source\":\"%s\",\"role\":\"%s\",\"words\":\"%s\",\"end\":\"%s\"}}",3u+PNR_ROLE_COUNT*(1u+PLAYBACK_REJECT_WORDS/4u),playback_native_chain_count(),PQ_FMT_BEGIN,PQ_FMT_SOURCE,PQ_FMT_ROLE,PQ_FMT_WORDS,PQ_FMT_END);
}
static void first_view_model(void){
 printf(",\"first_view\":{\"profile_bytes\":%zu,\"profile_hex\":\"",sizeof playback_first_view_profile);hexbytes(&playback_first_view_profile,sizeof playback_first_view_profile);
 printf("\",\"records_bytes\":%zu,\"facts_bytes\":%zu,\"state_offset\":%zu,\"facts_offset\":%zu,\"profile\":{",sizeof playback_first_view_records,sizeof(struct playback_first_view_facts),offsetof(struct playback_first_view_record,state),offsetof(struct playback_first_view_record,facts));comma=0;
#define PFV_PROFILE(name) number(#name,playback_first_view_profile.name)
 PFV_PROFILE(schema);PFV_PROFILE(roles);PFV_PROFILE(words);PFV_PROFILE(record_bytes);PFV_PROFILE(record_alignment);PFV_PROFILE(reason_count);PFV_PROFILE(known_mask);
#undef PFV_PROFILE
 printf("},\"fields\":{");comma=0;
#define PFV_FIELD(name) printf("%s\"" #name "\":{\"word\":%u,\"known_bit\":%u}",comma++?",":"",PFV_F_##name,PFV_K(name)&PFV_ALL_KNOWN);
 PLAYBACK_FIRST_VIEW_FIELDS(PFV_FIELD)
#undef PFV_FIELD
 printf("},\"roles\":{");comma=0;
#define PFV_ENUM(name) number(#name,name)
 PFV_ENUM(PFV_FIT_FIRST_CALL);PFV_ENUM(PFV_FIT_ENTRY_REJECT);PFV_ENUM(PFV_FIT_SNAPSHOT_REJECT);
 PFV_ENUM(PFV_FIT_RAW_L_REJECT);PFV_ENUM(PFV_FIT_RAW_L_SUCCESS);PFV_ENUM(PFV_GUIDE_RELEVANT_REJECT);
 PFV_ENUM(PFV_GUIDE_FIRST_HIDDEN);PFV_ENUM(PFV_PRODUCER_DECLINE);
 printf("},\"sites\":{");comma=0;
 PFV_ENUM(PFV_SITE_NONE);PFV_ENUM(PFV_SITE_FIT_BEGIN);PFV_ENUM(PFV_SITE_FIT_END);
 PFV_ENUM(PFV_SITE_GUIDE);PFV_ENUM(PFV_SITE_MAIN_INIT);PFV_ENUM(PFV_SITE_MAIN_END);PFV_ENUM(PFV_SITE_CACHE);
 printf("},\"states\":{");comma=0;PFV_ENUM(PFV_STATE_EMPTY);PFV_ENUM(PFV_STATE_WRITING);PFV_ENUM(PFV_STATE_READY);
 printf("},\"reasons\":{");comma=0;
 PFV_ENUM(PFV_R_NONE);PFV_ENUM(PFV_R_FIT_OK);PFV_ENUM(PFV_R_FIT_CPU_NULL);PFV_ENUM(PFV_R_FIT_CALLER_LR);
 PFV_ENUM(PFV_R_FIT_OBJECT);PFV_ENUM(PFV_R_FIT_SAVED_OBJECT);PFV_ENUM(PFV_R_FIT_SELECTOR);PFV_ENUM(PFV_R_FIT_SAVED_SELECTOR);
 PFV_ENUM(PFV_R_FIT_DRAWMODE);PFV_ENUM(PFV_R_FIT_ROTATION);PFV_ENUM(PFV_R_FIT_FRAME_SPAN);PFV_ENUM(PFV_R_FIT_VIEW_SOLE);
 PFV_ENUM(PFV_R_FIT_VIEW_READ);PFV_ENUM(PFV_R_FIT_VIEW_DEVICE);PFV_ENUM(PFV_R_FIT_VIEW_ORIENTATION);PFV_ENUM(PFV_R_FIT_VIEW_CAT13);
 PFV_ENUM(PFV_R_FIT_VIEW_COLOR);PFV_ENUM(PFV_R_FIT_CATEGORY_MISMATCH);PFV_ENUM(PFV_R_FIT_OBJECT_UNKNOWN);PFV_ENUM(PFV_R_FIT_CACHE_UNKNOWN);
 PFV_ENUM(PFV_R_FIT_OBJECT_NOT_RAW_L);PFV_ENUM(PFV_R_FIT_CACHE_NOT_RAW_L);PFV_ENUM(PFV_R_FIT_FACTS_MISMATCH);PFV_ENUM(PFV_R_FIT_MAIN_DIMS_MISMATCH);
 PFV_ENUM(PFV_R_FIT_GEOMETRY_FAIL);PFV_ENUM(PFV_R_FIT_SNAPSHOTS_FAIL);
 PFV_ENUM(PFV_R_FIT_END_FRAME);PFV_ENUM(PFV_R_FIT_END_OWNED);PFV_ENUM(PFV_R_FIT_END_STOCK_READ);PFV_ENUM(PFV_R_FIT_END_STOCK_MISMATCH);
 PFV_ENUM(PFV_R_FIT_END_RATIO);PFV_ENUM(PFV_R_FIT_END_PROJECT);PFV_ENUM(PFV_R_FIT_END_NATIVE_ELIGIBLE);PFV_ENUM(PFV_R_FIT_END_AUTHORITY);PFV_ENUM(PFV_R_FIT_END_VIEW_COLLISION);
 PFV_ENUM(PFV_R_GUIDE_HIDDEN);PFV_ENUM(PFV_R_GUIDE_CPU_NULL);PFV_ENUM(PFV_R_GUIDE_NOT_CANDIDATE);PFV_ENUM(PFV_R_GUIDE_FRAME_SPAN);
 PFV_ENUM(PFV_R_GUIDE_GEOMETRY_PTR);PFV_ENUM(PFV_R_GUIDE_RESOURCES);PFV_ENUM(PFV_R_GUIDE_SAVED_LR);PFV_ENUM(PFV_R_GUIDE_SAVED_GEOM);
 PFV_ENUM(PFV_R_GUIDE_HANDLE_ZERO);PFV_ENUM(PFV_R_GUIDE_VIEW_READ);PFV_ENUM(PFV_R_GUIDE_VIEW_ADMIT);PFV_ENUM(PFV_R_GUIDE_CAT_MISMATCH);
 PFV_ENUM(PFV_R_GUIDE_DEVICE_MISMATCH);PFV_ENUM(PFV_R_GUIDE_ORIENTATION_MISMATCH);
 PFV_ENUM(PFV_R_GUIDE_OBJECT_UNKNOWN);PFV_ENUM(PFV_R_GUIDE_CACHE_UNKNOWN);PFV_ENUM(PFV_R_GUIDE_OBJECT_REV);PFV_ENUM(PFV_R_GUIDE_CACHE_REV);
 PFV_ENUM(PFV_R_GUIDE_NOT_RAW_L);PFV_ENUM(PFV_R_GUIDE_FACTS_MISMATCH);PFV_ENUM(PFV_R_GUIDE_NATIVE_ELIGIBLE);PFV_ENUM(PFV_R_GUIDE_LATE_RACE);
 PFV_ENUM(PFV_R_MAIN_UNRESOLVED);PFV_ENUM(PFV_R_MAIN_END_TAG_MISSING);PFV_ENUM(PFV_R_MAIN_END_RESULT);
 PFV_ENUM(PFV_R_MAIN_END_OBJECT_FIELDS);PFV_ENUM(PFV_R_MAIN_END_FIELDS_MISMATCH);
 PFV_ENUM(PFV_R_CACHE_OBJECT_UNKNOWN);PFV_ENUM(PFV_R_CACHE_SNAPSHOT_RACED);PFV_ENUM(PFV_R_CACHE_FIELDS_MISMATCH);
 PFV_ENUM(PFV_R_MAIN_END_IMAGE_STATUS);PFV_ENUM(PFV_R_CACHE_IMAGE_STATUS);
#undef PFV_ENUM
 printf("},\"max_records\":%u,\"formats\":{\"begin\":\"%s\",\"source\":\"%s\",\"role\":\"%s\",\"words\":\"%s\",\"end\":\"%s\"}}",3u+PFV_ROLE_COUNT*(1u+PLAYBACK_FIRST_VIEW_WORDS/4u),PV_FMT_BEGIN,PV_FMT_SOURCE,PV_FMT_ROLE,PV_FMT_WORDS,PV_FMT_END);
}
int main(void){
 const uint16_t endian=1;if(*(const unsigned char*)&endian!=1)return 2;
 printf("{\"schema\":\"canon-r5-lab.playback-c-model.v2\",\"profiles\":{\"box\":{\"bytes\":%zu,\"hex\":\"",sizeof playback_box_profile);hexbytes(&playback_box_profile,sizeof playback_box_profile);
 printf("\",\"raw\":");tuple(playback_box_profile.raw,sizeof playback_box_profile.raw/sizeof playback_box_profile.raw[0]);printf(",\"jpeg\":");tuple(playback_box_profile.jpeg,sizeof playback_box_profile.jpeg/sizeof playback_box_profile.jpeg[0]);
 printf(",\"ratio\":[%" PRIu32 ",%" PRIu32 "]},\"native\":{\"bytes\":%zu,\"hex\":\"",playback_box_profile.numerator,playback_box_profile.denominator,sizeof playback_native_profile);hexbytes(&playback_native_profile,sizeof playback_native_profile);
 printf("\",\"values\":{");comma=0;
 number("schema",playback_native_profile.schema);
 number("metadata_bytes",playback_native_profile.metadata_bytes);
 number("metadata_count",playback_native_profile.metadata_count);
 number("cache_slot",playback_native_profile.cache_slot);
 number("cache_key",playback_native_profile.cache_key);
 number("cache_flags",playback_native_profile.cache_flags);
 number("inner_offset",playback_native_profile.inner_offset);
 number("enum_offset",playback_native_profile.enum_offset);
 number("width_offset",playback_native_profile.width_offset);
 number("height_offset",playback_native_profile.height_offset);
 number("header_offset",playback_native_profile.header_offset);
 number("ifd_magic",playback_native_profile.ifd_magic);
 number("ifd_bytes",playback_native_profile.ifd_bytes);
 number("primary_tuple",playback_native_profile.primary_tuple);
 number("primary_handle",playback_native_profile.primary_handle);
 number("primary_requested",playback_native_profile.primary_requested);
 number("alternate_tuple",playback_native_profile.alternate_tuple);
 number("alternate_handle",playback_native_profile.alternate_handle);
 number("alternate_requested",playback_native_profile.alternate_requested);
 number("main_frame",playback_native_profile.main_frame);
 number("main_scope_bytes",playback_native_profile.main_scope_bytes);
 number("chain_count",playback_native_profile.chain_count);
 printf("}},\"chains\":{\"count\":%u,\"entry_bytes\":%zu,\"bytes\":%zu,\"hex\":\"",playback_native_chain_count(),sizeof(struct playback_native_chain),playback_native_chain_count()*sizeof(struct playback_native_chain));hexbytes(playback_native_chains,playback_native_chain_count()*sizeof(struct playback_native_chain));
 printf("\",\"scope_deltas\":[");for(unsigned i=0;i<playback_native_chain_count();i++)printf("%s%" PRIu32,i?",":"",playback_native_chain_scope_delta(i));
 printf("]},\"ifd_memory\":{\"bytes\":%zu,\"hex\":\"",sizeof ifd_memory_profile);hexbytes(&ifd_memory_profile,sizeof ifd_memory_profile);
 printf("\",\"values\":{");comma=0;
 number("bytes",ifd_memory_profile.bytes);
 number("pool_begin",ifd_memory_profile.pool_begin);
 number("pool_end",ifd_memory_profile.pool_end);
 number("group_shift",ifd_memory_profile.group_shift);
 number("group_entries",ifd_memory_profile.group_entries);
 number("descriptor_attributes",ifd_memory_profile.descriptor_attributes);
 printf("}},\"mask\":{\"bytes\":%zu,\"hex\":\"",sizeof playback_mask_profile);hexbytes(&playback_mask_profile,sizeof playback_mask_profile);printf("\",\"values\":{");comma=0;
 number("schema",playback_mask_profile.schema);
 number("scope_bytes",playback_mask_profile.scope_bytes);
 number("native_frame",playback_mask_profile.native_frame);
 number("cpu_image",playback_mask_profile.cpu_image);
 number("caller_bytes",playback_mask_profile.caller_bytes);
 number("combined_span",playback_mask_profile.combined_span);
 number("fit_lr",playback_mask_profile.fit_lr);
 number("output_offset",playback_mask_profile.output_offset);
 number("guide_lr",playback_mask_profile.guide_lr);
 number("guide_span",playback_mask_profile.guide_span);
 number("guide_geometry",playback_mask_profile.guide_geometry);
 number("guide_return",playback_mask_profile.guide_return);
 number("guide_saved_geometry",playback_mask_profile.guide_saved_geometry);
 number("guide_handle",playback_mask_profile.guide_handle);
 number("guide_resource",playback_mask_profile.guide_resource);
 number("display_word",playback_mask_profile.display_word);
 number("orientation_word",playback_mask_profile.orientation_word);
 number("category_word",playback_mask_profile.category_word);
 number("clear_color",playback_mask_profile.clear_color);
 number("native_aspect_valid",playback_mask_profile.native_aspect_valid);
 number("native_header_set",playback_mask_profile.native_header_set);
 number("native_model_match",playback_mask_profile.native_model_match);
 number("thumb_roi_offset",playback_mask_profile.thumb_roi_offset);
 number("fit_facts_bytes",playback_mask_profile.fit_facts_bytes);
 number("guide_facts_bytes",playback_mask_profile.guide_facts_bytes);
 number("view_owner_bytes",playback_mask_profile.view_owner_bytes);
 number("observation_words",playback_mask_profile.observation_words);
 number("geometry_bytes",playback_mask_profile.geometry_bytes);
 number("projection_facts_bytes",playback_mask_profile.projection_facts_bytes);
 number("container_count",playback_mask_profile.container_count);
 printf("}},\"containers\":{\"entry_bytes\":%zu,\"count\":%zu,\"bytes\":%zu,\"hex\":\"",sizeof playback_mask_containers[0],sizeof playback_mask_containers/sizeof playback_mask_containers[0],sizeof playback_mask_containers);hexbytes(playback_mask_containers,sizeof playback_mask_containers);
 printf("\",\"rows\":[");
 for(size_t i=0;i<sizeof playback_mask_containers/sizeof playback_mask_containers[0];i++){
  const struct playback_mask_container_profile *v=&playback_mask_containers[i];
  printf("%s{",i?",":"");comma=0;number("selector",v->selector);number("token_offset",v->token_offset);number("width_offset",v->width_offset);number("height_offset",v->height_offset);putchar('}');
 }
 printf("]}},\"slots\":{\"count\":%u,\"metadata_count\":%u,\"cache_index\":%u,\"objects\":[",PLAYBACK_TAG_SLOT_COUNT,playback_native_profile.metadata_count,playback_native_profile.cache_slot);
 for(unsigned i=0;i<playback_native_profile.metadata_count;i++)printf("%s%" PRIu32,i?",":"",playback_native_profile.objects[i]);
 printf(",%" PRIu32 "]},\"layouts\":{",playback_native_profile.cache_key);comma=0;
 number("facts_bytes",sizeof(struct playback_box_facts));number("facts_words",PLAYBACK_TAG_FACT_WORDS);
 number("tag_record_bytes",sizeof(struct playback_tag_record));number("tag_record_alignment",_Alignof(struct playback_tag_record));number("tag_owner_bytes",sizeof(struct playback_tags));
 number("view_owner_bytes",sizeof(struct playback_mask_view_owner));number("view_owner_alignment",_Alignof(struct playback_mask_view_owner));
 /* Pointer-containing host scopes are intentionally NOT exported as native. */
 number("native_scope_bytes",playback_native_profile.main_scope_bytes);number("mask_scope_bytes",playback_mask_profile.scope_bytes);
 printf("},\"fields\":{");
 printf("\"box_facts\":{\"words\":%zu,\"members\":{",sizeof(struct playback_box_facts)/4u);comma=0;
 FIELD(playback_box_facts,words);
 FIELD(playback_box_facts,current_width);
 FIELD(playback_box_facts,current_height);
 printf("}}");
 printf(",\"native_observation\":{\"words\":%zu,\"members\":{",sizeof(struct playback_native_observation)/4u);comma=0;
 FIELD(playback_native_observation,main_entered);
 FIELD(playback_native_observation,main_admitted);
 FIELD(playback_native_observation,tag_entered);
 FIELD(playback_native_observation,tag_captured);
 FIELD(playback_native_observation,copy_entered);
 FIELD(playback_native_observation,copy_published);
 FIELD(playback_native_observation,cache_entered);
 FIELD(playback_native_observation,cache_published);
 printf("}}");
 printf(",\"mask_observation\":{\"words\":%zu,\"members\":{",sizeof(struct playback_mask_observation)/4u);comma=0;
 FIELD(playback_mask_observation,entered);
 FIELD(playback_mask_observation,admitted);
 FIELD(playback_mask_observation,projected);
 FIELD(playback_mask_observation,crop_candidate);
 FIELD(playback_mask_observation,guide_hidden);
 FIELD(playback_mask_observation,rejected);
 FIELD(playback_mask_observation,view_ready);
 FIELD(playback_mask_observation,view_revision);
 FIELD(playback_mask_observation,object_revision);
 FIELD(playback_mask_observation,cache_revision);
 printf("}}");
 printf(",\"read_facts\":{\"words\":%zu,\"members\":{",sizeof(struct playback_native_read_facts)/4u);comma=0;
 FIELD(playback_native_read_facts,entry_lr);
 FIELD(playback_native_read_facts,context);
 FIELD(playback_native_read_facts,selector);
 FIELD(playback_native_read_facts,request);
 FIELD(playback_native_read_facts,object);
 FIELD(playback_native_read_facts,r4);
 FIELD(playback_native_read_facts,r5);
 FIELD(playback_native_read_facts,r6);
 FIELD(playback_native_read_facts,r7);
 printf("}}");
 printf(",\"tag_facts\":{\"words\":%zu,\"members\":{",sizeof(struct playback_native_tag_facts)/4u);comma=0;
 FIELD(playback_native_tag_facts,site);
 FIELD(playback_native_tag_facts,status);
 FIELD(playback_native_tag_facts,inner);
 FIELD(playback_native_tag_facts,requested);
 FIELD(playback_native_tag_facts,handle);
 FIELD(playback_native_tag_facts,magic);
 FIELD(playback_native_tag_facts,bytes);
 FIELD(playback_native_tag_facts,tag);
 FIELD(playback_native_tag_facts,type);
 FIELD(playback_native_tag_facts,count);
 FIELD(playback_native_tag_facts,scope_object);
 FIELD(playback_native_tag_facts,scope_known);
 printf("}}");
 printf(",\"fit_facts\":{\"words\":%zu,\"members\":{",sizeof(struct playback_mask_fit_facts)/4u);comma=0;
 FIELD(playback_mask_fit_facts,entry_lr);
 FIELD(playback_mask_fit_facts,original_sp);
 FIELD(playback_mask_fit_facts,output);
 FIELD(playback_mask_fit_facts,object);
 FIELD(playback_mask_fit_facts,saved_object);
 FIELD(playback_mask_fit_facts,selector);
 FIELD(playback_mask_fit_facts,saved_selector);
 FIELD(playback_mask_fit_facts,drawmode);
 FIELD(playback_mask_fit_facts,rotation);
 FIELD(playback_mask_fit_facts,category);
 FIELD(playback_mask_fit_facts,view);
 FIELD(playback_mask_fit_facts,object_known);
 FIELD(playback_mask_fit_facts,cache_known);
 FIELD(playback_mask_fit_facts,object_facts);
 FIELD(playback_mask_fit_facts,cache_facts);
 printf("}}");
 printf(",\"guide_facts\":{\"words\":%zu,\"members\":{",sizeof(struct playback_mask_guide_facts)/4u);comma=0;
 FIELD(playback_mask_guide_facts,native_sp);
 FIELD(playback_mask_guide_facts,saved_lr);
 FIELD(playback_mask_guide_facts,geometry);
 FIELD(playback_mask_guide_facts,saved_geometry);
 FIELD(playback_mask_guide_facts,resources);
 FIELD(playback_mask_guide_facts,handle);
 FIELD(playback_mask_guide_facts,view);
 printf("}}");
 printf(",\"geometry\":{\"words\":%zu,\"members\":{",sizeof(struct playback_mask_geometry)/4u);comma=0;
 FIELD(playback_mask_geometry,pixel_token);FIELD(playback_mask_geometry,width);FIELD(playback_mask_geometry,height);FIELD(playback_mask_geometry,stock);
 printf("}}");
 printf(",\"projection_facts\":{\"words\":%zu,\"members\":{",sizeof(struct playback_mask_projection_facts)/4u);comma=0;
 FIELD(playback_mask_projection_facts,selector);FIELD(playback_mask_projection_facts,width);FIELD(playback_mask_projection_facts,height);FIELD(playback_mask_projection_facts,stock_result);FIELD(playback_mask_projection_facts,stock);FIELD(playback_mask_projection_facts,numerator);FIELD(playback_mask_projection_facts,denominator);
 printf("}}");
 printf(",\"container_profile\":{\"words\":%zu,\"members\":{",sizeof(struct playback_mask_container_profile)/4u);comma=0;
 FIELD(playback_mask_container_profile,selector);FIELD(playback_mask_container_profile,token_offset);FIELD(playback_mask_container_profile,width_offset);FIELD(playback_mask_container_profile,height_offset);
 printf("}}");
 printf("},\"box_kinds\":{");comma=0;number("PB_BOX_UNKNOWN",PB_BOX_UNKNOWN);number("PB_BOX_RAW_L",PB_BOX_RAW_L);number("PB_BOX_JPEG_L",PB_BOX_JPEG_L);
 printf("},\"report\":{");comma=0;number("schema",PLAYBACK_REPORT_SCHEMA);number("sequence_max",EXPORT_REFRESH_FORCED_MAX);number("native_words",PLAYBACK_REPORT_NATIVE_WORDS);number("mask_words",PLAYBACK_REPORT_MASK_WORDS);number("facts_words",PLAYBACK_TAG_FACT_WORDS);
 number("max_records",3u+(PLAYBACK_REPORT_NATIVE_WORDS+3u)/4u+(PLAYBACK_REPORT_MASK_WORDS+3u)/4u+PLAYBACK_TAG_SLOT_COUNT*(1u+(PLAYBACK_TAG_FACT_WORDS+3u)/4u));
 printf("},\"formats\":{\"begin\":\"%s\",\"source\":\"%s\",\"native0\":\"%s\",\"native4\":\"%s\",\"mask0\":\"%s\",\"mask4\":\"%s\",\"mask8\":\"%s\",\"tag\":\"%s\",\"words0\":\"%s\",\"words4\":\"%s\",\"end\":\"%s\"}",PP_FMT_BEGIN,PP_FMT_SOURCE,PP_FMT_NATIVE0,PP_FMT_NATIVE4,PP_FMT_MASK0,PP_FMT_MASK4,PP_FMT_MASK8,PP_FMT_TAG,PP_FMT_WORDS0,PP_FMT_WORDS4,PP_FMT_END);
 rejection_model();first_view_model();printf("}\n");
 return ferror(stdout)?3:0;
}
