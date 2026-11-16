#ifndef R5_PRIVATE_ROOT_PLAN_H
#define R5_PRIVATE_ROOT_PLAN_H
#include <stdint.h>
#include <stddef.h>
#include "boot_mmu.h"
#include "multi_plan.h"
#define ROOT_WORDS 4096u
#define ROOT_FIRST 0xe00u
#define ROOT_ORIGINAL 0xdffc0000u
enum root_result { ROOT_NONE, ROOT_BAD_CPU, ROOT_BAD_OWNER, ROOT_BAD_RAM,
 ROOT_BAD_PAGE, ROOT_BAD_PLAN, ROOT_PREPARED, ROOT_SWITCHED,
 ROOT_SWITCH_FAILED, ROOT_RESTORED, ROOT_FATAL };
struct root_profile {uint32_t midr_mask,midr_value,sctlr,actlr,ttbr0,ttbr1,ttbcr,dacr,core_mask,ttbr0_stride,boot_cpsr_mask,boot_cpsr_value;};
_Static_assert(sizeof(struct root_profile)==48,"host/native root profile ABI");
extern const struct root_profile root_target_profile;
uint32_t root_page_checksum(const volatile uint32_t *);
struct root_layout {uint32_t root, children, page, owned_start, initialized_end, objects_end;};
struct root_input {
 const volatile struct boot_mmu_snapshot *cpu;
 uint32_t midr;
 const volatile uint32_t *stock, *rom, *page, *low_ram;
 volatile uint32_t *root;
 volatile uint32_t (*children)[256];
 struct multi_shape *shape;
 struct root_layout layout;
};
int root_cpu_supported(const volatile struct boot_mmu_snapshot *, uint32_t midr);
unsigned root_view_kind(const volatile struct boot_mmu_snapshot *, uint32_t private_root);
int root_ram_supported(const volatile uint32_t *);
int root_layout_supported(const struct root_layout *);
enum root_result root_prepare(const struct root_input *);
uint32_t root_original_drift(const volatile uint32_t *stock, const volatile uint32_t *root);
#endif
