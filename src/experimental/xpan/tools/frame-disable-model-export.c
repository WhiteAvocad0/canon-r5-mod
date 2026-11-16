#include <stdio.h>
#include <stddef.h>
#include "frame_disable_trace.h"
static unsigned comma;
static void number(const char *name,unsigned value){printf("%s\"%s\":%u",comma++?",":"",name,value);}
#define ENUM(name) number(#name,name)
#define PROFILE(name) number(#name,frame_disable_profile.name)
int main(void){
 printf("{\"profile\":{");comma=0;
 PROFILE(schema);PROFILE(words);PROFILE(facts_bytes);PROFILE(record_bytes);PROFILE(record_alignment);
 PROFILE(state_offset);PROFILE(facts_offset);PROFILE(ready_state);PROFILE(known_all);PROFILE(reason_count);PROFILE(site_count);
 printf("},\"profile_hex\":\"");const unsigned char *p=(const unsigned char *)&frame_disable_profile;
 for(size_t i=0;i<sizeof frame_disable_profile;i++)printf("%02x",p[i]);
 printf("\",\"reasons\":{");comma=0;
 ENUM(FD_NONE);ENUM(FD_EXTERNAL);ENUM(FD_SAVED_LR_READ);ENUM(FD_SAVED_LR_MISMATCH);ENUM(FD_SCOPE_POINTER);
 ENUM(FD_SCOPE_MARKER);ENUM(FD_SCOPE_SELF);ENUM(FD_SCOPE_NATIVE_SP);ENUM(FD_SCOPE_CONTINUATION);ENUM(FD_SCOPE_KIND);
 ENUM(FD_DUPLICATE_BEGIN);ENUM(FD_BEGIN_CONTROL_REGISTER);ENUM(FD_BEGIN_SIZE_REGISTER);ENUM(FD_BEGIN_TABLE_REGISTER);
 ENUM(FD_BEGIN_INDEX_READ);ENUM(FD_BEGIN_INDEX_MISMATCH);ENUM(FD_BEGIN_SLOT_INDEX);ENUM(FD_BEGIN_SLOT_READ);
 ENUM(FD_BEGIN_SLOT_POINTER);ENUM(FD_BEGIN_DESTINATION);ENUM(FD_BEGIN_SOURCE);ENUM(FD_PUBLISH_WITHOUT_BEGIN);
 ENUM(FD_PUBLISH_STARTED);ENUM(FD_PUBLISH_CONTROL_REGISTER);ENUM(FD_PUBLISH_TABLE_REGISTER);ENUM(FD_PUBLISH_SLOT_INDEX);
 ENUM(FD_PUBLISH_SLOT_READ);ENUM(FD_PUBLISH_SLOT_POINTER);ENUM(FD_PUBLISH_DESTINATION);ENUM(FD_PUBLISH_FRAME_READ);
 ENUM(FD_PUBLISH_PRESERVED_FRAME);ENUM(FD_PUBLISH_RESTORED_FRAME);ENUM(FD_END_SCOPE_POINTER);ENUM(FD_END_SCOPE_MARKER);
 ENUM(FD_END_SCOPE_SELF);ENUM(FD_RESET_SCOPE_POINTER);ENUM(FD_RESET_SCOPE_MARKER);ENUM(FD_RESET_SCOPE_SELF);
 ENUM(FD_ROW_WRITE);ENUM(FD_OFFSET_WRITE);
 printf("},\"sites\":{");comma=0;ENUM(FD_SITE_EXTERNAL);ENUM(FD_SITE_COPY_DEFAULT);ENUM(FD_SITE_COPY_PREVIOUS);
 ENUM(FD_SITE_COPY_REFRESH);ENUM(FD_SITE_PUBLISH_SNAPSHOT);ENUM(FD_SITE_PUBLISH_REFRESH);ENUM(FD_SITE_COPY_END);
 ENUM(FD_SITE_RESET_END);ENUM(FD_SITE_ROW);ENUM(FD_SITE_OFFSET);
 printf("},\"fields\":{");comma=0;
 number("reason",FD_W_REASON);number("site",FD_W_SITE);number("native_sp",FD_W_NATIVE_SP);number("scope_ptr",FD_W_SCOPE_PTR);
 number("actual_saved_lr",FD_W_ACTUAL_SAVED_LR);number("expected_saved_lr",FD_W_EXPECTED_SAVED_LR);
 number("argument_index",FD_W_ARGUMENT_INDEX);number("current_index",FD_W_CURRENT_INDEX);
 number("recorded_destination",FD_W_RECORDED_DESTINATION);number("selected_destination",FD_W_SELECTED_DESTINATION);
 number("marker",FD_W_MARKER);number("self",FD_W_SELF);number("kind",FD_W_KIND);number("known_mask",FD_W_KNOWN_MASK);
 number("aux_a",FD_W_AUX_A);number("aux_b",FD_W_AUX_B);
 printf("},\"states\":{");comma=0;number("empty",FD_EMPTY);number("writing",FD_WRITING);number("ready",FD_READY);
 printf("},\"formats\":{\"begin\":\"%s\",\"source\":\"%s\",\"state\":\"%s\",\"words\":\"%s\",\"end\":\"%s\"}}\n",
 FD_FMT_BEGIN,FD_FMT_SOURCE,FD_FMT_STATE,FD_FMT_WORDS,FD_FMT_END);return 0;
}
