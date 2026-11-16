#ifndef R5_XPAN_JPEG_CROP_TRACE_H
#define R5_XPAN_JPEG_CROP_TRACE_H
#include "jpeg_crop.h"

#define JPEG_CROP_TRACE_SCHEMA 1u
#define JPEG_CROP_TRACE_ROLES 3u
#define JPEG_CROP_TRACE_KINDS 2u
#define JPEG_CROP_TRACE_JPEG_WORDS 17u
#define JPEG_CROP_TRACE_ROI_WORDS 18u
#define JPEG_CROP_TRACE_MAX_WORDS 18u
enum jpeg_crop_trace_role { JPEG_TRACE_PRIMARY=0,JPEG_TRACE_SECONDARY=1,JPEG_TRACE_ROI=2 };
enum jpeg_crop_trace_kind { JPEG_TRACE_IDENTIFIED=0,JPEG_TRACE_APPLIED=1 };
enum jpeg_crop_trace_state { JPEG_TRACE_EMPTY=0,JPEG_TRACE_WRITING=1,JPEG_TRACE_READY=2 };
/* Normative u32 word indices in each captured object representation. */
enum jpeg_crop_trace_jpeg_word {
 JT_CONSUMER=0,JT_ENTRY_LR,JT_NATIVE_SP,JT_JOB,JT_PARAM,JT_OUTER_LR,
 JT_DESCRIPTOR,JT_REQUEST,JT_ENGINE,JT_MODE,JT_TIER,JT_ASPECT,JT_RAW_ASPECT,
 JT_INPUT_LOW,JT_INPUT_HIGH,JT_REQUEST_INPUT_LOW,JT_REQUEST_INPUT_HIGH
};
enum jpeg_crop_trace_roi_word {
 RT_SELECTED=0,RT_STATE,RT_NATIVE_SP,RT_ARGUMENT,RT_CALLER_LR,RT_REQUEST,
 RT_RAW_ASPECT,RT_TIER,RT_ASPECT,RT_MODE,RT_FLAG16,RT_FLAG20,RT_SELECTION,
 RT_INPUT_RECORD,RT_CALLBACK,RT_CALLBACK_ARG,RT_REQUEST_CALLBACK,RT_REQUEST_CALLBACK_ARG
};
struct jpeg_crop_trace_counts {uint32_t entered,identified,applied;};

/* Entry adapters call enter before validation. Only after the existing native
 * extractor identifies a complete borrowed frame may it pass its local facts.
 * "Identified" describes frame provenance, not crop admission or queue/capture
 * ownership. First identified and first applied records are separate, owned,
 * immutable snapshots. No request pointer is followed by this module. */
void jpeg_crop_trace_enter(uint32_t role);
void jpeg_crop_trace_jpeg(uint32_t role,const struct xpan_jpeg_crop_facts *facts,uint32_t applied);
void jpeg_crop_trace_roi(const struct xpan_roi_crop_facts *facts,uint32_t applied);
uint32_t jpeg_crop_trace_word_count(uint32_t role);
/* Counts are independent relaxed atomic observations, not one coherent tuple. */
uint32_t jpeg_crop_trace_counters(uint32_t role,struct jpeg_crop_trace_counts *out);
/* Acquire state. Copies exactly17/18 words only when ready2; EMPTY/WRITING
 * leave out untouched and mean unknown. Caller may provide18-word storage. */
uint32_t jpeg_crop_trace_snapshot(uint32_t role,uint32_t kind,uint32_t *out);
/* Worker-only logging, never called by an entry adapter. R5J1 B(seq,V1), S
 * sourceID, C(role,entered,identified,applied), R(role,kind,state,wordcount),
 * F(role,kind,index,A,B), E(seq). JPEG word16 uses single-value F(...,A),
 * without an invented padding word. No F rows unless snapshot state is2.
 * Snapshots prove local applicability only; actual files/center comparison
 * and the config-to-execute queue ownership gap remain separate evidence. */
void jpeg_crop_trace_report(uint32_t sequence);
#endif
