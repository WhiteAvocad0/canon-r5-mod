/* Derived from the hardware-observed M4/M6 R5.221 descriptor owner. */
#include "multi_plan.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
CODE int multi_original_supported(const volatile uint32_t original[16])
{
 if(!original)return 0;
 for(unsigned i=0;i<16u;i++)if(original[i]!=0xe005d406u)return 0;
 return 1;
}
CODE int multi_rom_supported(const volatile uint32_t *original,unsigned blocks)
{
 if(!original||!blocks||blocks>4u)return 0;
 for(unsigned b=0;b<blocks;b++)for(unsigned i=0;i<16u;i++)
  if(original[b*16u+i]!=0xe005d406u+b*0x01000000u)return 0;
 return 1;
}
CODE static int contains(uint32_t start,uint32_t end,uint32_t address,uint32_t bytes)
{return address>=start && address<=end && end-address>=bytes;}
CODE static int overlaps(uint32_t a,uint32_t n,uint32_t b,uint32_t m)
{return a<b+m && b<a+n;}
CODE static unsigned targets_valid(const struct multi_target *t,unsigned count,uint32_t table)
{
 if(!t || !count || count>MULTI_MAX_PAGES)return MULTI_ARGUMENT;
 if((table&1023u)||!contains(0x100000u,0x300000u,table,MULTI_MAX_TABLES*1024u))return MULTI_TABLE;
 for(unsigned i=0;i<count;i++) {
  if((t[i].va&4095u)||t[i].va<0xe0000000u||t[i].va>=0xe1000000u ||
   (t[i].pa&4095u)||!contains(0x100000u,0x300000u,t[i].pa,4096u) ||
   overlaps(t[i].pa,4096u,table,MULTI_MAX_TABLES*1024u))return MULTI_TARGET;
  for(unsigned j=0;j<i;j++)if(t[i].va==t[j].va || t[i].pa==t[j].pa)return MULTI_TARGET;
 }
 return MULTI_OK;
}
CODE int multi_layout_supported(uint32_t root,uint32_t table,
 const struct multi_target *t,unsigned count,uint32_t start,uint32_t end)
{
 if(start<0x100000u||end>0x300000u||start>=end || (root&16383u) ||
  !contains(start,end,root,16384u)||targets_valid(t,count,table) ||
  !contains(start,end,table,MULTI_MAX_TABLES*1024u) ||
  overlaps(root,16384u,table,MULTI_MAX_TABLES*1024u))return 0;
 for(unsigned i=0;i<count;i++)
  if(!contains(start,end,t[i].pa,4096u)||overlaps(root,16384u,t[i].pa,4096u))return 0;
 return 1;
}
CODE static uint32_t small(uint32_t physical,uint32_t memory)
{
 const uint32_t rom=0xe005d406u;
 uint32_t attributes=(memory&0xcu)|((memory>>6)&0x5c0u);
 uint32_t permissions=((rom>>6)&0xa30u)|((rom>>4)&1u);
 return physical|attributes|permissions|2u;
}
CODE static uint32_t child(unsigned section,unsigned page,const struct multi_target *t,unsigned count)
{
 uint32_t va=0xe0000000u+section*0x100000u+page*4096u;
 for(unsigned i=0;i<count;i++)if(t[i].va==va)
  return small(t[i].pa,t[i].pa<0x200000u?0x00114406u:0x00214406u);
 return small(va,0xe005d406u);
}
CODE static unsigned shape_for(const struct multi_target *t,unsigned count,struct multi_shape *s)
{
 s->count=0;
 for(unsigned section=0;section<16u;section++) {
  unsigned present=0;
  for(unsigned i=0;i<count;i++)if(((t[i].va>>20)&15u)==section)present=1;
  if(present) {
   if(s->count==MULTI_MAX_TABLES)return MULTI_CAPACITY;
   s->sections[s->count++]=section;
  }
 }
 for(unsigned i=s->count;i<MULTI_MAX_TABLES;i++)s->sections[i]=0;
 return MULTI_OK;
}
CODE static uint32_t parent(unsigned section,uint32_t table,const struct multi_shape *s)
{
 for(unsigned i=0;i<s->count;i++)if(s->sections[i]==section)return (table+i*1024u)|1u;
 return (0xe0000000u+section*0x100000u)|0x0001d406u;
}
CODE unsigned multi_plan_build(const volatile uint32_t original[16],
 const struct multi_target *t,unsigned count,uint32_t table,
 volatile uint32_t parents[16],volatile uint32_t children[MULTI_MAX_TABLES][256],
 struct multi_shape *out)
{
 if(!original||!parents||!children||!out)return MULTI_ARGUMENT;
 unsigned error=targets_valid(t,count,table);if(error)return error;
 if(!multi_original_supported(original))return MULTI_ORIGINAL;
 struct multi_shape s;error=shape_for(t,count,&s);if(error)return error;
 for(unsigned i=0;i<16u;i++)parents[i]=parent(i,table,&s);
 for(unsigned n=0;n<s.count;n++)for(unsigned page=0;page<256u;page++)
  children[n][page]=child(s.sections[n],page,t,count);
 *out=s;return MULTI_OK;
}
CODE int multi_plan_matches(const volatile uint32_t parents[16],
 const volatile uint32_t children[MULTI_MAX_TABLES][256],
 const struct multi_target *t,unsigned count,uint32_t table,const struct multi_shape *s)
{
 if(!parents||!children||!s||targets_valid(t,count,table))return 0;
 struct multi_shape expected;if(shape_for(t,count,&expected))return 0;
 if(s->count!=expected.count)return 0;
 for(unsigned n=0;n<MULTI_MAX_TABLES;n++)if(s->sections[n]!=expected.sections[n])return 0;
 for(unsigned i=0;i<16u;i++)if(parents[i]!=parent(i,table,s))return 0;
 for(unsigned n=0;n<s->count;n++)for(unsigned page=0;page<256u;page++)
  if(children[n][page]!=child(s->sections[n],page,t,count))return 0;
 return 1;
}
