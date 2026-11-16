#include "menu_native.h"
#include "menu_table.h"
#include "root_native.h"
#include "heap_native.h"
#include "menu-vector-source.h"
#include "observer.h"
#include "observer-source.h"
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
extern const unsigned char menu_vector[];
extern void menu_heap_shim(void),menu_resource_shim(void);
struct menu_state {
 uint32_t attempted,prepare,heap,raw,table,prefix,ready;
 uint32_t generations,installs,result,delegate,map_count,map_sections[2];
};
static volatile struct menu_state state;
CODE static uint32_t word(uint32_t address)
{return *(const volatile uint32_t *)(uintptr_t)address;}
CODE static void clean(uint32_t start,uint32_t bytes)
{
 uint32_t end=start+bytes;start&=~31u;
 __asm__ volatile("dsb sy":::"memory");
 for(;start<end;start+=32u)
  __asm__ volatile("mcr p15,0,%0,c7,c10,1"::"r"(start):"memory");
 __asm__ volatile("dsb sy":::"memory");
}
CODE void menu_prepare(void)
{
 if(state.attempted)return;
 state.attempted=1u;
 uint32_t core;__asm__ volatile("mrc p15,0,%0,c0,c0,5":"=r"(core));
 state.prepare=PREP_BAD_CORE;if((core&15u)!=0u)return;
 state.heap=word(0x5350u);state.prepare=PREP_BAD_HEAP;
 /* Constructor E0808FA8 returns its exact base as heap identity. */
 if(state.heap!=R5_HEAP_BASE)return;
 state.raw=((uint32_t (*)(uint32_t,uint32_t))(uintptr_t)0xe08093c1u)
  (state.heap,MENU_ALLOCATION_BYTES);
 state.prepare=PREP_ALLOC_FAILED;if(!state.raw)return;
 /* The owner retains even a rejected non-null return; no guessed free. */
 state.prepare=PREP_BAD_RANGE;
 if(!heap_span_supported(state.raw,MENU_ALLOCATION_BYTES))return;
 struct heapobs mapping;
 state.prepare=PREP_BAD_MAPPING;
 unsigned admitted=heap_native_admit(state.raw,MENU_ALLOCATION_BYTES,&mapping);
 state.map_count=mapping.entry_count;
 state.map_sections[0]=mapping.first_words[0];state.map_sections[1]=mapping.first_words[1];
 if(!admitted)return;
 uint32_t table=(state.raw+31u)&~31u,prefix=table+MENU_TABLE_RESERVED;
 state.prepare=PREP_BAD_TABLE;
 if(menu_table_build((uint32_t *)(uintptr_t)table,table,
  (const volatile uint32_t *)(uintptr_t)MENU_SOURCE_TABLE,prefix))return;
 _Static_assert(MENU_VECTOR_BYTES+4u<=MENU_PREFIX_RESERVED,"vector capacity");
 unsigned char *target=(unsigned char *)(uintptr_t)prefix;
 for(unsigned i=0;i<MENU_PREFIX_RESERVED;i++)target[i]=0;
 *(uint32_t *)(uintptr_t)prefix=MENU_VECTOR_BYTES;
 for(unsigned i=0;i<MENU_VECTOR_BYTES;i++)target[i+4u]=menu_vector[i];
 state.table=table;state.prefix=prefix;
 clean(state.raw,MENU_ALLOCATION_BYTES);
 state.prepare=PREP_READY;
 __atomic_store_n(&state.ready,1u,__ATOMIC_RELEASE);
}
CODE uint32_t menu_image_table_getter(void)
{
 /* Always delegate to the source-bound original, never the current slot. */
 uint32_t original=((uint32_t (*)(void))(uintptr_t)0xe026af47u)();
 return original==MENU_SOURCE_TABLE && __atomic_load_n(&state.ready,__ATOMIC_ACQUIRE)
  ?state.table:original;
}
CODE void menu_install(uint32_t original_result)
{
 state.generations++;
 state.result=INSTALL_UNPREPARED;
 if(!__atomic_load_n(&state.ready,__ATOMIC_ACQUIRE))return;
 state.result=INSTALL_BAD_ROOT;
 if(original_result!=0x68d0u || word(0x6910u)!=0xe3500000u)return;
 uint32_t cb[8];
 for(unsigned n=0;n<8u;n++)cb[n]=word(0x68e0u+n*4u);
 state.delegate=cb[3];state.result=INSTALL_BAD_GETTER;
 if(cb[3]!=0xe026af47u)return;
 state.result=INSTALL_BAD_CALLBACK;
 for(unsigned n=0;n<8u;n++)if(!cb[n])return;
 uint32_t getter=(uint32_t)(uintptr_t)menu_image_table_getter;
 typedef uint32_t (*register_fn)(uint32_t,uint32_t,uint32_t,uint32_t,
  uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t);
 ((register_fn)(uintptr_t)0xe026aed3u)
  (0,cb[1],cb[2],0,cb[0],getter,cb[4],0,cb[5],cb[6],cb[7]);
 state.result=INSTALL_BAD_READBACK;
 for(unsigned n=0;n<8u;n++)
  if(word(0x68e0u+n*4u)!=(n==3u?getter:cb[n]))return;
 state.installs++;state.result=INSTALL_OK;
}
static const char b_fmt[] DATA="R5G1 B=%u V=%u";
static const char s_fmt[] DATA="R5G1 S=" OBSERVER_SOURCE_ID;
static const char p_fmt[] DATA="R5G1 P=%u H=%08X R=%08X T=%08X";
static const char a_fmt[] DATA="R5G1 A=%u P=%08X V=%u R=%u";
static const char h_fmt[] DATA="R5G1 H=%u A=%08X B=%08X";
static const char i_fmt[] DATA="R5G1 I=%u N=%u R=%u D=%08X";
static const char c_fmt[] DATA="R5G1 C=%08X R=%08X";
static const char e_fmt[] DATA="R5G1 E=%u";
CODE void menu_report(unsigned sequence,const volatile struct boot_mmu_snapshot *current)
{
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,b_fmt,sequence,1u);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,s_fmt);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,p_fmt,state.prepare,state.heap,state.raw,state.table);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,a_fmt,MENU_ALLOCATION_BYTES,state.prefix,MENU_VECTOR_BYTES,state.ready);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,h_fmt,state.map_count,state.map_sections[0],state.map_sections[1]);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,i_fmt,state.generations,state.installs,state.result,state.delegate);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,c_fmt,word(0x68ecu),word(0x6910u));
 root_report(sequence,current);
 observer_api.debug(OBSERVER_CLASS,OBSERVER_LEVEL,e_fmt,sequence);
}
