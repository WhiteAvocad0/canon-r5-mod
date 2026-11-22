/* One-press screenshot JPEG v1 (one_press.h owns the design and the evidence).
 * Host-testable through one_press_api (COMBO_OBSERVE_HOST). */
#include "one_press.h"
#include "combo_observe.h"
#include "hw_jpeg_inject.h"
#include "observer.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

struct one_press_state one_press_state;
uint8_t one_press_block[OP_BLOCK_BYTES] __attribute__((aligned(32)));
static uint32_t op_fm_buffer[8] __attribute__((aligned(32))); /* 32 B for PTP's 20-byte info */
#define S one_press_state

/* The block Canon's GUI passed to INDEV_DevelopStart for a stock S2 Resize of a
 * 3:2 JPEG (seen on camera: 20 nonzero words; every other word 0).
 * +0x04 = 4 (Resize), +0x5C bytes 0 / 15 (S2) / 0xFF, +0x60 = 3 (quality). */
static const uint16_t op_block_offsets[20] DATA = {0x04, 0x1c, 0x20, 0x24, 0x2c, 0x38, 0x3c, 0x40, 0x44, 0x4c,
                                                   0x58, 0x5c, 0x60, 0x64, 0x68, 0x70, 0x78, 0xd4, 0x104, 0x108};
static const uint32_t op_block_words[20] DATA = {4u, 0x157cu, 1u, 0x81u, 4u, 0xffffu, 0xffffu, 2u, 3u, 1u,
                                                 3u, 0x00ff0f00u, 3u, 1u, 1u, 1u, 3u, 1u, 1u, 1u};

static const char op_state_fmt[] DATA = CC_FMT_OP_STATE;
static const char op_more_fmt[] DATA = CC_FMT_OP_MORE;
static const char op_last_fmt[] DATA = CC_FMT_OP_LAST;
static const char op_check_fmt[] DATA = CC_FMT_OP_CHECK;
static const char op_fm_fmt[] DATA = CC_FMT_OP_FM;
static const char op_job_fmt[] DATA = CC_FMT_OP_JOB;
static const char op_view_fmt[] DATA = CC_FMT_OP_VIEW;

#ifndef COMBO_OBSERVE_HOST
CODE static uint32_t op_read(uint32_t address)
{
    return *(const volatile uint32_t *)(uintptr_t)address;
}
CODE static uint32_t op_timer(void)
{
    return ~*(volatile const uint32_t *)COMBO_LOG_TIMER_REGISTER;
}
const struct one_press_api one_press_api DATA = {
    op_read, op_timer, (int32_t (*)(uint32_t, uint32_t *))OP_FM_RESIZE_INFO,
    (void (*)(void))OP_GUI_RELEASE_FILE, (void (*)(uint32_t))OP_SET_GUI_BUSY, (void (*)(uint32_t))OP_SET_INDEV_BUSY,
    (uint32_t (*)(void))OP_GUI_BUSY, (uint32_t (*)(void))OP_INDEV_BUSY,
    (void (*)(uint32_t, void *, op_cb_fn, op_cb_fn, uint32_t))OP_INDEV_START,
    (void (*)(uint32_t, void *, op_cb_fn))OP_INDEV_END,
};
#endif

#define A one_press_api

CODE static void op_start_stub(uint32_t message)
{
    (void)message; /* type 4 never calls startCBR (E097D19A..E097D408); non-null to be safe */
}

CODE uint32_t one_press_busy(void)
{
    if (__atomic_load_n(&S.state, __ATOMIC_ACQUIRE) == OP_IDLE)
        return 0u;
    ++S.refused;
    return 1u;
}

/* Canon's Resize source check with PTP's rules (E068DCFA..E068DD5E): 1 = resizable;
 * op_fm_buffer keeps the info. */
CODE static uint32_t op_resizable(uint32_t handle, int32_t *rc)
{
    for (uint32_t i = 0; i < 8u; ++i)
        op_fm_buffer[i] = 0u;
    *rc = A.fm_resize_info(handle, op_fm_buffer);
    uint32_t type = op_fm_buffer[0] & 0xffffu, size = op_fm_buffer[0] >> 16;
    return !(*rc & 1) && (type & 4u) && size != 2u && size != 15u && size != 16u && size != 0xffu;
}

CODE static void refuse(uint32_t outcome)
{
    S.outcome = outcome;
    __atomic_store_n(&S.refusals, S.refusals + 1u, __ATOMIC_RELEASE);
}

CODE void one_press_arm(uint32_t gui_mode, uint32_t number, uint32_t tick, uint32_t y22_ready, uint32_t pic_outcome, uint32_t pic_bytes)
{
    S.number = number;
    S.gui_mode = gui_mode;
    S.pic_outcome = pic_outcome;
    S.pic_bytes = pic_bytes;
    S.pending_tick = tick;
    S.armed = 0u;
    if (gui_mode != OP_GUI_PLAYBACK) {
        S.outcome = OP_NOT_PLAYBACK;
        return;
    }
    ++S.attempts;
    S.dir = S.file = S.handle = 0u;
    S.rc_open = S.rc_read = S.fm_rc = 0;
    for (uint32_t i = 0; i < OP_FM_WORDS; ++i)
        S.fm_info[i] = 0u;
    S.source = OP_SOURCE_NONE;
    S.view_handle = S.view_info0 = S.view_info2 = 0u;
    S.view_fm_rc = 0;
    if (!y22_ready) {
        refuse(OP_NO_Y22);
        return;
    }
    if (S.latched) {
        refuse(OP_LATCHED);
        return;
    }
    S.outcome = OP_NONE;
    S.armed = 1u;
}

CODE void one_press_release(void)
{
    if (!S.armed)
        return;
    S.armed = 0u;
    if (__atomic_load_n(&S.state, __ATOMIC_ACQUIRE) != OP_IDLE) {
        refuse(OP_BUSY);
        return;
    }
    if (A.read(COMBO_GUI_MODE_WORD) != OP_GUI_PLAYBACK) { /* ▶ left since the press */
        refuse(OP_NOT_PLAYBACK);
        return;
    }
    /* Canon's GUI source: the shown photo, checked on its JPEG sub-handle. */
    S.view_handle = A.read(OP_GUI_VIEW_HANDLE);
    if (!S.view_handle) {
        refuse(OP_NO_VIEW);
        return;
    }
    uint32_t view_ok = op_resizable(S.view_handle | OP_SUB_JPEG, &S.view_fm_rc);
    S.view_info0 = op_fm_buffer[0];
    S.view_info2 = op_fm_buffer[2];
    if (!view_ok) {
        refuse(OP_VIEW_INVALID);
        return;
    }
    S.source = OP_SOURCE_VIEW;
    /* Canon's own starter requires its develop state idle; neither busy flag may be set. */
    S.develop_state = A.read(OP_GUI_DEVELOP_STATE);
    S.gui_busy = A.gui_busy();
    S.indev_busy = A.indev_busy();
    if (S.develop_state || S.gui_busy || S.indev_busy) {
        refuse(OP_BUSY);
        return;
    }
    for (uint32_t i = 0; i < OP_BLOCK_BYTES / 4u; ++i)
        ((uint32_t *)one_press_block)[i] = 0u;
    for (uint32_t i = 0; i < 20u; ++i)
        *(uint32_t *)(one_press_block + op_block_offsets[i]) = op_block_words[i];
    S.dry_seen = S.dry_ok = S.dry_wh = 0u;
    S.done_result = S.end_arg = S.end_reason = 0u;
    S.us_done = S.us_end = 0u;
    S.start_tick = __atomic_load_n(&combo_worker_tick, __ATOMIC_RELAXED);
    S.outcome = OP_START;
    ++S.jobs;
    /* Canon's order (E0719520..E0719574): release, busy flags, start. */
    A.release_file();
    A.set_gui_busy(1u);
    A.set_indev_busy(1u);
    S.us_start = A.timer_us();
    __atomic_store_n(&S.state, OP_STARTED, __ATOMIC_SEQ_CST); /* before INDEV can call back */
    A.start(S.view_handle, one_press_block, op_start_stub, one_press_done, 0u);
}

CODE uint32_t one_press_dry_wanted(void)
{
    return __atomic_load_n(&S.state, __ATOMIC_ACQUIRE) == OP_STARTED;
}

CODE void one_press_dry(uint32_t ok, uint32_t width, uint32_t height)
{
    S.dry_wh = width << 16 | (height & 0xffffu);
    __atomic_store_n(&S.dry_ok, ok, __ATOMIC_RELAXED);
    __atomic_store_n(&S.dry_seen, S.dry_seen + 1u, __ATOMIC_RELEASE);
}

CODE void one_press_done(uint32_t result)
{
    S.us_done = A.timer_us();
    S.done_result = result;
    A.set_indev_busy(0u); /* as Canon's doneCBR E0719404..E071940C */
    uint32_t expected = OP_STARTED, reason;
    if (!__atomic_compare_exchange_n(&S.state, &expected, OP_ENDING, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        ++S.late;
        reason = OP_END_LATE; /* after the timeout: never save */
    } else if (!result) {
        reason = OP_END_RESULT_ZERO;
    } else if (!__atomic_load_n(&S.dry_seen, __ATOMIC_ACQUIRE)) {
        reason = OP_END_NO_DRY;
    } else if (!__atomic_load_n(&S.dry_ok, __ATOMIC_RELAXED)) {
        reason = OP_END_DRY_GEOMETRY;
    } else if (!__atomic_load_n(&inject_pending.pending, __ATOMIC_ACQUIRE) ||
               __atomic_load_n(&inject_pending.set_tick, __ATOMIC_RELAXED) != S.pending_tick) {
        reason = OP_END_PENDING;
    } else {
        reason = OP_END_SAVE;
    }
    S.end_reason = reason;
    if (reason == OP_END_SAVE)
        ++S.saves;
    else
        ++S.cancels;
    /* Canon's RequestPlayDevelopEnd (E0719D22) clears these two words before its End (seen in its H8 block on camera). */
    *(uint32_t *)(one_press_block + 0x70u) = 0u;
    *(uint32_t *)(one_press_block + 0xd4u) = 0u;
    A.set_indev_busy(1u); /* as Canon's End path E0719DE6: busy through the save */
    A.end(reason == OP_END_SAVE, one_press_block, one_press_end);
}

CODE void one_press_end(uint32_t arg)
{
    S.us_end = A.timer_us();
    S.end_arg = arg;
    ++S.ends;
    A.set_indev_busy(0u); /* as Canon's endCBR E071931C..E0719322 */
    A.set_gui_busy(0u);
    __atomic_store_n(&S.state, OP_IDLE, __ATOMIC_RELEASE);
}

CODE void one_press_step(uint32_t tick)
{
    uint32_t st = __atomic_load_n(&S.state, __ATOMIC_ACQUIRE);
    if (st == OP_IDLE || tick - S.start_tick < OP_TIMEOUT_TICKS)
        return;
    if (st == OP_GAVE_UP)
        return;
    if (!__atomic_compare_exchange_n(&S.state, &st, OP_GAVE_UP, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        return; /* a callback moved it meanwhile: next tick */
    S.latched = 1u;
    ++S.timeouts;
    /* Both busy flags stay set; playback stays inert (no decode while
     * INDEV may hold its memory map) until a late callback or power-off. */
}

#define DEBUG(...) observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, __VA_ARGS__)

CODE void one_press_report(void)
{
    DEBUG(op_state_fmt, S.state, S.latched, S.attempts, S.jobs, S.saves, S.cancels, S.ends, S.timeouts);
    DEBUG(op_more_fmt, S.refused, S.late, S.start_tick, S.end_arg);
    DEBUG(op_last_fmt, S.outcome, S.number, S.gui_mode, S.dir, S.file, S.handle, S.pending_tick);
    DEBUG(op_check_fmt, S.rc_open, S.rc_read, S.fm_rc, S.develop_state, S.gui_busy, S.indev_busy, S.pic_outcome, S.pic_bytes);
    DEBUG(op_fm_fmt, S.fm_info[0], S.fm_info[1], S.fm_info[2], S.fm_info[3], S.fm_info[4]);
    DEBUG(op_view_fmt, S.source, S.view_handle, S.view_fm_rc, S.view_info0, S.view_info2);
    DEBUG(op_job_fmt, S.us_start, S.us_done, S.us_end, S.done_result, S.end_reason, S.dry_seen, S.dry_ok, S.dry_wh);
}
