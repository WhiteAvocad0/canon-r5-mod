#include "root_plan.h"
#include "multi_plan.h"
#include "feature_pages.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

CODE static int controls_supported(const volatile struct boot_mmu_snapshot *s)
{
 const struct root_profile *p=&root_target_profile;
 unsigned core=s->mpidr & p->core_mask;
 return core<2u && s->sctlr==p->sctlr && s->actlr==p->actlr &&
  s->ttbr0==p->ttbr0+core*p->ttbr0_stride && s->ttbcr==p->ttbcr && s->dacr==p->dacr;
}
CODE int root_cpu_supported(const volatile struct boot_mmu_snapshot *s, uint32_t midr)
{
 const struct root_profile *p=&root_target_profile;
 return s && (midr&p->midr_mask)==p->midr_value &&
  (s->cpsr&p->boot_cpsr_mask)==p->boot_cpsr_value && s->flags==3u &&
  s->ttbr1==p->ttbr1 && controls_supported(s) &&
  multi_rom_supported(s->l1,3u);
}
CODE unsigned root_view_kind(const volatile struct boot_mmu_snapshot *s,uint32_t root)
{
 if(!s || !(s->flags&1u) || !controls_supported(s))return 0u;
 unsigned mode=s->cpsr&31u;
 if(mode!=0x13u && mode!=0x1fu)return 0u;
 if(s->ttbr1==ROOT_ORIGINAL)return 1u;
 return s->ttbr1==root?2u:0u;
}
CODE int root_layout_supported(const struct root_layout *l)
{
 if(!l || l->owned_start<0x100000u || l->objects_end>0x300000u ||
  l->owned_start>=l->initialized_end || l->initialized_end>l->objects_end ||
  l->root<l->owned_start || l->root>l->initialized_end || l->initialized_end-l->root<16384u ||
  l->page<l->owned_start || l->page>l->initialized_end || l->initialized_end-l->page<4096u)return 0;
 return multi_layout_supported(l->root,l->children,feature_targets,FEATURE_PAGE_COUNT,l->owned_start,l->objects_end);
}
CODE uint32_t root_original_drift(const volatile uint32_t *stock,const volatile uint32_t *root)
{
 uint32_t count=0;
 for(unsigned i=0;i<ROOT_WORDS;i++)
  if(stock[i] != ((i>=ROOT_FIRST && i<ROOT_FIRST+16u)?0xe005d406u:root[i]))count++;
 return count;
}
CODE enum root_result root_prepare(const struct root_input *i)
{
 if(!i || !root_cpu_supported(i->cpu,i->midr))return ROOT_BAD_CPU;
 if(!root_layout_supported(&i->layout))return ROOT_BAD_OWNER;
 if(!i->stock||!i->rom||!i->page||!i->low_ram||!i->root||!i->children||!i->shape)return ROOT_BAD_OWNER;
 if(!root_ram_supported(i->low_ram))return ROOT_BAD_RAM;
 /* Feature callers/constant records and image resources span the64MiB main
  * ROM. Admit their complete identity supersections before source reads. */
 if(!multi_rom_supported(i->stock+ROOT_FIRST,4u))return ROOT_BAD_PLAN;
 if(feature_prepare_pages())return ROOT_BAD_PAGE;
 for(unsigned n=0;n<ROOT_WORDS;n++)i->root[n]=i->stock[n];
 if(multi_plan_build(i->stock+ROOT_FIRST,feature_targets,FEATURE_PAGE_COUNT,
  i->layout.children,i->root+ROOT_FIRST,i->children,i->shape)!=MULTI_OK)return ROOT_BAD_PLAN;
 if(root_original_drift(i->stock,i->root))return ROOT_BAD_PLAN;
 return ROOT_PREPARED;
}

CODE int root_ram_supported(const volatile uint32_t *r)
{
 if(!r)return 0;
 for(unsigned n=0;n<4;n++)if(r[n]!=(n%2u?0x00214406u:0x00114406u))return 0;
 return 1;
}
