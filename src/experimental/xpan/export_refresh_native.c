#include "export_refresh.h"
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
struct export_refresh_state export_refresh_control;
CODE struct export_refresh_context export_refresh_native_context(void *unused)
{
 (void)unused;
 struct export_refresh_context c={0,UINT32_MAX,0,0,0,UINT32_MAX};
 __asm__ volatile("mrs %0, cpsr":"=r"(c.cpsr));
 unsigned mode=c.cpsr&31u;
 /* Never follow native context pointers from interrupt/exception/IF-off mode. */
 if((mode!=0x13u && mode!=0x1fu) || (c.cpsr&0xc0u)) return c;
 __asm__ volatile("mrc p15, 0, %0, c0, c0, 5":"=r"(c.cpu));
 c.cpu &= 0xffu;
 if(c.cpu!=0) return c;
 /* DebugMsg routes through GetCore E045168A, whose producer is kernel1008.
  * MPIDR proves physical CPU identity but does not substitute for this route. */
 c.route_cpu=*(volatile const uint32_t *)0x1008u;
 if(c.route_cpu!=0) return c;
 c.native100c=*(volatile const uint32_t *)0x100cu;
 c.native102c=*(volatile const uint32_t *)0x102cu;
 if(c.native100c || c.native102c) return c;
 c.task=*(volatile const uint32_t *)0x1028u;
 return c;
}
CODE static void native_sleep(void *unused,unsigned ms)
{ (void)unused; ((void (*)(unsigned))(uintptr_t)0xe07f1703u)(ms); }
static const struct export_refresh_io native_io DATA={export_refresh_native_context,native_sleep,0};
CODE void export_refresh_native_before_copy(uint32_t caller,uint32_t source,uint32_t length)
{ (void)export_refresh_wait(&export_refresh_control,caller,source,length,&native_io); }
