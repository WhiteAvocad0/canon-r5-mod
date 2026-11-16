#include "ifd_memory.h"
#include "frame_memory.h"
const struct ifd_memory_binding_profile ifd_memory_profile={PN_IFD_BYTES,0x44f86000u,0x45003000u,24u,16u,0x00054402u};
_Static_assert(sizeof(struct ifd_memory_binding_profile)==24u,"IFD memory profile ABI");
uint32_t ifd_memory_high_span(uint32_t p,uint32_t n)
{
 return n==ifd_memory_profile.bytes&&!(p&3u)&&p>=ifd_memory_profile.pool_begin&&
  p<ifd_memory_profile.pool_end&&n<=ifd_memory_profile.pool_end-p;
}
uint32_t ifd_memory_span(uint32_t p,uint32_t n)
{return n==ifd_memory_profile.bytes&&(frame_memory_data_span(p,n)||ifd_memory_high_span(p,n));}
uint32_t ifd_memory_word_span(uint32_t p,uint32_t o)
{return ifd_memory_span(p,ifd_memory_profile.bytes)&&!(o&3u)&&o<=ifd_memory_profile.bytes-4u;}
uint32_t ifd_memory_first_index(uint32_t p,uint32_t n)
{
 if(!ifd_memory_high_span(p,n))return UINT32_MAX;
 return (p>>ifd_memory_profile.group_shift)*ifd_memory_profile.group_entries;
}
uint32_t ifd_memory_entry_count(uint32_t p,uint32_t n)
{
 if(!ifd_memory_high_span(p,n))return 0u;
 return (((p+n-1u)>>ifd_memory_profile.group_shift)-(p>>ifd_memory_profile.group_shift)+1u)*ifd_memory_profile.group_entries;
}
uint32_t ifd_memory_expected_descriptor(uint32_t i)
{
 uint32_t first=(ifd_memory_profile.pool_begin>>ifd_memory_profile.group_shift)*ifd_memory_profile.group_entries;
 uint32_t last=((ifd_memory_profile.pool_end-1u)>>ifd_memory_profile.group_shift)*ifd_memory_profile.group_entries+ifd_memory_profile.group_entries-1u;
 if(i<first||i>last)return 0u;
 return ((i/ifd_memory_profile.group_entries)<<ifd_memory_profile.group_shift)|ifd_memory_profile.descriptor_attributes;
}
uint32_t ifd_memory_mapping_supported(uint32_t p,uint32_t n,const volatile uint32_t *e,uint32_t c)
{
 if(!e||!ifd_memory_high_span(p,n)||c!=ifd_memory_entry_count(p,n))return 0u;
 uint32_t first=ifd_memory_first_index(p,n);
 for(uint32_t i=0;i<c;i++)if(e[i]!=ifd_memory_expected_descriptor(first+i))return 0u;
 return 1u;
}
