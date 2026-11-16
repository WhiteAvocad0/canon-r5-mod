#ifndef ROOT_IFD_HOST
#include "root_native.h"
#include "root_plan.h"
#include "feature_pages.h"
#include "multi_plan.h"
#include "boot_policy.h"
#include "observer.h"
#include "observer-source.h"
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
_Static_assert(sizeof(uintptr_t)==4,"native pointers");
extern uint32_t boot_shadow_page[1024];
extern char _resident_start[],_resident_data_end[],_bss_objects_end[];
extern unsigned root_switch(uint32_t root);
volatile uint32_t private_root[4096] __attribute__((section(".resident_root"),aligned(16384)))={0};
static volatile uint32_t children[MULTI_MAX_TABLES][256] __attribute__((aligned(1024)));
struct core_result {uint32_t result,midr,before,after,ttbcr,checksum,complete,pad;};
static volatile struct core_result cores[2] __attribute__((aligned(32)));
static volatile uint32_t decision __attribute__((aligned(32)));
static struct multi_shape shape;
static volatile uint32_t peer_verified;
static uint32_t prepare_result,reference_checksum;

CODE static uint32_t read_word(uintptr_t address){return *(const volatile uint32_t *)address;}
CODE static uint32_t midr(void){uint32_t v;__asm__ volatile("mrc p15,0,%0,c0,c0,0":"=r"(v));return v;}
CODE void root_current_snapshot(volatile struct boot_mmu_snapshot *s)
{
 uint32_t v;
 __asm__ volatile("mrs %0,cpsr":"=r"(v));s->cpsr=v;s->flags=0;
 if((v&31u)!=0x13u && (v&31u)!=0x1fu)return;
#define R(field,op) __asm__ volatile(op:"=r"(v));s->field=v
 R(mpidr,"mrc p15,0,%0,c0,c0,5");
 R(sctlr,"mrc p15,0,%0,c1,c0,0");
 R(actlr,"mrc p15,0,%0,c1,c0,1");
 R(ttbr0,"mrc p15,0,%0,c2,c0,0");
 R(ttbr1,"mrc p15,0,%0,c2,c0,1");
 R(ttbcr,"mrc p15,0,%0,c2,c0,2");
 R(dacr,"mrc p15,0,%0,c3,c0,0");
#undef R
 s->flags=1u;
 boot_mmu_read_l1(s,read_word);
}
CODE static struct root_layout layout(void)
{
 const struct root_layout l={(uint32_t)(uintptr_t)private_root,
  (uint32_t)(uintptr_t)children,(uint32_t)(uintptr_t)boot_shadow_page,
  (uint32_t)(uintptr_t)_resident_start,(uint32_t)(uintptr_t)_resident_data_end,
  (uint32_t)(uintptr_t)_bss_objects_end};return l;
}
CODE static void clean(const volatile void *p,uint32_t bytes)
{
 uintptr_t start=(uintptr_t)p&~(uintptr_t)31u,end=(uintptr_t)p+bytes;
 __asm__ volatile("dsb sy":::"memory");
 for(;start<end;start+=32u)__asm__ volatile("mcr p15,0,%0,c7,c10,1"::"r"(start):"memory");
 __asm__ volatile("dsb sy":::"memory");
}
CODE static unsigned plan_matches(void)
{
 return multi_plan_matches(private_root+ROOT_FIRST,children,feature_targets,FEATURE_PAGE_COUNT,
  (uint32_t)(uintptr_t)children,&shape) && feature_pages_match(0u);
}
CODE static void hold(void)
{
 __asm__ volatile("cpsid if":::"memory");
 for(;;)__asm__ volatile("wfi":::"memory");
}
CODE static void publish_decision(unsigned value)
{
 __atomic_store_n(&decision,value,__ATOMIC_RELEASE);
 __asm__ volatile("dsb sy\nsev":::"memory");
}
CODE static unsigned current_ram_supported(void)
{
 const uint32_t words[4]={read_word(0xdffc4804),read_word(0xdffc4808),
  read_word(0xdffc4884),read_word(0xdffc4888)};
 return root_ram_supported(words);
}
CODE static void take(unsigned core,const volatile struct boot_mmu_snapshot *s,uint32_t id)
{
 volatile struct core_result *c=&cores[core];c->midr=id;c->before=s->ttbr1;
 c->result=ROOT_BAD_CPU;
 c->after=s->ttbr1;c->ttbcr=s->ttbcr;
 if(!root_cpu_supported(s,id))goto done;
 c->result=ROOT_BAD_RAM;
 if(!current_ram_supported())goto done;
 const struct root_layout l=layout();
 c->result=ROOT_BAD_OWNER;
 if(!root_layout_supported(&l))goto done;
 c->result=ROOT_BAD_PLAN;
 if(!plan_matches() || root_original_drift((const volatile uint32_t *)ROOT_ORIGINAL,private_root)!=0u)goto done;
 {
  c->result=root_switch((uint32_t)(uintptr_t)private_root);
  __asm__ volatile("mrc p15,0,%0,c2,c0,1":"=r"(c->after));
  __asm__ volatile("mrc p15,0,%0,c2,c0,2":"=r"(c->ttbcr));
  if(c->result==ROOT_SWITCHED) {
   unsigned view_valid=plan_matches() && c->after==(uint32_t)(uintptr_t)private_root && c->ttbcr==7u && feature_pages_match(1u);
   if(view_valid)
    c->checksum=root_page_checksum((const volatile uint32_t *)FEATURE_BOOT_VA);
   if(!view_valid || c->checksum!=reference_checksum) {
    unsigned restored=root_switch(ROOT_ORIGINAL);(void)restored;
    __asm__ volatile("mrc p15,0,%0,c2,c0,1":"=r"(c->after));
    if(c->after!=ROOT_ORIGINAL)for(;;)__asm__ volatile("wfi":::"memory");
    c->result=ROOT_RESTORED;
   }
  }
 }
done:
 __atomic_store_n(&c->complete,1u,__ATOMIC_RELEASE);
}
CODE void root_primary_prepare(void)
{
 struct boot_mmu_snapshot s;root_current_snapshot(&s);
 /* The loaded entry already selected actual CPU0. An unobservable/foreign
  * entry cannot authorize either core to start Canon under this feature. */
 if(!s.flags || (s.mpidr&15u)!=0u){publish_decision(ROOT_DECISION_FATAL);hold();}
 uint32_t id=midr();cores[0].midr=id;cores[0].before=s.ttbr1;
 cores[0].after=s.ttbr1;cores[0].ttbcr=s.ttbcr;
 if(!root_cpu_supported(&s,id)) {
  prepare_result=ROOT_BAD_CPU;cores[0].result=ROOT_BAD_CPU;
  __atomic_store_n(&cores[0].complete,1u,__ATOMIC_RELEASE);
 } else {
  const uint32_t ram[4]={read_word(0xdffc4804),read_word(0xdffc4808),read_word(0xdffc4884),read_word(0xdffc4888)};
  const struct root_input in={&s,id,(const volatile uint32_t *)ROOT_ORIGINAL,
   (const volatile uint32_t *)FEATURE_BOOT_VA,boot_shadow_page,ram,private_root,children,&shape,layout()};
  prepare_result=root_prepare(&in);
  if(prepare_result!=ROOT_PREPARED) {
   cores[0].result=prepare_result;
   __atomic_store_n(&cores[0].complete,1u,__ATOMIC_RELEASE);
  } else {
   reference_checksum=feature_reference_checksums[0];
   clean(private_root,sizeof private_root);clean(children,sizeof children);
   for(unsigned i=0;i<FEATURE_PAGE_COUNT;i++)clean((const volatile void *)(uintptr_t)feature_targets[i].pa,4096u);
   take(0,&s,id);
  }
 }
 unsigned next=root_primary_decision(cores[0].result,cores[0].after,(uint32_t)(uintptr_t)private_root);
 publish_decision(next);
 if(next==ROOT_DECISION_FATAL)hold();
}
CODE void root_secondary_take(void)
{
 struct boot_mmu_snapshot s;root_current_snapshot(&s);
 unsigned command;
 /* Before Canon/kernel entry CPU0 performs only owned-memory preparation.
  * Do not turn an early arrival into implicit stock execution. */
 do {command=__atomic_load_n(&decision,__ATOMIC_ACQUIRE);
  if(command==ROOT_DECISION_PENDING)__asm__ volatile("wfe":::"memory");
 } while(command==ROOT_DECISION_PENDING);
 if(command==ROOT_DECISION_STOCK) {
  cores[1].result=ROOT_NONE;cores[1].before=s.flags?s.ttbr1:0u;
  cores[1].after=cores[1].before;cores[1].ttbcr=s.flags?s.ttbcr:0u;
  __atomic_store_n(&cores[1].complete,1u,__ATOMIC_RELEASE);
 } else if(command==ROOT_DECISION_MOD && s.flags && (s.mpidr&15u)==1u) {
  take(1,&s,midr());
 } else {
  cores[1].result=ROOT_BAD_CPU;cores[1].after=s.flags?s.ttbr1:0u;
  __atomic_store_n(&cores[1].complete,1u,__ATOMIC_RELEASE);
 }
 unsigned action=root_secondary_action(command,cores[1].result,cores[1].after,
  (uint32_t)(uintptr_t)private_root);
 if(action!=ROOT_ENTRY_STOCK && action!=ROOT_ENTRY_MOD)hold();
}
CODE void root_peer_check(void)
{
 /* C shim has issued Canon normal wake at its original boundary. No new
  * root change and no peer Sleep/timer dependency exist in this protocol. */
 if(__atomic_load_n(&decision,__ATOMIC_ACQUIRE)!=ROOT_DECISION_MOD)hold();
 unsigned remaining=1000000u;
 while(!__atomic_load_n(&cores[1].complete,__ATOMIC_ACQUIRE) && remaining)--remaining;
 if(!remaining || cores[1].result!=ROOT_SWITCHED ||
  cores[1].after!=(uint32_t)(uintptr_t)private_root || cores[1].ttbcr!=7u ||
  cores[1].checksum!=reference_checksum) {
  hold();
 }
 __atomic_store_n(&peer_verified,1u,__ATOMIC_RELEASE);
}

CODE void root_late_facts(const volatile struct boot_mmu_snapshot *current,struct root_view_facts *facts)
{
 facts->midr=0u;facts->kind=root_view_kind(current,(uint32_t)(uintptr_t)private_root);
 facts->plan=0u;facts->drift=0xffffffffu;facts->checksum=0u;
 /* Do not execute another privileged read after an unsupported snapshot. */
 if(!facts->kind)return;
 facts->midr=midr();
 if((facts->midr&root_target_profile.midr_mask)!=root_target_profile.midr_value ||
  !facts->kind || __atomic_load_n(&decision,__ATOMIC_ACQUIRE)!=ROOT_DECISION_MOD || !current_ram_supported())return;
 const struct root_layout l=layout();
 if(!root_layout_supported(&l))return;
 facts->plan=plan_matches() && (facts->kind!=2u || feature_pages_match(1u));
 facts->drift=root_original_drift((const volatile uint32_t *)ROOT_ORIGINAL,private_root);
 if(facts->kind==2u && facts->plan)
  facts->checksum=root_page_checksum((const volatile uint32_t *)FEATURE_BOOT_VA);
}

/* Completed M4/M5 mechanism detail stays in their immutable evidence. M6
 * retains compact source-bound roots/results alongside the shared M2 samples. */
static const char u_fmt[] DATA="R5G1 U=%u R=%u A=%u B=%u";
static const char t_fmt[] DATA="R5G1 T=%08X U=%08X R=%08X C=%08X";
static const char l_fmt[] DATA="R5G1 L=%u P=%u D=%u C=%08X";
CODE void root_report(unsigned sequence,const volatile struct boot_mmu_snapshot *current)
{
 (void)sequence;
 unsigned done0=__atomic_load_n(&cores[0].complete,__ATOMIC_ACQUIRE);
 unsigned done1=__atomic_load_n(&cores[1].complete,__ATOMIC_ACQUIRE);
 unsigned is_ready=__atomic_load_n(&decision,__ATOMIC_ACQUIRE)==ROOT_DECISION_MOD;
 struct root_view_facts facts;root_late_facts(current,&facts);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,u_fmt,prepare_result,is_ready,
  done0?cores[0].result:0u,done1?cores[1].result:0u);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,t_fmt,cores[0].after,
  done1?cores[1].after:0u,(uint32_t)(uintptr_t)private_root,reference_checksum);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,l_fmt,facts.kind,facts.plan,facts.drift,facts.checksum);
}

#else
/* Host tests replace privileged acquisition only. The gate below still calls
 * the real root-view and typed-IFD policy owners against a protected table. */
#include "root_native.h"
#include "root_plan.h"
extern volatile uint32_t private_root[ROOT_WORDS];
#define CODE
#endif
#include "ifd_memory.h"

CODE int root_ifd_memory_admitted(uint32_t handle,uint32_t bytes)
{
 if(!ifd_memory_span(handle,bytes))return 0;
 if(!ifd_memory_high_span(handle,bytes))return 1;
 struct boot_mmu_snapshot current;
 root_current_snapshot(&current);
 if(root_view_kind(&current,(uint32_t)(uintptr_t)private_root)!=2u)return 0;
 uint32_t first=ifd_memory_first_index(handle,bytes);
 uint32_t count=ifd_memory_entry_count(handle,bytes);
 /* Bound the owned array before forming its pointer; the IFD owner supplies
  * the source-selected group indices/count and descriptor interpretation. */
 if(first>=ROOT_WORDS||!count||count>ROOT_WORDS-first)return 0;
 return ifd_memory_mapping_supported(handle,bytes,private_root+first,count)!=0u;
}
