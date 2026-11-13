.if FW_OBSERVE_CAPTURE
.section .resident,"ax",%progbits
.thumb
.thumb_func
.global observer_start
observer_start:
 mrs r0, cpsr
 tst r0, #0x80
 bne observer_start_done
 ldr r0, =WITNESS_CONTEXT_BASE
 ldr r1, [r0, #12]
 cmp r1, #0
 bgt observer_start_done
 ldr r1, [r0, #44]
 cmp r1, #0
 bgt observer_start_done
 /* Public API interface slot; internal dispatcher0x770c is a different owner. */
 ldr r0, =OBSERVER_PUBLIC_SLOT
 ldr r0, [r0]
 cbz r0, observer_start_done
 ldr r0, [r0, #4]
 cbz r0, observer_start_done
 b.w observer_register
observer_start_done:
 bx lr
.ltorg
.endif
