#include "rec_state.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
_Atomic uint32_t rec_press_seq;
#ifdef REC_STATE_HOST
extern uint32_t rec_state_host_read(uint32_t);
#endif
CODE static uint32_t read_state(void)
{
#ifdef REC_STATE_HOST
    return rec_state_host_read(REC_STATE_WORD);
#else
    return *(volatile const uint32_t *)(uintptr_t)REC_STATE_WORD;
#endif
}
CODE void rec_state_observe(const uint32_t *saved)
{
    /* Read-only: the previous state, before the unchanged original stores the
     * new one. One release increment on a REC press; nothing else. */
    uint32_t old = read_state(), new_state = saved[0], caller = saved[5];
    if (old == 0u && new_state == REC_STATE_PRESSED && caller == REC_PRESS_CALLER)
        atomic_fetch_add_explicit(&rec_press_seq, 1u, memory_order_release);
}
