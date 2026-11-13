#ifndef R5_SOUND_WORKER_H
#define R5_SOUND_WORKER_H

#include <stdint.h>

#define SOUND_WORKER_PRIORITY 19
#define SOUND_WORKER_STACK_BYTES 4096u
#define SOUND_WORKER_OPTIONS 0u
#define SOUND_WORKER_POLL_MS 20u
#define SOUND_WORKER_READINESS_LIMIT 1u

#define SOUND_WORKER_AO_NOT_READY 2u
#define SOUND_WORKER_AO_STOCK_BUFFER_OWNED 3u
#define SOUND_WORKER_AO_SETTLED_OBSERVED 9u
#define SOUND_WORKER_AO_AUDIOCTRL_MISSING 13u
#define SOUND_WORKER_AO_AUDIOCTRL_NOT_READY 15u
#define SOUND_WORKER_AO_AUDIO_IC_UNREADY 16u
#define SOUND_WORKER_AO_OUTPUT_CONTEXT_MISSING 17u
#define SOUND_WORKER_AO_ICU_CACHE_UNREADY 18u
#define SOUND_WORKER_AO_PARAMETER_TABLE_MISSING 19u

#define SOUND_WORKER_POLL_DISABLED 1u
#define SOUND_WORKER_POLL_UNOBSERVED 2u
#define SOUND_WORKER_POLL_SEEDED 3u
#define SOUND_WORKER_POLL_RESEEDED_CONTEXT 4u
#define SOUND_WORKER_POLL_RESEEDED_RESET 5u
#define SOUND_WORKER_POLL_UNCHANGED 6u
#define SOUND_WORKER_POLL_PLAYED 7u
#define SOUND_WORKER_POLL_DROPPED_PREFLIGHT 8u
#define SOUND_WORKER_POLL_FAULTED 9u

#define SOUND_WORKER_FAULT_NONE 0u
#define SOUND_WORKER_FAULT_PLAYBACK 1u
#define SOUND_WORKER_FAULT_COUNTER 2u
#define SOUND_WORKER_FAULT_API 3u

typedef void (*sound_worker_entry_fn)(void *);
typedef int (*sound_worker_create_fn)(sound_worker_entry_fn entry, void *arg,
                                      void *stack_addr, unsigned stack_bytes,
                                      int priority, unsigned options,
                                      const char *name);
typedef int (*sound_worker_start_fn)(int raw_task_id);
typedef int (*sound_worker_delete_fn)(int raw_task_id);
typedef unsigned (*sound_worker_read_counter_fn)(unsigned *context_out,
                                                 unsigned *value_out);
typedef unsigned (*sound_worker_play_shutter_fn)(void);
typedef void (*sound_worker_sleep_fn)(unsigned ms);

struct sound_worker_api {
    sound_worker_create_fn create_task;
    sound_worker_start_fn start_task;
    sound_worker_delete_fn delete_task;
    sound_worker_read_counter_fn read_counter;
    sound_worker_play_shutter_fn play_shutter;
    sound_worker_sleep_fn sleep_ms;
};

/* Writers are separated by lifecycle phase so worker entry preempting start()
 * cannot have its state overwritten when start_task() returns. */
struct sound_worker_state {
    /* Start/init writer fields */
    unsigned start_attempted;
    unsigned start_succeeded;
    unsigned create_return_observed;
    int create_returned;
    int raw_task_id;
    unsigned task_owned;
    unsigned start_return_observed;
    int start_returned;
    unsigned delete_attempted;
    unsigned delete_return_observed;
    int delete_returned;

    /* Control writer field (pre-start/fail in start(), or sound_worker_set_enabled()) */
    unsigned enabled;

    /* Worker writer fields */
    unsigned worker_entered;
    unsigned worker_running;
    unsigned poll_count;
    unsigned has_baseline;
    unsigned baseline_ctx;
    unsigned baseline_val;
    unsigned last_counter_observed;
    unsigned last_counter_ctx;
    unsigned last_counter_val;
    unsigned last_poll_status;
    unsigned fault_latched;
    unsigned fault_reason;
    unsigned fault_outcome;
    unsigned shots_observed;
    unsigned playback_attempts;
    unsigned playback_successes;
    unsigned playback_preflight_drops;
    unsigned coalesced_drops;
    unsigned dropped_during_playback;
    unsigned dropped_disabled;
    unsigned disabled_polls;
    unsigned unobserved_reads;
    unsigned context_changes;
    unsigned counter_resets;
    unsigned last_play_outcome;
};

extern const struct sound_worker_api sound_worker_api;
extern volatile struct sound_worker_state sound_worker_state;

int sound_worker_start(void);
void sound_worker_set_enabled(unsigned enabled);
unsigned sound_worker_is_enabled(void);
unsigned sound_worker_poll_once(void);
void sound_worker_entry(void *arg);

#endif
