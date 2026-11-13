/* Opt-in one-shot/dual PCM diagnostic and serialized player. Included in resident code by handoff.S. */
.if FW_AUDIO_ONCE
.thumb
.thumb_func
.global audio_attempt
audio_attempt:
 ldr r0, =audio_descriptor
 ldr r1, =AUDIO_POLL_LIMIT
.thumb_func
.global audio_attempt_descriptor
audio_attempt_descriptor:
 push {r4-r11,lr}
 sub sp, sp, #12
 str r0, [sp, #0]
 str r1, [sp, #4]
 ldr r10, [r0, #AUDIO_DESC_PTR_OFFSET]
 ldr r11, [r0, #AUDIO_DESC_BYTES_OFFSET]
 ldr r4, =audio_state
 mov r2, r4
 movs r3, #0
 movs r0, #AUDIO_STATE_WORDS
0:
 str r3, [r2], #4
 subs r0, r0, #1
 bne 0b
 movs r7, #0
.global audio_ready_poll
audio_ready_poll:
 adds r7, r7, #1
 str r7, [r4, #AF_READY_POLLS]
 /* Before-fields and dependency blocker belong to this attempt, never a stale snapshot. */
 mov r9, #AO_NOT_READY
 bl audio_reset_dep_obs
 movs r0, #0
 str r0, [r4, #AF_STATE_BEFORE]
 str r0, [r4, #AF_PHASE_BEFORE]
 str r0, [r4, #AF_TEST_OWNER_BEFORE]
 ldr r0, [r4, #AF_OBS_FLAGS]
 bic r0, r0, #(OF_STATE_BEFORE_QUERIED|OF_PHASE_BEFORE_SAMPLED|OF_TEST_OWNER_BEFORE_SAMPLED)
 str r0, [r4, #AF_OBS_FLAGS]
 ldr r0, =AUDIO_SINGLETON_SLOT
 ldr r5, [r0]
 str r5, [r4, #AF_CONTEXT_BEFORE]
 ldr r0, [r4, #AF_OBS_FLAGS]
 orr r0, r0, #OF_CONTEXT_BEFORE_SAMPLED
 str r0, [r4, #AF_OBS_FLAGS]
 cmp r5, #0
 beq audio_ready_wait
 ldr r0, [r5]
 cmp r0, #0
 beq audio_ready_wait
 ldr r6, [r5, #4]
 cmp r6, #0
 beq audio_ready_wait
 mov r0, r6
 ldr r12, =AUDIO_STATE_QUERY
 blx r12
 str r0, [r4, #AF_STATE_BEFORE]
 ldr r1, [r4, #AF_OBS_FLAGS]
 orr r1, r1, #OF_STATE_BEFORE_QUERIED
 str r1, [r4, #AF_OBS_FLAGS]
 cmn r0, #1
 beq audio_state_invalid
 ldr r1, [r5, #0x48]
 str r1, [r4, #AF_PHASE_BEFORE]
 ldr r2, [r5, #0x4c]
 str r2, [r4, #AF_TEST_OWNER_BEFORE]
 ldr r3, [r4, #AF_OBS_FLAGS]
 orr r3, r3, #(OF_PHASE_BEFORE_SAMPLED|OF_TEST_OWNER_BEFORE_SAMPLED)
 str r3, [r4, #AF_OBS_FLAGS]
 cmp r2, #0
 bne audio_stock_owned
 cmp r0, #1
 bne audio_ready_wait
 cmp r1, #3
 bne audio_ready_wait
 bl audio_check_dependencies
 cmp r0, #0
 beq audio_ready_bound
 cmp r0, #AO_AUDIOCTRL_STATE_INVALID
 beq audio_done
 mov r9, r0
 b audio_ready_wait
audio_ready_bound:
 str r5, [r4, #AUDIO_BOUND_CONTEXT]
 str r6, [r4, #AUDIO_BOUND_HANDLE]
 b audio_recheck
audio_ready_wait:
 ldr r0, [sp, #4]
 cmp r7, r0
 bhs audio_ready_exhausted
 movs r0, #AUDIO_SLEEP_MS
 ldr r12, =WITNESS_SLEEP
 blx r12
 b audio_ready_poll
audio_ready_exhausted:
 mov r0, r9
 b audio_done
.global audio_recheck
audio_recheck:
 movs r0, #0
 str r0, [r4, #AF_STATE_BEFORE]
 str r0, [r4, #AF_PHASE_BEFORE]
 str r0, [r4, #AF_TEST_OWNER_BEFORE]
 ldr r0, [r4, #AF_OBS_FLAGS]
 bic r0, r0, #(OF_STATE_BEFORE_QUERIED|OF_PHASE_BEFORE_SAMPLED|OF_TEST_OWNER_BEFORE_SAMPLED)
 str r0, [r4, #AF_OBS_FLAGS]
 ldr r0, =AUDIO_SINGLETON_SLOT
 ldr r0, [r0]
 str r0, [r4, #AUDIO_LAST_CONTEXT]
 str r0, [r4, #AF_CONTEXT_BEFORE]
 cmp r0, r5
 bne audio_context_changed
 ldr r0, [r5]
 cmp r0, #0
 beq audio_not_ready
 ldr r0, [r5, #4]
 cmp r0, r6
 bne audio_context_changed
 mov r0, r6
 ldr r12, =AUDIO_STATE_QUERY
 blx r12
 str r0, [r4, #AF_STATE_BEFORE]
 ldr r1, [r4, #AF_OBS_FLAGS]
 orr r1, r1, #OF_STATE_BEFORE_QUERIED
 str r1, [r4, #AF_OBS_FLAGS]
 cmn r0, #1
 beq audio_state_invalid
 ldr r1, [r5, #0x48]
 str r1, [r4, #AF_PHASE_BEFORE]
 ldr r2, [r5, #0x4c]
 str r2, [r4, #AF_TEST_OWNER_BEFORE]
 ldr r3, [r4, #AF_OBS_FLAGS]
 orr r3, r3, #(OF_PHASE_BEFORE_SAMPLED|OF_TEST_OWNER_BEFORE_SAMPLED)
 str r3, [r4, #AF_OBS_FLAGS]
 cmp r2, #0
 bne audio_stock_owned
 cmp r0, #1
 bne audio_not_ready
 cmp r1, #3
 bne audio_not_ready
 bl audio_check_dependencies
 cmp r0, #0
 bne audio_done
.global audio_submit_once
audio_submit_once:
 movs r0, #1
 str r0, [r4, #AUDIO_SUBMIT_ATTEMPTED]
 movs r0, #0
 ldr r1, [sp, #0]
 ldr r12, =AUDIO_BEEP_START
 blx r12
 str r0, [r4, #AF_SUBMIT_RETURN]
 ldr r1, [r4, #AF_OBS_FLAGS]
 orr r1, r1, #OF_SUBMIT_RETURN_OBSERVED
 str r1, [r4, #AF_OBS_FLAGS]
 cmp r0, #0
 bne audio_submit_nonzero
 movs r8, #0
.global audio_observe_poll
audio_observe_poll:
 adds r8, r8, #1
 str r8, [r4, #AF_OBSERVE_POLLS]
 movs r0, #0
 str r0, [r4, #AF_STATE_AFTER]
 str r0, [r4, #AF_PHASE_AFTER]
 ldr r0, [r4, #AF_OBS_FLAGS]
 bic r0, r0, #(OF_STATE_AFTER_QUERIED|OF_PHASE_AFTER_SAMPLED)
 str r0, [r4, #AF_OBS_FLAGS]
 ldr r0, =AUDIO_SINGLETON_SLOT
 ldr r0, [r0]
 str r0, [r4, #AF_CONTEXT_AFTER]
 ldr r1, [r4, #AF_OBS_FLAGS]
 orr r1, r1, #OF_CONTEXT_AFTER_SAMPLED
 str r1, [r4, #AF_OBS_FLAGS]
 cmp r0, r5
 bne audio_context_changed
 ldr r0, [r5, #4]
 cmp r0, r6
 bne audio_context_changed
 mov r0, r6
 ldr r12, =AUDIO_STATE_QUERY
 blx r12
 str r0, [r4, #AF_STATE_AFTER]
 ldr r1, [r4, #AF_OBS_FLAGS]
 orr r1, r1, #OF_STATE_AFTER_QUERIED
 str r1, [r4, #AF_OBS_FLAGS]
 cmn r0, #1
 beq audio_state_invalid
 ldr r9, [r5, #0x48]
 str r9, [r4, #AF_PHASE_AFTER]
 orr r1, r1, #OF_PHASE_AFTER_SAMPLED
 str r1, [r4, #AF_OBS_FLAGS]
 ldr r0, [r5, #0x24]
 str r0, [r4, #AUDIO_BACKEND_RETURN]
 ldr r2, [r5, #0x28]
 str r2, [r4, #AUDIO_LAST_PCM_PTR]
 ldr r3, [r5, #0x2c]
 str r3, [r4, #AUDIO_LAST_PCM_BYTES]
 cmp r9, #4
 bhi audio_unexpected_phase
 movs r0, #1
 lsl r0, r0, r9
 ldr r1, [r4, #AF_PHASE_SEEN_MASK]
 orrs r0, r0, r1
 str r0, [r4, #AF_PHASE_SEEN_MASK]
 cmp r9, #2
 beq audio_interrupted
 cmp r9, #4
 beq audio_unexpected_phase
 ldr r0, [r4, #AF_ACTIVE_POLL]
 cmp r0, #0
 beq audio_check_active
 cmp r2, r10
 bne audio_buffer_changed
 cmp r3, r11
 bne audio_buffer_changed
audio_check_active:
 cmp r9, #1
 bne audio_check_settled
 cmp r2, r10
 bne audio_observe_wait
 cmp r3, r11
 bne audio_observe_wait
 cmp r0, #0
 bne audio_observe_wait
 str r8, [r4, #AF_ACTIVE_POLL]
 str r2, [r4, #AF_ACTIVE_PCM_PTR]
 str r3, [r4, #AF_ACTIVE_PCM_BYTES]
 ldr r0, [r4, #AF_OBS_FLAGS]
 orr r0, r0, #OF_OWN_PCM_ACTIVE_OBSERVED
 str r0, [r4, #AF_OBS_FLAGS]
 b audio_observe_wait
audio_check_settled:
 cmp r9, #3
 bne audio_observe_wait
 cmp r0, #0
 beq audio_observe_wait
 cmp r8, r0
 bls audio_observe_wait
 ldr r1, [r4, #AF_STATE_AFTER]
 cmp r1, #1
 bne audio_observe_wait
 movs r0, #AO_SETTLED_OBSERVED
 b audio_done
audio_observe_wait:
 cmp r8, #AUDIO_POLL_LIMIT
 bhs audio_timeout
 movs r0, #AUDIO_SLEEP_MS
 ldr r12, =WITNESS_SLEEP
 blx r12
 b audio_observe_poll
audio_timeout:
 ldr r0, [r4, #AF_ACTIVE_POLL]
 cmp r0, #0
 beq audio_active_not_seen
 movs r0, #AO_COMPLETION_TIMEOUT
 b audio_done
audio_active_not_seen: movs r0, #AO_ACTIVE_NOT_SEEN
 b audio_done
audio_not_ready: movs r0, #AO_NOT_READY
 b audio_done
audio_state_invalid: movs r0, #AO_STATE_INVALID
 b audio_done
audio_stock_owned: movs r0, #AO_STOCK_BUFFER_OWNED
 b audio_done
audio_context_changed: movs r0, #AO_CONTEXT_CHANGED
 b audio_done
audio_submit_nonzero: movs r0, #AO_SUBMIT_NONZERO
 b audio_done
audio_unexpected_phase: movs r0, #AO_UNEXPECTED_PHASE
 b audio_done
audio_interrupted: movs r0, #AO_STOPPED_OR_INTERRUPTED
 b audio_done
audio_buffer_changed: movs r0, #AO_BUFFER_CHANGED
.global audio_done
audio_done:
 str r0, [r4, #AF_OUTCOME]
 add sp, sp, #12
 pop {r4-r11,pc}
.ltorg

/* Shared read-only OutSel0 dependency guard; preserves r4-r11/sp/lr and never dereferences NULL. */
.thumb_func
.global audio_reset_dep_obs
audio_reset_dep_obs:
 ldr r0, =audio_state
 adds r0, r0, #AUDIO_DEP_OBS_FLAGS
 movs r1, #0
 movs r2, #AUDIO_DEP_OBS_WORDS
1:
 str r1, [r0], #4
 subs r2, r2, #1
 bne 1b
 bx lr

.thumb_func
.global audio_check_dependencies
audio_check_dependencies:
 push {r4,lr}
 bl audio_reset_dep_obs
 ldr r4, =audio_state
 ldr r0, =AUDIO_AUDIOCTRL_SLOT
 ldr r1, [r0]
 str r1, [r4, #AUDIO_DEP_AUDIOCTRL_PTR]
 ldr r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 orr r2, r2, #DEP_OF_AUDIOCTRL_PTR_SAMPLED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmp r1, #0
 beq dep_audioctrl_missing
 ldr r0, [r1, #AUDIO_AUDIOCTRL_HANDLE_OFFSET]
 str r0, [r4, #AUDIO_DEP_AUDIOCTRL_HANDLE]
 orr r2, r2, #DEP_OF_AUDIOCTRL_HANDLE_SAMPLED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmp r0, #0
 beq dep_audioctrl_missing
 ldr r12, =AUDIO_STATE_QUERY
 blx r12
 str r0, [r4, #AUDIO_DEP_AUDIOCTRL_STATE]
 ldr r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 orr r2, r2, #DEP_OF_AUDIOCTRL_STATE_QUERIED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmn r0, #1
 beq dep_audioctrl_state_invalid
 cmp r0, #1
 bne dep_audioctrl_not_ready
 ldr r0, =AUDIO_AUDIO_IC_SLOT
 ldr r1, [r0]
 str r1, [r4, #AUDIO_DEP_AUDIO_IC_PTR]
 ldr r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 orr r2, r2, #DEP_OF_AUDIO_IC_PTR_SAMPLED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmp r1, #0
 beq dep_audio_ic_unready
 ldr r0, [r1, #AUDIO_AUDIO_IC_INIT_OFFSET]
 str r0, [r4, #AUDIO_DEP_AUDIO_IC_INIT]
 orr r2, r2, #DEP_OF_AUDIO_IC_INIT_SAMPLED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmp r0, #1
 bne dep_audio_ic_unready
 ldr r0, =AUDIO_OUTPUT_CONTEXT_SLOT
 ldr r1, [r0]
 str r1, [r4, #AUDIO_DEP_OUTPUT_CONTEXT_PTR]
 ldr r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 orr r2, r2, #DEP_OF_OUTPUT_CONTEXT_PTR_SAMPLED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmp r1, #0
 beq dep_output_context_missing
 ldr r0, =AUDIO_ICU_CACHE_BASE
 ldr r1, [r0, #AUDIO_ICU_CACHE_INIT_OFFSET]
 str r1, [r4, #AUDIO_DEP_ICU_CACHE_INIT]
 ldr r3, [r0, #AUDIO_ICU_CACHE_KIND_OFFSET]
 str r3, [r4, #AUDIO_DEP_ICU_CACHE_KIND]
 ldr r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 orr r2, r2, #DEP_OF_ICU_CACHE_SAMPLED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmp r1, #0
 beq dep_icu_cache_unready
 cmp r3, #AUDIO_ICU_KIND_AK
 beq dep_select_ak
 cmp r3, #AUDIO_ICU_KIND_VAIF
 bne dep_icu_cache_unready
 ldr r0, =AUDIO_VAIF_PARAM_SLOT
 b dep_check_param_table
dep_select_ak:
 ldr r0, =AUDIO_AK_PARAM_SLOT
dep_check_param_table:
 ldr r1, [r0]
 str r1, [r4, #AUDIO_DEP_PARAM_TABLE_PTR]
 orr r2, r2, #DEP_OF_PARAM_TABLE_PTR_SAMPLED
 str r2, [r4, #AUDIO_DEP_OBS_FLAGS]
 cmp r1, #0
 beq dep_parameter_table_missing
 movs r0, #0
 pop {r4,pc}
dep_audioctrl_missing:
 movs r0, #AO_AUDIOCTRL_MISSING
 pop {r4,pc}
dep_audioctrl_state_invalid:
 movs r0, #AO_AUDIOCTRL_STATE_INVALID
 pop {r4,pc}
dep_audioctrl_not_ready:
 movs r0, #AO_AUDIOCTRL_NOT_READY
 pop {r4,pc}
dep_audio_ic_unready:
 movs r0, #AO_AUDIO_IC_UNREADY
 pop {r4,pc}
dep_output_context_missing:
 movs r0, #AO_OUTPUT_CONTEXT_MISSING
 pop {r4,pc}
dep_icu_cache_unready:
 movs r0, #AO_ICU_CACHE_UNREADY
 pop {r4,pc}
dep_parameter_table_missing:
 movs r0, #AO_PARAMETER_TABLE_MISSING
 pop {r4,pc}
.ltorg

/* Bounded fixed-width rendering only; no libc/heap and no PCM mutation. */
.thumb_func
.global audio_render
audio_render:
 push {r4-r7,lr}
 sub sp, sp, #4
 ldr r4, =audio_state
 ldr r5, =audio_log_offsets
 ldr r6, =witness_body
 movs r7, #AUDIO_LOG_FIELD_COUNT
1: ldr r0, [r4], #4
 ldr r1, [r5], #4
 add r1, r1, r6
 movs r2, #8
2: lsrs r3, r0, #28
 cmp r3, #10
 ite lo
 addlo r3, r3, #48
 addhs r3, r3, #87
 strb r3, [r1], #1
 lsls r0, r0, #4
 subs r2, r2, #1
 bne 2b
 subs r7, r7, #1
 bne 1b
 add sp, sp, #4
 pop {r4-r7,pc}
.ltorg
.balign 4
audio_log_offsets:
.include "audio-log-offsets.inc"
.balign 32
.global audio_descriptor
audio_descriptor:
.incbin "audio-descriptor.bin"
.balign 32
.global audio_pcm
audio_pcm:
.incbin "audio.pcm"
.global audio_pcm_end
audio_pcm_end:
.if FW_AUDIO_DUAL
.balign 32
.global shutter_descriptor
shutter_descriptor:
.incbin "shutter-descriptor.bin"
.balign 32
.global shutter_pcm
shutter_pcm:
.incbin "shutter.pcm"
.global shutter_pcm_end
shutter_pcm_end:
.endif
.endif
