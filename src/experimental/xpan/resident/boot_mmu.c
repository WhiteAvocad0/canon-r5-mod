#include "boot_mmu.h"
#ifdef OBSERVER_FIRMWARE
#include "../root_native.h"
#include "../menu_native.h"
#if !defined(R5_CROP_ONLY) || !R5_CROP_ONLY
#include "../pcm_runtime.h"
#endif
#include "../jpeg_crop_trace.h"
#include "../export_refresh.h"
#include "../frame_native.h"
#include "../frame_disable_trace.h"
#include "../playback_report.h"
#if defined(R5_RECORD_INDICATOR) && R5_RECORD_INDICATOR
#include "../rec_tone.h"
#endif
#endif
#ifdef OBSERVER_FIRMWARE
#include "observer.h"
#include "observer-source.h"
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#endif

volatile struct boot_mmu_snapshot boot_mmu_early;
volatile struct boot_mmu_secondary_state boot_mmu_secondary;

CODE uint32_t boot_mmu_expected_descriptor(unsigned block)
{
    return block < 3u ? UINT32_C(0xe005d406) + block * UINT32_C(0x01000000) : 0u;
}

CODE void boot_mmu_read_l1(volatile struct boot_mmu_snapshot *s, boot_mmu_read_fn read_word)
{
    s->flags &= ~BOOT_MMU_TABLE_OBSERVED;
    if (!(s->flags & BOOT_MMU_REGS_OBSERVED) || !(s->sctlr & 1u) ||
        s->ttbcr != 7u || (s->ttbr1 & UINT32_C(0xffffc000)) != BOOT_MMU_TTBR1)
        return;
    for (unsigned i = 0; i < BOOT_MMU_L1_WORDS; ++i)
        s->l1[i] = read_word(BOOT_MMU_L1_FIRST + 4u * i);
    s->flags |= BOOT_MMU_TABLE_OBSERVED;
}

CODE uint32_t boot_mmu_mismatch_mask(const volatile struct boot_mmu_snapshot *s, unsigned block)
{
    if (!(s->flags & BOOT_MMU_TABLE_OBSERVED) || block >= 3u)
        return UINT32_MAX;
    uint32_t mask = 0u;
    for (unsigned i = 0; i < 16u; ++i)
        if (s->l1[block * 16u + i] != boot_mmu_expected_descriptor(block))
            mask |= 1u << i;
    return mask;
}

#ifdef OBSERVER_FIRMWARE
static volatile struct boot_mmu_snapshot late;

/* The loader already clears BSS; retain the absent-sample zero object there. */
static struct boot_mmu_snapshot not_published;
static const char begin_fmt[] DATA = "R5M2 B=%u N=%u";
static const char source_fmt[] DATA = "R5M2 S=" OBSERVER_SOURCE_ID " V=%u C=%u R=%u";
static const char phase_fmt[] DATA = "R5M2 P=%u F=%u C=%08X M=%08X";
static const char regs_fmt[] DATA = "R5M2 A=%08X X=%08X T=%08X U=%08X";
static const char controls_fmt[] DATA = "R5M2 R=%08X D=%08X";
static const char table_fmt[] DATA = "R5M2 L=%u F=%08X E=%08X M=%08X";
static const char slots_fmt[] DATA = "R5M2 H=%08X J=%08X W=%08X Z=%08X";
static const char end_fmt[] DATA = "R5M2 E=%u";

CODE static uint32_t native_read_word(uintptr_t address)
{
    return *(const volatile uint32_t *)address;
}

CODE static void read_late_registers(void)
{
    uint32_t value;
    late.flags = 0u;
    __asm__ volatile ("mrs %0, cpsr" : "=r"(value));
    late.cpsr = value;
    /* Only the already-used privileged SVC/System task modes are admitted. */
    value &= 31u;
    if (value != 0x13u && value != 0x1fu)
        return;
#define READ_CP15(field, instruction) \
    __asm__ volatile (instruction : "=r"(value)); late.field = value
    READ_CP15(mpidr, "mrc p15, 0, %0, c0, c0, 5");
    READ_CP15(sctlr, "mrc p15, 0, %0, c1, c0, 0");
    READ_CP15(actlr, "mrc p15, 0, %0, c1, c0, 1");
    READ_CP15(ttbr0, "mrc p15, 0, %0, c2, c0, 0");
    READ_CP15(ttbr1, "mrc p15, 0, %0, c2, c0, 1");
    READ_CP15(ttbcr, "mrc p15, 0, %0, c2, c0, 2");
    READ_CP15(dacr, "mrc p15, 0, %0, c3, c0, 0");
#undef READ_CP15
    late.flags = BOOT_MMU_REGS_OBSERVED;
    boot_mmu_read_l1(&late, native_read_word);
}

CODE void boot_mmu_request_report(void)
{
    export_refresh_request_ordinary(&export_refresh_control);
}

CODE static void report_phase(unsigned phase, const volatile struct boot_mmu_snapshot *s)
{
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, phase_fmt,
                       phase, s->flags, s->cpsr, s->mpidr);
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, regs_fmt,
                       s->sctlr, s->actlr, s->ttbr0, s->ttbr1);
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, controls_fmt, s->ttbcr, s->dacr);
    for (unsigned block = 0; block < 3u; ++block)
        observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, table_fmt, block,
                           s->l1[block * 16u], boot_mmu_expected_descriptor(block),
                           boot_mmu_mismatch_mask(s, block));
}

/* The worker is the only caller and owner. ACK is outside this callback,
 * after every report returns; keep all appended reports inside it. */
CODE void boot_mmu_emit_bundle(void *unused, unsigned reports)
{
    (void)unused;
    read_late_registers();
    unsigned secondary_seen = __atomic_load_n(&boot_mmu_secondary.published, __ATOMIC_ACQUIRE);
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, begin_fmt, reports, 3u);
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, source_fmt, 2u, secondary_seen,
                       secondary_seen ? boot_mmu_secondary.restore : 0u);
    report_phase(0u, &boot_mmu_early);
    report_phase(1u, &late);
    report_phase(2u, secondary_seen ? &boot_mmu_secondary.snapshot : &not_published);
    /* Fixed cstart-owned words only. Never dereference these sampled values. */
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, slots_fmt,
        native_read_word(0x171dcu), native_read_word(0x158d0u),
        native_read_word(0x30830u), native_read_word(0x30840u));
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, end_fmt, reports);
    menu_report(reports,&late);
#if !defined(R5_CROP_ONLY) || !R5_CROP_ONLY
    pcm_runtime_report(reports);
#endif
    jpeg_crop_trace_report(reports);
    frame_native_report(reports);
    frame_disable_report(reports);
    playback_report(reports);
#if defined(R5_RECORD_INDICATOR) && R5_RECORD_INDICATOR
    rec_tone_report(reports);
#endif
}
static const char refresh_fmt[] DATA = "R5R1 S=" OBSERVER_SOURCE_ID " Q=%u I=%u R=%u";
CODE static void refresh_marker(void *unused, unsigned ticket, unsigned report, unsigned result)
{
    (void)unused;
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, refresh_fmt, ticket, report, result);
}
CODE void boot_mmu_tick(void)
{
    struct export_refresh_context context=export_refresh_native_context(0);
    export_refresh_worker(&export_refresh_control, &context,
        observer_api.debug ? boot_mmu_emit_bundle : 0,
        observer_api.debug ? refresh_marker : 0, 0);
}

#endif
