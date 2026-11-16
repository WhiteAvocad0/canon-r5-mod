.syntax unified
.cpu cortex-a9
.section .resident_text,"ax",%progbits
.thumb
.thumb_func
.global root_switch
/* Caller admits Cortex-A9, SVC/System with I/F masked, TTBCR=7 and
 * unchanged low identity TTBR0. r0=owned root (or stock on rollback).
 * Instructions, stack and literals here are all retained below 32MiB.
 * No ROM/DFFC/MMIO access is permitted while PD1 is set. */
root_switch:
 push {r4,lr}
 mov r4,r0
 movs r2,#0
 mrc p15,0,r1,c2,c0,2
 cmp r1,#7
 bne root_switch_fatal
 movs r1,#0x27
 dsb sy
 mcr p15,0,r1,c2,c0,2
 isb sy
 mcr p15,0,r2,c8,c7,0
 dsb sy
 isb sy
 mrc p15,0,r3,c2,c0,2
 cmp r3,#0x27
 bne root_switch_fatal
 mcr p15,0,r4,c2,c0,1
 isb sy
 mcr p15,0,r2,c8,c7,0
 dsb sy
 isb sy
 mrc p15,0,r3,c2,c0,1
 cmp r3,r4
 bne root_switch_restore
 movs r4,#7                  /* ROOT_SWITCHED */
 b root_switch_enable
root_switch_restore:
 ldr r1,=0xdffc0000
 mcr p15,0,r1,c2,c0,1
 isb sy
 mcr p15,0,r2,c8,c7,0
 dsb sy
 isb sy
 mrc p15,0,r3,c2,c0,1
 cmp r3,r1
 bne root_switch_fatal
 movs r4,#9                  /* ROOT_RESTORED */
root_switch_enable:
 movs r1,#7
 mcr p15,0,r1,c2,c0,2
 isb sy
 mcr p15,0,r2,c8,c7,0
 dsb sy
 isb sy
 mrc p15,0,r3,c2,c0,2
 cmp r3,#7
 bne root_switch_fatal
 mcr p15,0,r2,c7,c5,0
 mcr p15,0,r2,c7,c5,6
 dsb sy
 isb sy
 mov r0,r4
 pop {r4,pc}
root_switch_fatal:
 wfi
 b root_switch_fatal
.ltorg
