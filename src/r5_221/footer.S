/* SPDX-License-Identifier: GPL-2.0-or-later
 * Adapted from upstream src/footer.c's assembly block.
 * ML-owned image convention; not a claim about Canon loader requirements.
 */
.section .footer,"a",%progbits
.balign 32, 0xCE
    .rept 6
    .word 0xCEEEEEEC
    .endr
.global autoexec_bin_footer
autoexec_bin_footer:
    .word 0xE12FFF13
    .word 0xCCCCCCCC
.global autoexec_bin_checksum_end
autoexec_bin_checksum_end:
