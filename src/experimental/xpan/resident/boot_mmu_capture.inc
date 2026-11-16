/* ARM-only, stackless snapshot producer shared by the two boot paths.
 * Secondary capture admits only core 1 before writing its owned object. */
.macro BOOT_MMU_CAPTURE snapshot, secondary
 ldr r0, =\snapshot
 mrs r1, cpsr
.if !\secondary
 str r1, [r0]
.endif
 and r2, r1, #31
 cmp r2, #0x13
 beq .Lmmu_mode_ok_\@
 cmp r2, #0x1f
 bne .Lmmu_end_\@
.Lmmu_mode_ok_\@:
.if \secondary
 mrc p15, 0, r2, c0, c0, 5
 and r2, r2, #15
 cmp r2, #1
 bne .Lmmu_end_\@
 str r1, [r0]
.endif
 mrc p15, 0, r1, c0, c0, 5
 str r1, [r0, #4]
 mrc p15, 0, r1, c1, c0, 0
 str r1, [r0, #8]
 mrc p15, 0, r1, c1, c0, 1
 str r1, [r0, #12]
 mrc p15, 0, r1, c2, c0, 0
 str r1, [r0, #16]
 mrc p15, 0, r1, c2, c0, 1
 str r1, [r0, #20]
 mrc p15, 0, r1, c2, c0, 2
 str r1, [r0, #24]
 mrc p15, 0, r1, c3, c0, 0
 str r1, [r0, #28]
 mov r1, #BOOT_MMU_REGS_OBSERVED
 str r1, [r0, #BOOT_MMU_FLAGS_OFFSET]
 ldr r1, [r0, #8]
 tst r1, #1
 beq .Lmmu_end_\@
 ldr r1, [r0, #24]
 cmp r1, #7
 bne .Lmmu_end_\@
 ldr r1, [r0, #20]
 ldr r2, =0xffffc000
 and r1, r1, r2
 ldr r2, =BOOT_MMU_TTBR1
 cmp r1, r2
 bne .Lmmu_end_\@
 ldr r1, =BOOT_MMU_L1_FIRST
 add r4, r0, #BOOT_MMU_TABLE_OFFSET
 mov r2, #BOOT_MMU_L1_WORDS
.Lmmu_table_loop_\@:
 ldr r3, [r1], #4
 str r3, [r4], #4
 subs r2, r2, #1
 bne .Lmmu_table_loop_\@
 mov r1, #(BOOT_MMU_REGS_OBSERVED | BOOT_MMU_TABLE_OBSERVED)
 str r1, [r0, #BOOT_MMU_FLAGS_OFFSET]
.Lmmu_end_\@:
.endm
