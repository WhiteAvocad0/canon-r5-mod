#ifndef R5_IFD_MEMORY_H
#define R5_IFD_MEMORY_H
#include <stdint.h>
#include "playback_native_profile.h"
struct ifd_memory_binding_profile {
 uint32_t bytes,pool_begin,pool_end,group_shift,group_entries,descriptor_attributes;
};
extern const struct ifd_memory_binding_profile ifd_memory_profile;
/* Range eligibility is separate from live mapping and typed-object authority. */
uint32_t ifd_memory_span(uint32_t pointer,uint32_t bytes);
uint32_t ifd_memory_high_span(uint32_t pointer,uint32_t bytes);
uint32_t ifd_memory_word_span(uint32_t pointer,uint32_t offset);
uint32_t ifd_memory_first_index(uint32_t pointer,uint32_t bytes);
uint32_t ifd_memory_entry_count(uint32_t pointer,uint32_t bytes);
uint32_t ifd_memory_expected_descriptor(uint32_t index);
uint32_t ifd_memory_mapping_supported(uint32_t pointer,uint32_t bytes,
 const volatile uint32_t *entries,uint32_t count);
#endif
