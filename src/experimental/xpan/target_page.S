#include "feature_pages.h"
.section .resident_page,"ax",%progbits
.balign 4096
.global boot_shadow_page
.global boot_shadow_page_end
.global boot_shadow_target_va
.equ boot_shadow_target_va, FEATURE_BOOT_VA
boot_shadow_page:
.incbin "target-page.bin"
boot_shadow_page_end:
