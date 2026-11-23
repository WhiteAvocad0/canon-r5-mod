/* Issue #15: RATE-press screenshot writer on GuiMainTask, plus the
 * worker-side click / low-bip feedback. Host-testable through capture_api,
 * combo_observe_api and rec_tone_api (COMBO_OBSERVE_HOST). */
#include "combo_capture.h"
#include "rec_tone.h"
#include "hw_jpeg_inject.h"
#include "picture_layer.h"
#include "one_press.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

struct capture_state capture_state;
struct capture_feedback capture_feedback;
struct capture_watch capture_watch;
/* Our own 32-byte aligned header sector, cleaned by MapCpuBuffer before Write.
 * Shared by the YCA header (fill_header) and the .PIC header (pl_select); each
 * writer fills the whole sector (otherwise a YCA file carried the previous .PIC
 * header in bytes 32..511 on camera). */
static uint8_t capture_header[CAPTURE_HEADER_BYTES] __attribute__((aligned(32)));
static char capture_path[CAPTURE_PATH_BYTES];
static const char capture_drive[] DATA = "B:/";
static const char capture_prefix[] DATA = "B:/DCIM/R5SHOTS/SS_";
static const char capture_suffix[] DATA = ".YCA";

#ifdef COMBO_OBSERVE_HOST
uint8_t *capture_host_header(void) { return capture_header; } /* host seam: read the header bytes */
void (*capture_host_after_copy)(uint32_t seq);                /* host seam: a writer may lap here */
#else
CODE static uint32_t capture_timer_us(void)
{
    return ~*(volatile const uint32_t *)CAPTURE_TIMER_REGISTER;
}

const struct capture_api capture_api DATA = {
    (capture_open_fn)CAPTURE_FIO_OPEN,      (capture_path_fn)CAPTURE_FIO_CREATE,
    (capture_path_fn)CAPTURE_FIO_REMOVE,    (capture_write64_fn)CAPTURE_FIO_WRITE64,
    (capture_close_fn)CAPTURE_FIO_CLOSE,    (capture_path_fn)CAPTURE_FIO_FLUSH,
    (capture_path_fn)CAPTURE_FIO_MOUNT,     (capture_map_fn)CAPTURE_MAP_CPU_BUFFER,
    (capture_unmap_fn)CAPTURE_UNMAP_CPU_BUFFER, capture_timer_us,
};
#endif

#define G capture_state
#define FB capture_feedback

CODE static uint32_t sat_inc(uint32_t v)
{
    return v == UINT32_MAX ? v : v + 1u;
}

CODE static void put32(uint8_t *p, uint32_t v)
{
    for (uint32_t i = 0; i < 4u; ++i)
        p[i] = (uint8_t)(v >> (8u * i));
}

/* "B:/DCIM/R5SHOTS/SS_nnnnn.YCA" without sprintf. */
CODE static void format_path(uint32_t number)
{
    uint32_t n = 0;
    for (const char *s = capture_prefix; *s; ++s)
        capture_path[n++] = *s;
    /* Five digits by repeated subtraction: no divide libcall in the freestanding link. */
    static const uint32_t powers[5] DATA = {10000u, 1000u, 100u, 10u, 1u};
    for (uint32_t k = 0; k < 5u; ++k) {
        uint32_t digit = 0u;
        while (number >= powers[k] && digit < 9u) {
            number -= powers[k];
            ++digit;
        }
        capture_path[n++] = (char)('0' + digit);
    }
    for (const char *s = capture_suffix; *s; ++s)
        capture_path[n++] = *s;
    capture_path[n] = 0;
}

/* SaveVRAM's probe: Open(path, 0) returns -1 for an absent name, else an fd
 * that is closed at once. Any other value (another negative error, or an fd
 * Close would assert on) makes the namespace unprobeable: the attempt is
 * refused. -1 can also mean a missing drive lock (E07E1B98); that case is not
 * distinguished here. */
enum { PROBE_ABSENT = 0, PROBE_PRESENT = 1, PROBE_ERROR = 2 };
CODE static uint32_t exists(uint32_t number, uint32_t *probes)
{
    format_path(number);
    ++*probes;
    int32_t fd = capture_api.open(capture_path, 0u);
    if (fd == -1)
        return PROBE_ABSENT;
    if (fd < 3)
        return PROBE_ERROR;
    (void)capture_api.close(fd);
    return PROBE_PRESENT;
}

/* The next free SS_nnnnn, never one that exists. First use per boot: double
 * until absent, then bisect between the last present and the first absent
 * number (names need not be contiguous; the final name is re-probed absent). */
CODE static uint32_t free_number(uint32_t *probes)
{
    uint32_t n = G.next_number, e;
    if (!n) {
        uint32_t lo = 0u, hi = 1u;
        while (hi <= CAPTURE_MAX_NUMBER && (e = exists(hi, probes)) != PROBE_ABSENT) {
            if (e == PROBE_ERROR || *probes >= CAPTURE_PROBE_LIMIT)
                return 0u;
            lo = hi;
            hi = hi * 2u;
        }
        if (hi > CAPTURE_MAX_NUMBER)
            hi = CAPTURE_MAX_NUMBER + 1u;
        while (hi - lo > 1u && *probes < CAPTURE_PROBE_LIMIT) {
            uint32_t mid = lo + (hi - lo) / 2u;
            e = exists(mid, probes);
            if (e == PROBE_ERROR)
                return 0u;
            if (e == PROBE_PRESENT)
                lo = mid;
            else
                hi = mid;
        }
        n = lo + 1u;
    }
    while (n <= CAPTURE_MAX_NUMBER && *probes < CAPTURE_PROBE_LIMIT) {
        e = exists(n, probes);
        if (e == PROBE_ERROR)
            return 0u;
        if (e == PROBE_ABSENT) {
            format_path(n);
            return n;
        }
        ++n;
    }
    return 0u;
}

/* The whole R5SS v3 sector: CAPTURE_HEADER_USED bytes, then zeros. */
CODE static void fill_header(uint8_t *capture_header, const struct combo_marv *m, uint32_t sel, uint32_t number, uint32_t tick)
{
    for (uint32_t i = CAPTURE_HEADER_USED; i < CAPTURE_HEADER_BYTES; ++i)
        capture_header[i] = 0u;
    capture_header[0] = 'R';
    capture_header[1] = '5';
    capture_header[2] = 'S';
    capture_header[3] = 'S';
    capture_header[4] = (uint8_t)CAPTURE_HEADER_VERSION;
    capture_header[5] = 0;
    capture_header[6] = (uint8_t)CAPTURE_HEADER_BYTES;
    capture_header[7] = (uint8_t)(CAPTURE_HEADER_BYTES >> 8);
    put32(capture_header + 8, m->width);
    put32(capture_header + 12, m->height);
    put32(capture_header + 16, 3u * m->width);
    put32(capture_header + 20, 3u * m->width * m->height);
    capture_header[24] = 'U';
    capture_header[25] = 'Y';
    capture_header[26] = 'V';
    capture_header[27] = 'A';
    /* v2+ metadata: VMIX output 0, selected buffer (1 - cur), capture number low 16. */
    put32(capture_header + 28, (number & 0xffffu) << 16 | (sel & 0xffu) << 8);
    (void)tick;
}

/* One Map / Write / Unmap of a buffer, exactly as SaveVRAM calls them. A
 * failed Map establishes nothing: no Write, no Unmap, and 0 bytes written. */
CODE static int32_t write_buffer(int32_t fd, uint32_t vaddr, uint32_t map_bytes, uint32_t write_bytes, int32_t *rc_map)
{
    uint32_t phys[2] = {0u, 0u};
    *rc_map = capture_api.map(phys, vaddr, map_bytes, 0u, 1u);
    if (*rc_map != 0)
        return 0;
    int32_t rc = capture_api.write64(fd, 0u, phys[0], phys[1], write_bytes);
    (void)capture_api.unmap(vaddr, map_bytes, 0u, 1u);
    return rc;
}



/* The picture layer and its .PIC. Static: GuiMainTask's 8 KiB stack; one attempt at a time. */
static struct pl_entry capture_pl;
static char capture_pic_path[CAPTURE_PATH_BYTES];

/* After a CAPTURE_OK SS_nnnnn.YCA: the current picture layer as
 * SS_nnnnn.PIC, VramRead's way (FIO_WriteFile_IBUS straight from the Active
 * IBUS address, no CPU copy), behind a 512-byte header sector. Any doubt in
 * pl_select skips it with a reason; a failed file is removed. */
CODE static void write_pic(struct pl_entry *e, const struct combo_marv *m)
{
    uint32_t lo = 0u, hi = 0u, bytes = 0u, us0 = capture_api.timer_us();
    e->us_start = us0;
    uint32_t n = 0;
    for (; capture_path[n] && n + 1u < CAPTURE_PATH_BYTES; ++n)
        capture_pic_path[n] = capture_path[n];
    capture_pic_path[n] = 0;
    if (n < 4u) {
        e->outcome = PL_FAILED_CREATE;
        return;
    }
    capture_pic_path[n - 3u] = 'P';
    capture_pic_path[n - 2u] = 'I';
    capture_pic_path[n - 1u] = 'C';
    /* A stale .PIC of the same number (its .YCA deleted by hand) goes first, even
     * when no picture layer is written: the Y22 build must never composite it. */
    (void)capture_api.remove(capture_pic_path);
    e->outcome = pl_select(e, m->width, m->height, &lo, &hi, &bytes, capture_header);
    if (e->outcome != PL_OK)
        return;
    int32_t fd = capture_api.create(capture_pic_path);
    e->us_create = capture_api.timer_us() - us0;
    e->rc_create = fd;
    if (fd < 3) {
        e->outcome = PL_FAILED_CREATE;
        return;
    }
    int32_t rc_map;
    e->rc_header = write_buffer(fd, (uint32_t)(uintptr_t)capture_header, CAPTURE_HEADER_BYTES, CAPTURE_HEADER_BYTES, &rc_map);
    uint32_t short_write = e->rc_header != (int32_t)CAPTURE_HEADER_BYTES;
    if (!short_write) {
        e->rc_data = capture_api.write64(fd, 0u, lo, hi, bytes); /* IBUS source: no Map */
        short_write = e->rc_data != (int32_t)bytes;
    }
    e->us_data = capture_api.timer_us() - us0;
    e->rc_close = capture_api.close(fd);
    e->rc_flush = capture_api.flush(capture_drive);
    e->us_close = capture_api.timer_us() - us0;
    if (short_write)
        e->outcome = PL_FAILED_WRITE;
    else if (e->rc_close < 0 || e->rc_flush < 0)
        e->outcome = PL_FAILED_CLOSE;
    if (e->outcome != PL_OK)
        e->rc_remove = capture_api.remove(capture_pic_path);
}

CODE static void publish(struct capture_result *r)
{
    G.outcomes[r->outcome] = sat_inc(G.outcomes[r->outcome]);
    uint32_t seq = G.published + 1u;
    struct capture_result *slot = &G.results[(seq - 1u) % CAPTURE_RESULTS];
    volatile uint32_t *dst = (volatile uint32_t *)slot;
    const uint32_t *src = (const uint32_t *)r;
    for (uint32_t i = 1; i < sizeof(*r) / 4u; ++i)
        dst[i] = src[i];
    __atomic_store_n(&slot->seq, seq, __ATOMIC_RELEASE);
    __atomic_store_n(&G.published, seq, __ATOMIC_RELEASE);
    /* The next record's payload stores (into slot seq + 1 - RESULTS) come only
     * after this count is visible: the reader's torn test relies on it. */
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}

CODE void combo_capture_gui(const uint32_t *slot)
{
    uint32_t code = slot[1];
    if (code == CAPTURE_TRIGGER_CODE + 1u) {
        G.held = 0u;
        one_press_release(); /* after Canon has handled the press itself */
        return;
    }
    if (code != CAPTURE_TRIGGER_CODE) {
        one_press_key(code); /* an INFO release may start a deferred screenshot */
        return;
    }
    G.presses = sat_inc(G.presses);
    uint32_t tick = __atomic_load_n(&combo_worker_tick, __ATOMIC_ACQUIRE);
    uint32_t held_tick = G.held_tick;
    G.held_tick = tick;
    if (G.held) {
        if (tick - held_tick < CAPTURE_HELD_TICKS) { /* a press code while still held: never a new press */
            G.repeats = sat_inc(G.repeats);
            return;
        }
        G.stale_held = sat_inc(G.stale_held); /* the release was lost: this is a new press */
    }
    G.held = 1u;
    if (G.any && tick - G.last_tick < CAPTURE_DEBOUNCE_TICKS) {
        G.debounced = sat_inc(G.debounced);
        return;
    }
    G.any = 1u;
    G.last_tick = tick;
    G.attempts = sat_inc(G.attempts);

    uint32_t guarded = 0u;
    struct capture_result r;
    volatile uint32_t *w = (volatile uint32_t *)&r;
    for (uint32_t i = 0; i < sizeof(r) / 4u; ++i)
        w[i] = 0u;
    r.tick = tick;
    r.us_start = capture_api.timer_us();
    r.us_probed = r.us_create = r.us_header = r.us_data = r.us_close = r.us_flush = CAPTURE_STAGE_NOT_REACHED;
    r.job_main = r.job_sub = r.rec_idle = CAPTURE_STAGE_NOT_REACHED;
    if (G.busy) { /* only if FIO ever re-entered the GUI dispatcher */
        r.outcome = CAPTURE_REFUSED_BUSY;
        publish(&r);
        return;
    }
    G.busy = 1u;
    /* Preconditions, cheapest first. Known/pass use the COMBO_V_* bits. */
    r.gui_mode = combo_observe_api.read(COMBO_GUI_MODE_WORD);
    if (tick < CAPTURE_BOOT_GUARD_TICKS) {
        r.outcome = CAPTURE_REFUSED_BOOT;
        goto done;
    }
    r.known = COMBO_V_NOT_RECORDING | COMBO_V_SHOT_QUIET | COMBO_V_CONTEXT | COMBO_V_DEBOUNCE;
    r.pass = COMBO_V_CONTEXT | COMBO_V_DEBOUNCE; /* owner decision: RATE everywhere */
    if (combo_observe_api.read(COMBO_REC_STATE_WORD) != 0u) {
        r.outcome = CAPTURE_REFUSED_RECORDING;
        goto done;
    }
    r.pass |= COMBO_V_NOT_RECORDING;
    /* Signed age: a shot the worker records after our tick was sampled (a newer
     * change tick) reads as "just now", never as a huge unsigned quiet time. */
    if (!__atomic_load_n(&combo_observe_state.counter_known, __ATOMIC_ACQUIRE) ||
        (int32_t)(tick - __atomic_load_n(&combo_observe_state.counter_change_tick, __ATOMIC_ACQUIRE)) <
            (int32_t)CAPTURE_SHOT_QUIET_TICKS) {
        r.outcome = CAPTURE_REFUSED_SHOT;
        goto done;
    }
    r.pass |= COMBO_V_SHOT_QUIET;
    /* Canon's writer, before any FIO: our FIO calls would queue behind it on
     * the storage lock and freeze the GUI (seen on camera). Both checks are
     * evaluated (and logged); the job state refuses first. */
    r.known |= COMBO_V_JOB_IDLE | COMBO_V_MOVIE_QUIET;
    r.rec_idle = CAPTURE_REC_NEVER;
    if (__atomic_load_n(&capture_watch.rec_seen, __ATOMIC_ACQUIRE)) {
        uint32_t idle = tick - __atomic_load_n(&capture_watch.rec_last_tick, __ATOMIC_RELAXED);
        r.rec_idle = (int32_t)idle < 0 ? 0u : idle; /* seen after this press's tick: still recording */
    }
    r.job_main = combo_observe_api.read(CAPTURE_JOB_STATE_MAIN);
    r.job_sub = combo_observe_api.read(CAPTURE_JOB_STATE_SUB);
    if (!(r.job_main | r.job_sub))
        r.pass |= COMBO_V_JOB_IDLE;
    if (r.rec_idle >= CAPTURE_MOVIE_QUIET_TICKS)
        r.pass |= COMBO_V_MOVIE_QUIET;
    if (!(r.pass & COMBO_V_JOB_IDLE)) {
        r.outcome = CAPTURE_REFUSED_WRITING;
        goto done;
    }
    if (!(r.pass & COMBO_V_MOVIE_QUIET)) {
        r.outcome = CAPTURE_REFUSED_MOVIE;
        goto done;
    }
    /* A one-press job (start .. its end callback, or the timeout) is a live develop too. */
    if (one_press_busy()) {
        r.outcome = CAPTURE_REFUSED_DEVELOP;
        goto done;
    }
    /* Before any buffer or FIO use: a Resize save (H8 .. its H3) holds the guard. */
    if (inject_capture_begin()) {
        r.outcome = CAPTURE_REFUSED_DEVELOP;
        goto done;
    }
    guarded = 1u;
    inject_y22_clear(); /* every admitted RATE: no older screenshot stays pending */
    /* S0: the picture layer before any FIO (plain RAM reads). */
    {
        volatile uint32_t *z = (volatile uint32_t *)&capture_pl;
        for (uint32_t i = 0; i < sizeof(capture_pl) / 4u; ++i)
            z[i] = 0u;
        capture_pl.tick = tick;
        capture_pl.gui_mode = r.gui_mode;
        pl_sample(&capture_pl);
    }
    /* Static, not on GuiMainTask's 8 KiB stack: only this task, and only one
     * attempt at a time (busy), uses it. */
    static struct combo_display d;
    combo_sample_display(&d);
    uint32_t dk = 0u, sel = 0u, dp = combo_display_verdict(&d, &dk, &sel);
    r.known |= dk;
    r.pass |= dp;
    r.sel = sel;
    if (sel < 2u) {
        const struct combo_marv *m = &d.output_marv[0][sel];
        r.marv = m->pointer;
        r.data = m->data;
        r.format = m->format;
        r.width = m->width;
        r.height = m->height;
    }
    const uint32_t need = COMBO_V_GEOMETRY | COMBO_V_MARV | COMBO_V_ALIGN;
    if ((dp & need) != need || sel > 1u) {
        r.outcome = CAPTURE_REFUSED_DISPLAY;
        goto done;
    }
    const struct combo_marv *m = &d.output_marv[0][sel];
    r.known |= COMBO_V_CARD;
    r.rc_mount = capture_api.mount(capture_drive);
    if (r.rc_mount != 1) {
        r.outcome = CAPTURE_REFUSED_CARD;
        goto done;
    }
    r.pass |= COMBO_V_CARD;
    uint32_t number = free_number(&r.probes);
    r.us_probed = capture_api.timer_us() - r.us_start;
    if (!number) {
        r.outcome = CAPTURE_REFUSED_NAMESPACE;
        goto done;
    }
    r.number = number;
    int32_t fd = capture_api.create(capture_path);
    r.us_create = capture_api.timer_us() - r.us_start;
    r.rc_create = fd;
    if (fd < 3) { /* never Close an fd we do not own */
        r.outcome = CAPTURE_FAILED_CREATE;
        goto done;
    }
    fill_header(capture_header, m, sel, number, tick);
    /* The data length is SaveVRAM's: w * ((fmt >> 16) & 0xff) / ((fmt >> 8) & 0xff) * h,
     * here 3 * w * h because the verdict admitted exactly 6 / 2. */
    uint32_t bytes = 3u * m->width * m->height;
    r.bytes = bytes;
    r.rc_header = write_buffer(fd, (uint32_t)(uintptr_t)capture_header, CAPTURE_HEADER_BYTES, CAPTURE_HEADER_BYTES, &r.rc_map_header);
    r.us_header = capture_api.timer_us() - r.us_start;
    uint32_t short_write = r.rc_header != (int32_t)CAPTURE_HEADER_BYTES;
    if (!short_write) {
        r.rc_data = write_buffer(fd, m->data, bytes, bytes, &r.rc_map_data);
        r.us_data = capture_api.timer_us() - r.us_start;
        short_write = r.rc_data != (int32_t)bytes;
    }
    r.rc_close = capture_api.close(fd);
    r.us_close = capture_api.timer_us() - r.us_start;
    r.rc_flush = capture_api.flush(capture_drive);
    r.us_flush = capture_api.timer_us() - r.us_start;
    if (short_write) {
        r.rc_remove = capture_api.remove(capture_path); /* our own partial file only */
        r.outcome = CAPTURE_FAILED_WRITE;
        G.next_number = number; /* re-probed next time */
    } else if (r.rc_close < 0) {
        r.outcome = CAPTURE_FAILED_CLOSE;
        G.next_number = number + 1u;
    } else if (r.rc_flush < 0) {
        r.outcome = CAPTURE_FAILED_FLUSH;
        G.next_number = number + 1u;
    } else {
        r.outcome = CAPTURE_OK;
        G.next_number = number + 1u;
        capture_pl.number = number;
        write_pic(&capture_pl, m);
    }
done:
    /* HWJPG.Y22 here, on GuiMainTask, still under the capture guard; only a
     * complete Y22 is pending. Converting inside H3 failed with Err 70
     * (hw_jpeg_inject.h). */
    if (guarded) {
        const char *y22_source = r.outcome == CAPTURE_OK ? capture_path : 0;
        uint32_t ready = y22_source && !inject_y22_build(y22_source);
        if (ready)
            inject_y22_ready(r.tick, y22_source);
        /* Armed only: the job starts on the RATE release (one_press_release). */
        if (r.outcome == CAPTURE_OK)
            one_press_arm(r.gui_mode, r.number, r.tick, ready, capture_pl.outcome, capture_pl.bytes);
    }
    if (guarded)
        inject_capture_end();
    r.duration = __atomic_load_n(&combo_worker_tick, __ATOMIC_ACQUIRE) - tick;
    G.busy = 0u;
    publish(&r);
}

/* --- worker feedback ------------------------------------------------------ */

CODE static void publish_cues(void)
{
    FB.publish_attempted = 1u;
    if (!rec_tone_api.clean || !rec_tone_api.beep_start) {
        FB.fault = 1u;
        return;
    }
    uint32_t end = 0u;
    for (uint32_t i = 0; i < CAPTURE_CUES; ++i) {
        const struct capture_cue *c = &capture_cues[i];
        if (c->offset_samples != end || c->bytes != 2u * c->padded_samples || !c->segment_count ||
            c->segment_first > capture_cue_segment_total || c->segment_count > capture_cue_segment_total - c->segment_first ||
            tone_gen_fill(capture_cue_pcm + c->offset_samples, capture_cue_capacity - c->offset_samples,
                          &capture_cue_segments[c->segment_first], c->segment_count) != c->padded_samples) {
            FB.fault = 2u;
            return;
        }
        rec_tone_api.clean(capture_cue_pcm + c->offset_samples, c->bytes);
        end = c->offset_samples + c->padded_samples;
    }
    for (uint32_t i = 0; i < CAPTURE_CUES; ++i) {
        volatile uint32_t *d = capture_cue_descriptor[i];
        const int16_t *pcm = capture_cue_pcm + capture_cues[i].offset_samples;
        if (((uintptr_t)d & 31u) || ((uintptr_t)pcm & 31u) || (capture_cues[i].bytes & 31u) || d[0] != 1u || d[1] != 0u ||
            d[2] != capture_cues[i].bytes) {
            FB.fault = 3u;
            return;
        }
    }
    for (uint32_t i = 0; i < CAPTURE_CUES; ++i)
        capture_cue_descriptor[i][1] = (uint32_t)(uintptr_t)(capture_cue_pcm + capture_cues[i].offset_samples);
    rec_tone_api.clean(capture_cue_descriptor, sizeof(capture_cue_descriptor));
    __atomic_store_n(&FB.ready, 1u, __ATOMIC_RELEASE);
}

/* One-press cues reuse the low bip (no new PCM), CAPTURE_BIP_GAP_TICKS apart: a
 * refusal (screenshot kept pending, or no Y22) bips twice, a cancelled job three
 * times. A bip waits up to CAPTURE_BIP_WAIT_TICKS for the stock beep. */
#define CAPTURE_BIP_GAP_TICKS 10u
#define CAPTURE_BIP_WAIT_TICKS 40u

/* Nonzero when a one-press bip waits for the stock beep (our click) to finish. */
CODE static uint32_t sound(uint32_t cue, uint32_t may_wait)
{
    uint32_t reason;
    if (!__atomic_load_n(&FB.ready, __ATOMIC_ACQUIRE))
        reason = CAPTURE_CUE_UNPUBLISHED;
#if defined(R5_RECORD_INDICATOR) && R5_RECORD_INDICATOR
    else if (!__atomic_load_n(&rec_tone_state.ready, __ATOMIC_ACQUIRE) || rec_tone_state.fault)
        reason = CAPTURE_CUE_TONE_UNREADY;
    else if (rec_tone_state.mode != REC_TONE_IDLE || rec_tone_state.pending)
        reason = CAPTURE_CUE_TONE_BUSY; /* never disturb the record cue */
#endif
    else if (combo_observe_api.read(COMBO_REC_STATE_WORD) != 0u)
        reason = CAPTURE_CUE_RECORDING;
    else {
        struct rec_tone_attempt a;
        volatile uint32_t *w = (volatile uint32_t *)&a;
        for (unsigned i = 0; i < sizeof(a) / 4u; ++i)
            w[i] = 0u;
        reason = rec_tone_preflight(&a, COMBO_CUE_ACTRL_MASK);
    }
    FB.last_reason = reason;
    if (reason == REC_TONE_PF_BUSY && may_wait && FB.bip_waits < CAPTURE_BIP_WAIT_TICKS) {
        ++FB.bip_waits; /* on camera the first refusal bip met the click still playing */
        return 1u;
    }
    if (reason) {
        FB.skipped = sat_inc(FB.skipped);
        return 0u;
    }
    FB.last_rc = rec_tone_api.beep_start(REC_TONE_SE_ID, capture_cue_descriptor[cue]);
    if (cue == CAPTURE_CUE_CLICK)
        FB.clicks = sat_inc(FB.clicks);
    else
        FB.bips = sat_inc(FB.bips);
    return 0u;
}

CODE void capture_feedback_step(void)
{
    if (combo_observe_api.read(COMBO_REC_STATE_WORD) != 0u) {
        __atomic_store_n(&capture_watch.rec_last_tick, __atomic_load_n(&combo_worker_tick, __ATOMIC_RELAXED), __ATOMIC_RELAXED);
        __atomic_store_n(&capture_watch.rec_seen, 1u, __ATOMIC_RELEASE);
    }
    if (!FB.publish_attempted)
        publish_cues();
    uint32_t cued = FB.clicks + FB.bips + FB.skipped; /* one cue per tick at most */
    for (uint32_t n = 0; n < CAPTURE_RESULTS; ++n) {
        uint32_t want = FB.seen + 1u;
        const struct capture_result *slot = &G.results[(want - 1u) % CAPTURE_RESULTS];
        uint32_t seq = __atomic_load_n(&slot->seq, __ATOMIC_ACQUIRE);
        if ((int32_t)(seq - want) < 0)
            break;
        struct capture_result r;
        volatile uint32_t *dst = (volatile uint32_t *)&r;
        const volatile uint32_t *src = (const volatile uint32_t *)slot;
        for (uint32_t i = 0; i < sizeof(r) / 4u; ++i)
            dst[i] = src[i];
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
#ifdef COMBO_OBSERVE_HOST
        if (capture_host_after_copy)
            capture_host_after_copy(want);
#endif
        /* Lapped if the slot holds another seq. Torn if the writer has already
         * published want + RESULTS - 1: the record reusing this slot may be in
         * progress (its payload stores follow that count, see publish); a slot
         * already rewritten implies a later count, so this covers it too. */
        if (seq != want || __atomic_load_n(&G.published, __ATOMIC_ACQUIRE) - want >= CAPTURE_RESULTS - 1u) {
            /* lapped or rewritten while copying: skip to the oldest that can remain */
            uint32_t newest = __atomic_load_n(&G.published, __ATOMIC_ACQUIRE);
            uint32_t resume = newest > CAPTURE_RESULTS ? newest - CAPTURE_RESULTS + 1u : want + 1u;
            if ((int32_t)(resume - want) <= 0)
                resume = want + 1u;
            FB.lost += resume - want;
            FB.seen = resume - 1u;
            continue;
        }
        FB.seen = want;
        {
            volatile uint32_t *keep = (volatile uint32_t *)&FB.last[FB.kept % CAPTURE_RESULTS];
            const uint32_t *from = (const uint32_t *)&r;
            for (uint32_t i = 0; i < sizeof(r) / 4u; ++i)
                keep[i] = from[i];
        }
        FB.kept = sat_inc(FB.kept);
        if (r.outcome == CAPTURE_OK)
            sound(CAPTURE_CUE_CLICK, 0u);
        else if (r.outcome == CAPTURE_REFUSED_RECORDING)
            FB.silent = sat_inc(FB.silent); /* never sound while recording */
        else
            sound(CAPTURE_CUE_BIP, 0u);
    }
    /* A refused one-press (decided at the RATE release, after the capture's own
     * click) or a cancelled job: its bips, each on a tick that sounded nothing else. */
    uint32_t tick = __atomic_load_n(&combo_worker_tick, __ATOMIC_RELAXED);
    if (!FB.bips_owed) {
        uint32_t refusals = __atomic_load_n(&one_press_state.refusals, __ATOMIC_ACQUIRE);
        uint32_t cancels = __atomic_load_n(&one_press_state.cancels, __ATOMIC_ACQUIRE);
        if (refusals != FB.refusals_seen) {
            FB.refusals_seen = refusals;
            FB.bips_owed = 2u;
        } else if (cancels != FB.cancels_seen) {
            FB.cancels_seen = cancels;
            FB.bips_owed = 3u;
        }
        FB.bip_tick = tick - CAPTURE_BIP_GAP_TICKS;
        FB.bip_waits = 0u;
    }
    if (FB.bips_owed && cued == FB.clicks + FB.bips + FB.skipped && tick - FB.bip_tick >= CAPTURE_BIP_GAP_TICKS &&
        !sound(CAPTURE_CUE_BIP, 1u)) {
        --FB.bips_owed;
        FB.bip_tick = tick;
        FB.bip_waits = 0u;
    }
}
