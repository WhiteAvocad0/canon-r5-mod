#ifndef R5_HEAP_NATIVE_H
#define R5_HEAP_NATIVE_H
#include <stdint.h>
enum heap_native_status {
 HEAP_NATIVE_UNOBSERVED, HEAP_NATIVE_BAD_SPAN, HEAP_NATIVE_BAD_REGISTERS,
 HEAP_NATIVE_BAD_CORE, HEAP_NATIVE_BAD_VIEW, HEAP_NATIVE_BAD_PROFILE,
 HEAP_NATIVE_BAD_ROOT_FACTS, HEAP_NATIVE_BAD_MAPPING, HEAP_NATIVE_ADMITTED
};
enum heap_native_observation {
 HEAP_OBS_CPSR=1u, HEAP_OBS_REGISTERS=2u, HEAP_OBS_MIDR=4u,
 HEAP_OBS_ROOT_ADMITTED=8u, HEAP_OBS_TABLE_ENTRIES=16u
};
/* Historical preparation observations, not a live DMA/root monitor. A zero
 * MIDR/entry count without its flag is unknown. Plan0 may be unobserved;
 * drift UINT32_MAX retains the root owner's unknown encoding. */
struct heapobs {
 uint32_t status,flags,cpsr,mpidr,midr,ttbr0,ttbr1,ttbcr;
 uint32_t view,plan,drift,entry_count,first_words[2];
};
_Static_assert(sizeof(struct heapobs)==56u,"heap observation ABI");
/* Mapping validity only; the caller separately owns the allocator receipt,
 * lifetime and observation object. No allocation and no raw-buffer access.
 * Current supported CPU0/private-root
 * facts and every member of each covering source-exact group must be admitted.
 * noinline keeps the snapshot/group frame out of the decoder's call chain. */
int heap_native_admit(uint32_t raw,uint32_t bytes,volatile struct heapobs *out)
 __attribute__((noinline));
#endif
