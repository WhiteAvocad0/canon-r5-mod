/* Boot-resident single-photo shutter sound worker core. Host-testable via sound_worker_api. */
#include "sound_worker.h"

_Static_assert(sizeof(unsigned) == 4, "stock scalar ABI is u32");
_Static_assert(sizeof(int) == 4, "stock signed scalar ABI is i32");
#ifdef OBSERVER_FIRMWARE
_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

volatile struct sound_worker_state sound_worker_state;
static const char worker_task_name[] DATA = "r5shutter";

CODE static unsigned sat_add_u32(unsigned a, unsigned b)
{
    return (a > 0xffffffffu - b) ? 0xffffffffu : (a + b);
}

CODE static unsigned is_preflight_drop_outcome(unsigned outcome)
{
    return outcome == SOUND_WORKER_AO_NOT_READY ||
           outcome == SOUND_WORKER_AO_STOCK_BUFFER_OWNED ||
           outcome == SOUND_WORKER_AO_AUDIOCTRL_MISSING ||
           (outcome >= SOUND_WORKER_AO_AUDIOCTRL_NOT_READY &&
            outcome <= SOUND_WORKER_AO_PARAMETER_TABLE_MISSING);
}

CODE static void latch_fault(unsigned reason, unsigned outcome)
{
    if (!sound_worker_state.fault_latched) {
        sound_worker_state.fault_latched = 1u;
        sound_worker_state.fault_reason = reason;
        sound_worker_state.fault_outcome = outcome;
    }
}

CODE static void sync_counter_after_playback(void)
{
    unsigned ctx = 0u;
    unsigned val = 0u;
    unsigned obs = sound_worker_api.read_counter(&ctx, &val);
    sound_worker_state.last_counter_observed = obs;
    sound_worker_state.last_counter_ctx = ctx;
    sound_worker_state.last_counter_val = val;

    if (obs > 1u) {
        latch_fault(SOUND_WORKER_FAULT_COUNTER, obs);
        return;
    }
    if (obs == 0u || ctx == 0u) {
        sound_worker_state.has_baseline = 0u;
        sound_worker_state.unobserved_reads = sat_add_u32(sound_worker_state.unobserved_reads, 1u);
        return;
    }
    if (ctx != sound_worker_state.baseline_ctx) {
        sound_worker_state.context_changes = sat_add_u32(sound_worker_state.context_changes, 1u);
        sound_worker_state.baseline_ctx = ctx;
        sound_worker_state.baseline_val = val;
        return;
    }
    if (val < sound_worker_state.baseline_val) {
        sound_worker_state.counter_resets = sat_add_u32(sound_worker_state.counter_resets, 1u);
        sound_worker_state.baseline_val = val;
        return;
    }
    if (val > sound_worker_state.baseline_val) {
        unsigned busy_delta = val - sound_worker_state.baseline_val;
        sound_worker_state.shots_observed = sat_add_u32(sound_worker_state.shots_observed, busy_delta);
        sound_worker_state.dropped_during_playback =
            sat_add_u32(sound_worker_state.dropped_during_playback, busy_delta);
        sound_worker_state.baseline_val = val;
    }
}

CODE void sound_worker_set_enabled(unsigned enabled)
{
    __atomic_store_n(&sound_worker_state.enabled, enabled ? 1u : 0u, __ATOMIC_RELEASE);
}

CODE unsigned sound_worker_is_enabled(void)
{
    return __atomic_load_n(&sound_worker_state.enabled, __ATOMIC_ACQUIRE);
}

CODE unsigned sound_worker_poll_once(void)
{
    sound_worker_state.poll_count = sat_add_u32(sound_worker_state.poll_count, 1u);

    if (sound_worker_state.fault_latched) {
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_FAULTED;
        return SOUND_WORKER_POLL_FAULTED;
    }

    if (!sound_worker_is_enabled()) {
        sound_worker_state.has_baseline = 0u;
        sound_worker_state.disabled_polls = sat_add_u32(sound_worker_state.disabled_polls, 1u);
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_DISABLED;
        return SOUND_WORKER_POLL_DISABLED;
    }

    if (!sound_worker_api.read_counter || !sound_worker_api.play_shutter) {
        latch_fault(SOUND_WORKER_FAULT_API, 0u);
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_FAULTED;
        return SOUND_WORKER_POLL_FAULTED;
    }

    unsigned ctx = 0u;
    unsigned val = 0u;
    unsigned obs = sound_worker_api.read_counter(&ctx, &val);
    sound_worker_state.last_counter_observed = obs;
    sound_worker_state.last_counter_ctx = ctx;
    sound_worker_state.last_counter_val = val;

    if (obs > 1u) {
        latch_fault(SOUND_WORKER_FAULT_COUNTER, obs);
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_FAULTED;
        return SOUND_WORKER_POLL_FAULTED;
    }

    if (obs == 0u || ctx == 0u) {
        sound_worker_state.has_baseline = 0u;
        sound_worker_state.unobserved_reads = sat_add_u32(sound_worker_state.unobserved_reads, 1u);
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_UNOBSERVED;
        return SOUND_WORKER_POLL_UNOBSERVED;
    }

    if (!sound_worker_state.has_baseline) {
        sound_worker_state.has_baseline = 1u;
        sound_worker_state.baseline_ctx = ctx;
        sound_worker_state.baseline_val = val;
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_SEEDED;
        return SOUND_WORKER_POLL_SEEDED;
    }

    if (ctx != sound_worker_state.baseline_ctx) {
        sound_worker_state.context_changes = sat_add_u32(sound_worker_state.context_changes, 1u);
        sound_worker_state.baseline_ctx = ctx;
        sound_worker_state.baseline_val = val;
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_RESEEDED_CONTEXT;
        return SOUND_WORKER_POLL_RESEEDED_CONTEXT;
    }

    if (val < sound_worker_state.baseline_val) {
        sound_worker_state.counter_resets = sat_add_u32(sound_worker_state.counter_resets, 1u);
        sound_worker_state.baseline_val = val;
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_RESEEDED_RESET;
        return SOUND_WORKER_POLL_RESEEDED_RESET;
    }

    if (val == sound_worker_state.baseline_val) {
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_UNCHANGED;
        return SOUND_WORKER_POLL_UNCHANGED;
    }

    unsigned delta = val - sound_worker_state.baseline_val;
    sound_worker_state.baseline_val = val;
    sound_worker_state.shots_observed = sat_add_u32(sound_worker_state.shots_observed, delta);

    /* Recheck enable flag right before audio submission. */
    if (!sound_worker_is_enabled()) {
        sound_worker_state.has_baseline = 0u;
        sound_worker_state.dropped_disabled = sat_add_u32(sound_worker_state.dropped_disabled, delta);
        sound_worker_state.last_poll_status = SOUND_WORKER_POLL_DISABLED;
        return SOUND_WORKER_POLL_DISABLED;
    }

    if (delta > 1u) {
        sound_worker_state.coalesced_drops =
            sat_add_u32(sound_worker_state.coalesced_drops, delta - 1u);
    }

    sound_worker_state.playback_attempts = sat_add_u32(sound_worker_state.playback_attempts, 1u);
    unsigned outcome = sound_worker_api.play_shutter();
    sound_worker_state.last_play_outcome = outcome;

    unsigned result_status;
    if (outcome == SOUND_WORKER_AO_SETTLED_OBSERVED) {
        sound_worker_state.playback_successes = sat_add_u32(sound_worker_state.playback_successes, 1u);
        result_status = SOUND_WORKER_POLL_PLAYED;
    } else if (is_preflight_drop_outcome(outcome)) {
        sound_worker_state.playback_preflight_drops =
            sat_add_u32(sound_worker_state.playback_preflight_drops, 1u);
        result_status = SOUND_WORKER_POLL_DROPPED_PREFLIGHT;
    } else {
        latch_fault(SOUND_WORKER_FAULT_PLAYBACK, outcome);
        result_status = SOUND_WORKER_POLL_FAULTED;
    }

    /* Re-read after each playback attempt so shots during playback never queue. */
    sync_counter_after_playback();
    if (sound_worker_state.fault_latched) {
        result_status = SOUND_WORKER_POLL_FAULTED;
    }
    if (!sound_worker_is_enabled()) {
        sound_worker_state.has_baseline = 0u;
    }

    sound_worker_state.last_poll_status = result_status;
    return result_status;
}

CODE void sound_worker_entry(void *arg)
{
    (void)arg;
    sound_worker_state.worker_entered = 1u;
    sound_worker_state.worker_running = 1u;
    for (;;) {
        (void)sound_worker_poll_once();
        if (sound_worker_api.sleep_ms) {
            sound_worker_api.sleep_ms(SOUND_WORKER_POLL_MS);
        }
    }
}

CODE int sound_worker_start(void)
{
    if (sound_worker_state.start_attempted) {
        return -1;
    }
    sound_worker_state.start_attempted = 1u;
    sound_worker_state.start_succeeded = 0u;
    sound_worker_state.create_return_observed = 0u;
    sound_worker_state.create_returned = -1;
    sound_worker_state.raw_task_id = -1;
    sound_worker_state.task_owned = 0u;
    sound_worker_state.start_return_observed = 0u;
    sound_worker_state.start_returned = -1;
    sound_worker_state.delete_attempted = 0u;
    sound_worker_state.delete_return_observed = 0u;
    sound_worker_state.delete_returned = -1;
    sound_worker_set_enabled(0u);

    if (!sound_worker_api.create_task || !sound_worker_api.start_task ||
        !sound_worker_api.delete_task || !sound_worker_api.read_counter ||
        !sound_worker_api.play_shutter || !sound_worker_api.sleep_ms) {
        return -1;
    }

    int raw_id = sound_worker_api.create_task(
        sound_worker_entry,
        (void *)0,
        (void *)0,
        SOUND_WORKER_STACK_BYTES,
        SOUND_WORKER_PRIORITY,
        SOUND_WORKER_OPTIONS,
        worker_task_name
    );
    sound_worker_state.create_returned = raw_id;
    sound_worker_state.create_return_observed = 1u;
    /* RawCreateCore (0xe047e975) signals failure only with -1; raw_task_lookup
     * (0xe011f84e) uses low-16 index + full u32 match, so negative signed handles
     * (e.g. 0x8001002a) remain valid opaque task identities. */
    if (raw_id == -1) {
        return -1;
    }

    sound_worker_state.raw_task_id = raw_id;
    sound_worker_state.task_owned = 1u;
    sound_worker_set_enabled(1u);

    int start_rc = sound_worker_api.start_task(raw_id);
    sound_worker_state.start_returned = start_rc;
    sound_worker_state.start_return_observed = 1u;
    if (start_rc != 0) {
        sound_worker_set_enabled(0u);
        sound_worker_state.delete_attempted = 1u;
        int del_rc = sound_worker_api.delete_task(raw_id);
        sound_worker_state.delete_returned = del_rc;
        sound_worker_state.delete_return_observed = 1u;
        if (del_rc == 0) {
            sound_worker_state.task_owned = 0u;
            sound_worker_state.raw_task_id = -1;
        }
        return -1;
    }

    /* Do not touch enabled or worker-owned fields here: worker_entry may have
     * preempted start_task and updated/faulted/disabled state before return. */
    sound_worker_state.start_succeeded = 1u;
    return 0;
}
