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
 *    HWJPG.Y22 is pending is marked deferred (OP_DEFER_WAIT). Every admitted OK RATE
 *    replaces it (latest only); a RATE in playback runs the playback path. The deferred
 *    job starts on GuiMainTask at the release of INFO (COMBO_INFO_RELEASE, the only
 *    extra code the H2 hook passes on), in the same context and order as the RATE
 *    release: never from the worker. At that release: one-press idle and not latched;
 *    GUI mode re-read = playback; playback stable for OP_DEFER_SETTLE_TICKS
 *    (worker-sampled); the pending Y22 still this capture's; its age within
 *    INJECT_PENDING_TICKS - OP_TIMEOUT_TICKS (H3 keeps its 10-min expiry, so the job's
 *    H3 can never meet an expired screenshot and fall through to a stock Resize);
 *    Canon's develop state and both busy flags clear. Busy or settling only waits for
 *    the next release (no bips).
 *    Why only INFO: it stays in playback and confirms nothing. SET is the confirm key
 *    of every playback dialog (Erase, Protect, Q, Resize), and our hook runs before
 *    Canon handles the release. A key that leaves playback (the playback button, MENU)
 *    could see its release before Canon changes the mode word, and the job must never
 *    start while playback is being left.
 *  - Source, both paths. The view is used when FM(view|2) passes and the playback
 *    cache's AspectInfo tuple (playback_native.c, the XPan RAW/JPEG tuples of
 *    playback_box.c) is not XPan. Otherwise the last good source: the group handle of
 *    the latest view that passed the FM check and saved (DevelopEnd(1)) this boot,
 *    re-checked with FM. Otherwise the press is refused (two bips) and the screenshot
 *    stays pending; a deferred one then waits until playback is left and re-entered
 *    (two bips once per playback visit). The dry run remains the fail-closed check.
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
/* Writers: GuiMainTask (arm, key), except play_since (worker) and last_good (done). */
struct one_press_defer {
    uint32_t defer, why, xpan;           /* OP_DEFER_*, OP_DW_* (latest decision), OP_XPAN_* (latest view) */
    uint32_t marked, starts, fallbacks;  /* deferred captures; deferred jobs started; jobs started on the last-good source */
    uint32_t last_good;                  /* group handle, 0 = none this boot */
    int32_t last_good_rc;                /* its latest FM check */
    uint32_t decisions;                  /* every arm and every INFO decision while deferred */
    uint32_t play_since;                 /* worker: tick playback was entered (0 = not in playback) */
    uint32_t held_since;                 /* play_since of the visit that refused (OP_DW_HELD) */
};
_Static_assert(sizeof(struct one_press_defer) == 44u, "one-press defer ABI");
extern struct one_press_defer one_press_defer;
/* GuiMainTask, H2 hook: an INFO release (COMBO_INFO_RELEASE); other codes are ignored. */
void one_press_key(uint32_t code);

/* GuiMainTask, RATE admission (before any FIO): 1 while the state is not IDLE (refused). */
uint32_t one_press_busy(void);
/* GuiMainTask, end of a CAPTURE_OK press: y22_ready = HWJPG.Y22 complete and pending. */
void one_press_arm(uint32_t gui_mode, uint32_t number, uint32_t tick, uint32_t y22_ready, uint32_t pic_outcome, uint32_t pic_bytes);
void one_press_release(void); /* GuiMainTask, the RATE release: the source check and the start */
uint32_t one_press_dry_wanted(void);          /* H3: 1 while a job waits for its done callback */
void one_press_dry(uint32_t ok, uint32_t width, uint32_t height);
void one_press_done(uint32_t result);         /* Canon: doneCBR, InnerDevelopMgr */
void one_press_end(uint32_t arg);             /* Canon: endCBR */
void one_press_step(uint32_t tick);           /* worker: the playback visit and the timeout */
void one_press_report(void);                  /* worker: R5CA rows */
#endif
#endif
