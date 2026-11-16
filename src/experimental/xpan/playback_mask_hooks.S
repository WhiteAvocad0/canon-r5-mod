.syntax unified
.cpu cortex-a9
.thumb
#include "playback_mask_native.h"
.section .text.playback_mask_hooks,"ax",%progbits
.macro start name
 .balign 4
 .thumb_func
 .global \name
 .type \name,%function
\name:
.endm
.macro finish name
 .size \name,.-\name
 .ltorg
.endm
.macro save_cpu
 stmdb sp!,{r0-r12,lr}
 mrs r12,APSR
 sub sp,#8
 str r12,[sp]
.endm
.macro restore_cpu
 ldr r12,[sp]
 msr APSR_nzcvqg,r12
 add sp,#8
 ldmia sp!,{r0-r12,lr}
.endm
start playback_mask_fit_entry
 save_cpu
 sub sp,#PM_FIT_SCOPE_BYTES
 mov r0,sp
 add.w r1,sp,#PM_FIT_SCOPE_BYTES+8
 sub.w r2,sp,#PM_FIT_FRAME
 ldr r3,=playback_mask_fit_continue
 bl playback_mask_fit_begin
 ldr r0,[sp,#PM_FIT_SCOPE_BYTES+8]
 ldr r1,[sp,#PM_FIT_SCOPE_BYTES+12]
 ldr r2,[sp,#PM_FIT_SCOPE_BYTES+16]
 ldr r3,[sp,#PM_FIT_SCOPE_BYTES+20]
 ldr r12,[sp,#PM_FIT_SCOPE_BYTES+56]
 bl playback_mask_original_fit
 .thumb_func
 .global playback_mask_fit_continue
 .type playback_mask_fit_continue,%function
playback_mask_fit_continue:
 mov r1,r0
 mov r0,sp
 bl playback_mask_fit_end
 /* End returns1 for a qualified projection, else the exact native result. */
 str r0,[sp,#PM_FIT_SCOPE_BYTES+8]
 add sp,#PM_FIT_SCOPE_BYTES
 restore_cpu
 bx lr
 .size playback_mask_fit_continue,.-playback_mask_fit_continue
finish playback_mask_fit_entry
start playback_mask_original_fit
 push {r4,r5,r6,r7,lr}
 cmp r2,#1
 sub sp,#28
 mov r6,r2
 ldr pc,=PM_FIT_RESUME
finish playback_mask_original_fit
start playback_mask_guide_entry
 /* Entered only after native E06CBF6C hid the four guide resources. */
 save_cpu
 add r0,sp,#8
 bl playback_mask_guide_decide
 cmp r0,#0
 bne .Lguide_skip
 restore_cpu
 ldr r0,[r5,#16]
 cmp r0,#0
 beq .Lguide_bail
 ldr r0,[r5,#20]
 cmp r0,#0
 ldr pc,=PM_GUIDE_RESUME
.Lguide_skip:
 restore_cpu
.Lguide_bail:
 ldr pc,=PM_GUIDE_BAIL
finish playback_mask_guide_entry
