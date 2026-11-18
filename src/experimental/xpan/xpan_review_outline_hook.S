/* XPan review outline hook at _DrawAspectLine
 * E0A956FE, after the V/H decisions and before the first SetVisible call. */
.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.global xpan_review_outline_entry
.type xpan_review_outline_entry,%function
.thumb_func
xpan_review_outline_entry:
 /* Save the caller-saved registers and APSR, let C decide on the native frame
  * (it may zero only [sp+28] H and [sp+32] V of this call), restore, then
  * replay the exact 10 bytes (ldr r2,[sp,#32]; movs r1,#30; ldr.w r0,[r8,#20];
  * mov r4,r8) and resume at E0A95708. The replayed load picks up V after the
  * decision, so r2 stays consistent with the stack word. The MOVS sets N/Z as
  * stock does; C/V/Q/GE come back from the saved APSR. AAPCS keeps r4-r11. */
 push {r0-r3,r12,lr}
 mrs r12,APSR
 sub sp,#8
 str r12,[sp]
 add r0,sp,#32
 mov r1,sl
 bl xpan_review_outline_decide
 ldr r12,[sp]
 msr APSR_nzcvqg,r12
 add sp,#8
 pop {r0-r3,r12,lr}
 ldr r2,[sp,#32]
 movs r1,#30
 ldr.w r0,[r8,#20]
 mov r4,r8
 ldr pc,=0xe0a95709
.size xpan_review_outline_entry,.-xpan_review_outline_entry
.ltorg
.section .note.GNU-stack,"",%progbits
