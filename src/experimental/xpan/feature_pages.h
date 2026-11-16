#ifndef R5_FEATURE_PAGES_H
#define R5_FEATURE_PAGES_H
#include "feature-config.h"
#define FEATURE_BOOT_VA 0xe0100000
#ifndef __ASSEMBLER__
#include <stdint.h>
#include "multi_plan.h"
struct feature_witness {uint32_t va,checksum;};
extern const struct feature_witness feature_witnesses[FEATURE_WITNESS_COUNT];
extern const struct multi_target feature_targets[FEATURE_PAGE_COUNT];
extern volatile uint32_t feature_reference_checksums[FEATURE_PAGE_COUNT];
unsigned feature_prepare_pages(void);
int feature_pages_match(unsigned virtual_view);
#endif
#endif
