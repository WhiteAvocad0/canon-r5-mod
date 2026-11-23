/* One-press screenshot JPEG v1 (one_press.h owns the design and the evidence).
 * Host-testable through one_press_api (COMBO_OBSERVE_HOST). */
#include "one_press.h"
#include "combo_observe.h"
#include "hw_jpeg_inject.h"
#include "observer.h"
#ifndef COMBO_OBSERVE_HOST
#include "playback_box.h"
#include "playback_native.h"
#endif
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
struct one_press_defer one_press_defer;
#define D one_press_defer
_Static_assert(OP_DEFER_MAX_AGE_TICKS + OP_TIMEOUT_TICKS == INJECT_PENDING_TICKS, "a deferred job's H3 never meets an expired screenshot");
_Static_assert(OP_VIEW_XPAN != OP_NO_VIEW && OP_SOURCE_LAST_GOOD != OP_SOURCE_VIEW, "distinct codes");
_Static_assert(OP_INFO_RELEASE == COMBO_INFO_RELEASE && OP_INFO_PRESS + 1u == OP_INFO_RELEASE, "INFO codes (COMBO_BUTTONS)");
_Static_assert(OP_UIOPE_KEY_HANDLER == COMBO_UIOPE_MODE + 4u && OP_GUI_QUEUE == COMBO_GUI_CONTEXT, "Canon words");
uint32_t one_press_clear_visit; /* S4 (one_press.h); writer: the worker */
uint32_t one_press_auto;        /* the automatic INFO's pending press (one_press.h OP_AUTO_*); writer: the worker */
uint32_t one_press_rel_mark;     /* our posted release's slot range and verdict (one_press.h OP_REL_*) */
uint32_t one_press_visit_gen;   /* [0x5474] when the worker first saw this playback visit (S4) */
_Static_assert(OP_GUI_POST_INDEX == OP_GUI_QUEUE + 0x14u && OP_GUI_SLOT_COUNT - 1u <= OP_REL_INDEX_MASK, "Canon's post ring");
#ifdef COMBO_OBSERVE_HOST
uintptr_t one_press_host_slots;            /* the host's slot ring (Canon: OP_GUI_SLOTS) */
void (*one_press_host_before_claim)(void); /* host seam: GuiMainTask may run between the worker's checks and its claim */
#define OP_SLOTS one_press_host_slots
#else
#define OP_SLOTS ((uintptr_t)OP_GUI_SLOTS)
#endif

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
static const char op_defer_fmt[] DATA = CC_FMT_OP_DEFER;
static const char op_auto_fmt[] DATA = CC_FMT_OP_AUTO;

#ifndef COMBO_OBSERVE_HOST
CODE static uint32_t op_read(uint32_t address)
{
    return *(const volatile uint32_t *)(uintptr_t)address;
}
CODE static uint32_t op_timer(void)
{
    return ~*(volatile const uint32_t *)COMBO_LOG_TIMER_REGISTER;
}
/* The shown image's AspectInfo tuple, through playback_native.c's admitted cache
 * snapshot (0x16E28, its fields re-checked against Canon's live words): only an exact
 * XPan tuple (playback_box.c) is YES; no admitted tuple is UNKNOWN. Read only. */
CODE static uint32_t op_view_xpan(void)
{
    struct playback_tag_snapshot s;
    if (!playback_native_cache_snapshot(&s))
        return OP_XPAN_UNKNOWN;
    return playback_box_kind(&s.facts) == PB_BOX_UNKNOWN ? OP_XPAN_NO : OP_XPAN_YES;
}
const struct one_press_api one_press_api DATA = {
    op_read, op_timer, (int32_t (*)(uint32_t, uint32_t *))OP_FM_RESIZE_INFO,
    (void (*)(void))OP_GUI_RELEASE_FILE, (void (*)(uint32_t))OP_SET_GUI_BUSY, (void (*)(uint32_t))OP_SET_INDEV_BUSY,
    (uint32_t (*)(void))OP_GUI_BUSY, (uint32_t (*)(void))OP_INDEV_BUSY,
    (void (*)(uint32_t, void *, op_cb_fn, op_cb_fn, uint32_t))OP_INDEV_START,
    (void (*)(uint32_t, void *, op_cb_fn))OP_INDEV_END,
    op_view_xpan,
    (void (*)(uint32_t, uint32_t, uint32_t, uint32_t))OP_GUI_POST,
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

/* Mark this capture's pending HWJPG.Y22 as a deferred screenshot (INFO in playback starts it). */
CODE static void op_keep(void)
{
    D.defer = OP_DEFER_WAIT;
    D.held_since = 0u; /* the first INFO of any settled playback visit may try */
    ++D.marked;
}

CODE void one_press_arm(uint32_t gui_mode, uint32_t number, uint32_t tick, uint32_t y22_ready, uint32_t pic_outcome, uint32_t pic_bytes)
{
    S.number = number;
    S.gui_mode = gui_mode;
    S.pic_outcome = pic_outcome;
    S.pic_bytes = pic_bytes;
    S.pending_tick = tick;
    S.armed = 0u;
    D.defer = OP_DEFER_NONE; /* latest only: this capture replaces a deferred one */
    ++D.decisions;
    if (gui_mode != OP_GUI_PLAYBACK) {
        S.outcome = OP_NOT_PLAYBACK;
        if (!y22_ready)
            refuse(OP_NO_Y22); /* the screen files are saved, but no JPEG can follow */
        else if (S.latched)
            refuse(OP_LATCHED);
        else
            op_keep();
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

/* Playback's source (GuiMainTask): the shown photo, checked on its JPEG sub-handle,
 * unless the playback cache says it is XPan; otherwise the last good source of this
 * boot. Returns the group handle to start on (S.source says which), or 0 after a
 * refusal (two bips; the screenshot stays pending). */
CODE static uint32_t op_source(void)
{
    uint32_t why = OP_NO_VIEW, ok = 0u;
    S.source = OP_SOURCE_NONE;
    S.view_fm_rc = 0;
    S.view_info0 = S.view_info2 = 0u;
    S.view_handle = A.read(OP_GUI_VIEW_HANDLE);
    D.xpan = OP_XPAN_UNKNOWN; /* no view: not checked */
    if (S.view_handle) {
        D.xpan = A.view_xpan();
        if (D.xpan == OP_XPAN_YES) {
            why = OP_VIEW_XPAN;
        } else {
            ok = op_resizable(S.view_handle | OP_SUB_JPEG, &S.view_fm_rc);
            S.view_info0 = op_fm_buffer[0];
            S.view_info2 = op_fm_buffer[2];
            why = OP_VIEW_INVALID;
        }
    }
    if (ok) {
        S.source = OP_SOURCE_VIEW;
        return S.view_handle;
    }
    if (D.last_good && !(D.xpan == OP_XPAN_YES && D.last_good == S.view_handle) && /* a reused file number */
        op_resizable(D.last_good | OP_SUB_JPEG, &D.last_good_rc)) {
        S.source = OP_SOURCE_LAST_GOOD;
        return D.last_good;
    }
    refuse(why);
    return 0u;
}

/* Canon's own starter requires its develop state idle; neither busy flag may be set. */
CODE static uint32_t op_canon_busy(void)
{
    S.develop_state = A.read(OP_GUI_DEVELOP_STATE);
    S.gui_busy = A.gui_busy();
    S.indev_busy = A.indev_busy();
    return S.develop_state || S.gui_busy || S.indev_busy;
}

CODE static void op_start(uint32_t handle)
{
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
    if (S.source == OP_SOURCE_LAST_GOOD)
        ++D.fallbacks;
    /* Canon's order (E0719520..E0719574): release, busy flags, start. */
    A.release_file();
    A.set_gui_busy(1u);
    A.set_indev_busy(1u);
    S.us_start = A.timer_us();
    __atomic_store_n(&S.state, OP_STARTED, __ATOMIC_SEQ_CST); /* before INDEV can call back */
    A.start(handle, one_press_block, op_start_stub, one_press_done, 0u);
}

CODE void one_press_release(void)
{
    uint32_t armed = S.armed;
    if (!armed)
        return;
    S.armed = 0u;
    /* Every refusal below keeps this capture's HWJPG.Y22 as a deferred screenshot. */
    if (__atomic_load_n(&S.state, __ATOMIC_ACQUIRE) != OP_IDLE) {
        refuse(OP_BUSY);
    } else if (A.read(COMBO_GUI_MODE_WORD) != OP_GUI_PLAYBACK) { /* ▶ left since the press */
        refuse(OP_NOT_PLAYBACK);
    } else {
        uint32_t handle = op_source(); /* refuses itself when 0 */
        if (handle && op_canon_busy())
            refuse(OP_BUSY);
        else if (handle) {
            op_start(handle);
            return;
        }
    }
    op_keep();
}

/* The index of Canon's GUI event slot `slot` points into (the message GuiMainTask
 * dispatches, E04A7782), or OP_GUI_SLOT_COUNT for any other pointer. */
CODE static uint32_t op_slot_index(uintptr_t slot)
{
    uintptr_t d = slot - OP_SLOTS;
    return slot >= OP_SLOTS && !(d & 15u) && d / 16u < OP_GUI_SLOT_COUNT ? (uint32_t)(d / 16u) : OP_GUI_SLOT_COUNT;
}

CODE static uint32_t op_slot_after(uint32_t a, uint32_t b) /* posts from slot b to slot a, around the ring (both < count) */
{
    return a >= b ? a - b : a + OP_GUI_SLOT_COUNT - b;
}

CODE static uint32_t op_post_index(void) /* Canon's next slot index (always < OP_GUI_SLOT_COUNT; no libgcc division) */
{
    uint32_t i = A.read(OP_GUI_POST_INDEX);
    return i < OP_GUI_SLOT_COUNT ? i : 0u;
}

/* S4: 1 when Canon may be leaving playback: a queue clear the drain saw in this visit,
 * one pending now, [0x5474] changed since the worker first saw the visit, or a hook
 * record not drained yet that saw a clear. Read only. */
CODE static uint32_t op_leaving(uint32_t since, uint32_t undrained)
{
    uint32_t gen = A.read(OP_GUI_QUEUE);
    return (since && __atomic_load_n(&one_press_clear_visit, __ATOMIC_RELAXED) == since) ||
           gen != A.read(OP_GUI_QUEUE + 8u) || gen != __atomic_load_n(&one_press_visit_gen, __ATOMIC_RELAXED) ||
           (undrained & COMBO_UNDRAINED_CLEAR);
}

/* GuiMainTask, an INFO release dispatched from slot `slot`: 1 when it is our posted
 * release and must start nothing (a held verdict, or a user key recorded since our
 * press that the drain has not handled yet). Our release is identified by its slot
 * index, never by its content; a mark of one exact slot is consumed here. */
CODE static uint32_t op_release_vetoed(uintptr_t slot, uint32_t undrained)
{
    uint32_t mark = __atomic_load_n(&one_press_rel_mark, __ATOMIC_ACQUIRE), idx = op_slot_index(slot);
    if (!(mark & OP_REL_ARMED) || idx == OP_GUI_SLOT_COUNT)
        return 0u;
    uint32_t lo = mark & OP_REL_INDEX_MASK, n = (mark >> OP_REL_COUNT_SHIFT) & OP_REL_INDEX_MASK;
    uint32_t span = n ? n : op_slot_after(op_post_index(), lo); /* not narrowed yet: every slot posted since */
    if (op_slot_after(idx, lo) >= span)
        return 0u; /* not ours: an INFO pressed by hand */
    if (n == 1u)
        __atomic_compare_exchange_n(&one_press_rel_mark, &mark, 0u, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    return !(mark & OP_REL_OK) || (undrained & COMBO_UNDRAINED_USER_KEY);
}

/* GuiMainTask, an INFO release (combo_capture_gui, from the H2 hook): the deferred
 * screenshot's start (one_press.h). Busy, settling or outside playback only wait; a
 * pending Y22 that is gone or too old drops the deferral. */
CODE void one_press_key(uint32_t code, uintptr_t slot)
{
    if (code != COMBO_INFO_RELEASE) /* INFO only: SET confirms ▶ dialogs */
        return;
    uint32_t undrained = combo_undrained(); /* hook records this INFO release has seen and the worker has not */
    uint32_t vetoed = op_release_vetoed(slot, undrained);
    if (D.defer != OP_DEFER_WAIT)
        return;
    /* The visit stamp first, then the tick: a stamp the worker publishes in between is
     * ahead of the tick and counts as unsettled (signed difference), never as old. */
    uint32_t since = __atomic_load_n(&D.play_since, __ATOMIC_ACQUIRE);
    uint32_t tick = __atomic_load_n(&combo_worker_tick, __ATOMIC_ACQUIRE), why;
    ++D.decisions;
    if (vetoed) {
        why = OP_DW_SETTLING; /* our release, held: wait, INFO by hand */
    } else if (S.latched) {
        D.defer = OP_DEFER_NONE;
        why = OP_DW_LATCHED;
    } else if (__atomic_load_n(&S.state, __ATOMIC_ACQUIRE) != OP_IDLE) {
        why = OP_DW_BUSY;
    } else if (A.read(COMBO_GUI_MODE_WORD) != OP_GUI_PLAYBACK || !since) { /* re-read at this release */
        why = OP_DW_NOT_PLAYBACK;
    } else if (op_leaving(since, undrained)) {
        why = OP_DW_NOT_PLAYBACK; /* S4: wait as outside playback (no job, no bips, the deferral kept) */
    } else if ((int32_t)(tick - since) < (int32_t)OP_DEFER_SETTLE_TICKS) {
        why = OP_DW_SETTLING;
    } else if (since == D.held_since) {
        why = OP_DW_HELD;
    } else if (!__atomic_load_n(&inject_pending.pending, __ATOMIC_ACQUIRE) ||
               __atomic_load_n(&inject_pending.set_tick, __ATOMIC_RELAXED) != S.pending_tick) {
        D.defer = OP_DEFER_NONE;
        why = OP_DW_LOST;
    } else if (tick - S.pending_tick > OP_DEFER_MAX_AGE_TICKS) {
        D.defer = OP_DEFER_NONE;
        why = OP_DW_EXPIRED;
    } else if (op_canon_busy()) {
        why = OP_DW_BUSY;
    } else {
        uint32_t handle = op_source(), expect = OP_DEFER_WAIT;
        if (!handle) {
            D.held_since = since; /* two bips once; the next playback visit retries */
            why = OP_DW_NO_SOURCE;
        } else if (op_canon_busy()) { /* again after the FM calls: only wait */
            why = OP_DW_BUSY;
        } else if (!__atomic_compare_exchange_n(&D.defer, &expect, OP_DEFER_NONE, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            why = OP_DW_BUSY; /* the worker is posting its INFO press this instant: wait */
        } else {
            ++D.starts;
            why = OP_DW_STARTED;
            op_start(handle);
        }
    }
    D.why = why;
}

/* worker: our INFO release, once the GUI queue is quiet. The verdict and the slot range
 * are armed before the post (GuiMainTask may dispatch it at once), the range narrowed to
 * the slots actually posted meanwhile right after it. */
CODE static void op_post_release(uint32_t tick, uint32_t state)
{
    uint32_t press = state >> OP_AUTO_TICK_SHIFT, waited = (tick - press) & OP_AUTO_TICK_MASK;
    if (waited < OP_AUTO_RELEASE_TICKS)
        return;
    uint32_t depth = A.read(OP_GUI_QUEUE + 4u), quiet = A.read(OP_GUI_QUEUE) == A.read(OP_GUI_QUEUE + 8u) && depth <= OP_GUI_QUEUE_DEPTH_MAX;
    if (!quiet && (waited < OP_AUTO_RELEASE_TICKS + OP_AUTO_RELEASE_WAIT_TICKS || depth > OP_GUI_QUEUE_DEPTH_MAX)) {
        D.auto_why = OP_AI_RELEASE_WAIT; /* never post into a busy queue (a full one asserts: Err 70) */
        return;
    }
    uint32_t since = __atomic_load_n(&D.play_since, __ATOMIC_RELAXED);
    uint32_t ok = quiet && waited == OP_AUTO_RELEASE_TICKS &&                                   /* not delayed */
                  since && ((press - since) & OP_AUTO_TICK_MASK) - 1u < (OP_AUTO_TICK_MASK >> 1) && /* the visit began before the press */
                  (state & (OP_AUTO_SEEN | OP_AUTO_USER_KEY)) == OP_AUTO_SEEN &&                 /* ours dispatched, no user key */
                  A.read(OP_UIOPE_KEY_HANDLER) == OP_UIOPE_POST_DIRECT && A.read(COMBO_GUI_MODE_WORD) == OP_GUI_PLAYBACK &&
                  __atomic_load_n(&one_press_clear_visit, __ATOMIC_RELAXED) != since &&
                  A.read(OP_GUI_QUEUE) == __atomic_load_n(&one_press_visit_gen, __ATOMIC_RELAXED) &&
                  !combo_undrained() && !S.latched && __atomic_load_n(&S.state, __ATOMIC_ACQUIRE) == OP_IDLE;
    if (!ok)
        D.auto_why = OP_AI_RELEASE_HELD;
    uint32_t lo = op_post_index(), base = OP_REL_ARMED | (ok ? OP_REL_OK : 0u) | lo;
    __atomic_store_n(&one_press_rel_mark, base, __ATOMIC_RELEASE);
    __atomic_store_n(&one_press_auto, 0u, __ATOMIC_RELEASE);
    A.gui_post(0u, OP_INFO_RELEASE, 0u, 1u);
    uint32_t n = op_slot_after(op_post_index(), lo);
    if (n) /* ours is one of the n slots from lo (n = 1: exactly lo) */
        __atomic_compare_exchange_n(&one_press_rel_mark, &base, base | n << OP_REL_COUNT_SHIFT, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
}

/* worker (one_press_step, after the playback-visit update): at most one INFO press, and
 * later its release, per deferral through Canon's GUI post, only in settled, quiet
 * playback with every one_press_key precheck clear (one_press.h "Automatic INFO"). The
 * posted release starts the job on GuiMainTask through the INFO path; besides the two
 * posts nothing here calls or writes Canon. */
CODE static void op_auto_info(uint32_t tick)
{
    uint32_t rel = __atomic_load_n(&one_press_rel_mark, __ATOMIC_ACQUIRE);
    if ((rel & OP_REL_ARMED) && ((rel >> OP_REL_COUNT_SHIFT) & OP_REL_INDEX_MASK) &&
        op_slot_after(op_post_index(), rel & OP_REL_INDEX_MASK) >= OP_REL_FORGET_POSTS)
        __atomic_compare_exchange_n(&one_press_rel_mark, &rel, 0u, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE); /* long dispatched (or dropped) */
    uint32_t state = __atomic_load_n(&one_press_auto, __ATOMIC_ACQUIRE);
    if ((state & OP_AUTO_STATE) == OP_AUTO_PRESSED) { /* the release: a later tick, re-checked, always posted */
        op_post_release(tick, state);
        return;
    }
    uint32_t marked = D.marked; /* GuiMainTask stores defer = WAIT before ++marked */
    if (D.defer != OP_DEFER_WAIT || D.auto_for == marked)
        return; /* nothing deferred, or this deferral already handled: decision frozen */
    uint32_t since = __atomic_load_n(&D.play_since, __ATOMIC_RELAXED), why, undrained = combo_undrained();
    uint32_t gui = A.read(COMBO_GUI_MODE_WORD); /* re-read right before any post */
    if (S.gui_mode == OP_GUI_PLAYBACK) {
        why = OP_AI_KEPT; /* a playback RATE's kept deferral: INFO by hand only */
    } else if (S.latched) {
        why = OP_AI_LATCHED;
    } else if (__atomic_load_n(&S.state, __ATOMIC_ACQUIRE) != OP_IDLE) {
        why = OP_AI_BUSY;
    } else if (gui != OP_GUI_PLAYBACK || !since) {
        why = OP_AI_NOT_PLAYBACK;
    } else if (__atomic_load_n(&one_press_clear_visit, __ATOMIC_RELAXED) == since || (undrained & COMBO_UNDRAINED_CLEAR) ||
               A.read(OP_GUI_QUEUE) != __atomic_load_n(&one_press_visit_gen, __ATOMIC_RELAXED)) {
        why = OP_AI_LEAVING; /* S4 (a pending clear: QUEUE_BUSY below) */
    } else if ((int32_t)(tick - since) < (int32_t)OP_AUTO_SETTLE_TICKS) {
        why = OP_AI_SETTLING;
    } else if (since == D.held_since) {
        why = OP_AI_HELD;
    } else if (!__atomic_load_n(&inject_pending.pending, __ATOMIC_ACQUIRE) ||
               __atomic_load_n(&inject_pending.set_tick, __ATOMIC_RELAXED) != S.pending_tick) {
        why = OP_AI_LOST;
    } else if (tick - S.pending_tick > OP_DEFER_MAX_AGE_TICKS) {
        why = OP_AI_EXPIRED;
    } else if (A.read(OP_GUI_DEVELOP_STATE) || A.gui_busy() || A.indev_busy()) {
        why = OP_AI_CANON_BUSY; /* the getters only (op_canon_busy's S fields belong to GuiMainTask) */
    } else if (A.read(OP_UIOPE_KEY_HANDLER) != OP_UIOPE_POST_DIRECT) {
        why = OP_AI_KEYS_HELD;
    } else if (A.read(OP_GUI_QUEUE) != A.read(OP_GUI_QUEUE + 8u) || A.read(OP_GUI_QUEUE + 4u) > OP_GUI_QUEUE_DEPTH_MAX) {
        why = OP_AI_QUEUE_BUSY;
    } else if (combo_observe_state.seq_count && (int32_t)(tick - combo_observe_state.seq_last_tick) < (int32_t)OP_AUTO_QUIET_TICKS) {
        why = OP_AI_GUI_EVENTS; /* Canon's playback-entry events not finished (the drain's words, same task) */
    } else if (undrained & COMBO_UNDRAINED_USER_KEY) {
        why = OP_AI_GUI_EVENTS; /* a key the hook saw after this tick's drain: the next drain cancels the post */
    } else {
        why = OP_AI_POSTED;
    }
    D.auto_why = why;
    if (why != OP_AI_POSTED)
        return;
#ifdef COMBO_OBSERVE_HOST
    if (one_press_host_before_claim)
        one_press_host_before_claim();
#endif
    /* Claim the deferral: an INFO by hand that consumed it first wins (no post). */
    uint32_t expect = OP_DEFER_WAIT;
    if (!__atomic_compare_exchange_n(&D.defer, &expect, OP_DEFER_WAIT | OP_DEFER_CLAIM, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        D.auto_why = OP_AI_CONSUMED;
        return;
    }
    if (D.marked == marked) {
        D.auto_for = marked; /* handled before the post: one post per deferral whatever follows */
        D.auto_gui = gui;
        D.auto_ticks = tick - since;
        ++D.auto_posts;
        __atomic_store_n(&one_press_auto, (tick << OP_AUTO_TICK_SHIFT) | OP_AUTO_PRESSED, __ATOMIC_RELEASE);
        A.gui_post(0u, OP_INFO_PRESS, 0u, 1u); /* exactly Canon's mode-2 key post (E08ABFF4) */
    } else {
        D.auto_why = OP_AI_CONSUMED; /* a newer capture: decide again next tick */
    }
    expect = OP_DEFER_WAIT | OP_DEFER_CLAIM;
    __atomic_compare_exchange_n(&D.defer, &expect, OP_DEFER_WAIT, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
}

/* worker, combo_observe.c drain: a user key the hook recorded at `tick` (code 0: lost
 * hook records). While our press waits for its release, the first INFO press is ours
 * (SEEN) and any other key marks the gap (USER_KEY); after an OK release verdict it
 * holds that release; before the post, a key after playback entry (other than the
 * playback press itself) handles the deferral without a post. */
CODE void one_press_user_key(uint32_t tick, uint32_t code)
{
    uint32_t rel = __atomic_load_n(&one_press_rel_mark, __ATOMIC_ACQUIRE);
    while ((rel & (OP_REL_ARMED | OP_REL_OK)) == (OP_REL_ARMED | OP_REL_OK) &&
           !__atomic_compare_exchange_n(&one_press_rel_mark, &rel, rel & ~OP_REL_OK, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
    }
    uint32_t st = __atomic_load_n(&one_press_auto, __ATOMIC_RELAXED);
    if ((st & OP_AUTO_STATE) == OP_AUTO_PRESSED) {
        st |= (code == OP_INFO_PRESS && !(st & OP_AUTO_SEEN)) ? OP_AUTO_SEEN : OP_AUTO_USER_KEY;
        __atomic_store_n(&one_press_auto, st, __ATOMIC_RELAXED);
        return; /* the deferral is already handled (auto_for) */
    }
    uint32_t marked = D.marked, since = __atomic_load_n(&D.play_since, __ATOMIC_RELAXED);
    int32_t age = (int32_t)(tick - since);
    if (D.defer != OP_DEFER_WAIT || D.auto_for == marked || !since || age < 0 || (age == 0 && code == OP_PLAY_PRESS))
        return; /* before the visit, or the playback press that opened it (stamped with its first tick) */
    D.auto_for = marked;
    D.auto_why = OP_AI_KEY;
}

/* worker, combo_observe.c drain: a hook record with COMBO_F_GEN_UNEQUAL (S4). Marks the
 * current playback visit; a clear outside playback marks nothing. */
CODE void one_press_queue_clear(void)
{
    uint32_t since = __atomic_load_n(&D.play_since, __ATOMIC_RELAXED);
    if (since)
        __atomic_store_n(&one_press_clear_visit, since, __ATOMIC_RELAXED);
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
    if (reason == OP_END_SAVE && S.source == OP_SOURCE_VIEW)
        D.last_good = S.view_handle; /* a 3:2 view (its dry run was S2 2400x1600) */
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
    /* The playback visit (single writer): its first tick, 0 outside playback. */
    if (A.read(COMBO_GUI_MODE_WORD) != OP_GUI_PLAYBACK)
        __atomic_store_n(&D.play_since, 0u, __ATOMIC_RELEASE);
    else if (!__atomic_load_n(&D.play_since, __ATOMIC_RELAXED)) {
        __atomic_store_n(&one_press_visit_gen, A.read(OP_GUI_QUEUE), __ATOMIC_RELAXED); /* S4: before the stamp is published */
        __atomic_store_n(&D.play_since, tick ? tick : 1u, __ATOMIC_RELEASE);
    }
    op_auto_info(tick);
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
    DEBUG(op_defer_fmt, D.defer, D.why, D.xpan, D.marked, D.starts, D.fallbacks, D.last_good, D.last_good_rc);
    DEBUG(op_auto_fmt, D.auto_for, D.auto_why, D.auto_gui, D.auto_posts, D.auto_ticks);
}
