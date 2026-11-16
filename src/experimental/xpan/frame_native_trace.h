#ifndef R5_FRAME_NATIVE_TRACE_H
#define R5_FRAME_NATIVE_TRACE_H
#include "frame_native.h"
#define FN_FMT_BEGIN "R5F1 B=%u V=%u"
#define FN_FMT_SOURCE "R5F1 S=" OBSERVER_SOURCE_ID
#define FN_FMT_DISABLED "R5F1 D=%u"
#define FN_FMT_COUNTS "R5F1 C=%u N=%u I=%u A=%u X=%u"
#define FN_FMT_READY "R5F1 R=%u K=%u S=%u N=%u"
#define FN_FMT_WORDS "R5F1 W=%u A=%08X B=%08X C=%08X D=%08X"
#define FN_FMT_TAIL "R5F1 W=%u A=%08X B=%08X C=%08X"
#define FN_FMT_END "R5F1 E=%u"
enum frame_native_trace_kind {FN_FIRST_IDENTIFIED=0,FN_FIRST_APPLIED=1};
enum frame_native_trace_state {FN_TRACE_EMPTY=0,FN_TRACE_WRITING=1,FN_TRACE_READY=2};
struct frame_native_counts {uint32_t entered,identified,applied,rejected;};
/* Transport/reset roles use this24-word schema. CAPTURE uses the complete
 * frame_native_capture_facts12 words; ROW/OFFSET use frame_native_facts59.
 * Unknown optional transport facts are zero with KNOWN=0. */
enum frame_native_trace_word {FN_T_ROLE=0,FN_T_SITE,FN_T_NATIVE_SP,FN_T_CONTEXT,
 FN_T_SOURCE,FN_T_DESTINATION,FN_T_FRAME,FN_T_RAW,FN_T_REVISION,FN_T_INDEX,
 FN_T_KNOWN,FN_T_DESCRIPTOR,FN_T_PACKET,FN_T_DISPLAY,FN_T_MODE,FN_T_RICH,
 FN_T_ROW_CASE,FN_T_NATIVE_RETURN,FN_T_NATIVE_X,FN_T_NATIVE_Y,FN_T_PROJECTED_Y,
 FN_T_SOURCE_FRAME,FN_T_SOURCE_REVISION,FN_T_ENABLED};
void frame_native_trace_enter(uint32_t role);
void frame_native_trace_observe(uint32_t role,const void *facts,uint32_t applied);
void frame_native_trace_reject(uint32_t role);
uint32_t frame_native_trace_counts(uint32_t role,struct frame_native_counts *out);
uint32_t frame_native_trace_word_count(uint32_t role);
uint32_t frame_native_trace_snapshot(uint32_t role,uint32_t kind,uint32_t out[FRAME_NATIVE_MAX_WORDS]);
#endif
