.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.thumb_func
.global xpan_gui_original_gather
.type xpan_gui_original_gather,%function
/* Source E07D030C..0313: b570,1d81,4d66,4604.
 * The PC-relative LDR originally reads E07D04AC=171D8. Replay that absolute
 * value instead of copying its old displacement. Enter by BL/BLX: the original
 * frame owns the newLR and returns to the wrapper through E07D05EC's POP.
 * There are no caller-stack arguments. Preserve the original PUSH and ADDS.
 */
xpan_gui_original_gather:
 push {r4,r5,r6,lr}
 adds r1,r0,#6
 ldr r5,=0x000171d8
 mov r4,r0
 ldr pc,=0xe07d0315
.size xpan_gui_original_gather,.-xpan_gui_original_gather
.ltorg
