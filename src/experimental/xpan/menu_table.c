#include "menu_table.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
CODE unsigned menu_table_build(uint32_t *out,uint32_t table,
 const volatile uint32_t *src,uint32_t prefix)
{
 if(!out || !src)return MENU_TABLE_BAD_ARGUMENT;
 if((table&3u)||(prefix&3u)||table<R5_HEAP_BASE ||
  table>R5_HEAP_END-MENU_TABLE_BYTES || prefix<R5_HEAP_BASE ||
  prefix>R5_HEAP_END-MENU_PREFIX_RESERVED ||
  (table<prefix+MENU_PREFIX_RESERVED && prefix<table+MENU_TABLE_BYTES))
  return MENU_TABLE_BAD_DESTINATION;
 const uint32_t span=MENU_SOURCE_END-MENU_SOURCE_TABLE;
 if(src[0]!=MENU_COUNT ||
  src[MENU_TARGET_ID]!=MENU_SOURCE_TARGET_PREFIX-MENU_SOURCE_TABLE)
  return MENU_TABLE_BAD_SOURCE;
 for(unsigned n=1;n<=MENU_COUNT;n++) {
  uint32_t offset=src[n];
  if((offset&3u)||offset<MENU_TABLE_BYTES||offset>span-4u)
   return MENU_TABLE_BAD_SOURCE;
  uint32_t bytes=src[offset/4u];
  if(bytes>span-offset-4u ||
   (n==MENU_TARGET_ID && bytes!=MENU_SOURCE_TARGET_BYTES))
   return MENU_TABLE_BAD_SOURCE;
 }
 out[0]=MENU_COUNT;
 for(unsigned n=1;n<=MENU_COUNT;n++)
  out[n]=(n==MENU_TARGET_ID?prefix:MENU_SOURCE_TABLE+src[n])-table;
 return MENU_TABLE_OK;
}
