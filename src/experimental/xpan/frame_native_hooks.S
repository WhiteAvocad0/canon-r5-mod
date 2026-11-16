.syntax unified
.cpu cortex-a9
.thumb
#include "frame_native.h"
#include "frame_native_profile.h"
.section .text.frame_native_hooks,"ax",%progbits

/* 56B complete CPU image +8B APSR/pad. All C calls keep SP8 alignment.
 * Only APSR NZCVQ/GE are restored: no CPSR control/IF/CP15 operations. */
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
.macro c_entry name,helper
 start \name
 save_cpu
 add r3,sp,#8
 bl \helper
 str r0,[sp,#8]
 restore_cpu
 bx lr
 finish \name
.endm
c_entry frame_native_default_entry,frame_native_default_adapter
c_entry frame_native_row_entry,frame_native_row_adapter
c_entry frame_native_offset_entry,frame_native_offset_adapter

/* The full native routine is called with its entry SP at the scope base.
 * Its exact native frame lies immediately BELOW the owned private scope. */
.macro restore_inputs scope_bytes
 ldr r0,[sp,#\scope_bytes+8]
 ldr r1,[sp,#\scope_bytes+12]
 ldr r2,[sp,#\scope_bytes+16]
 ldr r3,[sp,#\scope_bytes+20]
 ldr r12,[sp,#\scope_bytes+56]
.endm
.macro copy_wrapper name,original,continuation,native_frame,kind
 start \name
 save_cpu
 sub sp,#FN_COPY_SCOPE_BYTES
 mov r0,sp
 sub.w r1,sp,#\native_frame
 ldr r2,=\continuation
 movs r3,#\kind
 bl frame_native_copy_scope_init
 restore_inputs FN_COPY_SCOPE_BYTES
 bl \original
 .thumb_func
 .global \continuation
 .type \continuation,%function
\continuation:
 str r0,[sp,#FN_COPY_SCOPE_BYTES+8]
 mov r0,sp
 bl frame_native_copy_scope_end
 add sp,#FN_COPY_SCOPE_BYTES
 restore_cpu
 bx lr
 .size \continuation,.-\continuation
 finish \name
.endm
copy_wrapper frame_native_snapshot_entry,frame_native_original_snapshot,frame_native_snapshot_continue,FN_SNAPSHOT_FRAME,1
copy_wrapper frame_native_refresh_entry,frame_native_original_refresh,frame_native_refresh_continue,FN_REFRESH_FRAME,3

start frame_native_reset_entry
 save_cpu
 sub sp,#FN_RESET_SCOPE_BYTES
 mov r0,sp
 bl frame_native_reset_begin
 restore_inputs FN_RESET_SCOPE_BYTES
 bl frame_native_original_reset
 str r0,[sp,#FN_RESET_SCOPE_BYTES+8]
 mov r0,sp
 bl frame_native_reset_end
 add sp,#FN_RESET_SCOPE_BYTES
 restore_cpu
 bx lr
finish frame_native_reset_entry

.macro scope_call helper,native_frame,site
 save_cpu
 add r2,sp,#64
 add.w r0,r2,#\native_frame
 add r1,sp,#8
 movs r3,#\site
 bl \helper
 restore_cpu
.endm
start frame_native_copy_default
 scope_call frame_native_copy_begin,FN_SNAPSHOT_FRAME,1
 /* Complete10B E07C37DE prefix; native memcpy is ARM. */
 ldr.w r0,[r5,r0,lsl #2]
 ldr r12,=FN_NATIVE_MEMCPY
 blx r12
 ldr lr,=0xe07c37e7
 movs r0,#0
 ldr pc,=FN_COPY_DEFAULT_RESUME
finish frame_native_copy_default

start frame_native_copy_previous
 scope_call frame_native_copy_begin,FN_SNAPSHOT_FRAME,2
 ldr.w r0,[r5,r0,lsl #2]
 ldr r12,=FN_NATIVE_MEMCPY
 blx r12
 ldr lr,=0xe07c3809
 ldr pc,=FN_COPY_PREVIOUS_RESUME
finish frame_native_copy_previous

start frame_native_snapshot_publish
 scope_call frame_native_copy_publish,FN_SNAPSHOT_FRAME,1
 /* Finalize after fallback overrides, before current-index publication. */
 str r0,[r4,#28]
 adds r0,r0,#1
 ldr r12,=FN_NATIVE_DIVIDE
 blx r12
 ldr lr,=0xe07c3b55
 ldr pc,=FN_PUBLISH_RESUME
finish frame_native_snapshot_publish

start frame_native_copy_refresh
 /* Preserve this slot's native frame BEFORE reserving/copying it. */
 ldr r5,[r0]
 scope_call frame_native_copy_begin,FN_REFRESH_FRAME,3
 ldr r12,=FN_NATIVE_MEMCPY
 blx r12
 ldr lr,=0xe07c42d5
 ldr r0,[r4,#28]
 movs r1,#3
 ldr pc,=FN_COPY_REFRESH_RESUME
finish frame_native_copy_refresh

start frame_native_refresh_publish
 /* Restore Canon's native frame WHILE the owned lease is still active.
  * Publication follows the final canonical object store, never precedes it. */
 str r5,[r0]
 scope_call frame_native_copy_publish,FN_REFRESH_FRAME,3
 movs r0,#0
 str r0,[r4,#52]
 movs r0,#156
 ldmia.w sp!,{r4,r5,r6,lr}
 ldr pc,=FN_FINISH_REFRESH_RESUME
finish frame_native_refresh_publish

/* Exact whole source prefixes, including additional instructions required by
 * half-aligned far jumps. PC-relative source loads/calls are relocated. */
start frame_native_original_default
 push {r4,r5,r6,lr}
 mov r6,r3
 mov r5,r2
 mov r4,r0
 ldr r12,=FN_NATIVE_COUNTER_BEGIN+1
 blx r12
 ldr lr,=FN_DEFAULT_RESUME
 ldr pc,=FN_DEFAULT_RESUME
finish frame_native_original_default

start frame_native_original_snapshot
 stmdb sp!,{r4,r5,r6,r7,r8,lr}
 movs r6,#1
 ldr r4,=FN_DTS_CONTROL
 movw r7,#FN_DTS_BYTES
 ldr pc,=FN_SNAPSHOT_RESUME
finish frame_native_original_snapshot

start frame_native_original_refresh
 push {r4,r5,r6,lr}
 ldr r4,=FN_DTS_CONTROL+100
 subs r4,#100
 ldr r0,[r4,#52]
 ldr pc,=FN_REFRESH_RESUME
finish frame_native_original_refresh

start frame_native_original_reset
 stmdb sp!,{r4-r12,lr}
 movs r1,#0
 ldr r6,=FN_DTS_CONTROL
 mov r0,r1
 ldr pc,=FN_RESET_RESUME
finish frame_native_original_reset

start frame_native_original_row
 push {r4,r5,lr}
 mov r3,r2
 sub sp,#28
 mov r4,r1
 mov r5,r0
 ldr pc,=FN_ROW_RESUME
finish frame_native_original_row

start frame_native_original_offset
 mov r3,r2
 mov r2,r1
 mov r1,r0
 mov r0,r3
 ldr pc,=FN_OFFSET_RESUME
finish frame_native_original_offset
