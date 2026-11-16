#ifndef R5_CODE_PATCH_H
#define R5_CODE_PATCH_H
#include <stdint.h>
#define CODE_PATCH_MAX 32u
struct code_patch {uint32_t va,destination,length;uint8_t before[16];};
enum code_patch_error {CODE_PATCH_OK,CODE_PATCH_ARGUMENT,CODE_PATCH_LAYOUT,
 CODE_PATCH_SOURCE,CODE_PATCH_OVERLAP};
/* The caller owns the full4KiB output page and has already copied/admitted its
 * original bytes. This owner checks complete windows before any modification.
 * Trampolines must independently replay complete source instructions. */
unsigned code_patch_apply(uint8_t page[4096],uint32_t page_va,
 const struct code_patch *,unsigned count,uint32_t code_start,uint32_t code_end);
#endif
