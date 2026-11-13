/* Read-only, boot-lifetime R5O2 multiplex observer. No capture/audio interpretation. */
#include "observer.h"

_Static_assert(sizeof(unsigned) == 4, "stock scalar ABI is u32");
#ifdef OBSERVER_FIRMWARE
_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

volatile struct observer_state observer_state;
static const unsigned properties[] DATA = OBSERVER_PROPERTY_LIST;
_Static_assert(sizeof(properties) / sizeof(properties[0]) == OBSERVER_PROPERTY_COUNT,
               "observer property count must match generated list");
_Static_assert(OBSERVER_PROPERTY_COUNT > 0u, "observer property list must be non-empty");
static const char token_format[] DATA = OBSERVER_PROTOCOL " S=" OBSERVER_SOURCE_ID " T=%08X";
static const char event_format[] DATA = OBSERVER_PROTOCOL " N=%u P=%08X L=%u V=%08X F=%u";
static const char sample_format[] DATA = OBSERVER_PROTOCOL " G=%u N=%u F=%u P=%08X V=%08X";
static const char overflow_format[] DATA = OBSERVER_PROTOCOL " OVERFLOW";
static const char dump_format[] DATA = OBSERVER_PROTOCOL " D=%u C=%u O=%u";
static const char end_format[] DATA = OBSERVER_PROTOCOL " E=%u";

CODE unsigned counter_read_slot(const volatile uintptr_t *slot, unsigned field_offset,
                                uintptr_t *context_out, unsigned *value_out)
{
    /* Load the context pointer once; null yields unobserved/0 without dereference. */
    uintptr_t ctx = *slot;
    *context_out = ctx;
    if (!ctx) {
        *value_out = 0u;
        return 0u;
    }
    *value_out = *(const volatile unsigned *)((const volatile unsigned char *)ctx + field_offset);
    return 1u;
}

CODE static void observer_report(void)
{
    unsigned dump = observer_state.dump_count;
    if (dump >= OBSERVER_MAX_DUMPS)
        return;
    unsigned callback_seq = observer_state.count;
    unsigned ctx = 0;
    unsigned val = 0;
    unsigned counter_obs = observer_api.read_counter(&ctx, &val);
    volatile struct observer_sample *s = &observer_state.samples[dump];
    s->callback_sequence = callback_seq;
    s->observed = counter_obs;
    s->context_pointer = ctx;
    s->value = val;
    observer_state.dump_count = ++dump;

    unsigned count = observer_state.count;
    unsigned overflow = count > OBSERVER_MAX_NOTIFICATIONS_LOGGED;
    if (overflow)
        count = OBSERVER_MAX_NOTIFICATIONS_LOGGED;
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, dump_format, dump, count, overflow);
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, token_format, observer_state.token);
    for (unsigned i = 0; i < count; i++) {
        const volatile struct observer_record *r = &observer_state.records[i];
        observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, event_format,
                            i + 1, r->property, r->length, r->value, r->observed_first_u32);
    }
    for (unsigned i = 0; i < dump; i++) {
        const volatile struct observer_sample *sample = &observer_state.samples[i];
        observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, sample_format,
                            i + 1, sample->callback_sequence, sample->observed,
                            sample->context_pointer, sample->value);
    }
    if (observer_api.report_sound)
        observer_api.report_sound(observer_api.debug, OBSERVER_CLASS, OBSERVER_LEVEL);
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, end_format, dump);
}

CODE void observer_token(unsigned token)
{
    observer_state.token = token;
    observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, token_format, token);
}

CODE void observer_callback_body(unsigned property, void *argument, const void *buffer, unsigned length)
{
    (void)argument;
    unsigned token = observer_state.token;
    /* Zero has separate stock master-ACK semantics, not a missing listener. */
    if (!token)
        return;
    unsigned count = observer_state.count;
    if (count < OBSERVER_MAX_NOTIFICATIONS_LOGGED) {
        unsigned value = 0;
        /* Retain only the first little-endian u32 prefix for any nonnull payload with len >= 4.
         * Trailing words (e.g. 8-byte MechaCounter) remain intentionally unread. */
        unsigned observed = length >= 4u && buffer != 0;
        if (observed) {
            const volatile unsigned char *p = buffer;
            value = (unsigned)p[0] | ((unsigned)p[1] << 8) |
                    ((unsigned)p[2] << 16) | ((unsigned)p[3] << 24);
        }
        observer_state.records[count].property = property;
        observer_state.records[count].length = length;
        observer_state.records[count].value = value;
        observer_state.records[count].observed_first_u32 = observed;
        observer_state.count = count + 1;
        observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, event_format, count + 1, property, length, value, observed);
    } else if (count == OBSERVER_MAX_NOTIFICATIONS_LOGGED) {
        observer_state.count = OBSERVER_MAX_NOTIFICATIONS_LOGGED + 1;
        observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, overflow_format);
    }
    /* Includes initial snapshots, short/null payloads and all notifications after cap. */
    observer_api.ack(token, property);
    /* A manual Beep-setting change refreshes retained history immediately and samples the
     * guarded release-counter slot AFTER ACK. Capture candidates never replay/sample/play. */
    if (property == OBSERVER_BEEP_PROPERTY)
        observer_report();
}

CODE void observer_register(void)
{
    if (observer_state.requested)
        return;
    observer_state.requested = 1;
    /* Asynchronous: only token_callback establishes receipt of a listener identity. */
    observer_api.register_slave(properties, OBSERVER_PROPERTY_COUNT, observer_callback_body,
                                (void *)&observer_state, observer_token);
}
