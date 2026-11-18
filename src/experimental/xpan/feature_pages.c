#include "feature_pages.h"
#include "code_patch.h"
#include "root_plan.h"
#include "menu_native.h"
#include "gui_crop.h"
#include "rect_crop.h"
#include "jpeg_crop.h"
#include "frame_native.h"
#include "playback_native.h"
#include "playback_mask_native.h"
#include "q_label.h"
#include "export_refresh.h"
#if defined(R5_RECORD_INDICATOR) && R5_RECORD_INDICATOR
#include "rec_state.h"
#endif
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
extern uint32_t boot_shadow_page[];
extern void root_peer_shim(void);
extern char _resident_start[],_resident_data_end[];
uint32_t feature_extra_pages[FEATURE_PAGE_COUNT-1u][1024] __attribute__((aligned(4096)));
volatile uint32_t feature_reference_checksums[FEATURE_PAGE_COUNT];
static unsigned prepared;
#include "feature-targets.h"

CODE unsigned feature_prepare_pages(void)
{
 prepared=0;
 for(unsigned n=0;n<FEATURE_WITNESS_COUNT;n++)
  if(root_page_checksum((const volatile uint32_t *)(uintptr_t)feature_witnesses[n].va)!=feature_witnesses[n].checksum)return 3u;
 /* Called only by the admitted primary before its first translation change.
  * All destinations are source-symbol-owned initialized/BSS reservations. */
 for(unsigned n=0;n<FEATURE_PAGE_COUNT;n++) {
  const volatile uint32_t *rom=(const volatile uint32_t *)(uintptr_t)feature_targets[n].va;
  uint32_t *page=(uint32_t *)(uintptr_t)feature_targets[n].pa;
  if(root_page_checksum(rom)!=feature_source_checksums[n])return 1u;
  for(unsigned i=0;i<1024u;i++)page[i]=rom[i];
  if(code_patch_apply((uint8_t *)page,feature_targets[n].va,feature_patches,FEATURE_PATCH_COUNT,
   (uint32_t)(uintptr_t)_resident_start,(uint32_t)(uintptr_t)_resident_data_end))return 2u;
  feature_reference_checksums[n]=root_page_checksum(page);
 }
 prepared=1u;return 0u;
}
CODE int feature_pages_match(unsigned virtual_view)
{
 if(!prepared)return 0;
 for(unsigned n=0;n<FEATURE_PAGE_COUNT;n++) {
  uint32_t address=virtual_view?feature_targets[n].va:feature_targets[n].pa;
  if(root_page_checksum((const volatile uint32_t *)(uintptr_t)address)!=feature_reference_checksums[n])return 0;
 }
 return 1;
}
