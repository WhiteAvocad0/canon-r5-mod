.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.thumb_func
.global xpan_original_get_aspect_rect
.type xpan_original_get_aspect_rect,%function
/* E098FB2C's complete8B prefix has no PC-relative instruction. The original
 * PUSH consumes36B, followed atFB34 by SUB SP28: original total64B frame,
 * fifth output pointer at newSP+64. Replaying this prefix preserves the
 * calling wrapper's exact argument stack and return link. */
xpan_original_get_aspect_rect:
 push {r4,r5,r6,r7,r8,r9,r10,r11,lr}
 mov.w r5,#0
 ldr pc,=0xe098fb35
.size xpan_original_get_aspect_rect,.-xpan_original_get_aspect_rect
.ltorg
