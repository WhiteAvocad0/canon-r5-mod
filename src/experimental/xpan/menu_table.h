#ifndef R5_MENU_TABLE_H
#define R5_MENU_TABLE_H
#include <stdint.h>
#include "heap_span.h"
#define MENU_COUNT 2728u
#define MENU_TARGET_ID 518u
#define MENU_SOURCE_TABLE 0xe3896c10u
#define MENU_SOURCE_END 0xe3aea4f0u
#define MENU_SOURCE_TARGET_PREFIX 0xe38f6148u
#define MENU_SOURCE_TARGET_BYTES 968u
#define MENU_TABLE_BYTES ((MENU_COUNT + 1u) * 4u)
#define MENU_TABLE_RESERVED ((MENU_TABLE_BYTES + 31u) & ~31u)
#define MENU_PREFIX_RESERVED 1024u
#define MENU_ALLOCATION_BYTES (MENU_TABLE_RESERVED + MENU_PREFIX_RESERVED + 32u)
enum menu_table_result { MENU_TABLE_OK, MENU_TABLE_BAD_ARGUMENT,
 MENU_TABLE_BAD_SOURCE, MENU_TABLE_BAD_DESTINATION };
/* Pure layout/contents validation. The caller separately owns the allocation.
 * src spans the complete pinned image blob; all validation precedes writes. */
unsigned menu_table_build(uint32_t *out, uint32_t table_va,
 const volatile uint32_t *src, uint32_t new_prefix_va);
#endif
