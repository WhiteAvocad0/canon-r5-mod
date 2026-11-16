/* Host-only expectations from the actual linked records and shared C owners. */
#include "feature-config.h"
#include "menu-vector-source.h"
#include "root_plan.h"
#include "multi_plan.h"
#include "code_patch.h"
#include "heap_span.h"
#include "boot_policy.h"
#include "menu_table.h"
#include "menu_native.h"
#if !defined(R5_CROP_ONLY) || !R5_CROP_ONLY
#include "pcm_runtime.h"
#include "packed_pcm.h"
#endif
#include "heap_native.h"
#include "feature_pages.h"
#include "jpeg_crop_trace.h"
#include "rect_crop.h"
#include "geometry.h"
#include <stdio.h>
#include <stdlib.h>
_Static_assert(sizeof(struct multi_target)==8 && sizeof(struct code_patch)==28,"linked record ABI");
static int read_at(FILE *f,unsigned long at,void *p,size_t bytes)
{return !fseek(f,(long)at,SEEK_SET)&&fread(p,1,bytes,f)==bytes;}
static unsigned long number(const char *p){return strtoul(p,0,0);}
static void words(const uint32_t *p,unsigned n)
{putchar('[');for(unsigned i=0;i<n;i++)printf("%s%u",i?",":"",p[i]);putchar(']');}
#define KV(label,value) printf("\"" label "\":%u,",(uint32_t)(value))
#define ENUM(value) KV(#value,value)
int main(int argc,char **argv)
{
 if(argc!=13)return 1;
 FILE *rom=fopen(argv[1],"rb"),*native=fopen(argv[2],"rb");if(!rom||!native)return 2;
 const uint16_t endian=1;if(*(const unsigned char *)&endian!=1)return 3;
 struct multi_target targets[FEATURE_PAGE_COUNT];struct code_patch patches[FEATURE_PATCH_COUNT];
 uint32_t checksums[FEATURE_PAGE_COUNT],original[64],heap[32];
#if !defined(R5_CROP_ONLY) || !R5_CROP_ONLY
 uint32_t roles[8];
#endif
 struct feature_witness witnesses[FEATURE_WITNESS_COUNT];
 if(!read_at(native,number(argv[3]),targets,sizeof targets)||
  !read_at(native,number(argv[4]),patches,sizeof patches)||
  !read_at(native,number(argv[5]),checksums,sizeof checksums)||
#if !defined(R5_CROP_ONLY) || !R5_CROP_ONLY
  !read_at(native,number(argv[6]),roles,sizeof roles)||
#endif
  !read_at(rom,0x3800,original,sizeof original)||
  !read_at(rom,0x4840,heap,64)||!read_at(rom,0x80,heap+16,64)||
  !read_at(native,number(argv[12]),witnesses,sizeof witnesses))return 4;
 uint32_t root=number(argv[7]),table=number(argv[8]),start=number(argv[9]),init_end=number(argv[10]),end=number(argv[11]);
 uint32_t parents[16],children[MULTI_MAX_TABLES][256]={{0}};struct multi_shape shape;
 if(!multi_rom_supported(original,4u)||!multi_layout_supported(root,table,targets,FEATURE_PAGE_COUNT,start,end) ||
  multi_plan_build(original,targets,FEATURE_PAGE_COUNT,table,parents,children,&shape) ||
  !multi_plan_matches(parents,children,targets,FEATURE_PAGE_COUNT,table,&shape))return 5;
#if defined(R5_CROP_ONLY) && R5_CROP_ONLY
 if(number(argv[6])!=0u||!heap_span_mapping_supported(0x1fff000u,MENU_ALLOCATION_BYTES,heap,32u))return 6;
 uint32_t min=R5_HEAP_BASE;for(unsigned n=0;n<64u&&!heap_span_supported(min,MENU_ALLOCATION_BYTES);n++)min+=4u;
 if(!heap_span_supported(min,MENU_ALLOCATION_BYTES))return 7;
#else
 uint32_t request=pcm_heap_request(roles[2],roles[6]);
 if(!request||!heap_span_mapping_supported(0x1fff000u,request,heap,32u))return 6;
 uint32_t min=R5_HEAP_BASE;for(unsigned n=0;n<64u&&!heap_span_supported(min,request);n++)min+=4u;
 if(!heap_span_supported(min,request))return 7;
#endif
 printf("{");KV("feature_patch_count",FEATURE_PATCH_COUNT);
 printf("\"source_rom_parent_groups_admitted\":true,\"profile_words\":");words((const uint32_t *)&root_target_profile,12);
 printf(",\"witnesses\":[");
 for(unsigned n=0;n<FEATURE_WITNESS_COUNT;n++) {
  uint32_t page[1024];
  if(!read_at(rom,witnesses[n].va-0xe0000000u,page,sizeof page)||root_page_checksum(page)!=witnesses[n].checksum)return 11;
  printf("%s{\"va\":%u,\"checksum\":%u}",n?",":"",witnesses[n].va,witnesses[n].checksum);
 }
 printf("]");
 printf(",\"planned_parents\":");words(parents,16);
 printf(",\"table_sections\":");words(shape.sections,shape.count);
 printf(",\"planned_children\":[");for(unsigned n=0;n<shape.count;n++){if(n)putchar(',');words(children[n],256);}printf("],\"pages\":[");
 for(unsigned n=0;n<FEATURE_PAGE_COUNT;n++) {
  uint32_t page[1024];if(!read_at(rom,targets[n].va-0xe0000000u,page,sizeof page)||root_page_checksum(page)!=checksums[n])return 8;
  if(code_patch_apply((uint8_t *)page,targets[n].va,patches,FEATURE_PATCH_COUNT,start,init_end))return 9;
  printf("%s{\"va\":%u,\"pa\":%u,\"source_checksum\":%u,\"checksum\":%u,\"patched_hex\":\"",n?",":"",targets[n].va,targets[n].pa,checksums[n],root_page_checksum(page));
  for(unsigned i=0;i<4096;i++)printf("%02x",((const unsigned char *)page)[i]);printf("\"}");
 }
#if defined(R5_CROP_ONLY) && R5_CROP_ONLY
 printf("],\"roles\":[],\"menu\":{");
#else
 printf("],\"roles\":[");words(roles,4);putchar(',');words(roles+4,4);printf("],\"menu\":{");
#endif
 KV("count",MENU_COUNT);KV("target_id",MENU_TARGET_ID);KV("source_table",MENU_SOURCE_TABLE);KV("source_end",MENU_SOURCE_END);
 KV("source_target_prefix",MENU_SOURCE_TARGET_PREFIX);KV("source_target_bytes",MENU_SOURCE_TARGET_BYTES);
 KV("table_bytes",MENU_TABLE_BYTES);KV("table_reserved",MENU_TABLE_RESERVED);KV("prefix_reserved",MENU_PREFIX_RESERVED);
 KV("allocation_bytes",MENU_ALLOCATION_BYTES);KV("heap_base",R5_HEAP_BASE);KV("heap_end",R5_HEAP_END);
 KV("heap_min_raw",min);KV("heap_max_raw",(R5_HEAP_END-MENU_ALLOCATION_BYTES)&~3u);KV("vector_bytes",MENU_VECTOR_BYTES);
#if defined(R5_CROP_ONLY) && R5_CROP_ONLY
 printf("\"heap_supersections\":{\"1\":%u,\"2\":%u}},\"pcm\":{\"available\":false,\"role_count\":0,\"request\":0},\"rectangle_projection\":{",heap[0],heap[16]);
#else
 printf("\"heap_supersections\":{\"1\":%u,\"2\":%u}},\"pcm\":{\"available\":true,\"role_count\":2,",heap[0],heap[16]);
 KV("heap_base",R5_HEAP_BASE);KV("heap_end",R5_HEAP_END);KV("heap_min_raw",min);
 KV("heap_max_raw",(R5_HEAP_END-request)&~3u);KV("request",request);
 printf("\"heap_supersections\":{\"1\":%u,\"2\":%u},\"layout_offsets\":{",heap[0],heap[16]);
 for(unsigned offset=0;offset<32u;offset+=4u) {
  struct pcm_heap_extent extent;uint32_t sample=0x018ca000u+offset;
  if(!pcm_heap_layout(sample,roles[2],roles[6],&extent)||extent.request!=request)return 12;
  printf("%s\"%u\":[%u,%u,%u]",offset?",":"",offset,extent.xp-sample,extent.mambo-sample,extent.end-sample);
 }
 printf("}},\"rectangle_projection\":{");
#endif
 KV("output_bytes",XPAN_RECT_OUTPUT_BYTES);
 KV("low_large_admitted",xpan_rect_is_low_large(0x100u,0u,7u));
 KV("high_only_admitted",xpan_rect_is_low_large(0u,0x100u,7u));
 KV("outer_raw_admitted",xpan_rect_is_low_large(0x100u,0u,255u));
 uint32_t rectangle[12]={XPAN_LARGE_WIDTH,XPAN_STOCK_LARGE_HEIGHT,
  XPAN_LARGE_WIDTH,XPAN_STOCK_LARGE_HEIGHT,0x01600160u,0xa5a55a5au,
  XPAN_LARGE_WIDTH,XPAN_STOCK_LARGE_HEIGHT,XPAN_LARGE_WIDTH,XPAN_LARGE_FULL_HEIGHT,
  0x11223344u,0x55667788u};
 KV("sample_status",xpan_rect_project(0x100u,0u,7u,rectangle));
 printf("\"sample_output\":");words(rectangle,12u);
#if defined(R5_CROP_ONLY) && R5_CROP_ONLY
 printf("},\"phase_codes\":{},\"heap_codes\":{");
#else
 printf("},\"phase_codes\":{");
 ENUM(PCM_RUNTIME_NONE);ENUM(PCM_RUNTIME_BAD_CONTEXT);ENUM(PCM_RUNTIME_BAD_CORE);ENUM(PCM_RUNTIME_BAD_HEAP);
 ENUM(PCM_RUNTIME_BAD_SOURCE);ENUM(PCM_RUNTIME_BAD_DESCRIPTOR);ENUM(PCM_RUNTIME_BAD_REQUEST);ENUM(PCM_RUNTIME_ALLOCATING);
 ENUM(PCM_RUNTIME_ALLOC_FAILED);ENUM(PCM_RUNTIME_BAD_LAYOUT);ENUM(PCM_RUNTIME_BAD_MAPPING);ENUM(PCM_RUNTIME_XP_DECODE);
 ENUM(PCM_RUNTIME_XP_FAILED);ENUM(PCM_RUNTIME_MAMBO_DECODE);ENUM(PCM_RUNTIME_MAMBO_FAILED);ENUM(PCM_RUNTIME_PUBLISHING);
 printf("\"PCM_RUNTIME_READY\":%u},\"heap_codes\":{",PCM_RUNTIME_READY);
#endif
 ENUM(HEAP_NATIVE_UNOBSERVED);ENUM(HEAP_NATIVE_BAD_SPAN);ENUM(HEAP_NATIVE_BAD_REGISTERS);ENUM(HEAP_NATIVE_BAD_CORE);
 ENUM(HEAP_NATIVE_BAD_VIEW);ENUM(HEAP_NATIVE_BAD_PROFILE);ENUM(HEAP_NATIVE_BAD_ROOT_FACTS);ENUM(HEAP_NATIVE_BAD_MAPPING);
 printf("\"HEAP_NATIVE_ADMITTED\":%u},\"heap_flags\":{",HEAP_NATIVE_ADMITTED);
 ENUM(HEAP_OBS_CPSR);ENUM(HEAP_OBS_REGISTERS);ENUM(HEAP_OBS_MIDR);ENUM(HEAP_OBS_ROOT_ADMITTED);
#if defined(R5_CROP_ONLY) && R5_CROP_ONLY
 printf("\"HEAP_OBS_TABLE_ENTRIES\":%u},\"decode_codes\":{},\"decode_flags\":{},\"prepare_codes\":{",HEAP_OBS_TABLE_ENTRIES);
#else
 printf("\"HEAP_OBS_TABLE_ENTRIES\":%u},\"decode_codes\":{",HEAP_OBS_TABLE_ENTRIES);
 ENUM(PACKED_PCM_OK);ENUM(PACKED_PCM_BAD_ARGUMENT);ENUM(PACKED_PCM_BAD_ALIGNMENT);ENUM(PACKED_PCM_CAPACITY);
 ENUM(PACKED_PCM_DEFLATE_FAILED);ENUM(PACKED_PCM_INPUT_LENGTH);ENUM(PACKED_PCM_OUTPUT_LENGTH);
 printf("\"PACKED_PCM_CHECKSUM_MISMATCH\":%u},\"decode_flags\":{",PACKED_PCM_CHECKSUM_MISMATCH);
 ENUM(PACKED_PCM_COUNTS_OBSERVED);ENUM(PACKED_PCM_ADLER_OBSERVED);printf("\"PACKED_PCM_READY\":%u},\"prepare_codes\":{",PACKED_PCM_READY);
#endif
 ENUM(PREP_NONE);ENUM(PREP_BAD_CORE);ENUM(PREP_BAD_HEAP);ENUM(PREP_ALLOC_FAILED);ENUM(PREP_BAD_RANGE);ENUM(PREP_BAD_TABLE);ENUM(PREP_READY);
 printf("\"PREP_BAD_MAPPING\":%u},\"install_codes\":{",PREP_BAD_MAPPING);
 ENUM(INSTALL_NONE);ENUM(INSTALL_UNPREPARED);ENUM(INSTALL_BAD_ROOT);ENUM(INSTALL_BAD_GETTER);ENUM(INSTALL_BAD_CALLBACK);ENUM(INSTALL_BAD_READBACK);
 printf("\"INSTALL_OK\":%u},\"root_codes\":{",INSTALL_OK);
 ENUM(ROOT_NONE);ENUM(ROOT_BAD_CPU);ENUM(ROOT_BAD_OWNER);ENUM(ROOT_BAD_RAM);ENUM(ROOT_BAD_PAGE);ENUM(ROOT_BAD_PLAN);ENUM(ROOT_PREPARED);ENUM(ROOT_SWITCHED);ENUM(ROOT_SWITCH_FAILED);ENUM(ROOT_RESTORED);
 printf("\"ROOT_FATAL\":%u},\"boot_policy\":{",ROOT_FATAL);
 KV("mod",ROOT_DECISION_MOD);KV("stock",ROOT_DECISION_STOCK);KV("fatal",ROOT_DECISION_FATAL);
 KV("entry_mod",ROOT_ENTRY_MOD);KV("entry_stock",ROOT_ENTRY_STOCK);KV("entry_hold",ROOT_ENTRY_HOLD);
 printf("\"primary\":[");for(unsigned r=0;r<=ROOT_FATAL;r++)printf("%s[%u,%u,%u]",r?",":"",r,root_primary_decision(r,ROOT_ORIGINAL,root),root_primary_decision(r,root,root));
 printf("],\"secondary\":[");for(unsigned r=0;r<=ROOT_FATAL;r++)printf("%s[%u,%u,%u]",r?",":"",r,root_secondary_action(ROOT_DECISION_STOCK,r,ROOT_ORIGINAL,root),root_secondary_action(ROOT_DECISION_MOD,r,root,root));
 printf("]},\"jpeg_trace\":{");
 KV("schema",JPEG_CROP_TRACE_SCHEMA);KV("roles",JPEG_CROP_TRACE_ROLES);KV("kinds",JPEG_CROP_TRACE_KINDS);
 KV("jpeg_words",JPEG_CROP_TRACE_JPEG_WORDS);KV("roi_words",JPEG_CROP_TRACE_ROI_WORDS);
 KV("primary",JPEG_TRACE_PRIMARY);KV("secondary",JPEG_TRACE_SECONDARY);KV("roi",JPEG_TRACE_ROI);
 KV("identified",JPEG_TRACE_IDENTIFIED);KV("applied",JPEG_TRACE_APPLIED);
 KV("empty",JPEG_TRACE_EMPTY);KV("writing",JPEG_TRACE_WRITING);KV("ready",JPEG_TRACE_READY);
 printf("\"jpeg_word_indices\":{");
 ENUM(JT_CONSUMER);ENUM(JT_ENTRY_LR);ENUM(JT_NATIVE_SP);ENUM(JT_JOB);ENUM(JT_PARAM);ENUM(JT_OUTER_LR);
 ENUM(JT_DESCRIPTOR);ENUM(JT_REQUEST);ENUM(JT_ENGINE);ENUM(JT_MODE);ENUM(JT_TIER);ENUM(JT_ASPECT);
 ENUM(JT_RAW_ASPECT);ENUM(JT_INPUT_LOW);ENUM(JT_INPUT_HIGH);ENUM(JT_REQUEST_INPUT_LOW);
 printf("\"JT_REQUEST_INPUT_HIGH\":%u},\"roi_word_indices\":{",JT_REQUEST_INPUT_HIGH);
 ENUM(RT_SELECTED);ENUM(RT_STATE);ENUM(RT_NATIVE_SP);ENUM(RT_ARGUMENT);ENUM(RT_CALLER_LR);ENUM(RT_REQUEST);
 ENUM(RT_RAW_ASPECT);ENUM(RT_TIER);ENUM(RT_ASPECT);ENUM(RT_MODE);ENUM(RT_FLAG16);ENUM(RT_FLAG20);
 ENUM(RT_SELECTION);ENUM(RT_INPUT_RECORD);ENUM(RT_CALLBACK);ENUM(RT_CALLBACK_ARG);ENUM(RT_REQUEST_CALLBACK);
 printf("\"RT_REQUEST_CALLBACK_ARG\":%u}}}\n",RT_REQUEST_CALLBACK_ARG);
 fclose(rom);fclose(native);return ferror(stdout)?10:0;
}
