#ifndef R5_ONE_PRESS_H
#define R5_ONE_PRESS_H
/* One-press screenshot JPEG, v1 (opt-in --one-press, build-14; design
 * build-13/ONE-PRESS-PLAN.md, issue-research/screenshot/hw-jpeg/
 * auto-trigger-research.md). Owner of the job state, the 284-byte develop block,
 * the source check and the R5CA rows.
 *
 * A RATE pressed in playback (GUI mode OP_GUI_PLAYBACK) whose capture is OK and
 * whose HWJPG.Y22 is complete (build-15: built on GuiMainTask at the press, the
 * A2-1 pattern) arms a job; the RATE release (review M R2: after Canon has handled
 * the press itself) starts Canon's own Resize -> S2 job on the shown photo, with our
 * own callbacks, as Canon's GUI starter RequestPlayDevelopStart (E071950C) does,
 * minus its dialogs:
 *   GUI_ReleaseFileObject E07104F4; guiSetInnerDevelopBusy(1) E07C5F52;
 *   SetINDEVBusyOnDevelop(1) E07C5F7E;
 *   INDEV_DevelopStart(handle, block, start_stub, one_press_done, 0) E0548F8A.
 * one_press_done (InnerDevelopMgr, O: returned-14 P2 SP) mirrors Canon's doneCBR
 * E07193F6 (SetINDEVBusyOnDevelop(0)), then ends the job itself, setting
 * SetINDEVBusyOnDevelop(1) again first as Canon's End path does (E0719DE6, review
 * M R1; Canon's E0719D22 also re-releases the file object and sets its develop
 * state 3, which we do not):
 * INDEV_DevelopEnd(1, block, one_press_end) only when the result is nonzero, the
 * dry run's H3 kind-0 descriptor was exactly S2 3:2 (hw_jpeg_inject.c owns that
 * check) and this job's screenshot is still pending; otherwise
 * INDEV_DevelopEnd(0, ...). DevelopEnd only allocates, copies and posts to
 * INDEV's queue without waiting (O: E0549258 -> E0549406 -> E07F09DE, node from
 * E07F092A with IRQs off, post E07E6D2E with timeout 0). one_press_end (endCBR)
 * mirrors Canon's endCBR E0719306 (both busy flags 0, from the same context).
 * Our H8 sees our own DevelopEnd(1) and arms H3, which reads the ready
 * HWJPG.Y22 into the encoder (the A1 path) as for a manual S2.
 *
 * Never DevelopEnd before the done callback (Canon's PTP path defers End the same
 * way, O). If neither callback arrives within OP_TIMEOUT_TICKS the worker gives
 * up: state OP_GAVE_UP, one-press latched off, and (review M R3) both busy flags
 * stay set, so playback never decodes while INDEV may still hold its memory map;
 * playback stays inert until a late callback or power-off. A late done is answered
 * with DevelopEnd(0); its end returns to IDLE and clears the flags. RATE is refused
 * (CAPTURE_REFUSED_DEVELOP, no FIO) while the state is not IDLE.
 *
 * Source: playback's current image (GuiUniqueDataStorage+0xDC, the handle Canon's
 * own GUI Resize passes to INDEV_DevelopStart, research 1.5), checked with Canon's
 * Resize check on its JPEG sub-handle (build-17, OP_SUB_JPEG; verified returned-20).
 * build-18 removes the CARRIER.TXT fallback (builds 14-17): the view always exists
 * in playback, and the code space is needed. The carrier fields of the state and
 * its rows keep their place (zero) so the R5CA row layout stays the same. */
#include <stdint.h>

#define OP_GUI_PLAYBACK 0x2002u /* O: returned-14 / returned-16 GUI mode word in playback */
#define OP_TIMEOUT_TICKS 1500u  /* 30 s of 20 ms ticks; a stock S2 job is about 1 s, ours about 5 s (D) */
/* GuiUniqueDataStorage (0x13F0F4, static) + 0xDC: playback's current image handle
 * (O: getter E0750DAC; E0750D92 sets it and publishes property 0x02050000). */
#define OP_GUI_VIEW_HANDLE 0x0013f1d0u
#define OP_DRIVE_B 2u /* O: returned-14 P1 handle 0x919000C0 for card B */
/* Canon's object handle, E07E1724(drive, dir, file, sub) (O, research 1.5). */
#define OP_HANDLE(dir, file) ((OP_DRIVE_B << 30) | (1u << 28) | ((dir) << 18) | ((file) << 4))
/* FileMgr's per-file sub (low nibble): ctgGetObject accepts 1 Crw, 2 Jpg, ... 8 Heif;
 * sub 0 is "FileType Not Found" = 0x82100019 (O: E0363784 tbb -> E03638B2; returned-18/19),
 * and fmGetFileObjectForResize takes only 2 or 1 (O: E02E5776). ▶ and INDEV use the
 * group handle (sub 0), so only the FM check gets OP_SUB_JPEG. */
#define OP_SUB_JPEG 2u

/* Thumb entries, ROM 2.2.1 (build-14/NOTES.md section 2 re-verifies each). */
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
#define OP_NO_VIEW 3        /* playback's current-image word is 0 (build-18; builds 14-17: CARRIER_FILE) */
#define OP_VIEW_INVALID 4   /* FM refused the view, or not a JPEG, or S2 / S3 / no size (builds 14-17: CARRIER_INVALID) */
#define OP_BUSY 5           /* Canon's develop state or a busy flag is set */
#define OP_LATCHED 6        /* off after a timeout this boot */
#define OP_NO_Y22 7         /* the press's HWJPG.Y22 failed: nothing to save */
/* the job's source (build-16) */
#define OP_SOURCE_NONE 0u
#define OP_SOURCE_VIEW 1u    /* playback's current image (2 was CARRIER.TXT, builds 16-17) */
/* why the job ended (done callback) */
#define OP_END_SAVE 1
#define OP_END_RESULT_ZERO 2
#define OP_END_NO_DRY 3    /* no dry-run H3 kind 0 seen */
#define OP_END_DRY_GEOMETRY 4
#define OP_END_PENDING 5   /* this job's screenshot is no longer pending */
#define OP_END_LATE 6      /* done after the timeout or out of state */

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
};
extern const struct one_press_api one_press_api;

struct one_press_state {
    uint32_t state, latched, start_tick;           /* state: OP_IDLE / STARTED / ENDING */
    uint32_t attempts, jobs, saves, cancels, ends, timeouts, refused, late;
    /* the latest RATE in playback */
    uint32_t outcome, number, gui_mode, dir, file, handle, pending_tick; /* dir, file, handle: 0 since build-18 */
    int32_t rc_open, rc_read, fm_rc;               /* 0 since build-18 (the carrier's) */
    uint32_t fm_info[OP_FM_WORDS];                 /* 0 since build-18 */
    uint32_t develop_state, gui_busy, indev_busy;
    uint32_t pic_outcome, pic_bytes;               /* Stage I of this capture (P0 rows are not built) */
    uint32_t us_start, us_done, us_end;            /* Canon log timer */
    uint32_t done_result, end_arg, end_reason;
    uint32_t dry_seen, dry_ok, dry_wh;             /* written by H3 while STARTED */
    uint32_t armed;                                /* 1 from a ready press in playback to its release */
    uint32_t refusals;                             /* worker feedback: each one a double bip */
    /* build-16: the latest release's view check */
    uint32_t source, view_handle;
    int32_t view_fm_rc;
    uint32_t view_info0, view_info2;
};
_Static_assert(sizeof(struct one_press_state) == 188u, "one-press state ABI");
extern struct one_press_state one_press_state;
extern uint8_t one_press_block[OP_BLOCK_BYTES];

/* GuiMainTask, RATE admission (before any FIO): 1 while the state is not IDLE (refused). */
uint32_t one_press_busy(void);
/* GuiMainTask, end of a CAPTURE_OK press: y22_ready = HWJPG.Y22 complete and pending. */
void one_press_arm(uint32_t gui_mode, uint32_t number, uint32_t tick, uint32_t y22_ready, uint32_t pic_outcome, uint32_t pic_bytes);
void one_press_release(void); /* GuiMainTask, the RATE release: the carrier check and the start */
uint32_t one_press_dry_wanted(void);          /* H3: 1 while a job waits for its done callback */
void one_press_dry(uint32_t ok, uint32_t width, uint32_t height);
void one_press_done(uint32_t result);         /* Canon: doneCBR, InnerDevelopMgr */
void one_press_end(uint32_t arg);             /* Canon: endCBR */
void one_press_step(uint32_t tick);           /* worker: the timeout */
void one_press_report(void);                  /* worker: R5CA rows */
#endif
#endif
