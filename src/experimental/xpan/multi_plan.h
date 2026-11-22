#ifndef R5_MULTI_PLAN_H
#define R5_MULTI_PLAN_H
#include <stdint.h>
#define MULTI_MAX_PAGES 21u
#define MULTI_MAX_TABLES 10u
/* --screenshot: + pages E04A8000 (GUI dispatch), E0CCB000 (JPEG encode),
 * E0549000 (INDEV_DevelopEnd); the E0C section adds one table. Sized for the
 * screenshot together with the record indicator (its page E07C6000). */
#define MULTI_MAX_PAGES_SCREENSHOT 24u
#define MULTI_MAX_TABLES_SCREENSHOT 11u
#if defined(R5_SCREENSHOT) && R5_SCREENSHOT
#undef MULTI_MAX_PAGES
#undef MULTI_MAX_TABLES
#define MULTI_MAX_PAGES MULTI_MAX_PAGES_SCREENSHOT
#define MULTI_MAX_TABLES MULTI_MAX_TABLES_SCREENSHOT
#endif
struct multi_target { uint32_t va,pa; };
struct multi_shape { uint32_t count,sections[MULTI_MAX_TABLES]; };
enum multi_error { MULTI_OK,MULTI_ARGUMENT,MULTI_ORIGINAL,MULTI_TARGET,
 MULTI_TABLE,MULTI_CAPACITY };
/* Validity of source-symbol-owned reservation extents; no allocation authority
 * is inferred from an address. Initialized/BSS objects must already be owned. */
int multi_original_supported(const volatile uint32_t original[16]);
int multi_rom_supported(const volatile uint32_t *original,unsigned blocks);
int multi_layout_supported(uint32_t root,uint32_t table,
 const struct multi_target *,unsigned count,uint32_t owned_start,uint32_t owned_end);
/* R5.221 E0 16MiB supersection -> ordinary sections and selected coarse tables.
 * Caller supplies disjoint immutable source and owned destination arrays. */
unsigned multi_plan_build(const volatile uint32_t original[16],
 const struct multi_target *,unsigned count,uint32_t table_physical,
 volatile uint32_t parents[16],volatile uint32_t children[MULTI_MAX_TABLES][256],
 struct multi_shape *);
int multi_plan_matches(const volatile uint32_t parents[16],
 const volatile uint32_t children[MULTI_MAX_TABLES][256],
 const struct multi_target *,unsigned count,uint32_t table_physical,
 const struct multi_shape *);
#endif
