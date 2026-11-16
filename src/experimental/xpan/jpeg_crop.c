#include "jpeg_crop.h"
#include "geometry.h"
#include "jpeg_crop_trace.h"
#include <stddef.h>

#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_data"),aligned(8)))
#else
#define CODE
#define DATA __attribute__((aligned(8)))
#endif
#define PARAM UINT32_C(0xe1d56fe0)
#define ROI_A UINT32_C(0xe1d5c088)
#define ROI_B UINT32_C(0xe1d5c358)
#define STATE UINT32_C(0x00030a14)
#define DESCRIPTOR UINT32_C(0x000158ac)
#define DISPATCH_A UINT32_C(0xe0172adb)
#define DISPATCH_B UINT32_C(0xe0172f4d)
#define PRIMARY_RETURN UINT32_C(0xe0cd97df)
#define SECONDARY_RETURN UINT32_C(0xe0cd97ed)
#define CONFIG_RETURN UINT32_C(0xe013cef9)

/* Full original80B E1D56FE0 SHA256:
 * bb79e197e9b5d2b253bc8a592daadee41ae64018eb7a6665620b7fa00cea376f.
 * Only words2/12 differ. Real retained RAM, not an ARM-only ROM alias. */
static uint32_t parameters[20] DATA={XPAN_LARGE_WIDTH,XPAN_LARGE_WIDTH,XPAN_LARGE_HEIGHT,0u,0u,
 512u,512u,512u,15u,8u,8u,8u,XPAN_LARGE_HEIGHT/8u-1u,
 1920u,15u,16u,1u,1u,4u,4u};
/* Each complete original48B ROI has SHA256
 * 1ecf37768790413e5317a39915e2dedf39259c11dd768d055f1f9a2df23110a2.
 * Separate retained copies for C088/C358; only words9/11 differ. */
static uint32_t roi_a[12] DATA={XPAN_LARGE_WIDTH,1u,1u,1u,1u,0u,0u,1u,0u,
 XPAN_LARGE_TOP,XPAN_LARGE_WIDTH,XPAN_LARGE_HEIGHT};
static uint32_t roi_b[12] DATA={XPAN_LARGE_WIDTH,1u,1u,1u,1u,0u,0u,1u,0u,
 XPAN_LARGE_TOP,XPAN_LARGE_WIDTH,XPAN_LARGE_HEIGHT};
_Static_assert(sizeof(parameters)==80 && sizeof(roi_a)==48 && sizeof(roi_b)==48,
 "retained native record sizes");
_Static_assert(XPAN_LARGE_HEIGHT%8u==0 && XPAN_LARGE_TOP+XPAN_LARGE_HEIGHT<=XPAN_LARGE_FULL_HEIGHT,
 "shared crop geometry");
_Static_assert(XPAN_LARGE_WIDTH==8192u && XPAN_STOCK_LARGE_HEIGHT==4608u,
 "pinned source Large profile");

CODE static uint32_t frame_space(uint32_t sp,uint32_t extent)
{return sp && !(sp&7u) && sp<=UINT32_MAX-extent;}
CODE static uint32_t request_space(uint32_t request,uint32_t extent)
{return request && !(request&3u) && request<=UINT32_MAX-extent;}
CODE static uint32_t consumer_return(uint32_t consumer,uint32_t lr)
{
 return (consumer==XPAN_JPEG_PRIMARY && lr==PRIMARY_RETURN) ||
  (consumer==XPAN_JPEG_SECONDARY && lr==SECONDARY_RETURN);
}
CODE static uint32_t job_offset(uint32_t outer_lr)
{return outer_lr==DISPATCH_A?0x30u:(outer_lr==DISPATCH_B?0x38u:0u);}

CODE uint32_t xpan_jpeg_crop_admit(const struct xpan_jpeg_crop_facts *f)
{
 if(!f || !consumer_return(f->consumer,f->entry_lr) || f->param!=PARAM ||
  !frame_space(f->native_sp,0x58u))return 0u;
 uint32_t offset=job_offset(f->outer_lr);
 return offset && f->job==f->native_sp+offset &&
  f->descriptor==DESCRIPTOR && request_space(f->request,0x50u) &&
  f->engine==0u && f->mode==0u && f->tier==0u && f->aspect==3u &&
  f->raw_aspect==7u && f->input_low==f->request_input_low &&
  f->input_high==f->request_input_high;
}
CODE uint32_t xpan_roi_crop_admit(const struct xpan_roi_crop_facts *f)
{
 if(!f || !frame_space(f->native_sp,0x108u) || f->state!=STATE ||
  f->argument!=f->native_sp+0x108u || f->caller_lr!=CONFIG_RETURN ||
  !request_space(f->request,0x1cu) || f->raw_aspect!=7u || f->tier!=0u ||
  f->aspect!=3u || f->mode>3u || f->flag16 || f->flag20 || f->selection!=2u ||
  f->callback!=f->request_callback || f->callback_arg!=f->request_callback_arg)
  return 0u;
 return f->mode<2u ? (f->selected==ROI_A && f->input_record==UINT32_C(0xe1d5b7c8)) :
  (f->selected==ROI_B && f->input_record==UINT32_C(0xe1d5b958));
}
CODE const uint32_t *xpan_jpeg_crop_parameters(void){return parameters;}
CODE const uint32_t *xpan_roi_crop_record(uint32_t original)
{return original==ROI_A?roi_a:(original==ROI_B?roi_b:NULL);}

#ifndef XPAN_JPEG_HOST
_Static_assert(sizeof(uintptr_t)==4,"native address ABI");
CODE static uint32_t read_word(uint32_t address)
{return *(const volatile uint32_t *)(uintptr_t)address;}

CODE static uint32_t select_parameter(uint32_t consumer,uint32_t job,
 uint32_t param,uint32_t sp,uint32_t lr)
{
 jpeg_crop_trace_enter(consumer);
 /* Reject foreign entries before reading any native frame. The exact entry
  * LR and stack-job relation establish the selector frame; only its saved
  * outer LR is read to select a dispatcher layout. No request dereference
  * precedes these identity checks and the original descriptor check. */
 if(!consumer_return(consumer,lr) || param!=PARAM || !frame_space(sp,0x58u) ||
  (job!=sp+0x30u && job!=sp+0x38u))return param;
 uint32_t outer=read_word(sp+0x1cu),offset=job_offset(outer);
 if(!offset || job!=sp+offset || read_word(sp+0x10u)!=DESCRIPTOR)return param;
 uint32_t request=read_word(sp+0x0cu);
 if(!request_space(request,0x50u))return param;
 uint32_t raw_offset=outer==DISPATCH_A?0x18u:0x10u;
 uint32_t input_offset=outer==DISPATCH_A?0x20u:0x18u;
 struct xpan_jpeg_crop_facts facts={consumer,lr,sp,job,param,outer,DESCRIPTOR,request,
  read_word(request),read_word(job),read_word(job+4u),read_word(job+8u),
  read_word(request+raw_offset),read_word(job+0x10u),read_word(job+0x14u),
  read_word(request+input_offset),read_word(request+input_offset+4u)};
 uint32_t applied=xpan_jpeg_crop_admit(&facts);
 jpeg_crop_trace_jpeg(consumer,&facts,applied);
 return applied?(uint32_t)(uintptr_t)parameters:param;
}
CODE uint32_t xpan_jpeg_crop_primary(uint32_t job,uint32_t param,uint32_t sp,uint32_t lr)
{return xpan_jpeg_original_primary(job,select_parameter(XPAN_JPEG_PRIMARY,job,param,sp,lr));}
CODE uint32_t xpan_jpeg_crop_secondary(uint32_t job,uint32_t param,uint32_t sp,uint32_t lr)
{return xpan_jpeg_original_secondary(job,select_parameter(XPAN_JPEG_SECONDARY,job,param,sp,lr));}

CODE uint32_t xpan_roi_crop_select(uint32_t selected,uint32_t state,uint32_t sp,uint32_t argument)
{
 jpeg_crop_trace_enter(JPEG_TRACE_ROI);
 if((selected!=ROI_A && selected!=ROI_B) || state!=STATE ||
  !frame_space(sp,0x108u) || argument!=sp+0x108u)return selected;
 if(read_word(sp+0x104u)!=CONFIG_RETURN)return selected;
 uint32_t request=read_word(sp+0xf8u);
 if(!request_space(request,0x1cu))return selected;
 struct xpan_roi_crop_facts facts={selected,state,sp,argument,CONFIG_RETURN,request,
  read_word(request+0x18u),read_word(state+0x28u),read_word(state+0x34u),
  read_word(state+0x30u),read_word(state+0x10u),read_word(state+0x14u),
  read_word(state+0x18u),read_word(state+0x3cu),read_word(state+8u),read_word(state+12u),
  read_word(request),read_word(request+4u)};
 uint32_t applied=xpan_roi_crop_admit(&facts);
 jpeg_crop_trace_roi(&facts,applied);
 if(!applied)return selected;
 return (uint32_t)(uintptr_t)xpan_roi_crop_record(selected);
}
#endif
