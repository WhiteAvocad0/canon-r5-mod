.syntax unified
.cpu cortex-a9
.thumb
#include "playback_native.h"
.section .text.playback_native_hooks,"ax",%progbits
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

start playback_native_main_entry
 save_cpu
 sub sp,#PN_MAIN_SCOPE_BYTES
 mov r0,sp
 add r1,sp,#PN_MAIN_SCOPE_BYTES+8
 subw r2,sp,#PN_MAIN_FRAME
 ldr r3,=playback_native_main_continue
 bl playback_native_main_scope_init
 ldr r0,[sp,#PN_MAIN_SCOPE_BYTES+8]
 ldr r1,[sp,#PN_MAIN_SCOPE_BYTES+12]
 ldr r2,[sp,#PN_MAIN_SCOPE_BYTES+16]
 ldr r3,[sp,#PN_MAIN_SCOPE_BYTES+20]
 ldr r12,[sp,#PN_MAIN_SCOPE_BYTES+56]
 bl playback_native_original_main
.thumb_func
.global playback_native_main_continue
.type playback_native_main_continue,%function
playback_native_main_continue:
 str r0,[sp,#PN_MAIN_SCOPE_BYTES+8]
 mov r1,r0
 mov r0,sp
 bl playback_native_main_scope_end
 add sp,#PN_MAIN_SCOPE_BYTES
 restore_cpu
 bx lr
.size playback_native_main_continue,.-playback_native_main_continue
finish playback_native_main_entry

.macro tag_entry name,site,success,resume
 start \name
 save_cpu
 add r0,sp,#8
 add r1,sp,#64
 movs r2,#\site
 bl playback_native_tag_observe
 restore_cpu
 /* Replay source CBZ without changing APSR; failures retain Canon stores. */
 cbz r0,1f
 strh.w r6,[r4,#0x62]
 str.w r6,[r4,#0x298]
 ldr pc,=\resume
1:
 ldr pc,=\success
 finish \name
.endm
tag_entry playback_native_tag_primary_entry,1,PN_TAG_PRIMARY_SUCCESS,PN_TAG_PRIMARY_RESUME
tag_entry playback_native_tag_alternate_entry,2,PN_TAG_ALTERNATE_SUCCESS,PN_TAG_ALTERNATE_RESUME

.macro copy_call role,return_lr
 save_cpu
 movs r3,#\role
 bl playback_native_copy_adapter
 str r0,[sp,#8]
 restore_cpu
 ldr lr,=\return_lr
.endm
start playback_native_backup1_entry
 copy_call 1,0xe071061b
 ldr r0,=PN_STATE
 ldr r1,[r0,#0x10]
 str r1,[r0,#0x14]
 ldr pc,=PN_BACKUP1_RESUME
finish playback_native_backup1_entry

start playback_native_backup2_entry
 copy_call 2,0xe071093d
 ldr r0,=PN_STATE
 ldr r1,[r0,#0x10]
 ldr pc,=PN_BACKUP2_RESUME
finish playback_native_backup2_entry

start playback_native_restore1_entry
 copy_call 3,0xe0710959
 ldmia.w sp!,{r4,lr}
 ldr pc,=PN_RESTORE1_RESUME
finish playback_native_restore1_entry

start playback_native_restore2_entry
 copy_call 4,0xe0710977
 ldmia.w sp!,{r4,lr}
 ldr r0,=PN_OBJECT0
 ldr pc,=PN_RESTORE2_RESUME
finish playback_native_restore2_entry

start playback_native_cache_entry
 save_cpu
 bl playback_native_cache_adapter
 str r0,[sp,#8]
 restore_cpu
 bx lr
finish playback_native_cache_entry

start playback_native_original_main
 stmdb sp!,{r4-r11,lr}
 mov r10,r1
 sub.w sp,sp,#676
 ldr pc,=PN_MAIN_RESUME
finish playback_native_original_main

start playback_native_original_cache
 stmdb sp!,{r3,r4,r5,r6,r7,r8,r9,lr}
 mov r4,r0
 ldrh r0,[r0]
 movs r1,#3
 ldr pc,=PN_CACHE_RESUME
finish playback_native_original_cache
