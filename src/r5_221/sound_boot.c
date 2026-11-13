/* Boot glue for guarded shutter-feedback startup ordering and R5O2 summary rows. */
#include "sound_boot.h"
#include "sound_worker.h"

_Static_assert(sizeof(unsigned) == 4, "stock scalar ABI is u32");
_Static_assert(sizeof(int) == 4, "stock signed scalar ABI is i32");
#ifdef OBSERVER_FIRMWARE
_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#define RDATA __attribute__((section(".resident_data")))
#else
#define CODE
#define DATA
#define RDATA
#endif

volatile struct sound_boot_state sound_boot_state RDATA = {
    0u,
    -1,
    0u,
    0u,
    0u,
    0u,
};

static const char boot_row_fmt[] DATA = "R5O2 I=%u R=%d A=%u O=%u C=%u";
static const char task_row_fmt[] DATA = "R5O2 T=%d C=%d S=%d O=%u E=%u";
static const char play_row_fmt[] DATA = "R5O2 W=%u P=%u S=%u D=%u F=%u";
static const char base_row_fmt[] DATA = "R5O2 B=%u C=%08X V=%u L=%u R=%u";
static const char count_row_fmt[] DATA = "R5O2 Q=%u C=%u D=%u X=%u R=%u";

CODE void sound_boot_reset(void)
{
    sound_boot_state.boot_return_observed = 0u;
    sound_boot_state.boot_rc = -1;
    sound_boot_state.startup_outcome_observed = 0u;
    sound_boot_state.startup_outcome = 0u;
    sound_boot_state.context_checked = 0u;
    sound_boot_state.context_ready = 0u;
}

CODE void sound_boot_record_boot_return(int boot_rc)
{
    sound_boot_state.boot_rc = boot_rc;
    sound_boot_state.boot_return_observed = 1u;
}

CODE void sound_boot_record_startup_outcome(unsigned outcome)
{
    sound_boot_state.startup_outcome = outcome;
    sound_boot_state.startup_outcome_observed = 1u;
}

CODE unsigned sound_boot_context_is_ready(void)
{
    if (!sound_boot_api.read_context) {
        return 0u;
    }
    unsigned cpsr = 0x80u;
    unsigned slot_100c = 1u;
    int slot_102c = 1;
    sound_boot_api.read_context(&cpsr, &slot_100c, &slot_102c);
    return ((cpsr & 0x80u) == 0u) && (slot_100c == 0u) && (slot_102c <= 0);
}

CODE int sound_boot_start_worker(void)
{
    if (!sound_boot_api.read_context) {
        sound_boot_state.context_checked = 0u;
        sound_boot_state.context_ready = 0u;
        return -1;
    }
    unsigned ready = sound_boot_context_is_ready() ? 1u : 0u;
    sound_boot_state.context_checked = 1u;
    sound_boot_state.context_ready = ready;
    if (!ready || !sound_boot_api.start_worker) {
        return -1;
    }
    return sound_boot_api.start_worker();
}

CODE void sound_boot_report(sound_boot_debug_fn debug, unsigned cls, unsigned level)
{
    if (!debug) {
        return;
    }
    unsigned boot_obs = sound_boot_state.boot_return_observed ? 1u : 0u;
    /* Opaque FW_CANON_INIT post-return r0 value when observed; -1 placeholder otherwise. */
    int boot_rc = boot_obs ? sound_boot_state.boot_rc : -1;
    unsigned start_obs = sound_boot_state.startup_outcome_observed ? 1u : 0u;
    unsigned start_out = start_obs ? sound_boot_state.startup_outcome : 0u;
    unsigned ctx_checked = sound_boot_state.context_checked ? 1u : 0u;
    unsigned ctx_flags = ctx_checked | ((ctx_checked && sound_boot_state.context_ready) ? 2u : 0u);

    debug(cls, level, boot_row_fmt, boot_obs, boot_rc, start_obs, start_out, ctx_flags);

    unsigned start_attempted = sound_worker_state.start_attempted ? 1u : 0u;
    unsigned create_obs = (start_attempted && sound_worker_state.create_return_observed) ? 1u : 0u;
    unsigned start_ret_obs = (start_attempted && sound_worker_state.start_return_observed) ? 1u : 0u;
    unsigned delete_ret_obs = (start_attempted && sound_worker_state.delete_return_observed) ? 1u : 0u;
    int raw_task_id = start_attempted ? sound_worker_state.raw_task_id : -1;
    int create_ret = create_obs ? sound_worker_state.create_returned : -1;
    int start_ret = start_ret_obs ? sound_worker_state.start_returned : -1;
    unsigned task_owned = sound_worker_state.task_owned ? 1u : 0u;
    unsigned worker_flags = (sound_worker_state.worker_entered ? 1u : 0u)
                          | (create_obs ? 2u : 0u)
                          | (start_ret_obs ? 4u : 0u)
                          | (delete_ret_obs ? 8u : 0u);

    debug(cls, level, task_row_fmt, raw_task_id, create_ret, start_ret, task_owned, worker_flags);

    unsigned enabled = sound_worker_is_enabled();
    unsigned attempts = sound_worker_state.playback_attempts;
    unsigned successes = sound_worker_state.playback_successes;
    unsigned preflight = sound_worker_state.playback_preflight_drops;
    unsigned fault_latched = sound_worker_state.fault_latched;

    debug(cls, level, play_row_fmt, enabled, attempts, successes, preflight, fault_latched);

    unsigned has_base = sound_worker_state.has_baseline;
    unsigned base_ctx = sound_worker_state.baseline_ctx;
    unsigned base_val = sound_worker_state.baseline_val;
    unsigned last_out = sound_worker_state.last_play_outcome;
    unsigned fault_reason = sound_worker_state.fault_reason;

    debug(cls, level, base_row_fmt, has_base, base_ctx, base_val, last_out, fault_reason);

    unsigned shots = sound_worker_state.shots_observed;
    unsigned coalesced = sound_worker_state.coalesced_drops;
    unsigned during_play = sound_worker_state.dropped_during_playback;
    unsigned disabled_drop = sound_worker_state.dropped_disabled;
    unsigned resets = sound_worker_state.counter_resets;

    debug(cls, level, count_row_fmt, shots, coalesced, during_play, disabled_drop, resets);
}
