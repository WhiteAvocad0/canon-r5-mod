#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include "frame_native_trace.h"
#include "export_refresh.h"
#include "frame_memory.h"
#include "frame-row/frame_row_constants.h"

static unsigned comma;
static void number(const char *name, size_t value)
{printf("%s\"%s\":%zu",comma++?",":"",name,value);}
static void field(const char *name, size_t offset, size_t bytes)
{printf("%s\"%s\":{\"word\":%zu,\"count\":%zu}",comma++?",":"",name,offset/4u,bytes/4u);}
#define PROFILE(name) number(#name,frame_native_profile.name)
#define FACT(name) field(#name,offsetof(struct frame_native_facts,name),sizeof(((struct frame_native_facts *)0)->name))
#define CAPTURE(name) field(#name,offsetof(struct frame_native_capture_facts,name),sizeof(((struct frame_native_capture_facts *)0)->name))
#define TRANSPORT(name,id) number(name,id)

int main(void)
{
 uint32_t endian=1u;
 if(*(const unsigned char *)&endian!=1u)return 2;
 printf("{\"schema\":\"canon-r5-lab.framing-c-model.v1\",\"profile\":{");comma=0;
 PROFILE(schema);PROFILE(dts_control);PROFILE(dts_default);PROFILE(dts_table);PROFILE(slot_count);PROFILE(slot_bytes);
 PROFILE(descriptors);PROFILE(descriptor_stride);PROFILE(descriptor_count);PROFILE(packet_table);PROFILE(packet_count);PROFILE(orca_slot);
 PROFILE(capture_lr_a);PROFILE(capture_lr_b);PROFILE(row_lr);PROFILE(row_outer_lr);PROFILE(offset_lr);
 PROFILE(mode_base);PROFILE(mode_stride);PROFILE(mode_count);PROFILE(mode_index);PROFILE(raw_offset);
 PROFILE(row_output);PROFILE(row_outer_return);PROFILE(offset_x);PROFILE(offset_y);PROFILE(offset_packet);PROFILE(offset_prefix);
 PROFILE(offset_token);PROFILE(offset_descriptor);PROFILE(snapshot_frame);PROFILE(refresh_frame);
 PROFILE(copy_scope_bytes);PROFILE(reset_scope_bytes);PROFILE(role_count);PROFILE(transport_words);PROFILE(policy_words);PROFILE(capture_words);
 printf(",\"offset_returns\":[");for(unsigned i=0;i<4u;i++)printf("%s%" PRIu32,i?",":"",frame_native_profile.offset_returns[i]);
 printf("],\"offset_return_values\":[");for(unsigned i=0;i<4u;i++)printf("%s%" PRIu32,i?",":"",frame_native_profile.offset_return_values[i]);
 printf("]},\"profile_hex\":\"");
 const unsigned char *bytes=(const unsigned char *)&frame_native_profile;
 for(size_t i=0;i<sizeof frame_native_profile;i++)printf("%02x",bytes[i]);
 printf("\",\"lcd_rows\":[");
 for(unsigned row=0;row<2u;row++){
  printf("%s{\"original\":[",row?",":"");
  for(unsigned word=0;word<12u;word++)printf("%s%" PRIu32,word?",":"",xpan_frame_row_cases[row].original[word]);
  printf("],\"projected\":[");
  for(unsigned word=0;word<12u;word++)printf("%s%" PRIu32,word?",":"",xpan_frame_row_cases[row].projected[word]);
  printf("]}");
 }
 printf("],\"facts\":{\"words\":%zu,\"fields\":{",sizeof(struct frame_native_facts)/4u);comma=0;
 FACT(role);FACT(entry_lr);FACT(native_sp);FACT(output_x);FACT(output_y);FACT(context);FACT(display);FACT(descriptor);FACT(descriptor_index);
 FACT(descriptor_context);FACT(outer_lr);FACT(packet);FACT(packet_token_low);FACT(packet_token_high);
 FACT(descriptor_token_low);FACT(descriptor_token_high);FACT(outer_token_low);FACT(outer_token_high);FACT(saved_returns);
 FACT(prefix);FACT(orca_owner);FACT(orca_mode);FACT(orca_aspect);FACT(packet_display);
 FACT(mode);FACT(rich);FACT(flags38);FACT(flags_eb0);FACT(prohibit);FACT(quality);FACT(flag410);FACT(flag418);FACT(display_mask);
 FACT(slot_index);FACT(native_frame);FACT(tag_pointer);FACT(tag_frame);FACT(tag_raw);FACT(tag_revision);FACT(tag_ready);FACT(tag_same);
 FACT(original_return);FACT(original_x);FACT(original_y);FACT(row);
 printf("}},\"capture\":{\"words\":%zu,\"fields\":{",sizeof(struct frame_native_capture_facts)/4u);comma=0;
 CAPTURE(entry_lr);CAPTURE(offset);CAPTURE(second_offset);CAPTURE(source);CAPTURE(length);CAPTURE(original_r4);CAPTURE(index);
 CAPTURE(raw_before);CAPTURE(raw_after);CAPTURE(writer_count);CAPTURE(index_after);CAPTURE(writer_count_after);
 printf("}},\"transport\":{\"words\":%u,\"fields\":{",FRAME_NATIVE_FACT_WORDS);comma=0;
 TRANSPORT("role",FN_T_ROLE);TRANSPORT("site",FN_T_SITE);TRANSPORT("native_sp",FN_T_NATIVE_SP);TRANSPORT("context",FN_T_CONTEXT);
 TRANSPORT("source",FN_T_SOURCE);TRANSPORT("destination",FN_T_DESTINATION);TRANSPORT("frame",FN_T_FRAME);TRANSPORT("raw",FN_T_RAW);
 TRANSPORT("revision",FN_T_REVISION);TRANSPORT("index",FN_T_INDEX);TRANSPORT("known",FN_T_KNOWN);TRANSPORT("descriptor",FN_T_DESCRIPTOR);
 TRANSPORT("packet",FN_T_PACKET);TRANSPORT("display",FN_T_DISPLAY);TRANSPORT("mode",FN_T_MODE);TRANSPORT("rich",FN_T_RICH);
 TRANSPORT("row_case",FN_T_ROW_CASE);TRANSPORT("native_return",FN_T_NATIVE_RETURN);TRANSPORT("native_x",FN_T_NATIVE_X);
 TRANSPORT("native_y",FN_T_NATIVE_Y);TRANSPORT("projected_y",FN_T_PROJECTED_Y);TRANSPORT("source_frame",FN_T_SOURCE_FRAME);
 TRANSPORT("source_revision",FN_T_SOURCE_REVISION);TRANSPORT("enabled",FN_T_ENABLED);
 printf("}},\"role_ids\":{");comma=0;
 number("capture",FN_ROLE_CAPTURE);number("default_copy",FN_ROLE_DEFAULT);number("previous_copy",FN_ROLE_PREVIOUS);
 number("refresh",FN_ROLE_REFRESH);number("reset",FN_ROLE_RESET);number("row",FN_ROLE_ROW);number("offset",FN_ROLE_OFFSET);
 printf("},\"snapshot_kind\":{");comma=0;number("identified",FN_FIRST_IDENTIFIED);number("applied",FN_FIRST_APPLIED);
 printf("},\"snapshot_state\":{");comma=0;number("empty",FN_TRACE_EMPTY);number("writing",FN_TRACE_WRITING);number("ready",FN_TRACE_READY);
 printf("},\"role_words\":[");for(unsigned i=0;i<FN_ROLE_COUNT;i++)printf("%s%u",i?",":"",frame_native_trace_word_count(i));
 printf("],\"formats\":{\"begin\":\"%s\",\"source\":\"%s\",\"disabled\":\"%s\",\"counts\":\"%s\",\"ready\":\"%s\",\"words\":\"%s\",\"tail\":\"%s\",\"end\":\"%s\"}",
  FN_FMT_BEGIN,FN_FMT_SOURCE,FN_FMT_DISABLED,FN_FMT_COUNTS,FN_FMT_READY,FN_FMT_WORDS,FN_FMT_TAIL,FN_FMT_END);
 printf(",\"layout\":{");comma=0;
 number("profile_bytes",sizeof frame_native_profile);number("tag_record_bytes",sizeof(struct frame_tag_record));
 number("tag_record_alignment",_Alignof(struct frame_tag_record));number("tag_owner_bytes",sizeof(struct frame_tags));
 number("copy_scope_bytes",FN_COPY_SCOPE_BYTES);number("reset_scope_bytes",FN_RESET_SCOPE_BYTES);
 printf("},\"refresh\":{");comma=0;
 number("ordinary_max",EXPORT_REFRESH_ORDINARY_MAX);number("forced_max",EXPORT_REFRESH_FORCED_MAX);
 number("polls",EXPORT_REFRESH_POLLS);number("sleep_ms",EXPORT_REFRESH_SLEEP_MS);
 number("context_bytes",sizeof(struct export_refresh_context));number("control_bytes",sizeof(struct export_refresh_state));
 number("ok",EXPORT_REFRESH_OK);number("exhausted",EXPORT_REFRESH_EXHAUSTED);number("unsupported",EXPORT_REFRESH_UNSUPPORTED);
 printf("},\"memory\":{\"profile_bytes\":%zu,\"profile_hex\":\"",sizeof frame_memory_profile);
 bytes=(const unsigned char *)&frame_memory_profile;
 for(size_t i=0;i<sizeof frame_memory_profile;i++)printf("%02x",bytes[i]);
 printf("\",\"profile\":{");comma=0;
#define MEMORY(name) number(#name,frame_memory_profile.name)
 MEMORY(data_begin);MEMORY(data_end);MEMORY(irq0_begin);MEMORY(irq0_end);MEMORY(irq1_begin);MEMORY(irq1_end);
#undef MEMORY
 printf("},\"row_stack_bytes\":%" PRIu32 ",\"offset_stack_bytes\":%" PRIu32 "}}\n",
  frame_native_stack_extent(FN_ROLE_ROW),frame_native_stack_extent(FN_ROLE_OFFSET));return 0;
}
