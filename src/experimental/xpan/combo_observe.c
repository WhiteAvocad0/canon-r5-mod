/* Issue #15 passive button-combo observer: worker side.
 * Host-testable through combo_observe_api (COMBO_OBSERVE_HOST). */
#include "combo_observe.h"
#include "observer.h"
#include "combo_capture.h"
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

/* Hook-written ring (BSS, zero at boot). Its own 32-byte lines. */
struct combo_hook_ring combo_hook_ring __attribute__((aligned(32)));
volatile uint32_t combo_worker_tick;
struct combo_observe_state combo_observe_state;
#define S combo_observe_state

#ifdef COMBO_OBSERVE_HOST
extern void combo_host_after_copy(uint32_t seq);
#else
CODE static uint32_t native_read(uint32_t address)
{
    return *(const volatile uint32_t *)(uintptr_t)address;
}
const struct combo_observe_api combo_observe_api DATA = {native_read, observer_read_counter};
#endif

#define COMBO_PRESS_CODE(name, code) code,
static const uint16_t button_press[COMBO_BUTTON_COUNT] DATA = {COMBO_BUTTONS(COMBO_PRESS_CODE)};
#define COMBO_PAIR_N(n, a, b) n,
#define COMBO_PAIR_A(n, a, b) a,
#define COMBO_PAIR_B(n, a, b) b,
static const uint16_t pair_n[COMBO_PAIR_COUNT] DATA = {COMBO_PAIRS(COMBO_PAIR_N)};
static const uint16_t pair_a[COMBO_PAIR_COUNT] DATA = {COMBO_PAIRS(COMBO_PAIR_A)};
static const uint16_t pair_b[COMBO_PAIR_COUNT] DATA = {COMBO_PAIRS(COMBO_PAIR_B)};
static const uint8_t vmix_offsets[5] DATA = {0, 8, 12, 16, 20};
static const uint8_t output_offsets[9] DATA = {0, 4, 16, 20, 32, 36, 40, 44, 48};

static const char cap_head_fmt[] DATA = CW_FMT_HEAD;
static const char cap_verdict_fmt[] DATA = CW_FMT_VERDICT;
static const char cap_geometry_fmt[] DATA = CW_FMT_GEOMETRY;
static const char cap_marv_fmt[] DATA = CW_FMT_MARV;
static const char d_begin_fmt[] DATA = CD_FMT_BEGIN;
static const char d_vmix_fmt[] DATA = CD_FMT_VMIX;
static const char d_output_fmt[] DATA = CD_FMT_OUTPUT;
static const char d_ptrs_fmt[] DATA = CD_FMT_OUTPUT_PTRS;
static const char d_renderer_fmt[] DATA = CD_FMT_RENDERER;
static const char d_marv_fmt[] DATA = CD_FMT_MARV;
static const char d_end_fmt[] DATA = CD_FMT_END;
static const char c_begin_fmt[] DATA = CC_FMT_BEGIN;
static const char c_ring_fmt[] DATA = CC_FMT_RING;
static const char c_events_fmt[] DATA = CC_FMT_EVENTS;
static const char c_keys_fmt[] DATA = CC_FMT_KEYS;
static const char c_button_fmt[] DATA = CC_FMT_BUTTON;
static const char c_other_fmt[] DATA = CC_FMT_OTHER;
static const char c_pairs_fmt[] DATA = CC_FMT_PAIRS;
static const char c_overlap_fmt[] DATA = CC_FMT_OVERLAP;
static const char c_captures_fmt[] DATA = CC_FMT_CAPTURES;
static const char c_capture_fmt[] DATA = CC_FMT_CAPTURE;
static const char c_display_fmt[] DATA = CC_FMT_DISPLAY;
static const char c_shot_fmt[] DATA = CC_FMT_SHOT;
static const char c_cue_fmt[] DATA = CC_FMT_CUE;
static const char c_end_fmt[] DATA = CC_FMT_END;
static const char c_trigger_fmt[] DATA = CC_FMT_TRIGGER;
static const char c_cap_counts_fmt[] DATA = CC_FMT_CAP_COUNTS;
static const char c_cap_out_a_fmt[] DATA = CC_FMT_CAP_OUT_A;
static const char c_cap_out_b_fmt[] DATA = CC_FMT_CAP_OUT_B;
static const char c_cap_res_fmt[] DATA = CC_FMT_CAP_RESULT;
static const char c_cap_rc_fmt[] DATA = CC_FMT_CAP_RC;
static const char c_cap_desc_fmt[] DATA = CC_FMT_CAP_DESC;
static const char c_cap_misc_fmt[] DATA = CC_FMT_CAP_MISC;
static const char c_cap_time_fmt[] DATA = CC_FMT_CAP_TIME;
static const char c_cap_held_fmt[] DATA = CC_FMT_CAP_HELD;
static const char c_cap_us_fmt[] DATA = CC_FMT_CAP_US;
static const char c_cap_out_c_fmt[] DATA = CC_FMT_CAP_OUT_C;
static const char c_cap_job_fmt[] DATA = CC_FMT_CAP_JOB;
static const char c_cap_yca_fmt[] DATA = CC_FMT_CAP_YCA;
static const char c_cap_yca_us_fmt[] DATA = CC_FMT_CAP_YCA_US;
static const char c_cap_out_d_fmt[] DATA = CC_FMT_CAP_OUT_D;
static const char c_hist_fmt[] DATA = CC_FMT_HIST;
static const char c_hist_tail_fmt[] DATA = CC_FMT_HIST_TAIL;
static const char c_seq_head_fmt[] DATA = CC_FMT_SEQ_HEAD;
static const char c_seq_anchor_fmt[] DATA = CC_FMT_SEQ_ANCHOR;
static const char c_seq_fmt[] DATA = CC_FMT_SEQ;
static const char c_map_fmt[] DATA = CC_FMT_MATRIX_MAP;
static const char c_mcount_fmt[] DATA = CC_FMT_MATRIX_COUNT;
static const char c_mskew_fmt[] DATA = CC_FMT_MATRIX_SKEW;

#define DEBUG(...) observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, __VA_ARGS__)
/* Live rows (not the export summary) also count toward the per-boot usage figure. */
#define LIVE(...) (S.live_rows = sat_inc(S.live_rows), DEBUG(__VA_ARGS__))

CODE static uint32_t sat_inc(uint32_t v)
{
    return v == UINT32_MAX ? v : v + 1u;
}

/* Little-endian word of four census bytes (byte 0 in bits 7:0). */
CODE static uint32_t pack4(const uint8_t *b)
{
    return (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
}

CODE static uint32_t rd(uint32_t address)
{
    return combo_observe_api.read(address);
}

CODE uint32_t combo_button_index(uint32_t code, uint32_t *is_release)
{
    for (uint32_t i = 0; i < COMBO_BUTTON_COUNT; ++i) {
        if (code == button_press[i] || code == button_press[i] + 1u) {
            *is_release = code != button_press[i];
            return i;
        }
    }
    *is_release = 0u;
    return COMBO_BUTTON_COUNT;
}

CODE static uint32_t pair_of(uint32_t x, uint32_t y)
{
    for (uint32_t p = 0; p < COMBO_PAIR_COUNT; ++p)
        if ((pair_a[p] == x && pair_b[p] == y) || (pair_a[p] == y && pair_b[p] == x))
            return p;
    return COMBO_PAIR_COUNT;
}

/* ---- display state (read-only; descriptors dereferenced only in range) ---- */

CODE static void read_marv(struct combo_marv *m, uint32_t pointer)
{
    m->pointer = pointer;
    m->magic = m->data = m->format = m->width = m->height = 0u;
    if (pointer < COMBO_POINTER_MIN || pointer >= COMBO_POINTER_END - 36u || (pointer & 3u))
        return;
    m->magic = rd(pointer);
    if (m->magic != COMBO_MARV_MAGIC)
        return;
    m->data = rd(pointer + 4u);
    m->format = rd(pointer + 24u);
    m->width = rd(pointer + 28u);
    m->height = rd(pointer + 32u);
}

CODE void combo_sample_display(struct combo_display *d)
{
    for (uint32_t k = 0; k < 5u; ++k)
        d->vmix[k] = rd(COMBO_VMIX_STATE + vmix_offsets[k]);
    for (uint32_t i = 0; i < COMBO_VMIX_OUTPUT_COUNT; ++i) {
        for (uint32_t j = 0; j < 9u; ++j)
            d->output[i][j] = rd(COMBO_VMIX_OUTPUTS + COMBO_VMIX_OUTPUT_BYTES * i + output_offsets[j]);
        read_marv(&d->output_marv[i][0], d->output[i][6]);
        read_marv(&d->output_marv[i][1], d->output[i][7]);
    }
    for (uint32_t k = 0; k < COMBO_RENDERER_SLOT_COUNT; ++k) {
        for (uint32_t j = 0; j < 3u; ++j)
            d->renderer[k][j] = rd(COMBO_RENDERER_SLOTS + COMBO_RENDERER_SLOT_BYTES * k + 4u * j);
        read_marv(&d->renderer_marv[k], d->renderer[k][0]);
    }
}

CODE static uint32_t marv_equal(const struct combo_marv *a, const struct combo_marv *b)
{
    return a->pointer == b->pointer && a->magic == b->magic && a->data == b->data && a->format == b->format &&
           a->width == b->width && a->height == b->height;
}

/* Geometry identity: everything except cur (vmix +16, flips at the OSD rate,
 * counted separately) and each output's +48 dirty word. */
CODE static uint32_t display_same(const struct combo_display *a, const struct combo_display *b)
{
    for (uint32_t k = 0; k < 5u; ++k)
        if (k != 3u && a->vmix[k] != b->vmix[k])
            return 0u;
    for (uint32_t i = 0; i < COMBO_VMIX_OUTPUT_COUNT; ++i) {
        for (uint32_t j = 0; j < 8u; ++j)
            if (a->output[i][j] != b->output[i][j])
                return 0u;
        if (!marv_equal(&a->output_marv[i][0], &b->output_marv[i][0]) ||
            !marv_equal(&a->output_marv[i][1], &b->output_marv[i][1]))
            return 0u;
    }
    for (uint32_t k = 0; k < COMBO_RENDERER_SLOT_COUNT; ++k) {
        for (uint32_t j = 0; j < 3u; ++j)
            if (a->renderer[k][j] != b->renderer[k][j])
                return 0u;
        if (!marv_equal(&a->renderer_marv[k], &b->renderer_marv[k]))
            return 0u;
    }
    return 1u;
}

CODE static uint32_t admitted_size(uint32_t w, uint32_t h)
{
    return (w == 1024u && h == 682u) || (w == 1600u && h == 1200u);
}

/* The single owner of the display admission, faithful to SaveVRAM's YCCA pass
 * (E0263F58..E0264002): descriptor *(0x150B80 + 40 + 4 * (1 - cur)) for output
 * 0; magic 'MARV' at +0; data +4; format +24 whose bytes [23:16] / [15:8] give
 * the bytes per pixel (6 / 2 = 3 for Ycc422A); width +28, height +32; the
 * MapCpuBuffer(op 0, mode 1) preconditions of E07F068A: vaddr < 0xC0000000 and,
 * for cacheable vaddr < 0x40000000, start and end 32-byte aligned. An earlier
 * check also required format == 0x11060200 and MARV w/h == table w/h, and that
 * failed on every camera press; the table w/h is now recorded only. */
CODE uint32_t combo_display_verdict(const struct combo_display *d, uint32_t *known, uint32_t *geometry)
{
    uint32_t pass = 0u, cur = d->vmix[3];
    *known = COMBO_V_GEOMETRY;
    *geometry = COMBO_VMIX_OUTPUT_COUNT; /* no selected descriptor */
    if (cur > 1u) { /* sel = 1 - cur is undefined: descriptor facts stay unknown */
        if (admitted_size(d->output[0][2], d->output[0][3]))
            pass |= COMBO_V_GEOMETRY;
        return pass;
    }
    uint32_t sel = 1u - cur;
    const struct combo_marv *m = &d->output_marv[0][sel];
    *geometry = sel;
    *known |= COMBO_V_MARV;
    uint32_t marv = m->magic == COMBO_MARV_MAGIC && ((m->format >> 16) & 0xffu) == 6u && ((m->format >> 8) & 0xffu) == 2u;
    /* Geometry: the descriptor's own size when it is admitted (the bytes written
     * come from it), else the output table's. */
    if (admitted_size(marv ? m->width : d->output[0][2], marv ? m->height : d->output[0][3]))
        pass |= COMBO_V_GEOMETRY;
    if (!marv || !admitted_size(m->width, m->height))
        return pass;
    pass |= COMBO_V_MARV;
    *known |= COMBO_V_ALIGN;
    uint32_t bytes = 3u * m->width * m->height, data = m->data;
    if (data && data < COMBO_POINTER_END && COMBO_POINTER_END - data > bytes &&
        (data >= UINT32_C(0x40000000) || (!(data & 31u) && !((data + bytes) & 31u))))
        pass |= COMBO_V_ALIGN;
    return pass;
}

CODE static void emit_display(const struct combo_display *d, uint32_t snapshot, uint32_t tick, uint32_t rec)
{
    LIVE(d_begin_fmt, snapshot, tick, d->vmix[3], S.cur_flips, rec);
    LIVE(d_vmix_fmt, d->vmix[0], d->vmix[1], d->vmix[2], d->vmix[4]);
    for (uint32_t i = 0; i < COMBO_VMIX_OUTPUT_COUNT; ++i) {
        LIVE(d_output_fmt, i, d->output[i][0], d->output[i][1], d->output[i][2], d->output[i][3], d->output[i][8]);
        LIVE(d_ptrs_fmt, i, d->output[i][4], d->output[i][5], d->output[i][6], d->output[i][7]);
    }
    for (uint32_t k = 0; k < COMBO_RENDERER_SLOT_COUNT; ++k)
        LIVE(d_renderer_fmt, k, d->renderer[k][0], d->renderer[k][1], d->renderer[k][2]);
    for (uint32_t id = 0; id < 2u * COMBO_VMIX_OUTPUT_COUNT + COMBO_RENDERER_SLOT_COUNT; ++id) {
        const struct combo_marv *m = id < 2u * COMBO_VMIX_OUTPUT_COUNT ? &d->output_marv[id >> 1][id & 1u]
                                                                        : &d->renderer_marv[id - 2u * COMBO_VMIX_OUTPUT_COUNT];
        if (m->pointer)
            LIVE(d_marv_fmt, id, m->pointer, m->magic, m->data, m->format, m->width, m->height);
    }
    LIVE(d_end_fmt, snapshot);
}

/* Word copy: no memcpy libcall in the freestanding link. */
CODE static void copy_display(struct combo_display *to, const struct combo_display *from)
{
    volatile uint32_t *d = (volatile uint32_t *)to;
    const uint32_t *s = (const uint32_t *)from;
    for (uint32_t i = 0; i < sizeof(*to) / 4u; ++i)
        d[i] = s[i];
}

CODE static void display_tick(uint32_t tick)
{
    uint32_t cur = rd(COMBO_VMIX_STATE + 16u);
    if (S.cur_seen && cur != S.last_cur)
        S.cur_flips = sat_inc(S.cur_flips);
    S.cur_seen = 1u;
    S.last_cur = cur;
    if (++S.display_countdown < COMBO_DISPLAY_PERIOD_TICKS)
        return;
    S.display_countdown = 0u;
    struct combo_display d;
    combo_sample_display(&d);
    S.display_samples = sat_inc(S.display_samples);
    if (S.display_valid && display_same(&d, &S.last_display))
        return;
    copy_display(&S.last_display, &d);
    S.display_valid = 1u;
    if (S.snapshots >= COMBO_SNAPSHOT_BUDGET) {
        S.snapshots_suppressed = sat_inc(S.snapshots_suppressed);
        return;
    }
    S.snapshots++;
    emit_display(&d, S.snapshots, tick, rd(COMBO_REC_STATE_WORD));
}

/* ---- shot counter (the parent's shutter-feedback capture counter) ---- */

CODE static void counter_tick(uint32_t tick)
{
    unsigned context = 0u, value = 0u;
    unsigned observed = combo_observe_api.read_counter ? combo_observe_api.read_counter(&context, &value) : 0u;
    if (observed > 1u)
        S.counter_faults = sat_inc(S.counter_faults);
    if (observed != 1u || !context) {
        S.counter_known = 0u;
        return;
    }
    if (!S.counter_known || context != S.counter_context) {
        S.counter_context = context;
        S.counter_value = value;
        S.counter_change_tick = tick; /* a fresh baseline counts as a change */
        /* GuiMainTask (combo_capture_gui) reads known with acquire, then the tick. */
        __atomic_store_n(&S.counter_known, 1u, __ATOMIC_RELEASE);
        return;
    }
    if (value != S.counter_value) {
        S.counter_value = value;
        S.counter_change_tick = tick;
    }
}

/* ---- combo detector ---- */

CODE static void would_capture(uint32_t candidate, uint32_t first, uint32_t second, uint32_t dt, uint32_t tick)
{
    uint32_t known = COMBO_V_NOT_RECORDING | COMBO_V_DEBOUNCE, pass = 0u, rec = rd(COMBO_REC_STATE_WORD), quiet = 0u;
    uint32_t gui_mode = rd(COMBO_GUI_MODE_WORD); /* observation only: no verdict depends on it */
    if (rec == 0u)
        pass |= COMBO_V_NOT_RECORDING;
    if (S.counter_known) {
        known |= COMBO_V_SHOT_QUIET;
        quiet = tick - S.counter_change_tick;
        if (quiet >= COMBO_SHOT_QUIET_TICKS)
            pass |= COMBO_V_SHOT_QUIET;
    } else {
        quiet = UINT32_MAX;
    }
    struct combo_display d;
    combo_sample_display(&d);
    uint32_t dk = 0u, sel = 0u, dp = combo_display_verdict(&d, &dk, &sel);
    known |= dk;
    pass |= dp;
    if (!S.any_capture || tick - S.last_capture_tick >= COMBO_DEBOUNCE_TICKS)
        pass |= COMBO_V_DEBOUNCE;
    /* No playback signal in this build: only candidate 3 (LOCK + INFO) has a
     * context-free rule. The trigger button and candidate 2 stay unknown. */
    if (candidate == 3u) {
        known |= COMBO_V_CONTEXT;
        pass |= COMBO_V_CONTEXT;
    }
    S.any_capture = 1u;
    S.last_capture_tick = tick;
    struct combo_capture *c = &S.capture[S.captures % COMBO_CAPTURES];
    c->candidate = candidate;
    c->first = first;
    c->second = second;
    c->ticks = dt;
    c->known = known;
    c->pass = pass;
    c->tick = tick;
    c->gui_mode = gui_mode;
    S.captures = sat_inc(S.captures);
    if (S.capture_rows < COMBO_CAPTURE_ROW_BUDGET) {
        S.capture_rows++;
        const struct combo_marv *m = sel < 2u ? &d.output_marv[0][sel] : 0;
        LIVE(cap_head_fmt, S.captures, tick, candidate, c->first, c->second, dt);
        LIVE(cap_verdict_fmt, S.captures, known, pass, rec, quiet, gui_mode);
        LIVE(cap_geometry_fmt, S.captures, d.output[0][2], d.output[0][3], d.vmix[3], m ? m->pointer : 0u,
              m ? m->data : 0u);
        LIVE(cap_marv_fmt, S.captures, m ? m->magic : 0u, m ? m->format : 0u, m ? m->width : 0u,
              m ? m->height : 0u);
    } else {
        S.suppressed_rows = sat_inc(S.suppressed_rows);
    }
}

/* Census matrix slot of a button press code (first 16 distinct), or COMBO_MATRIX. */
CODE static uint32_t matrix_slot(uint32_t code)
{
    for (uint32_t i = 0; i < S.matrix_used; ++i)
        if (S.matrix_code[i] == code)
            return i;
    if (S.matrix_used >= COMBO_MATRIX) {
        S.matrix_overflow = sat_inc(S.matrix_overflow);
        return COMBO_MATRIX;
    }
    S.matrix_code[S.matrix_used] = (uint8_t)code;
    for (uint32_t j = 0; j < COMBO_MATRIX; ++j) {
        S.matrix_skew[S.matrix_used][j] = (uint8_t)COMBO_MATRIX_NONE;
        S.matrix_skew[j][S.matrix_used] = (uint8_t)COMBO_MATRIX_NONE;
    }
    return S.matrix_used++;
}


CODE static void key_event(uint32_t index, uint32_t release, uint32_t tick)
{
    struct combo_held *h = &S.held[index];
    if (release) {
        S.release_count[index] = sat_inc(S.release_count[index]);
        if (!h->held)
            S.releases_unmatched = sat_inc(S.releases_unmatched);
        h->held = 0u;
        for (uint32_t p = 0; p < COMBO_PAIR_COUNT; ++p) {
            uint32_t ra = 0u, rb = 0u, a = combo_button_index(pair_a[p], &ra), b = combo_button_index(pair_b[p], &rb);
            if (!S.armed[p] && !S.held[a].held && !S.held[b].held)
                S.armed[p] = 1u;
        }
        return;
    }
    S.press_count[index] = sat_inc(S.press_count[index]);
    uint32_t repeated = h->held; /* a press code while still held (auto-repeat or a lost release) */
    if (repeated)
        S.presses_repeated = sat_inc(S.presses_repeated);
    uint32_t n = ++S.press_index, others = 0u, other = COMBO_BUTTON_COUNT;
    uint32_t slot = matrix_slot(button_press[index]);
    for (uint32_t i = 0; i < COMBO_BUTTON_COUNT; ++i)
        if (i != index && S.held[i].held) {
            others++;
            other = i;
            uint32_t from = matrix_slot(button_press[i]);
            if (from < COMBO_MATRIX && slot < COMBO_MATRIX) {
                uint32_t skew = tick - S.held[i].press_tick;
                if (S.matrix_count[from][slot] < 255u)
                    S.matrix_count[from][slot]++;
                if (skew < S.matrix_skew[from][slot])
                    S.matrix_skew[from][slot] = (uint8_t)(skew < 254u ? skew : 254u);
            }
        }
    if (others + 1u > S.max_held)
        S.max_held = others + 1u;
    h->held = 1u;
    h->press_tick = tick;
    h->press_index = n;
    if (!others)
        return;
    if (others > 1u) {
        S.extra_keys = sat_inc(S.extra_keys);
        return;
    }
    uint32_t dt = tick - S.held[other].press_tick;
    struct combo_overlap *o = &S.overlap[S.overlaps % COMBO_OVERLAPS];
    o->first = button_press[other];
    o->second = button_press[index];
    o->ticks = dt;
    S.overlaps = sat_inc(S.overlaps);
    uint32_t p = pair_of(button_press[other], button_press[index]);
    if (p == COMBO_PAIR_COUNT)
        return;
    if (dt > COMBO_WINDOW_TICKS) {
        S.outside_window = sat_inc(S.outside_window);
        return;
    }
    if (S.held[other].press_index != n - 1u) { /* another key was pressed inside the window */
        S.extra_keys = sat_inc(S.extra_keys);
        return;
    }
    if (!S.armed[p])
        return;
    S.armed[p] = 0u;
    S.fired[p] = sat_inc(S.fired[p]);
    would_capture(pair_n[p], button_press[other], button_press[index], dt, tick);
}

CODE static void other_event(uint32_t code)
{
    for (uint32_t i = 0; i < COMBO_OTHER_CODES; ++i) {
        if (S.other_count[i] && S.other_code[i] == code) {
            S.other_count[i] = sat_inc(S.other_count[i]);
            return;
        }
        if (!S.other_count[i]) {
            S.other_code[i] = code;
            S.other_count[i] = 1u;
            return;
        }
    }
    S.other_overflow = sat_inc(S.other_overflow);
}

/* ---- drain ---- */

/* The one rule for "a user key" (one_press.h): a plain class-0 COMBO_BUTTONS press, or a
 * joystick / direction-key code. Used by the drain and by combo_undrained. */
CODE static uint32_t user_key(uint32_t code, uint32_t flags)
{
    if (flags & (COMBO_F_CODE_WIDE | COMBO_F_CLASS_NONZERO))
        return 0u;
    uint32_t release = 0u;
    return (combo_button_index(code, &release) < COMBO_BUTTON_COUNT && !release) ||
           (code >= OP_DIRECTION_FIRST && code <= OP_MULTI_CONTROLLER_LAST);
}

/* Read only: the records the hook has published and the drain not yet handled (the drain
 * advances next_seq only after a record's effects). On GuiMainTask the hook is the same
 * task, so the records are complete; on the worker a record is used only if its seq word
 * still matches. More than the ring can hold: unknown, both flags. */
CODE uint32_t combo_undrained(void)
{
    uint32_t next = __atomic_load_n(&S.next_seq, __ATOMIC_ACQUIRE), out = 0u;
    uint32_t written = __atomic_load_n(&combo_hook_ring.written, __ATOMIC_ACQUIRE);
    if (!next)
        next = 1u;
    if ((int32_t)(written - next) >= (int32_t)COMBO_RING_MASK)
        return COMBO_UNDRAINED_USER_KEY | COMBO_UNDRAINED_CLEAR;
    for (uint32_t seq = next; (int32_t)(written - seq) >= 0; ++seq) {
        const struct combo_ring_entry *e = &combo_hook_ring.entries[(seq - 1u) & COMBO_RING_MASK];
        if (__atomic_load_n(&e->seq, __ATOMIC_ACQUIRE) != seq)
            return COMBO_UNDRAINED_USER_KEY | COMBO_UNDRAINED_CLEAR;
        uint32_t code = __atomic_load_n(&e->code_tick, __ATOMIC_RELAXED) & 0xffffu;
        uint32_t flags = __atomic_load_n(&e->context, __ATOMIC_RELAXED) >> 24;
        if (flags & COMBO_F_GEN_UNEQUAL)
            out |= COMBO_UNDRAINED_CLEAR;
        if (user_key(code, flags))
            out |= COMBO_UNDRAINED_USER_KEY;
    }
    return out;
}

CODE static void record(uint32_t seq, uint32_t code_tick, uint32_t args, uint32_t context, uint32_t now)
{
    uint32_t code = code_tick & 0xffffu, flags = context >> 24;
    /* Full tick: the hook stored the low 16 bits of the tick it saw (<= now). */
    uint32_t tick = now - ((now - (code_tick >> 16)) & 0xffffu);
    if (flags & COMBO_F_GEN_UNEQUAL) {
        S.gen_unequal = sat_inc(S.gen_unequal);
        one_press_queue_clear(); /* S4: a queue clear marks this playback visit (one_press.h) */
    }
    if (flags & (COMBO_F_CODE_WIDE | COMBO_F_ARG2_WIDE | COMBO_F_ARG3_WIDE | COMBO_F_MODE_WIDE | COMBO_F_CLASS_NONZERO))
        S.wide = sat_inc(S.wide);
    uint32_t release = 0u;
    uint32_t index = (flags & (COMBO_F_CODE_WIDE | COMBO_F_CLASS_NONZERO)) ? COMBO_BUTTON_COUNT : combo_button_index(code, &release);
    uint32_t plain = !(flags & (COMBO_F_CODE_WIDE | COMBO_F_CLASS_NONZERO)) && code < COMBO_HIST_CODES;
    if (plain) {
        if (S.hist[code] < 0xffffu)
            S.hist[code]++;
    } else {
        S.hist_overflow = sat_inc(S.hist_overflow);
    }
    {
        uint32_t k = S.seq_count % COMBO_SEQ_EVENTS, dt = S.seq_count ? tick - S.seq_last_tick : 255u;
        uint32_t byte = plain && code < COMBO_SEQ_ESCAPE ? code : COMBO_SEQ_ESCAPE;
        S.seq[k] = (uint16_t)((byte << 8) | (dt < 255u ? dt : 255u));
        if (!(k % COMBO_SEQ_ANCHOR))
            S.seq_anchor[k / COMBO_SEQ_ANCHOR] = tick;
        S.seq_last_tick = tick;
        S.seq_count = sat_inc(S.seq_count);
    }
    (void)seq;
    (void)args;
    if (user_key(code, flags))
        one_press_user_key(tick, code); /* a user key cancels or holds the automatic INFO (one_press.h) */
    if (index < COMBO_BUTTON_COUNT) {
        S.key_events = sat_inc(S.key_events);
        key_event(index, release, tick);
    } else {
        S.other_events = sat_inc(S.other_events);
        other_event(code);
    }
}

/* Records lost before the drain read them may have been user keys: fail closed, before
 * next_seq moves past them (combo_undrained). */
CODE static void lost_records(uint32_t now)
{
    one_press_user_key(now, 0u);
}

CODE static void drain(uint32_t now)
{
    if (!S.next_seq)
        S.next_seq = 1u;
    for (uint32_t n = 0; n < COMBO_RING_ENTRIES; ++n) {
        uint32_t want = S.next_seq;
        struct combo_ring_entry *e = &combo_hook_ring.entries[(want - 1u) & COMBO_RING_MASK];
        uint32_t seq = __atomic_load_n(&e->seq, __ATOMIC_ACQUIRE);
        int32_t ahead = (int32_t)(seq - want);
        if (ahead < 0)
            break; /* not yet written */
        if (ahead > 0) {
            /* Lapped: the slot already holds want + 64k. The oldest record that
             * can still be in the ring is (newest written) - 63; the hook's own
             * count is a monotonic lower bound of the newest. Everything before
             * the resume point is lost (counted, never guessed). */
            uint32_t resume = seq - COMBO_RING_MASK;
            uint32_t newest = __atomic_load_n(&combo_hook_ring.written, __ATOMIC_ACQUIRE);
            if ((int32_t)(newest - COMBO_RING_MASK - resume) > 0)
                resume = newest - COMBO_RING_MASK;
            S.lost += resume - want;
            lost_records(now);
            __atomic_store_n(&S.next_seq, resume, __ATOMIC_RELEASE);
            continue;
        }
        uint32_t code_tick = __atomic_load_n(&e->code_tick, __ATOMIC_RELAXED);
        uint32_t args = __atomic_load_n(&e->args, __ATOMIC_RELAXED);
        uint32_t context = __atomic_load_n(&e->context, __ATOMIC_RELAXED);
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
#ifdef COMBO_OBSERVE_HOST
        combo_host_after_copy(want); /* host seam: a writer may lap here */
#endif
        /* Torn if the slot was rewritten, or if the hook has already written
         * want + 63, so the record that reuses this slot (want + 64) may be in
         * progress: its payload stores come only after that count (dmb ish). */
        if (__atomic_load_n(&e->seq, __ATOMIC_RELAXED) != want ||
            __atomic_load_n(&combo_hook_ring.written, __ATOMIC_RELAXED) - want >= COMBO_RING_MASK) {
            /* Overwritten (or possibly being overwritten) while copying: this
             * record is lost, not guessed. */
            S.torn = sat_inc(S.torn);
            S.lost++;
            lost_records(now);
            __atomic_store_n(&S.next_seq, want + 1u, __ATOMIC_RELEASE);
            continue;
        }
        S.drained = sat_inc(S.drained);
        record(want, code_tick, args, context, now);
        __atomic_store_n(&S.next_seq, want + 1u, __ATOMIC_RELEASE); /* after its effects (combo_undrained) */
    }
    S.unread_after_step = __atomic_load_n(&combo_hook_ring.written, __ATOMIC_RELAXED) - (S.next_seq - 1u);
}

CODE void combo_observe_step(void)
{
    S.steps = sat_inc(S.steps);
    if (!S.steps_armed_init) {
        S.steps_armed_init = 1u;
        for (uint32_t p = 0; p < COMBO_PAIR_COUNT; ++p)
            S.armed[p] = 1u;
    }
    /* The tick the hook stamps; one writer (this worker), one increment per step. */
    uint32_t now = combo_worker_tick + 1u;
    __atomic_store_n(&combo_worker_tick, now, __ATOMIC_RELEASE);
    counter_tick(now);
    drain(now);
    display_tick(now);
    capture_feedback_step();
    one_press_step(now); /* the playback visit, the automatic INFO and the one-press timeout */
}

CODE void combo_observe_report(unsigned reports)
{
    if (!observer_api.debug)
        return;
    DEBUG(c_begin_fmt, reports);
    DEBUG(c_ring_fmt, __atomic_load_n(&combo_hook_ring.written, __ATOMIC_RELAXED), S.drained, S.lost, S.torn,
          S.unread_after_step);
    DEBUG(c_events_fmt, S.key_events, S.other_events, S.gen_unequal, S.wide, S.steps);
    DEBUG(c_keys_fmt, S.presses_repeated, S.releases_unmatched, S.suppressed_rows, S.live_rows);
    for (uint32_t i = 0; i < COMBO_BUTTON_COUNT; ++i)
        if (S.press_count[i] || S.release_count[i] || S.held[i].held)
            DEBUG(c_button_fmt, button_press[i], S.press_count[i], S.release_count[i], S.held[i].held);
    for (uint32_t i = 0; i < COMBO_OTHER_CODES && S.other_count[i]; ++i)
        DEBUG(c_other_fmt, S.other_code[i], S.other_count[i]);
    if (S.other_overflow)
        DEBUG(c_other_fmt, UINT32_MAX, S.other_overflow);
    DEBUG(c_pairs_fmt, S.overlaps, S.outside_window, S.extra_keys);
    for (uint32_t k = S.overlaps > COMBO_OVERLAPS ? S.overlaps - COMBO_OVERLAPS : 0u; k < S.overlaps; ++k) {
        const struct combo_overlap *o = &S.overlap[k % COMBO_OVERLAPS];
        DEBUG(c_overlap_fmt, o->first, o->second, o->ticks);
    }
    DEBUG(c_captures_fmt, S.captures, S.trigger_fired, S.fired[0], S.fired[1]);
    for (uint32_t k = S.captures > COMBO_CAPTURES ? S.captures - COMBO_CAPTURES : 0u; k < S.captures; ++k) {
        const struct combo_capture *c = &S.capture[k % COMBO_CAPTURES];
        DEBUG(c_capture_fmt, k + 1u, c->candidate, c->ticks, c->known, c->pass, c->gui_mode);
    }
    DEBUG(c_display_fmt, S.display_samples, S.snapshots, S.snapshots_suppressed, S.cur_flips);
    DEBUG(c_shot_fmt, S.counter_known, S.counter_context, S.counter_value, S.counter_change_tick, S.counter_faults);
    DEBUG(c_cue_fmt, capture_feedback.clicks, capture_feedback.bips, capture_feedback.skipped, capture_feedback.last_reason,
          capture_feedback.last_rc);
    {
        const struct capture_state *g = &capture_state;
        DEBUG(c_cap_counts_fmt, g->presses, g->repeats, g->debounced, g->attempts, g->published, capture_feedback.lost);
        DEBUG(c_cap_out_a_fmt, g->outcomes[1], g->outcomes[2], g->outcomes[3], g->outcomes[4], g->outcomes[5], g->outcomes[6]);
        DEBUG(c_cap_out_b_fmt, g->outcomes[7], g->outcomes[8], g->outcomes[9], g->outcomes[10], g->outcomes[11], g->next_number);
        DEBUG(c_cap_held_fmt, g->stale_held, g->outcomes[CAPTURE_REFUSED_BOOT]);
        DEBUG(c_cap_out_c_fmt, g->outcomes[CAPTURE_REFUSED_WRITING], g->outcomes[CAPTURE_REFUSED_MOVIE]);
        DEBUG(c_cap_out_d_fmt, g->outcomes[CAPTURE_REFUSED_DEVELOP]);
        uint32_t kept = capture_feedback.kept;
        for (uint32_t k = kept > CAPTURE_RESULTS ? kept - CAPTURE_RESULTS : 0u; k < kept; ++k) {
            const struct capture_result *r = &capture_feedback.last[k % CAPTURE_RESULTS];
            DEBUG(c_cap_res_fmt, r->seq, r->outcome, r->number, r->known, r->pass, r->gui_mode);
            DEBUG(c_cap_rc_fmt, r->seq, r->rc_create, r->rc_header, r->rc_data, r->rc_close, r->rc_flush, r->rc_remove);
            DEBUG(c_cap_desc_fmt, r->seq, r->marv, r->data, r->format, r->width, r->height);
            DEBUG(c_cap_misc_fmt, r->seq, r->probes, r->bytes, r->rc_map_header, r->sel, r->rc_mount);
            DEBUG(c_cap_time_fmt, r->seq, r->duration, r->rc_map_data);
            DEBUG(c_cap_us_fmt, r->seq, r->us_start, r->us_probed, r->us_create, r->us_header, r->us_data, r->us_close, r->us_flush);
            DEBUG(c_cap_job_fmt, r->seq, r->job_main, r->job_sub, r->rec_idle);
            if (r->yca_outcome) {
                DEBUG(c_cap_yca_fmt, r->seq, r->yca_outcome, r->rc_yca_remove, r->rc_yca_create, r->rc_yca_header, r->rc_yca_data,
                      r->rc_yca_close, r->rc_yca_flush, r->rc_yca_cleanup);
                DEBUG(c_cap_yca_us_fmt, r->seq, r->us_yca_start, r->us_yca_create, r->us_yca_header, r->us_yca_data, r->us_yca_flush);
            }
        }
    }
    DEBUG(c_trigger_fmt, COMBO_TRIGGER_CODE, S.trigger_presses, S.trigger_fired, S.trigger_debounced);
    /* Histogram: nonzero codes, five (code << 16 | count) words per row, zero padded. */
    {
        uint32_t w[5], n = 0u;
        for (uint32_t code = 0; code < COMBO_HIST_CODES; ++code) {
            if (!S.hist[code])
                continue;
            w[n++] = code << 16 | S.hist[code];
            if (n == 5u) {
                DEBUG(c_hist_fmt, w[0], w[1], w[2], w[3], w[4]);
                n = 0u;
            }
        }
        if (n) {
            for (uint32_t i = n; i < 5u; ++i)
                w[i] = 0u;
            DEBUG(c_hist_fmt, w[0], w[1], w[2], w[3], w[4]);
        }
    }
    DEBUG(c_hist_tail_fmt, S.hist_overflow, S.max_held);
    /* Sequence ring, oldest first: ten events per row, two per word (older in the high half). */
    {
        uint32_t count = S.seq_count < COMBO_SEQ_EVENTS ? S.seq_count : COMBO_SEQ_EVENTS;
        uint32_t first = S.seq_count - count;
        DEBUG(c_seq_head_fmt, first, count);
        for (uint32_t k = first; k < S.seq_count; ++k)
            if (!(k % COMBO_SEQ_ANCHOR))
                DEBUG(c_seq_anchor_fmt, k, S.seq_anchor[(k % COMBO_SEQ_EVENTS) / COMBO_SEQ_ANCHOR]);
        for (uint32_t k = first; k < S.seq_count; k += 10u) {
            uint32_t w[5];
            for (uint32_t i = 0; i < 5u; ++i) {
                uint32_t a = k + 2u * i, b = a + 1u;
                uint32_t hi = a < S.seq_count ? S.seq[a % COMBO_SEQ_EVENTS] : 0u;
                uint32_t lo = b < S.seq_count ? S.seq[b % COMBO_SEQ_EVENTS] : 0u;
                w[i] = hi << 16 | lo;
            }
            DEBUG(c_seq_fmt, k, w[0], w[1], w[2], w[3], w[4]);
        }
    }
    /* Matrix: slot codes, then per used slot its overlap counts and min skews (bytes). */
    {
        /* Unused code bytes are 0 (BSS); a slot's skew row and column are set to
         * COMBO_MATRIX_NONE for all 16 entries when the slot is created. */
        const uint8_t *m = S.matrix_code;
        DEBUG(c_map_fmt, pack4(m), pack4(m + 4), pack4(m + 8), pack4(m + 12), S.matrix_used, S.matrix_overflow);
        for (uint32_t i = 0; i < S.matrix_used; ++i) {
            const uint8_t *n = S.matrix_count[i], *k = S.matrix_skew[i];
            if (!(pack4(n) | pack4(n + 4) | pack4(n + 8) | pack4(n + 12)))
                continue;
            DEBUG(c_mcount_fmt, i, pack4(n), pack4(n + 4), pack4(n + 8), pack4(n + 12));
            DEBUG(c_mskew_fmt, i, pack4(k), pack4(k + 4), pack4(k + 8), pack4(k + 12));
        }
    }
    inject_report();
    one_press_report();
    DEBUG(c_end_fmt, reports);
}
