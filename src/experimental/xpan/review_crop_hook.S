/* XPan review-descriptor crop hook at E063FBEE (10 bytes 2de9f047174698229846).
 * Completed E063FBEE invocation only; the common E063BECE path is never hooked. */
.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.global review_crop_entry
.type review_crop_entry,%function
.thumb_func
review_crop_entry:
 stmdb sp!,{r0-r12,lr}
 mrs r12,APSR
 sub sp,#8
 str r12,[sp]
 /* Original consumes eight formal args. Reconstruct its four scalar stack
  * args on our aligned callframe; preserve both original CPU image and SP. */
 sub sp,#16
 add.w r12,sp,#80
 ldmia.w r12,{r0-r3}
 stmia.w sp,{r0-r3}
 ldr r12,[sp,#16]
 msr APSR_nzcvqg,r12
 add.w r12,sp,#24
 ldmia.w r12,{r0-r3}
 ldr r12,[sp,#72]
 bl review_crop_original
.global review_crop_completed
.type review_crop_completed,%function
.thumb_func
review_crop_completed:
 /* Capture every actual original output and APSR BEFORE any flag-changing
  * instruction. Original r14 may be its memcpy-return link; preserve it. */
 stmdb sp!,{r0-r12,lr}
 mrs r12,APSR
 sub sp,#8
 str r12,[sp]
 add.w r0,sp,#88
 add.w r1,sp,#144
 add.w r2,sp,#8
 ldr r3,=0x53524331
 bl review_crop_native_post
 ldr r12,[sp]
 msr APSR_nzcvqg,r12
 add sp,#8
 ldmia.w sp!,{r0-r12,lr}
 /* Return through saved original caller LR WITHOUT replacing native r14.
  * Pop it while still at/above SP; released stack may be reused by IRQs. */
 add sp,#76
 pop {pc}
.size review_crop_completed,.-review_crop_completed
.size review_crop_entry,.-review_crop_entry
.ltorg
.balign 4
.global review_crop_original
.type review_crop_original,%function
.thumb_func
review_crop_original:
 /* Replay exact 10-byte prefix including MOVS flags, then exact resume. */
 stmdb sp!,{r4-r10,lr}
 mov r7,r2
 movs r2,#152
 mov r8,r3
 ldr pc,=0xe063fbf9
.size review_crop_original,.-review_crop_original
.ltorg
.section .note.GNU-stack,"",%progbits
