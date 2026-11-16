.syntax unified
.cpu cortex-a9
.thumb
.section .resident_text,"ax",%progbits
.balign 4
.thumb_func
.global xpan_jpeg_primary_entry
.type xpan_jpeg_primary_entry,%function
xpan_jpeg_primary_entry:
 mov r2,sp
 mov r3,lr
 b.w xpan_jpeg_crop_primary
.size xpan_jpeg_primary_entry,.-xpan_jpeg_primary_entry

.balign 4
.thumb_func
.global xpan_jpeg_secondary_entry
.type xpan_jpeg_secondary_entry,%function
xpan_jpeg_secondary_entry:
 mov r2,sp
 mov r3,lr
 b.w xpan_jpeg_crop_secondary
.size xpan_jpeg_secondary_entry,.-xpan_jpeg_secondary_entry

.balign 4
.thumb_func
.global xpan_jpeg_original_primary
.type xpan_jpeg_original_primary,%function
/* Complete10B source prefix E0CD98DA..98E3, including MOVS r1,#32.
 * Original64B frame and native return r0 remain owned by Canon. */
xpan_jpeg_original_primary:
 push {r4,r5,lr}
 mov r4,r1
 sub sp,#52
 mov r5,r0
 movs r1,#32
 ldr pc,=0xe0cd98e5
.size xpan_jpeg_original_primary,.-xpan_jpeg_original_primary
.ltorg

.balign 4
.thumb_func
.global xpan_jpeg_original_secondary
.type xpan_jpeg_original_secondary,%function
/* Complete8B source prefix E0CD986C..9873. MOVS r1,#120 is at resume. */
xpan_jpeg_original_secondary:
 push {r4,r5,lr}
 mov r4,r1
 sub sp,#124
 mov r5,r0
 ldr pc,=0xe0cd9875
.size xpan_jpeg_original_secondary,.-xpan_jpeg_original_secondary
.ltorg

.balign 4
.thumb_func
.global xpan_roi_crop_entry
.type xpan_roi_crop_entry,%function
/* Inside B564 at E017B61C. Save56B registers +8B APSR/pad =64B,
 * retaining8-byte C-call alignment. C receives the ORIGINAL SP and r4.
 * No CPSR control fields/IF/CP15 operations; restore NZCVQ and GE flags. */
xpan_roi_crop_entry:
 stmdb sp!,{r0-r12,lr}
 mrs r12,APSR
 sub sp,#8
 str r12,[sp]
 mov r1,r6
 add r2,sp,#64
 mov r3,r4
 bl xpan_roi_crop_select
 str r0,[sp,#8]
 ldr r12,[sp]
 msr APSR_nzcvqg,r12
 add sp,#8
 ldmia sp!,{r0-r12,lr}
 /* Replay STR/LDR/CBNZ/B semantics, preserving restored flags. */
 str r0,[r6,#64]
 ldr r0,[r6,#60]
 cbnz r0,1f
 ldr pc,=0xe017b637
1:
 ldr pc,=0xe017b645
.size xpan_roi_crop_entry,.-xpan_roi_crop_entry
.ltorg
