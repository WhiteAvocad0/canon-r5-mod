/* GPL-2.0-or-later. Host-only R5.221 diagnostic, no deploy gate satisfied.
 * Dual-entry convention follows the retained ML bootstrap source.
 */
.syntax unified
.cpu cortex-a9
.include "firmware.inc"
.section .entry,"ax",%progbits
.global _start
.thumb
_start:
 nop
 b.n thumb_entry
.arm
 b check_image
.thumb
.thumb_func
thumb_entry:
 blx check_image
.balign 4
.arm
check_image:
 cpsid if
 adr r8, _start
 ldr r1, =_image_bytes
 add r1, r8, r1
 sub r2, r1, #8
 ldr r3, [r2]
 ldr r4, =0xe12fff13
 cmp r3, r4
 bne stage0_fail
 mov r0, r8
 mov r2, #0
1: ldr r3, [r0], #4
 eor r2, r2, r3
 cmp r0, r1
 blo 1b
 cmp r2, #0
 bne stage0_fail
 ldr r0, =FW_MAIN_START
 ldr r1, =FW_SIGNATURE_WORDS
 mov r2, #0
2: ldr r3, [r0], #4
 add r2, r2, r3
 subs r1, r1, #1
 bne 2b
 ldr r3, =FW_SIGNATURE
 cmp r2, r3
 bne stage0_fail
 mrc p15, 0, r0, c0, c0, 5
 ands r0, r0, #15
 bne secondary_wait
 ldr r0, =_resident_load_offset
 add r0, r8, r0
 ldr r1, =_resident_start
 ldr r2, =_resident_data_end
3: cmp r1, r2
 ldrlo r3, [r0], #4
 strlo r3, [r1], #4
 blo 3b
 ldr r1, =_bss_start
 ldr r2, =_reserved_end
 mov r3, #0
4: cmp r1, r2
 strlo r3, [r1], #4
 blo 4b
 /* Clean written resident cache lines to coherency, invalidate instruction view. */
 ldr r0, =_resident_start
 bic r0, r0, #31
 ldr r1, =_reserved_end
5: mcr p15, 0, r0, c7, c10, 1
 add r0, r0, #32
 cmp r0, r1
 blo 5b
 dsb sy
 mov r0, #0
 mcr p15, 0, r0, c7, c5, 0
 mcr p15, 0, r0, c7, c5, 6
 dsb sy
 isb sy
 ldr r0, =FW_SECONDARY_SLOT
 ldr r1, =(FW_MAIN_START+1)
 str r1, [r0]
 mcr p15, 0, r0, c7, c10, 1
 dsb sy
 sev
 ldr r0, =resident_entry
 bx r0
secondary_wait:
 ldr r0, =FW_SECONDARY_SLOT
 ldr r2, =(FW_MAIN_START+1)
6: dmb sy
 ldr r1, [r0]
 cmp r1, r2
 bne 6b
 bx r1
stage0_fail:
 wfi
 b stage0_fail
.ltorg

.section .resident,"ax",%progbits
.thumb
.thumb_func
.global resident_entry
resident_entry:
 ldr sp, =_private_stack_top
 ldr r0, =copied_startup
 orr r0, r0, #1
 bx r0

/* Tail veneers preserve copied startup LR and r3. */
.thumb_func
.global cache_clean_wrapper
cache_clean_wrapper: ldr pc, =FW_CACHE_CLEAN
.thumb_func
.global cache_invalidate_wrapper
cache_invalidate_wrapper: ldr pc, =FW_CACHE_INVALIDATE
.thumb_func
.global bzero_wrapper
bzero_wrapper: ldr pc, =FW_BZERO

.thumb_func
.global create_wrapper
create_wrapper:
 push {r0-r12,lr}
 mov r11, sp
 ldr sp, =_private_stack_top
 /* Stock helper still contains unbounded submission polling: deployment gate. */
 ldr r12, =FW_CAMIF_FIXUP
 blx r12
 ldr r4, =FW_CAMIF_POLL_BUDGET
7: ldr r12, =FW_CAMIF_STATUS
 blx r12
 cmp r0, #0
 beq 8f
 cmp r0, #1
 bne resident_fail
 subs r4, r4, #1
 bne 7b
 b resident_fail
8: mov sp, r11
 ldr r0, [sp]
 ldr r1, [r0, #8]
 ldr r2, =FW_USER_START
 cmp r1, r2
 bne resident_fail
 ldr r1, [r0, #12]
 ldr r2, =FW_USER_LENGTH
 cmp r1, r2
 bne resident_fail
 ldr r1, [r0, #16]
 ldr r2, =FW_OBJECTS_START
 cmp r1, r2
 bne resident_fail
 ldr r1, [sp, #4]
 ldr r2, =FW_CANON_INIT
 cmp r1, r2
 bne resident_fail
 ldr r1, =_reserved_end
 str r1, [r0, #8]
 ldr r2, =FW_USER_START
 subs r1, r1, r2
 ldr r2, =FW_USER_LENGTH
 subs r2, r2, r1
 ldr r3, =FW_MINIMUM_USER_LEFT
 cmp r2, r3
 blo resident_fail
 str r2, [r0, #12]
 pop {r0-r12,lr}
 ldr r1, =init_wrapper
 ldr pc, =FW_INIT_FACTORY

.thumb_func
.global init_wrapper
init_wrapper:
 push {r4-r11,lr}
 sub sp, sp, #4
 /* Original callback receives its original four register arguments and context. */
 ldr r12, =FW_CANON_INIT
 blx r12
 mov r4, r0
 mov r5, sp
.if FW_SHUTTER_FEEDBACK
 mov r0, r4
 bl sound_boot_record_boot_return
.endif
 /* Retain original init-task stack across scheduling FIO/sleep calls. */
 ldr r6, =witness_state
 movs r0, #STAGE_CANON_INIT_RETURNED
 str r0, [r6, #STATE_STAGE]
 movs r0, #0
 mvns r0, r0
 str r0, [r6, #STATE_FD]
 /* Preserve context; do not enable interrupts or unlock Canon counters. */
 mrs r0, cpsr
 tst r0, #0x80
 bne witness_context_unready
 ldr r0, =WITNESS_CONTEXT_BASE
 ldr r1, [r0, #12]
 cmp r1, #0
 bgt witness_context_unready
 ldr r1, [r0, #44]
 cmp r1, #0
 bgt witness_context_unready
.if FW_AUDIO_ONCE
 bl audio_attempt
.if FW_SHUTTER_FEEDBACK
 bl sound_boot_record_startup_outcome
.endif
 bl audio_render
.endif
.if FW_SHUTTER_FEEDBACK
 b witness_done
.endif
 movs r7, #0
.global witness_mount
witness_mount:
 adds r7, r7, #1
 str r7, [r6, #STATE_ATTEMPT_COUNT]
 ldr r0, [r6, #STATE_ATTEMPT_FLAGS]
 orr r0, r0, #ATTEMPT_MOUNT
 str r0, [r6, #STATE_ATTEMPT_FLAGS]
 ldr r0, =witness_drive
 ldr r12, =WITNESS_MOUNT_CHECK
 blx r12
 str r0, [r6, #STATE_MOUNT_RETURN]
 cmp r0, #1
 beq witness_create
 ldr r0, [r6, #STATE_ATTEMPT_FLAGS]
 orr r0, r0, #ATTEMPT_SLEEP
 str r0, [r6, #STATE_ATTEMPT_FLAGS]
 movs r0, #WITNESS_SLEEP_MS
 ldr r12, =WITNESS_SLEEP
 blx r12
 str r0, [r6, #STATE_SLEEP_RETURN]
 cmp r7, #WITNESS_MOUNT_ATTEMPT_LIMIT
 blo witness_mount
 movs r0, #STAGE_MOUNT_TIMEOUT
 b witness_set_stage_done
witness_context_unready:
.if FW_AUDIO_ONCE
 ldr r0, =audio_state
 movs r1, #AO_CONTEXT_UNREADY
 str r1, [r0, #AF_OUTCOME]
.endif
 movs r0, #STAGE_CONTEXT_UNREADY
 b witness_set_stage_done
.global witness_create
witness_create:
 ldr r0, [r6, #STATE_ATTEMPT_FLAGS]
 orr r0, r0, #ATTEMPT_CREATE
 str r0, [r6, #STATE_ATTEMPT_FLAGS]
 ldr r0, =witness_path
 ldr r12, =WITNESS_CREATE
 blx r12
 str r0, [r6, #STATE_CREATE_RETURN]
 str r0, [r6, #STATE_FD]
 cmp r0, #3
 bge witness_fd_owned
 movs r0, #STAGE_CREATE_INVALID
 b witness_set_stage_done
witness_fd_owned:
 mov r8, r0
 movs r0, #1
 str r0, [r6, #STATE_FD_OWNED]
.global witness_write
witness_write:
 movs r0, #STAGE_WRITE_ATTEMPTED
 str r0, [r6, #STATE_STAGE]
 ldr r0, [r6, #STATE_ATTEMPT_FLAGS]
 orr r0, r0, #ATTEMPT_WRITE
 str r0, [r6, #STATE_ATTEMPT_FLAGS]
 /* Storage requires an uncached view. Publish final rendered bytes first;
  * the alias is an I/O representation of our same retained resident owner. */
 ldr r0, =witness_body
 ldr r1, =WITNESS_BODY_LENGTH
 ldr r12, =FW_CACHE_CLEAN
 blx r12
 ldr r0, =witness_body
 ldr r12, =WITNESS_TO_UNCACHED
 blx r12
 mov r1, r0
 mov r0, r8
 ldr r2, =WITNESS_BODY_LENGTH
 ldr r12, =WITNESS_WRITE
 blx r12
 str r0, [r6, #STATE_WRITE_RETURN]
 /* Every valid descriptor reaches this single close call, including short write. */
.global witness_close
witness_close:
 ldr r0, [r6, #STATE_ATTEMPT_FLAGS]
 orr r0, r0, #ATTEMPT_CLOSE
 str r0, [r6, #STATE_ATTEMPT_FLAGS]
 mov r0, r8
 ldr r12, =WITNESS_CLOSE
 blx r12
 str r0, [r6, #STATE_CLOSE_RETURN]
 cmp r0, #0
 bge witness_closed
 movs r0, #STAGE_CLOSE_FAILED
 b witness_set_stage_done
witness_closed:
 movs r0, #0
 str r0, [r6, #STATE_FD_OWNED]
.global witness_flush
witness_flush:
 ldr r0, [r6, #STATE_ATTEMPT_FLAGS]
 orr r0, r0, #ATTEMPT_FLUSH
 str r0, [r6, #STATE_ATTEMPT_FLAGS]
 ldr r0, =witness_drive
 ldr r12, =WITNESS_FLUSH
 blx r12
 str r0, [r6, #STATE_FLUSH_RETURN]
 cmp r0, #0
 bge witness_check_write
 movs r0, #STAGE_FLUSH_FAILED
 b witness_set_stage_done
witness_check_write:
 ldr r0, [r6, #STATE_WRITE_RETURN]
 ldr r1, =WITNESS_BODY_LENGTH
 cmp r0, r1
 beq witness_calls_complete
 movs r0, #STAGE_WRITE_SHORT
 b witness_set_stage_done
witness_calls_complete:
 movs r0, #STAGE_CALLS_COMPLETE
witness_set_stage_done:
 str r0, [r6, #STATE_STAGE]
.global witness_done
witness_done:
.if FW_OBSERVE_CAPTURE
 bl observer_start
.endif
.if FW_SHUTTER_FEEDBACK
 bl sound_boot_start_worker
.endif
 mov sp, r5
 mov r0, r4
 add sp, sp, #4
 pop {r4-r11,pc}
resident_fail:
 cpsid if
9: wfi
 b 9b
.ltorg
#include "audio.S"
#include "observer.S"
.balign 4
witness_path:
.include "witness-path.inc"
witness_drive: .asciz "B:/"
.balign 4
.global witness_body
witness_body:
.incbin "witness-template.bin"
.balign 16
.global copied_startup
copied_startup:
.incbin "startup.bin"
.global copied_cstart
.set copied_cstart, copied_startup + FW_CSTART_OFFSET
.balign 16
.section .resident_data,"aw",%progbits
.word 0x221
.section .bss,"aw",%nobits
.balign 16
.global witness_state
witness_state: .space WITNESS_STATE_BYTES

.if FW_AUDIO_ONCE
.balign 16
.global audio_state
audio_state: .space AUDIO_STATE_BYTES
.endif
