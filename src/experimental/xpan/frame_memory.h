#ifndef R5_FRAME_MEMORY_H
#define R5_FRAME_MEMORY_H
#include <stdint.h>
struct frame_memory_binding_profile {
 uint32_t data_begin,data_end,irq0_begin,irq0_end,irq1_begin,irq1_end;
};
extern const struct frame_memory_binding_profile frame_memory_profile;
/* DATA retains the previous low-mapping rule, including zero-byte membership.
 * STACK is a nonempty word-aligned frame anchored to the original hardware SP.
 * Eligibility alone does not prove ownership of a Canon object or caller. */
uint32_t frame_memory_data_span(uint32_t pointer,uint32_t bytes);
uint32_t frame_memory_stack_frame_span(uint32_t actual_sp,uint32_t extent);
uint32_t frame_memory_stack_offset_span(uint32_t actual_sp,uint32_t extent,uint32_t offset,uint32_t bytes);
#endif
