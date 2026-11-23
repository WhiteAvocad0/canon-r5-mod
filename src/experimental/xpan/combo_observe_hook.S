/* Issue #15 hook H2 gui_class0_dispatch (opt-in --screenshot).
 * Window E04A8028, 8 bytes 0146784808b58368 (bit 1 clear; interior E04A802A..
 * E04A8030 has no branch target):
 *   E04A8028 4601  mov r1, r0
 *   E04A802A 4878  ldr r0, [pc, #480]   @ literal E04A820C = 0x00005474
 *   E04A802C b508  push {r3, lr}
 *   E04A802E 6883  ldr r3, [r0, #8]
 * Entry r0 = 16-byte GUI ring slot {+0 class, +4 code, +8 arg2, +12 arg3}.
 * Record one entry, publish it with one release store, restore every register
 * and APSR, then replay the four instructions (the PC-relative literal is
 * re-materialised from this file's own literal pool) and resume at E04A8030.
 * No calls, no Canon writes, the slot is never modified. GuiMainTask is the
 * only caller, so the ring has a single writer. */
#include "combo_observe.h"
.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.global combo_gui_dispatch_entry
.type combo_gui_dispatch_entry,%function
.thumb_func
combo_gui_dispatch_entry:
 push {r0-r5,r12,lr}
 mrs r12, APSR
 ldr r4, .Lring
 ldr r5, [r4, #COMBO_RING_WRITTEN_OFFSET]
 add r5, r5, #1                 @ seq of this record
 sub r3, r5, #1
 and r3, r3, #COMBO_RING_MASK
 add r3, r4, r3, lsl #4         @ &entries[(seq - 1) & 63]
 mov lr, #0                     @ flags
 ldr r1, [r0, #0]               @ class
 cmp r1, #0
 it ne
 orrne lr, lr, #COMBO_F_CLASS_NONZERO
 ldr r1, [r0, #4]               @ event code
 cmp r1, #0x10000
 it hs
 orrhs lr, lr, #COMBO_F_CODE_WIDE
 uxth r1, r1
 ldr r2, .Ltick
 ldr r2, [r2]
 orr r1, r1, r2, lsl #16
 str r1, [r3, #4]               @ code_tick
 ldr r1, [r0, #8]               @ arg2
 cmp r1, #0x10000
 it hs
 orrhs lr, lr, #COMBO_F_ARG2_WIDE
 uxth r1, r1
 ldr r2, [r0, #12]              @ arg3
 cmp r2, #0x10000
 it hs
 orrhs lr, lr, #COMBO_F_ARG3_WIDE
 orr r1, r1, r2, lsl #16
 str r1, [r3, #8]               @ args
 ldr r4, .Lgui_context
 ldr r1, [r4, #0]               @ [0x5474]
 ldr r2, [r4, #8]               @ [0x547C]
 cmp r1, r2
 it ne
 orrne lr, lr, #COMBO_F_GEN_UNEQUAL
 uxtb r1, r1
 uxtb r2, r2
 orr r1, r1, r2, lsl #8
 ldr r4, .Luiope_mode
 ldr r2, [r4]                   @ [0x92D0]
 cmp r2, #0x100
 it hs
 orrhs lr, lr, #COMBO_F_MODE_WIDE
 uxtb r2, r2
 orr r1, r1, r2, lsl #16
 orr r1, r1, lr, lsl #24
 str r1, [r3, #12]              @ context
 dmb ish
 str r5, [r3, #0]               @ the single release store: publishes the record
 ldr r4, .Lring
 str r5, [r4, #COMBO_RING_WRITTEN_OFFSET]  @ hook-private count
 dmb ish                        @ the count is visible before the next record's payload
 /* A plain class-0 trigger press or release goes to the GuiMainTask
  * capture owner (combo_capture.h explains the context). r0 is still the
  * slot; r4/r5 are callee-saved, so APSR (r12) and SP survive the call. */
 tst lr, #(COMBO_F_CODE_WIDE | COMBO_F_CLASS_NONZERO)  @ lr still holds this record's flags
 bne 1f
 ldr r1, [r0, #4]
 cmp r1, #COMBO_TRIGGER_CODE
 beq 2f
 /* The INFO release too (a deferred screenshot's start, one_press.h). Not SET:
  * it is the confirm key of every playback dialog (Erase, Protect, Q, Resize). */
 cmp r1, #COMBO_INFO_RELEASE
 beq 2f
 cmp r1, #(COMBO_TRIGGER_CODE + 1)
 bne 1f
2:
 mov r5, r12                    @ APSR across the call
 mov r4, sp
 bic r1, r4, #7                 @ AAPCS: 8-byte aligned SP at the call
 mov sp, r1
 bl combo_capture_gui
 mov sp, r4
 mov r12, r5
1:
 msr APSR_nzcvqg, r12
 pop {r0-r5,r12,lr}
 /* Exact replay of E04A8028..E04A802F. */
 mov r1, r0
 ldr r0, .Lgui_context
 push {r3, lr}
 ldr r3, [r0, #8]
 ldr pc, .Lresume
.balign 4
.Lring: .word combo_hook_ring
.Ltick: .word combo_worker_tick
.Lgui_context: .word COMBO_GUI_CONTEXT
.Luiope_mode: .word COMBO_UIOPE_MODE
.Lresume: .word COMBO_HOOK_RESUME
.size combo_gui_dispatch_entry,.-combo_gui_dispatch_entry
.section .note.GNU-stack,"",%progbits
