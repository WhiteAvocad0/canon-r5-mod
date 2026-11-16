#ifndef R5_PLAYBACK_REPORT_H
#define R5_PLAYBACK_REPORT_H
#include "playback_tags.h"
#define PLAYBACK_REPORT_SCHEMA 2u
#define PLAYBACK_REPORT_NATIVE_WORDS 8u
#define PLAYBACK_REPORT_MASK_WORDS 10u
#define PP_FMT_BEGIN "R5P1 B=%u V=%u"
#define PP_FMT_SOURCE "R5P1 S=" OBSERVER_SOURCE_ID
#define PP_FMT_NATIVE0 "R5P1 N=0 A=%u B=%u C=%u D=%u"
#define PP_FMT_NATIVE4 "R5P1 N=4 A=%u B=%u C=%u D=%u"
#define PP_FMT_MASK0 "R5P1 M=0 A=%u B=%u C=%u D=%u"
#define PP_FMT_MASK4 "R5P1 M=4 A=%u B=%u C=%u D=%u"
#define PP_FMT_MASK8 "R5P1 M=8 A=%u B=%u"
#define PP_FMT_TAG "R5P1 T=%u Q=%u O=%08X R=%u"
#define PP_FMT_WORDS0 "R5P1 W=%u I=0 A=%u B=%u C=%u D=%u"
#define PP_FMT_WORDS4 "R5P1 W=%u I=4 A=%u B=%u C=%u"
#define PP_FMT_END "R5P1 E=%u"
#define PQ_FMT_BEGIN "R5Q1 B=%u V=%u N=%u"
#define PQ_FMT_SOURCE "R5Q1 S=" OBSERVER_SOURCE_ID
#define PQ_FMT_ROLE "R5Q1 T=%u Q=%u H=%u"
#define PQ_FMT_WORDS "R5Q1 W=%u I=%u A=%08X B=%08X C=%08X D=%08X"
#define PQ_FMT_END "R5Q1 E=%u"
#define PV_FMT_BEGIN "R5V1 B=%u V=%u N=%u"
#define PV_FMT_SOURCE "R5V1 S=" OBSERVER_SOURCE_ID
#define PV_FMT_ROLE "R5V1 T=%u Q=%u H=%u"
#define PV_FMT_WORDS "R5V1 W=%u I=%u A=%08X B=%08X C=%08X D=%08X"
#define PV_FMT_END "R5V1 E=%u"
/* Worker-only. Observations/slots are separate samples, not a table/frame join. */
void playback_report(unsigned sequence);
#endif
