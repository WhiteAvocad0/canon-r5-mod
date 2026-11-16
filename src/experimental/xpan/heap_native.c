#include "heap_native.h"
#include "heap_span.h"
#include "root_native.h"
#include "root_plan.h"
#define CODE __attribute__((section(".resident_text")))
_Static_assert(sizeof(uintptr_t)==4u,"native heap pointers");

CODE __attribute__((noinline)) int heap_native_admit(uint32_t raw,uint32_t bytes,
 volatile struct heapobs *out)
{
 if(!out)return 0;
 out->status=HEAP_NATIVE_BAD_SPAN;out->flags=0u;
 out->cpsr=0u;out->mpidr=0u;out->midr=0u;out->ttbr0=0u;out->ttbr1=0u;
 out->ttbcr=0u;out->view=0u;out->plan=0u;out->drift=UINT32_MAX;
 out->entry_count=0u;out->first_words[0]=0u;out->first_words[1]=0u;
 if(!heap_span_supported(raw,bytes))return 0;
 struct boot_mmu_snapshot cpu;
 root_current_snapshot(&cpu);
 out->cpsr=cpu.cpsr;out->flags=HEAP_OBS_CPSR;
 out->status=HEAP_NATIVE_BAD_REGISTERS;
 if(!(cpu.flags&1u))return 0;
 out->mpidr=cpu.mpidr;out->ttbr0=cpu.ttbr0;out->ttbr1=cpu.ttbr1;
 out->ttbcr=cpu.ttbcr;out->flags|=HEAP_OBS_REGISTERS;
 out->status=HEAP_NATIVE_BAD_CORE;
 if((cpu.mpidr&root_target_profile.core_mask)!=0u)return 0;
 struct root_view_facts view;
 root_late_facts(&cpu,&view);
 out->view=view.kind;out->plan=view.plan;out->drift=view.drift;
 if(view.kind){out->midr=view.midr;out->flags|=HEAP_OBS_MIDR;}
 out->status=HEAP_NATIVE_BAD_VIEW;
 if(view.kind!=2u || cpu.ttbcr!=7u)return 0;
 out->status=HEAP_NATIVE_BAD_PROFILE;
 if((view.midr&root_target_profile.midr_mask)!=root_target_profile.midr_value)return 0;
 out->status=HEAP_NATIVE_BAD_ROOT_FACTS;
 if(!view.plan || view.drift!=0u)return 0;
 out->flags|=HEAP_OBS_ROOT_ADMITTED;
 /* The shared root owner admitted the actual CPU0 TTBR0, owned TTBR1 and N7.
  * The split at32 entries also aligns the sixteen-entry supersection groups. */
 uint32_t first=(raw>>24)*16u;
 uint32_t last=((raw+bytes-1u)>>24)*16u+15u;
 uint32_t groups[32],count=last-first+1u;
 out->status=HEAP_NATIVE_BAD_MAPPING;
 if(count>32u)return 0;
 for(uint32_t n=0;n<count;n++) {
  uint32_t index=first+n;
  uintptr_t address=(index<32u?cpu.ttbr0:cpu.ttbr1)+index*4u;
  groups[n]=*(const volatile uint32_t *)address;
 }
 out->entry_count=count;out->first_words[0]=groups[0];
 if(count==32u)out->first_words[1]=groups[16];
 out->flags|=HEAP_OBS_TABLE_ENTRIES;
 if(!heap_span_mapping_supported(raw,bytes,groups,count))return 0;
 out->status=HEAP_NATIVE_ADMITTED;
 return 1;
}
