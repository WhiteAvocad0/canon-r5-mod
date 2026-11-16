/* Shared C/assembler layout for primary and published secondary boot snapshots. */
#ifndef R5_BOOT_MMU_H
#define R5_BOOT_MMU_H
#define BOOT_MMU_TTBR1 0xdffc0000
#define BOOT_MMU_L1_FIRST 0xdffc3800
#define BOOT_MMU_L1_WORDS 48
#define BOOT_MMU_REGS_OBSERVED 1
#define BOOT_MMU_TABLE_OBSERVED 2
#define BOOT_MMU_FLAGS_OFFSET 32
#define BOOT_MMU_TABLE_OFFSET 36
#define BOOT_MMU_RESTORE_OFFSET 228
#define BOOT_MMU_PUBLISHED_OFFSET 232

#ifndef __ASSEMBLER__
#include <stddef.h>
#include <stdint.h>
struct boot_mmu_snapshot {
    uint32_t cpsr, mpidr, sctlr, actlr, ttbr0, ttbr1, ttbcr, dacr;
    uint32_t flags;
    uint32_t l1[BOOT_MMU_L1_WORDS];
};
typedef uint32_t (*boot_mmu_read_fn)(uintptr_t address);
_Static_assert(offsetof(struct boot_mmu_snapshot, flags) == BOOT_MMU_FLAGS_OFFSET, "flags ABI");
_Static_assert(offsetof(struct boot_mmu_snapshot, l1) == BOOT_MMU_TABLE_OFFSET, "table ABI");
_Static_assert(sizeof(struct boot_mmu_snapshot) == 228, "snapshot ABI");
extern volatile struct boot_mmu_snapshot boot_mmu_early;
/* One CPU1-owned allocation, including its publication word and tail padding. */
struct boot_mmu_secondary_state {
    struct boot_mmu_snapshot snapshot;
    uint32_t restore;
    uint32_t published;
    uint32_t reserved[5];
} __attribute__((aligned(32)));
_Static_assert(offsetof(struct boot_mmu_secondary_state, snapshot) == 0, "secondary snapshot ABI");
_Static_assert(offsetof(struct boot_mmu_secondary_state, restore) == BOOT_MMU_RESTORE_OFFSET, "restore ABI");
_Static_assert(offsetof(struct boot_mmu_secondary_state, published) == BOOT_MMU_PUBLISHED_OFFSET, "publication ABI");
_Static_assert(sizeof(struct boot_mmu_secondary_state) == 256, "exclusive cache-line storage");
extern volatile struct boot_mmu_secondary_state boot_mmu_secondary;
uint32_t boot_mmu_expected_descriptor(unsigned block);
void boot_mmu_read_l1(volatile struct boot_mmu_snapshot *s, boot_mmu_read_fn read_word);
uint32_t boot_mmu_mismatch_mask(const volatile struct boot_mmu_snapshot *s, unsigned block);
void boot_mmu_request_report(void);
void boot_mmu_tick(void);
void boot_mmu_emit_bundle(void *, unsigned);
#endif
#endif
