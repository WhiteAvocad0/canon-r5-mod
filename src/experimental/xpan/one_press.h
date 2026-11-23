#ifndef R5_ONE_PRESS_H
#define R5_ONE_PRESS_H
/* One-press screenshot JPEG, v1 (opt-in --screenshot). Owner of the job
 * state, the 284-byte develop block, the source check (view or last good),
 * the deferred start (screenshot anywhere, below) and the R5CA rows.
 *
 * A RATE pressed in playback (GUI mode OP_GUI_PLAYBACK) whose capture is OK and
 * whose HWJPG.Y22 is complete (built on GuiMainTask at the press) arms a job;
 * the RATE release (after Canon has handled the press itself) starts Canon's own Resize -> S2 job on the shown photo, with our
 * own callbacks, as Canon's GUI starter RequestPlayDevelopStart (E071950C) does,
 * minus its dialogs:
 *   GUI_ReleaseFileObject E07104F4; guiSetInnerDevelopBusy(1) E07C5F52;
 *   SetINDEVBusyOnDevelop(1) E07C5F7E;
 *   INDEV_DevelopStart(handle, block, start_stub, one_press_done, 0) E0548F8A.
 * one_press_done (InnerDevelopMgr, seen on camera) mirrors Canon's doneCBR
 * E07193F6 (SetINDEVBusyOnDevelop(0)), then ends the job itself, setting
 * SetINDEVBusyOnDevelop(1) again first as Canon's End path does (E0719DE6;
 * Canon's E0719D22 also re-releases the file object and sets its develop
 * state 3, which we do not):
 * INDEV_DevelopEnd(1, block, one_press_end) only when the result is nonzero, the
 * dry run's H3 kind-0 descriptor was exactly S2 3:2 (hw_jpeg_inject.c owns that
 * check) and this job's screenshot is still pending; otherwise
 * INDEV_DevelopEnd(0, ...). DevelopEnd only allocates, copies and posts to
 * INDEV's queue without waiting (E0549258 -> E0549406 -> E07F09DE, node from
 * E07F092A with IRQs off, post E07E6D2E with timeout 0). one_press_end (endCBR)
 * mirrors Canon's endCBR E0719306 (both busy flags 0, from the same context).
 * Our H8 sees our own DevelopEnd(1) and arms H3, which reads the ready
 * HWJPG.Y22 into the encoder as for a manual S2.
 *
 * Never DevelopEnd before the done callback (Canon's PTP path defers End the same
 * way). If neither callback arrives within OP_TIMEOUT_TICKS the worker gives
 * up: state OP_GAVE_UP, one-press latched off, and both busy flags
 * stay set, so playback never decodes while INDEV may still hold its memory map;
 * playback stays inert until a late callback or power-off. A late done is answered
 * with DevelopEnd(0); its end returns to IDLE and clears the flags. RATE is refused
 * (CAPTURE_REFUSED_DEVELOP, no FIO) while the state is not IDLE.
 *
 * Source: playback's current image (GuiUniqueDataStorage+0xDC, the handle Canon's
 * own GUI Resize passes to INDEV_DevelopStart), checked with Canon's
 * Resize check on its JPEG sub-handle (OP_SUB_JPEG; verified on camera), unless
 * it is an XPan photo; then the last good source (see "Screenshot anywhere" below).
 * Unused fields of the state and its rows keep their place (zero) so the R5CA
 * row layout stays the same. */
#include <stdint.h>

#define OP_GUI_PLAYBACK 0x2002u /* GUI mode word in playback (seen on camera) */
#define OP_TIMEOUT_TICKS 1500u  /* 30 s of 20 ms ticks; a stock S2 job is about 1 s, ours about 5 s */
/* GuiUniqueDataStorage (0x13F0F4, static) + 0xDC: playback's current image handle
 * (getter E0750DAC; E0750D92 sets it and publishes property 0x02050000). */
#define OP_GUI_VIEW_HANDLE 0x0013f1d0u
#define OP_DRIVE_B 2u /* card B (e.g. handle 0x919000C0 on camera) */
/* Canon's object handle, E07E1724(drive, dir, file, sub). */
#define OP_HANDLE(dir, file) ((OP_DRIVE_B << 30) | (1u << 28) | ((dir) << 18) | ((file) << 4))
/* FileMgr's per-file sub (low nibble): ctgGetObject accepts 1 Crw, 2 Jpg, ... 8 Heif;
 * sub 0 is "FileType Not Found" = 0x82100019 (E0363784 tbb -> E03638B2; seen on camera),
 * and fmGetFileObjectForResize takes only 2 or 1 (E02E5776). ▶ and INDEV use the
 * group handle (sub 0), so only the FM check gets OP_SUB_JPEG. */
#define OP_SUB_JPEG 2u

/* Thumb entries, ROM 2.2.1. */
#define OP_INDEV_START 0xe0548f8bu    /* (handle, block, startCBR, doneCBR, [sp] flag) */
#define OP_INDEV_END 0xe0549259u      /* (save, block, endCBR); also our H8 */
#define OP_FM_RESIZE_INFO 0xe0a08bd1u /* FM_GetFileObjectForResize(handle, info): odd = error */
#define OP_GUI_RELEASE_FILE 0xe07104f5u
#define OP_SET_GUI_BUSY 0xe07c5f53u   /* guiSetInnerDevelopBusy(v): [base+0x4C] = v, posts the GUI event */
#define OP_SET_INDEV_BUSY 0xe07c5f7fu /* SetINDEVBusyOnDevelop(v): [base+0x50] = v */
#define OP_GUI_BUSY 0xe07c5f79u       /* returns [base+0x4C] */
#define OP_INDEV_BUSY 0xe07c5f95u     /* returns [base+0x50] */
#define OP_GUI_DEVELOP_STATE 0x00017454u /* = INJECT_GUI_DEVELOP_STATE: 0 idle */

#define OP_BLOCK_BYTES 284u
#define OP_FM_WORDS 5u /* PTP's own buffer is 20 bytes (E068DBFA sub sp,#24; info at sp+4) */

/* state */
#define OP_IDLE 0u
#define OP_STARTED 1u /* INDEV_DevelopStart called; waiting for done */
#define OP_ENDING 2u  /* DevelopEnd called; waiting for end */
#define OP_GAVE_UP 3u /* no callback within OP_TIMEOUT_TICKS: flags kept, RATE refused */
/* outcome of the latest RATE in playback */
#define OP_NONE 0
#define OP_START 1
#define OP_NOT_PLAYBACK 2   /* plain capture (pending for a manual S2): no sound change; at the release, ▶ was left (refusal) */
#define OP_NO_VIEW 3        /* playback's current-image word is 0 */
#define OP_VIEW_INVALID 4   /* FM refused the view, or not a JPEG, or S2 / S3 / no size */
#define OP_BUSY 5           /* Canon's develop state or a busy flag is set */
#define OP_LATCHED 6        /* off after a timeout this boot */
#define OP_NO_Y22 7         /* the press's HWJPG.Y22 failed: nothing to save */
#define OP_VIEW_XPAN 8      /* the shown photo is an XPan crop (S2 would be 2400x1344) and no last-good source */
/* the job's source */
#define OP_SOURCE_NONE 0u
#define OP_SOURCE_VIEW 1u    /* playback's current image (2 is unused) */
#define OP_SOURCE_LAST_GOOD 3u /* the last view of this boot that passed the FM check and saved */
/* why the job ended (done callback) */
#define OP_END_SAVE 1
#define OP_END_RESULT_ZERO 2
#define OP_END_NO_DRY 3    /* no dry-run H3 kind 0 seen */
#define OP_END_DRY_GEOMETRY 4
#define OP_END_PENDING 5   /* this job's screenshot is no longer pending */
#define OP_END_LATE 6      /* done after the timeout or out of state */

/* Screenshot anywhere (stage 1):
 *  - Deferred delivery. A RATE outside playback (GUI mode != OP_GUI_PLAYBACK) whose
 *    HWJPG.Y22 is pending is marked deferred (OP_DEFER_WAIT); one whose Y22 failed, or
 *    while latched, is refused instead (two bips after the click). A RATE in playback
 *    runs the playback path; a refusal at its release (busy, playback left, no source)
 *    marks it deferred too. Every admitted OK RATE replaces a deferred one (latest
 *    only). The deferred
 *    job starts on GuiMainTask at the release of INFO (COMBO_INFO_RELEASE, the only
 *    extra code the H2 hook passes on), in the same context and order as the RATE
 *    release: never from the worker. At that release: one-press idle and not latched;
 *    GUI mode re-read = playback; playback stable for OP_DEFER_SETTLE_TICKS
 *    (worker-sampled; the visit stamp is loaded before the tick and compared signed,
 *    so a stamp published between the two loads is unsettled); the pending Y22 still this capture's; its age within
 *    INJECT_PENDING_TICKS - OP_TIMEOUT_TICKS (H3 keeps its 10-min expiry, so the job's
 *    H3 never meets an expired screenshot unless the job itself runs to the edge of
 *    OP_TIMEOUT_TICKS, about 30 s against a stock 1 s; not handled);
 *    Canon's develop state and both busy flags clear, before the source lookup and
 *    again after its FM calls. Busy or settling only waits for the next release (no
 *    bips). A GUI queue clear seen in this playback visit, or pending at this release,
 *    also only waits (OP_DW_NOT_PLAYBACK, "the S4 guard" below), as does a release
 *    whose automatic INFO post failed its re-check (OP_DW_SETTLING, below).
 *    Why only INFO: it stays in playback and confirms nothing. SET is the confirm key
 *    of every playback dialog (Erase, Protect, Q, Resize), and our hook runs before
 *    Canon handles the release. A key that leaves playback (the playback button, MENU)
 *    could see its release before Canon changes the mode word, and the job must never
 *    start while playback is being left.
 *  - Source, both paths. The view is used when FM(view|2) passes and the playback
 *    cache's AspectInfo tuple (playback_native.c, the XPan RAW/JPEG tuples of
 *    playback_box.c) is not XPan. Otherwise the last good source: the group handle of
 *    the latest view that passed the FM check and saved (DevelopEnd(1)) this boot,
 *    re-checked with FM, and never the very handle the cache just identified as XPan (a
 *    reused file number). Otherwise the press is refused (two bips) and the screenshot
 *    stays pending; after a refused INFO, the next INFO in that playback visit is
 *    ignored (two bips once per visit). The dry run remains the fail-closed check.
 *  - Row R5CA D= (combo_observe.h CC_FMT_OP_DEFER). */
#define OP_DEFER_SETTLE_TICKS 25u /* 0.5 s of stable playback before a deferred start */
#define OP_DEFER_MAX_AGE_TICKS (INJECT_PENDING_TICKS - OP_TIMEOUT_TICKS) /* hw_jpeg_inject.h: 9.5 min */
/* one_press_defer.defer */
#define OP_DEFER_NONE 0u
#define OP_DEFER_WAIT 1u /* a non-playback capture's HWJPG.Y22 waits for playback and an INFO release */
/* one_press_defer.why: the latest decision at an INFO release while deferred */
#define OP_DW_NONE 0u
#define OP_DW_SETTLING 1u     /* playback shown for less than OP_DEFER_SETTLE_TICKS: wait */
#define OP_DW_NOT_PLAYBACK 2u /* the release came outside playback: wait */
#define OP_DW_BUSY 3u         /* a one-press job, Canon's develop state or a busy flag: wait */
#define OP_DW_HELD 4u         /* refused (two bips) earlier in this playback visit: wait for the next visit */
#define OP_DW_LOST 5u         /* HWJPG.Y22 is no longer this capture's (consumed, cleared, replaced): dropped */
#define OP_DW_EXPIRED 6u      /* older than OP_DEFER_MAX_AGE_TICKS: dropped (H3 would refuse it) */
#define OP_DW_NO_SOURCE 7u    /* no usable view and no last-good source: two bips, held for this visit */
#define OP_DW_STARTED 8u      /* the job was started */
#define OP_DW_LATCHED 9u      /* one-press latched off after a timeout: dropped */
/* one_press_defer.xpan: the playback cache's AspectInfo pre-check of the latest view checked */
#define OP_XPAN_UNKNOWN 0u /* no admitted tuple for the shown image (the dry run decides) */
#define OP_XPAN_NO 1u      /* a known tuple that is not XPan */
#define OP_XPAN_YES 2u     /* the XPan RAW+L or JPEG L tuple [7,8192,3024,0,x] */

/* The S4 guard. Canon can begin leaving playback while the mode word [0x8F40] still
 * reads 0x2002. [0x8F48] (SetGUIRequestMode's last request) is not used: a playback
 * visit with no new request need not hold 0x2002 there. ROM 2.2.1: ++[0x5474] has one
 * writer, E04A7974 (QueueClearFlg), called from the IDLEHandler's LOCAL_COMPLETE_TIMER
 * and from GUILock_ProhibitLock E079416C (GUILock handleGuiState, GuiState 0); [0x547C]
 * catches up when GuiMainTask has processed the clear. An INFO release (posted or by
 * hand) waits (OP_DW_NOT_PLAYBACK: no job, no bips, the deferral kept), and the
 * automatic INFO is not posted (OP_AI_LEAVING) nor its release judged OK, when any of:
 *  - the drain marked this visit (a hook record with COMBO_F_GEN_UNEQUAL,
 *    one_press_queue_clear);
 *  - a hook record not drained yet carries that flag (combo_undrained, read live);
 *  - a clear is pending now ([0x5474] != [0x547C]);
 *  - [0x5474] differs from one_press_visit_gen, its value when the worker first saw this
 *    visit (a clear requested and finished between two drains leaves no other trace).
 * The guard narrows the exit race; it does not close it (a half-press exit just before
 * the automatic post, without a queue clear, is not seen; docs/screenshot.md "Limits").
 *
 * Automatic INFO. While a screenshot taken outside playback is deferred, the worker
 * (20 ms, one_press_step -> op_auto_info) posts ONE INFO press, then later its release,
 * through Canon's GUI event post E04A787A(0, 14|15, 0, 1): exactly the call Canon's
 * UiOpe key handler E08ABFF4 makes for a real INFO key (SwINFO_Button E01095FE ->
 * E08ABFA0 -> [0x92D4]). The posted release reaches the H2 hook on GuiMainTask like the
 * user's, and one_press_key starts the job with all of its own checks: the worker never
 * starts a job or calls a GUI setter.
 *  - The press is posted only when, in this order: the deferral is not handled yet
 *    (auto_for != marked); it was taken outside playback (OP_AI_KEPT otherwise: a
 *    playback RATE refused at its release is delivered by INFO by hand only); not
 *    latched; idle; the mode word, re-read right before the post, is playback; no S4
 *    sign in this visit; the visit stable for OP_AUTO_SETTLE_TICKS; not refused in this
 *    visit (held_since); this capture's Y22 still pending and within
 *    OP_DEFER_MAX_AGE_TICKS; Canon's develop state and both busy getters 0; the UiOpe
 *    key handler [0x92D4] is the direct poster E08ABFF5 (in UiOpe mode 0 Canon keeps keys
 *    for a later replay: OP_AI_KEYS_HELD); the GUI queue quiet (clear generations equal,
 *    depth [0x5478] <= OP_GUI_QUEUE_DEPTH_MAX, compared unsigned); no class-0 event
 *    through the H2 hook for OP_AUTO_QUIET_TICKS, and no user key among the records
 *    not drained yet. Then the worker claims the deferral (compare-exchange
 *    OP_DEFER_WAIT -> | OP_DEFER_CLAIM): an INFO by hand that consumed it first wins and
 *    nothing is posted (OP_AI_CONSUMED); GuiMainTask in turn starts a job only by its
 *    own compare-exchange OP_DEFER_WAIT -> NONE.
 *  - One post per deferral: the deferral is handled (auto_for = marked) before the post,
 *    or by a user key the drain records after playback entry before the post
 *    (OP_AI_KEY: any COMBO_BUTTONS press, a joystick or direction-key code
 *    OP_DIRECTION_FIRST..OP_MULTI_CONTROLLER_LAST, or a lost hook record; only the
 *    playback press itself, stamped with the visit's first tick, does not count). INFO
 *    by hand then works as before.
 *  - The release is posted at least OP_AUTO_RELEASE_TICKS after the press, and only
 *    into a quiet queue: ROM 2.2.1 E04A787A asserts (Err 70) when its queue send fails,
 *    and raises the depth word only after the send. While the queue is not quiet it
 *    waits; after OP_AUTO_RELEASE_WAIT_TICKS more it is posted with a clear still
 *    pending, but never above OP_GUI_QUEUE_DEPTH_MAX (it keeps waiting). A press thus
 *    always gets its release once the queue drains.
 *  - The release's verdict is OK only when: not delayed (posted exactly
 *    OP_AUTO_RELEASE_TICKS after the press, into a quiet queue); the visit began before
 *    the press; the hook saw our INFO press (OP_AUTO_SEEN) and no user key since it
 *    (OP_AUTO_USER_KEY: another button press, a second INFO press, a joystick or
 *    direction-key code, a lost record), drained or not; [0x92D4] is still E08ABFF5; the
 *    mode word is playback; no S4 sign; idle and not latched. Otherwise
 *    OP_AI_RELEASE_HELD. A user key the drain handles after an OK verdict turns it held.
 *  - Our release is identified by its GUI event slot, never by its content (a physical
 *    INFO release is the same event). ROM 2.2.1 E04A787A: the slot index is [ctx+0x14]
 *    (OP_GUI_POST_INDEX), taken and advanced mod 0x401 with IRQs off; the slot is
 *    OP_GUI_SLOTS + 16 * index (literal E04A7BFC), and that pointer is the message
 *    GuiMainTask receives (E04A7762) and hands to the class-0 handler, our H2 hook
 *    (E04A7782). Before the post the worker arms one_press_rel_mark with the verdict and
 *    the index it read; right after, it narrows the range to the slots posted meanwhile
 *    (one slot when no other task posted in between). one_press_key decides only on an
 *    INFO release from a slot in that range: a held verdict, or a user key the hook has
 *    recorded but the drain not yet handled, starts nothing (OP_DW_SETTLING: no job, no
 *    bips, the deferral kept for INFO by hand). An INFO release from any other slot is
 *    an INFO by hand. A one-slot mark is consumed by its release; any mark is forgotten
 *    once OP_REL_FORGET_POSTS more events were posted.
 *  - Row R5CA A= (combo_observe.h CC_FMT_OP_AUTO). */
#define OP_GUI_POST 0xe04a787bu            /* Thumb entry: GUI event post (class, code, arg2, arg3) */
#define OP_UIOPE_KEY_HANDLER 0x000092d4u   /* [COMBO_UIOPE_MODE + 4]: E08ABFA0's key handler */
#define OP_UIOPE_POST_DIRECT 0xe08abff5u   /* UiOpe mode 2 handler: E04A787A(0, code, 0, arg) */
#define OP_GUI_QUEUE 0x00005474u           /* = COMBO_GUI_CONTEXT: +0 / +8 clear generations, +4 queue depth */
#define OP_GUI_QUEUE_DEPTH_MAX 4u          /* posted only while at most this many GUI events are queued */
#define OP_GUI_POST_INDEX 0x00005488u      /* [ctx+0x14]: the next GUI event slot index (E04A7892..E04A789C) */
#define OP_GUI_SLOTS 0x00082454u           /* GUI event slot 0 (literal E04A7BFC); 16 bytes each */
#define OP_GUI_SLOT_COUNT 0x401u           /* the slot ring (E04A788E) */
#define OP_INFO_PRESS 14u                  /* COMBO_BUTTONS INFO; SwINFO_Button's press code */
#define OP_INFO_RELEASE 15u                /* = COMBO_INFO_RELEASE */
#define OP_PLAY_PRESS 18u                  /* COMBO_BUTTONS PLAY: the press that opens playback */
#define OP_AUTO_SETTLE_TICKS 100u          /* 2 s of a stable playback visit before the press (the human INFO: 0.5 s) */
#define OP_AUTO_QUIET_TICKS 25u            /* 0.5 s without a class-0 event through the H2 hook */
#define OP_AUTO_RELEASE_TICKS 5u           /* 100 ms from the press to the release */
#define OP_AUTO_RELEASE_WAIT_TICKS 50u     /* 1 s of a busy queue, then the release goes with a clear pending (held) */
#define OP_REL_FORGET_POSTS 512u           /* a release mark older than this many GUI posts is forgotten */
/* ROM 2.2.1: SwDirectionKeyCBR E0108B02 posts 0x76..0x7F (state 0 -> 0x76 at E0108B3C) and
 * SwSubDialMultiControlerCBR E0108AA2 0x80..0x88, both through E08ABFA0(code, 1): class-0
 * events 118..136. Canon's own posts of these codes are class 2. */
#define OP_DIRECTION_FIRST 118u
#define OP_MULTI_CONTROLLER_LAST 136u
/* one_press_auto (worker): tick << OP_AUTO_TICK_SHIFT | flags | OP_AUTO_PRESSED while our
 * press waits for its release, else 0. Flags (the combo drain): OP_AUTO_SEEN, OP_AUTO_USER_KEY. */
#define OP_AUTO_PRESSED 1u
#define OP_AUTO_STATE 3u
#define OP_AUTO_SEEN 4u
#define OP_AUTO_USER_KEY 8u
#define OP_AUTO_TICK_SHIFT 4u
#define OP_AUTO_TICK_MASK 0x0fffffffu
/* one_press_rel_mark: OP_REL_ARMED | OP_REL_OK (verdict) | count << OP_REL_COUNT_SHIFT | first slot
 * index; count 0 = not narrowed yet (every slot posted since the first). */
#define OP_REL_ARMED 0x80000000u
#define OP_REL_OK 0x40000000u
#define OP_REL_INDEX_MASK 0x7ffu
#define OP_REL_COUNT_SHIFT 11u
/* one_press_defer.defer: the worker's claim while it posts its INFO press */
#define OP_DEFER_CLAIM 2u
/* one_press_defer.auto_why: the latest worker decision while a deferral was not handled */
#define OP_AI_NONE 0u
#define OP_AI_POSTED 1u        /* INFO press posted (this deferral handled) */
#define OP_AI_KEY 2u           /* a user key after playback entry came first (handled, no post) */
#define OP_AI_LATCHED 3u       /* one-press latched off after a timeout */
#define OP_AI_BUSY 4u          /* a one-press job is in progress */
#define OP_AI_NOT_PLAYBACK 5u  /* the GUI mode word is not OP_GUI_PLAYBACK */
#define OP_AI_SETTLING 6u      /* playback seen for less than OP_AUTO_SETTLE_TICKS */
#define OP_AI_HELD 7u          /* refused (two bips) in this playback visit */
#define OP_AI_LOST 8u          /* HWJPG.Y22 is no longer this capture's */
#define OP_AI_EXPIRED 9u       /* older than OP_DEFER_MAX_AGE_TICKS */
#define OP_AI_CANON_BUSY 10u   /* Canon's develop state or a busy flag */
#define OP_AI_KEYS_HELD 11u    /* Canon's UiOpe key handler is not the direct poster */
#define OP_AI_QUEUE_BUSY 12u   /* GUI queue clear pending or more than OP_GUI_QUEUE_DEPTH_MAX queued */
#define OP_AI_KEPT 13u         /* a playback RATE refused at its release: INFO by hand only */
#define OP_AI_LEAVING 14u      /* S4: a sign of a GUI queue clear in this playback visit */
#define OP_AI_GUI_EVENTS 15u   /* a GUI event less than OP_AUTO_QUIET_TICKS ago, or a key not drained yet */
#define OP_AI_RELEASE_HELD 16u /* the release verdict failed: our release starts nothing */
#define OP_AI_RELEASE_WAIT 17u /* the release waits for a quiet GUI queue */
#define OP_AI_CONSUMED 18u     /* GuiMainTask took the deferral first: no post */

#ifndef __ASSEMBLER__
typedef void (*op_cb_fn)(uint32_t);
struct one_press_api {
    uint32_t (*read)(uint32_t address); /* aligned u32 load */
    uint32_t (*timer_us)(void);
    int32_t (*fm_resize_info)(uint32_t handle, uint32_t *info);
    void (*release_file)(void);
    void (*set_gui_busy)(uint32_t value);
    void (*set_indev_busy)(uint32_t value);
    uint32_t (*gui_busy)(void);
    uint32_t (*indev_busy)(void);
    void (*start)(uint32_t handle, void *block, op_cb_fn start_cb, op_cb_fn done_cb, uint32_t remote);
    void (*end)(uint32_t save, void *block, op_cb_fn end_cb);
    uint32_t (*view_xpan)(void); /* OP_XPAN_*: the playback cache's AspectInfo tuple */
    void (*gui_post)(uint32_t event_class, uint32_t code, uint32_t arg2, uint32_t arg3); /* OP_GUI_POST */
};
extern const struct one_press_api one_press_api;

struct one_press_state {
    uint32_t state, latched, start_tick;           /* state: OP_IDLE / STARTED / ENDING */
    uint32_t attempts, jobs, saves, cancels, ends, timeouts, refused, late;
    /* the latest RATE in playback */
    uint32_t outcome, number, gui_mode, dir, file, handle, pending_tick; /* dir, file, handle: unused, 0 */
    int32_t rc_open, rc_read, fm_rc;               /* unused, 0 */
    uint32_t fm_info[OP_FM_WORDS];                 /* unused, 0 */
    uint32_t develop_state, gui_busy, indev_busy;
    uint32_t pic_outcome, pic_bytes;               /* the .PIC write of this capture */
    uint32_t us_start, us_done, us_end;            /* Canon log timer */
    uint32_t done_result, end_arg, end_reason;
    uint32_t dry_seen, dry_ok, dry_wh;             /* written by H3 while STARTED */
    uint32_t armed;                                /* 1 from a ready press in playback to its release */
    uint32_t refusals;                             /* worker feedback: each one a double bip */
    /* the latest release's view check */
    uint32_t source, view_handle;
    int32_t view_fm_rc;
    uint32_t view_info0, view_info2;
};
_Static_assert(sizeof(struct one_press_state) == 188u, "one-press state ABI");
extern struct one_press_state one_press_state;
extern uint8_t one_press_block[OP_BLOCK_BYTES];
/* Writers: GuiMainTask (arm, key), except play_since and auto_* (worker) and last_good (done). */
struct one_press_defer {
    uint32_t defer, why, xpan;           /* OP_DEFER_*, OP_DW_* (latest decision), OP_XPAN_* (latest view) */
    uint32_t marked, starts, fallbacks;  /* deferred captures; deferred jobs started; jobs started on the last-good source */
    uint32_t last_good;                  /* group handle, 0 = none this boot */
    int32_t last_good_rc;                /* its latest FM check */
    uint32_t decisions;                  /* every arm and every INFO decision while deferred */
    uint32_t play_since;                 /* worker: tick playback was entered (0 = not in playback) */
    uint32_t held_since;                 /* play_since of the visit that refused (OP_DW_HELD) */
    /* automatic INFO (worker: op_auto_info, one_press_user_key) */
    uint32_t auto_for;                   /* the deferral (marked) handled by a post or a user key; 0 = none */
    uint32_t auto_why;                   /* OP_AI_*: the latest decision (frozen once the deferral is handled) */
    uint32_t auto_gui;                   /* the GUI mode word re-read right before the latest post */
    uint32_t auto_posts;                 /* INFO presses posted this boot */
    uint32_t auto_ticks;                 /* worker ticks from playback entry (play_since) to the latest post */
};
_Static_assert(sizeof(struct one_press_defer) == 64u, "one-press defer ABI");
extern struct one_press_defer one_press_defer;
/* GuiMainTask, H2 hook: an INFO release (COMBO_INFO_RELEASE) dispatched from GUI event
 * slot `slot` (the hook's r0); other codes are ignored. */
void one_press_key(uint32_t code, uintptr_t slot);
/* worker, combo_observe.c drain: a button press, a joystick or direction-key code
 * (OP_DIRECTION_FIRST..OP_MULTI_CONTROLLER_LAST) the H2 hook recorded at worker tick
 * `tick`, or code 0 for lost hook records (at the drain tick). */
void one_press_user_key(uint32_t tick, uint32_t code);
/* worker, combo_observe.c drain: a hook record with COMBO_F_GEN_UNEQUAL marks this playback visit (S4). */
void one_press_queue_clear(void);
extern uint32_t one_press_clear_visit; /* the play_since of the visit with a queue clear; 0 = none (worker) */
extern uint32_t one_press_auto;        /* OP_AUTO_*: our press waiting for its release (worker) */
extern uint32_t one_press_rel_mark;     /* OP_REL_*: our posted release (worker arms; GuiMainTask consumes) */
extern uint32_t one_press_visit_gen;   /* [0x5474] when the worker first saw this playback visit (S4) */

/* GuiMainTask, RATE admission (before any FIO): 1 while the state is not IDLE (refused). */
uint32_t one_press_busy(void);
/* GuiMainTask, end of a CAPTURE_OK press: y22_ready = HWJPG.Y22 complete and pending. */
void one_press_arm(uint32_t gui_mode, uint32_t number, uint32_t tick, uint32_t y22_ready, uint32_t pic_outcome, uint32_t pic_bytes);
void one_press_release(void); /* GuiMainTask, the RATE release: the source check and the start */
uint32_t one_press_dry_wanted(void);          /* H3: 1 while a job waits for its done callback */
void one_press_dry(uint32_t ok, uint32_t width, uint32_t height);
void one_press_done(uint32_t result);         /* Canon: doneCBR, InnerDevelopMgr */
void one_press_end(uint32_t arg);             /* Canon: endCBR */
void one_press_step(uint32_t tick);           /* worker: the playback visit, the automatic INFO and the timeout */
void one_press_report(void);                  /* worker: R5CA rows */
#endif
#endif
