.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.thumb_func
.global menu_heap_shim
menu_heap_shim:
 push {r4,lr}
 ldr r12,=0xe07e9acd
 blx r12
 ldr r12,=0xe07f17a7
 blx r12
 push {r0-r3,r12,lr}
 mrs r4,apsr
#if !defined(R5_CROP_ONLY) || !R5_CROP_ONLY
 bl pcm_runtime_prepare
#endif
 bl menu_prepare
 msr APSR_nzcvqg,r4
 pop {r0-r3,r12,lr}
 pop {r4,lr}
 ldr lr,=0xe0100785
 ldr pc,=0xe0100785
.thumb_func
.global menu_resource_shim
menu_resource_shim:
 push {r4,lr}
 ldr r12,=0xe026b003
 blx r12
 push {r0-r3,r12,lr}
 mrs r4,apsr
 bl menu_install
 msr APSR_nzcvqg,r4
 pop {r0-r3,r12,lr}
 pop {r4,lr}
 ldr lr,=0xe01005dd
 ldr pc,=0xe01005e1
.ltorg
.thumb_func
.global root_peer_shim
root_peer_shim:
 push {r4,lr}
 ldr r12,=0xe0168bc9
 blx r12
 push {r0-r3,r12,lr}
 mrs r4,apsr
 bl root_peer_check
 msr APSR_nzcvqg,r4
 pop {r0-r3,r12,lr}
 ldr r12,=0xe03dadeb
 blx r12
 pop {r4,lr}
 ldr lr,=0xe0100c5d
 ldr pc,=0xe0100c5d
.ltorg
.section .resident_rodata,"a",%progbits
.balign 4
.global menu_vector
.global menu_vector_end
menu_vector:
.incbin "menu-vector.bin"
menu_vector_end:
