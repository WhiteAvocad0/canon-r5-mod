#include "rec_state.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif
_Atomic uint32_t rec_press_seq;
_Atomic uint32_t rec_stop_seq;
/* 1 from a confirmed start until the next idle set. Read and written only by
 * the hook (GUI task) and cleared at every idle set, so a take ends once. */
static uint32_t take_confirmed;
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
     * new one. One release increment on a REC press and one when a confirmed
     * take returns to idle; nothing else. */
    uint32_t old = read_state(), new_state = saved[0], caller = saved[5];
    if (old == 0u && new_state == REC_STATE_PRESSED && caller == REC_PRESS_CALLER)
        atomic_fetch_add_explicit(&rec_press_seq, 1u, memory_order_release);
    if (old == 2u && new_state == REC_STATE_RECORDING && caller == REC_CONFIRMED_CALLER)
        take_confirmed = 1u;
    if (new_state == REC_STATE_IDLE) {
        if (take_confirmed)
            atomic_fetch_add_explicit(&rec_stop_seq, 1u, memory_order_release);
        take_confirmed = 0u;
    }
}
