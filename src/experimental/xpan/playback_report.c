#include "playback_report.h"
#include "playback_native.h"
#include "playback_mask_native.h"
#include "playback_reject.h"
#include "playback_first_view.h"
#include "observer.h"
_Static_assert(sizeof(struct playback_native_observation)==4u*PLAYBACK_REPORT_NATIVE_WORDS,"native report extent");
_Static_assert(sizeof(struct playback_mask_observation)==4u*PLAYBACK_REPORT_MASK_WORDS,"mask report extent");
_Static_assert(PLAYBACK_TAG_FACT_WORDS==7u,"typed playback facts extent");
_Static_assert(PN_METADATA_COUNT+1u==PLAYBACK_TAG_SLOT_COUNT && PN_CACHE_SLOT==PN_METADATA_COUNT,"report slot order");
static const char begin_format[]=PP_FMT_BEGIN,source_format[]=PP_FMT_SOURCE;
static const char native0_format[]=PP_FMT_NATIVE0,native4_format[]=PP_FMT_NATIVE4;
static const char mask0_format[]=PP_FMT_MASK0,mask4_format[]=PP_FMT_MASK4,mask8_format[]=PP_FMT_MASK8;
static const char tag_format[]=PP_FMT_TAG,words0_format[]=PP_FMT_WORDS0,words4_format[]=PP_FMT_WORDS4,end_format[]=PP_FMT_END;

_Static_assert(PNR_FIELD_COUNT==PLAYBACK_REJECT_WORDS,"rejection report field extent");
_Static_assert(PFV_FIELD_COUNT==PLAYBACK_FIRST_VIEW_WORDS,"first_view report field extent");
static const char reject_begin_format[]=PQ_FMT_BEGIN,reject_source_format[]=PQ_FMT_SOURCE;
static const char reject_role_format[]=PQ_FMT_ROLE,reject_words_format[]=PQ_FMT_WORDS,reject_end_format[]=PQ_FMT_END;
static const char pfv_begin_format[]=PV_FMT_BEGIN,pfv_source_format[]=PV_FMT_SOURCE;
static const char pfv_role_format[]=PV_FMT_ROLE,pfv_words_format[]=PV_FMT_WORDS,pfv_end_format[]=PV_FMT_END;
static void emit_word_chunks(const char *fmt,uint32_t role,const uint32_t *words,uint32_t count)
{
 for(uint32_t i=0u;i<count;i+=4u)
  observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,fmt,role,i,words[i],words[i+1u],words[i+2u],words[i+3u]);
}
static void rejection_report(unsigned sequence)
{
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,reject_begin_format,sequence,playback_reject_profile.schema,playback_reject_profile.words);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,reject_source_format);
 for(uint32_t role=0u;role<playback_reject_profile.roles;role++) {
  struct playback_reject_facts facts;
  uint32_t hits=playback_reject_hits(role),state=playback_reject_snapshot(role,&facts);
  observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,reject_role_format,role,state,hits);
  if(state==PNR_STATE_READY) {
#define PNR_REPORT_WORD(name) facts.name,
   uint32_t words[PLAYBACK_REJECT_WORDS]={PLAYBACK_REJECT_FIELDS(PNR_REPORT_WORD)};
#undef PNR_REPORT_WORD
   emit_word_chunks(reject_words_format,role,words,PLAYBACK_REJECT_WORDS);
  }
 }
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,reject_end_format,sequence);
}
static void first_view_report(unsigned sequence)
{
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,pfv_begin_format,sequence,playback_first_view_profile.schema,playback_first_view_profile.words);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,pfv_source_format);
 for(uint32_t role=0u;role<playback_first_view_profile.roles;role++) {
  struct playback_first_view_facts facts;
  uint32_t hits=playback_first_view_hits(role),state=playback_first_view_snapshot(role,&facts);
  observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,pfv_role_format,role,state,hits);
  if(state==PFV_STATE_READY) {
#define PFV_REPORT_WORD(name) facts.name,
   uint32_t words[PLAYBACK_FIRST_VIEW_WORDS]={PLAYBACK_FIRST_VIEW_FIELDS(PFV_REPORT_WORD)};
#undef PFV_REPORT_WORD
   emit_word_chunks(pfv_words_format,role,words,PLAYBACK_FIRST_VIEW_WORDS);
  }
 }
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,pfv_end_format,sequence);
}

void playback_report(unsigned sequence)
{
 if(!observer_api.debug)return;
 struct playback_native_observation n;struct playback_mask_observation m;
 playback_native_observe(&n);playback_mask_observe(&m);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,begin_format,sequence,PLAYBACK_REPORT_SCHEMA);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,source_format);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,native0_format,n.main_entered,n.main_admitted,n.tag_entered,n.tag_captured);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,native4_format,n.copy_entered,n.copy_published,n.cache_entered,n.cache_published);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,mask0_format,m.entered,m.admitted,m.projected,m.crop_candidate);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,mask4_format,m.guide_hidden,m.rejected,m.view_ready,m.view_revision);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,mask8_format,m.object_revision,m.cache_revision);
 for(unsigned index=0u;index<PLAYBACK_TAG_SLOT_COUNT;index++) {
  uint32_t object=index==PN_CACHE_SLOT?playback_native_profile.cache_key:playback_native_profile.objects[index];
  struct playback_tag_snapshot snapshot;
  unsigned known=(index==PN_CACHE_SLOT?playback_native_cache_snapshot(&snapshot):
   playback_native_object_snapshot(object,&snapshot))==1u;
  known=known && snapshot.index==index && snapshot.object==object && snapshot.revision!=0u &&
   playback_native_snapshot_same(&snapshot)==1u;
  observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,tag_format,index,known,object,known?snapshot.revision:0u);
  if(known) {
   /* Explicit facts members, never an array/struct alias or invented padding. */
   observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,words0_format,index,snapshot.facts.words[0],
    snapshot.facts.words[1],snapshot.facts.words[2],snapshot.facts.words[3]);
   observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,words4_format,index,snapshot.facts.words[4],
    snapshot.facts.current_width,snapshot.facts.current_height);
  }
 }
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,end_format,sequence);
 rejection_report(sequence);
 first_view_report(sequence);
}
