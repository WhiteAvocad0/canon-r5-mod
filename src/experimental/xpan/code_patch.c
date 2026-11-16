#include "code_patch.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
CODE static void le32(uint8_t *out,uint32_t value)
{for(unsigned i=0;i<4u;i++)out[i]=(uint8_t)(value>>(i*8u));}
CODE unsigned code_patch_apply(uint8_t page[4096],uint32_t page_va,
 const struct code_patch *patch,unsigned count,uint32_t start,uint32_t end)
{
 if(!page||!patch||count>CODE_PATCH_MAX)return CODE_PATCH_ARGUMENT;
 if((page_va&4095u)||page_va<0xe0000000u||page_va>=0xe1000000u||start>=end)
  return CODE_PATCH_LAYOUT;
 for(unsigned n=0;n<count;n++) {
  const struct code_patch *p=&patch[n];
  unsigned offset=p->va&4095u,minimum=(p->va&2u)?10u:8u;
  if((p->va&1u)||p->va<0xe0000000u||p->va>=0xe1000000u ||
   (p->length&1u)||p->length<minimum||p->length>16u||offset+p->length>4096u ||
   !(p->destination&1u)||(p->destination&~1u)<start||(p->destination&~1u)>=end)
   return CODE_PATCH_LAYOUT;
  if((p->va&~4095u)!=page_va)continue;
  for(unsigned i=0;i<p->length;i++)if(page[offset+i]!=p->before[i])return CODE_PATCH_SOURCE;
  for(unsigned j=0;j<n;j++)if((patch[j].va&~4095u)==page_va &&
   p->va<patch[j].va+patch[j].length && patch[j].va<p->va+p->length)
   return CODE_PATCH_OVERLAP;
 }
 for(unsigned n=0;n<count;n++) {
  const struct code_patch *p=&patch[n];
  if((p->va&~4095u)!=page_va)continue;
  uint8_t *out=page+(p->va&4095u);
  unsigned literal=(p->va&2u)?6u:4u;
  out[0]=0xdf;out[1]=0xf8;out[2]=(p->va&2u)?4u:0u;out[3]=0xf0;
  if(literal==6u){out[4]=0;out[5]=0xbf;}
  le32(out+literal,p->destination);
  for(unsigned i=literal+4u;i<p->length;i+=2u){out[i]=0;out[i+1u]=0xbf;}
 }
 return CODE_PATCH_OK;
}
