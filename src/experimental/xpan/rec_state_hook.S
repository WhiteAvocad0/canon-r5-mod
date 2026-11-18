/* Issue #14 record-indicator hook (opt-in --record-indicator): GUI_SetRecordingState
 * entry E07C6FBC, 8 bytes (push {r4,lr}; mov r4,r0; mov r3,r0; movs r1,#3). */
.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.global rec_state_entry
.type rec_state_entry,%function
.thumb_func
rec_state_entry:
 /* Observe old/new recording state and caller, then replay the exact 8-byte
  * GUI_SetRecordingState prologue and resume. Original always runs once. */
 push {r0-r3,r12,lr}
 mrs r12,APSR
 sub sp,#8
 str r12,[sp]
 add r0,sp,#8
 bl rec_state_observe
 ldr r12,[sp]
 msr APSR_nzcvqg,r12
 add sp,#8
 pop {r0-r3,r12,lr}
 push {r4,lr}
 mov r4,r0
 mov r3,r0
 movs r1,#3
 ldr pc,=0xe07c6fc5
.size rec_state_entry,.-rec_state_entry
.ltorg
.section .note.GNU-stack,"",%progbits
