#ifndef R5_PRIVATE_ROOT_NATIVE_H
#define R5_PRIVATE_ROOT_NATIVE_H
#include "boot_mmu.h"
struct root_view_facts {uint32_t midr,kind,plan,drift,checksum;};
void root_current_snapshot(volatile struct boot_mmu_snapshot *);
void root_late_facts(const volatile struct boot_mmu_snapshot *,struct root_view_facts *);
void root_primary_prepare(void);
void root_secondary_take(void);
void root_peer_check(void);
void root_report(unsigned, const volatile struct boot_mmu_snapshot *);
/* Typed IFD range plus a fresh current-root mapping check for the high pool.
 * Low eligible DATA keeps its existing behavior. No handle dereference,
 * cached readiness, root mutation or whole-root/page scan occurs here. */
int root_ifd_memory_admitted(uint32_t handle,uint32_t bytes);
#endif
