.syntax unified
.cpu cortex-a9
.thumb
#include "q_label.h"
.section .text.q_label_hooks,"ax",%progbits
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
 ldmia.w sp!,{r0-r12,lr}
.endm
.balign 4
.thumb_func
.global q_label_entry
.type q_label_entry,%function
q_label_entry:
 /* No flags change while cheaply delegating unrelated resource lookups. */
 push {r1,lr}
 subw r1,r0,#QL_STRING_ID
 cbnz r1,.Ldelegate
 subw r1,r8,#QL_ITEM_KEY
 cbnz r1,.Ldelegate
 ldr r1,=QL_PROPERTY_CALLER
 eor.w r1,r1,lr
 cbz r1,.Lcandidate
 ldr r1,=QL_WIDTH_CALLER
 eor.w r1,r1,lr
 cbz r1,.Lcandidate
.Ldelegate:
 ldmia.w sp!,{r1,lr}
 b.w q_label_original_getter
.Lcandidate:
 ldmia.w sp!,{r1,lr}
 save_cpu
 /* Restore the complete original input image, including r12, for Canon. */
 add lr,sp,#8
 ldmia.w lr,{r0-r12}
 bl q_label_original_getter
 /* Preserve Canon's post-call registers/APSR while selecting only r0. */
 save_cpu
 add r0,sp,#QL_SAVE_BYTES+8
 add r1,sp,#QL_SAVE_BYTES*2
 ldr r2,[sp,#8]
 bl q_label_after
 str r0,[sp,#8]
 restore_cpu
 /* Preserve Canon's post-call LR; consume the owned actual return into PC. */
 add sp,#QL_SAVE_BYTES-4
 pop {pc}
.size q_label_entry,.-q_label_entry
.ltorg
.balign 4
.thumb_func
.global q_label_original_getter
.type q_label_original_getter,%function
q_label_original_getter:
 push {r4,r5,r6,lr}
 mov r4,r0
 movs r1,#1
 movs r0,#14
 ldr pc,=QL_GETTER_RESUME
.size q_label_original_getter,.-q_label_original_getter
.ltorg
