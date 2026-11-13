#ifndef R5_SOUND_BOOT_H
#define R5_SOUND_BOOT_H

#include <stdint.h>

typedef void (*sound_boot_read_context_fn)(unsigned *cpsr_out,
                                           unsigned *slot_100c_out,
                                           int *slot_102c_out);
typedef int (*sound_boot_start_worker_fn)(void);
typedef void (*sound_boot_debug_fn)(unsigned cls, unsigned level, const char *fmt, ...);

struct sound_boot_api {
    sound_boot_read_context_fn read_context;
    sound_boot_start_worker_fn start_worker;
};

struct sound_boot_state {
    /* Opaque FW_CANON_INIT post-return r0 register value (not WorkerStart rc). */
    unsigned boot_return_observed;
    int boot_rc;
    unsigned startup_outcome_observed;
    unsigned startup_outcome;
    unsigned context_checked;
    unsigned context_ready;
};

extern const struct sound_boot_api sound_boot_api;
extern volatile struct sound_boot_state sound_boot_state;

void sound_boot_reset(void);
void sound_boot_record_boot_return(int boot_rc);
void sound_boot_record_startup_outcome(unsigned outcome);
unsigned sound_boot_context_is_ready(void);
int sound_boot_start_worker(void);
void sound_boot_report(sound_boot_debug_fn debug, unsigned cls, unsigned level);

#endif
