/* XPan review-authority observer hook at E054C530 (8 bytes 70b504000e461546). */
.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.global xpan_review_authority_entry
.type xpan_review_authority_entry,%function
.thumb_func
xpan_review_authority_entry:
 /* Passive: save the caller-saved registers and APSR, observe r0/r1/r2/lr in C
  * (AAPCS preserves r4-r11), restore them, then replay the exact 8-byte
  * E054C530 prologue (push {r4-r6,lr}; movs r4,r0; mov r6,r1; mov r5,r2) and
  * resume at E054C538. The MOVS recomputes N/Z from the restored r0 for the
  * stock bne.n; C/V/Q/GE come back from the saved APSR. */
 push {r0-r3,r12,lr}
 mrs r12,APSR
 sub sp,#8
 str r12,[sp]
 add r0,sp,#8
 bl xpan_review_authority_observe
 ldr r12,[sp]
 msr APSR_nzcvqg,r12
 add sp,#8
 pop {r0-r3,r12,lr}
 push {r4,r5,r6,lr}
 movs r4,r0
 mov r6,r1
 mov r5,r2
 ldr pc,=0xe054c539
.size xpan_review_authority_entry,.-xpan_review_authority_entry
.ltorg
.section .note.GNU-stack,"",%progbits
