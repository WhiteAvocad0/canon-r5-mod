#ifndef R5_OBSERVER_H
#define R5_OBSERVER_H
#include <stdint.h>
#include "observer-source.h"

typedef void (*observer_callback)(unsigned, void *, const void *, unsigned);
typedef void (*observer_register_fn)(const unsigned *, unsigned, observer_callback, void *, void (*)(unsigned));
typedef void (*observer_ack_fn)(unsigned, unsigned);
typedef void (*observer_debug_fn)(unsigned, unsigned, const char *, ...);
typedef unsigned (*observer_counter_read_fn)(unsigned *, unsigned *);
typedef void (*observer_sound_report_fn)(observer_debug_fn, unsigned, unsigned);
struct observer_api {
    observer_register_fn register_slave;
    observer_ack_fn ack;
    observer_debug_fn debug;
    observer_counter_read_fn read_counter;
    observer_sound_report_fn report_sound;
};
struct observer_record {
    unsigned property;
    unsigned length;
    unsigned value;
    unsigned observed_first_u32;
};
struct observer_sample {
    unsigned callback_sequence;
    unsigned observed;
    unsigned context_pointer;
    unsigned value;
};
struct observer_state {
    unsigned token;
    unsigned count;
    unsigned requested;
    unsigned dump_count;
    struct observer_record records[OBSERVER_MAX_NOTIFICATIONS_LOGGED];
    struct observer_sample samples[OBSERVER_MAX_DUMPS];
};
extern const struct observer_api observer_api;
extern volatile struct observer_state observer_state;
unsigned counter_read_slot(const volatile uintptr_t *slot, unsigned field_offset,
                           uintptr_t *context_out, unsigned *value_out);
unsigned observer_read_counter(unsigned *context_out, unsigned *value_out);
void observer_register(void);

#endif
