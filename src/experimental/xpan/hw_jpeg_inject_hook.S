/* Screenshot JPEG injection (opt-in --screenshot): H3 / H8 entries.
 * hw_jpeg_inject.h describes both windows. Each
 * entry saves r0-r5, r12, lr and APSR, calls inject_record(hook, saved,
 * entry_sp) with an 8-byte aligned SP (AAPCS; Canon's FIO uses strd/ldrd on
 * its frame), restores everything and replays the window's instructions
 * exactly, then resumes after the window. r0-r3 reach Canon unchanged: H3
 * changes only the pixels at desc[0]'s IBUS address, never a register or a
 * descriptor word. Neither window has a PC-relative instruction. */
#include "hw_jpeg_inject.h"
.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits

.macro INJECT_RECORD hook
 push {r0-r5, r12, lr}
 mrs r4, APSR                   @ r4/r5 are callee-saved: they survive the call
 mov r5, sp
 bic r1, r5, #7                 @ AAPCS: 8-byte aligned SP at the call
 mov sp, r1
 mov r1, r5                     @ saved[] = r0 r1 r2 r3 r4 r5 r12 lr
 add r2, r5, #32                @ entry SP (before this push)
 mov r0, #\hook
 bl inject_record
 mov sp, r5
 msr APSR_nzcvqg, r4
 pop {r0-r5, r12, lr}
.endm

.balign 4
.global inject_encode_entry
.type inject_encode_entry,%function
.thumb_func
inject_encode_entry:
 INJECT_RECORD INJECT_HOOK_ENCODE
 /* Exact replay of E0CCB284..E0CCB28B. */
 push {r4, r5, r6, r7, r8, r9, sl, lr}
 mov sl, r3
 mov r4, r1
 ldr pc, .Lencode_resume
.balign 4
.Lencode_resume: .word INJECT_H3_RESUME
.size inject_encode_entry,.-inject_encode_entry

.balign 4
.global inject_develop_end_entry
.type inject_develop_end_entry,%function
.thumb_func
inject_develop_end_entry:
 INJECT_RECORD INJECT_HOOK_DEVELOP_END
 /* Exact replay of E0549258..E0549261. */
 push {r3, r4, r5, r6, r7, r8, r9, lr}
 mov r6, r0
 mov r8, #292
 ldr pc, .Ldevelop_end_resume
.balign 4
.Ldevelop_end_resume: .word INJECT_H8_RESUME
.size inject_develop_end_entry,.-inject_develop_end_entry
.section .note.GNU-stack,"",%progbits
