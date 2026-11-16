/* Immutable-root splice at E02A73E6; original 48-byte saver frame remains. */
.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.global export_refresh_precopy
.type export_refresh_precopy,%function
.thumb_func
export_refresh_precopy:
 /* 32-byte shim preserves call-clobbered values, flags and 8-byte alignment. */
 push.w {r0-r3,r12,lr}
 mrs r3,apsr
 sub sp,sp,#8
 str r3,[sp]
 /* Scope checks in C precede any new native context reads. Saved LR is
    original SP+44, now shim SP+76. r6/r7/r11 remain untouched. */
 ldr r0,[sp,#76]
 mov r1,r11
 mov r2,r7
 bl export_refresh_native_before_copy
 ldr r3,[sp]
 msr APSR_nzcvqg,r3
 add sp,sp,#8
 pop.w {r0-r3,r12,lr}
 /* Replay the three exact 16-bit MOVs; tail interwork to the original ARM
    memcpy with its exact original Thumb return LR, preserving r12. */
 mov r2,r7
 mov r1,r11
 mov r0,r6
 ldr.w lr,export_refresh_copy_return
 ldr.w pc,export_refresh_copy_target
.balign 4
export_refresh_copy_return: .word 0xe02a73f1
export_refresh_copy_target: .word 0xe080a04c
.size export_refresh_precopy,.-export_refresh_precopy
.section .note.GNU-stack,"",%progbits
