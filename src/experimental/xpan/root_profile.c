#include "root_plan.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif
const struct root_profile root_target_profile DATA={0xff0ffff0u,0x410fc090u,0x40c5187du,0x45u,0xdffc4800u,ROOT_ORIGINAL,7u,0x55555555u,15u,0x80u,0xdfu,0xd3u};
CODE uint32_t root_page_checksum(const volatile uint32_t *p)
{uint32_t v=0;for(unsigned n=0;n<1024;n++)v=(v>>1|v<<31)^p[n];return v;}

