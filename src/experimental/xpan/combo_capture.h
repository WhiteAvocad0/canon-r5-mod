#ifndef R5_COMBO_CAPTURE_H
#define R5_COMBO_CAPTURE_H
#include <stdint.h>
#include "combo_observe.h"
#include "tone_gen.h"

/* Issue #15 Phase C' (opt-in --screenshot-capture): one YCCA screenshot per
 * RATE press, written by our own writer on GuiMainTask.
 *
 * Context decision (ROM + lab evidence): the capture runs synchronously inside
 * the H2 hook (class-0 handler entry E04A8028) on GuiMainTask, on the trigger
 * press itself, before Canon dispatches it.
 *  - Stock SaveVRAM ran this same FIO sequence (Open probe, CreateSC,
 *    MapCpuBuffer op 0 / mode 1, Write E07E283E, UnmapCpuBuffer, Close) on
 *    GuiMainTask from inside the SET dispatcher three times without error
 *    (stock-vram-capture DbgRA108 "@GuiMainTask Open/Create(B:/YCCA_0000.bin)";
 *    card16/card-01 idx 00-02).
 *  - H2 is entered straight from the GuiMainTask loop (E04A775C receive;
 *    E04A778A blx handler[class]), so no GUI lock is held and the stack is
 *    shallower than in that dispatcher path; GuiMainTask has a 0x2000 stack
 *    (UiOpe_Initialize E04A7812).
 *  - SaveVRAM itself takes no semaphore or GUI/renderer lock; FIO serialises
 *    on its own storage lock (E07E1B0A).
 *  - A worker-task writer would be an unproven FIO context (inference: it
 *    might also race GuiMainTask's own VMIX updates; not shown from ROM).
 * Known cost: every FIO entry waits on that storage lock (5000 ms, then
 * without timeout, E07E1BEC), so while Canon is still writing to the card
 * (buffer drain after a burst, movie finalisation) GuiMainTask, and the GUI,
 * stall until the lock is free. The 2 s shot-quiet window does not cover a
 * long drain; each result records its duration in worker ticks.
 * build-05 (returned-05: captures 3, 6 and 10 s after a 10-frame RAW burst
 * froze the GUI 2.7, 6.6 and 3.3 s in the probes, Create, Close and Flush)
 * refuses, before any FIO, while Canon's job state is nonzero (a shot not yet
 * written, CAPTURE_JOB_STATE_* below) and for CAPTURE_MOVIE_QUIET_TICKS after
 * the recording state was last seen nonzero. Moving the writer off GuiMainTask
 * stays future work (a worker-task FIO context is unproven, see above).
 * Every other class-0 event keeps the B' path; only codes TRIGGER and
 * TRIGGER + 1 reach combo_capture_gui. */

#define CAPTURE_TRIGGER_CODE COMBO_TRIGGER_CODE
#define CAPTURE_DEBOUNCE_TICKS 50u    /* about 1 s from the previous attempt */
#define CAPTURE_HELD_TICKS 50u        /* a press code within 1 s of the last one while held is a repeat */
#define CAPTURE_SHOT_QUIET_TICKS 100u /* no shot-counter change for 2 s */
/* No capture in the first 5 s of worker ticks (the worker starts with the
 * boot; returned-04: worker steps x 20 ms tracked Canon's boot timer). The one
 * 2.3 s capture so far (returned-04 SS_00005) came 2.9 s after power-on. */
#define CAPTURE_BOOT_GUARD_TICKS 250u
#define CAPTURE_RESULTS 8u
#define CAPTURE_MAX_NUMBER 99999u
#define CAPTURE_PROBE_LIMIT 40u       /* Open probes per attempt */
/* One 512-byte sector: the 32 used bytes, then zeros, so the YCCA data write
 * starts sector-aligned (SaveVRAM and the witness only ever wrote at offset 0). */
#define CAPTURE_HEADER_BYTES 512u
#define CAPTURE_HEADER_USED 32u
#define CAPTURE_PATH_BYTES 32u
#define CAPTURE_HEADER_VERSION 3u

/* Thumb entry points (ROM 2.2.1). Evidence: SaveVRAM's YCCA pass
 * E0263F2C-E026403A (Open E07E1E4A probe, CreateSC E07E1F0A, MapCpuBuffer
 * E07F068A, Write E07E283E, UnmapCpuBuffer E07F0718, Close E07E2D6E); its RGBA
 * pass (Remove E07E207C before Create); the lab's hardware-run R5BOOT witness
 * (MountCheck E07E4860, Flush E07E40EE, Close E07E2D6E). */
#define CAPTURE_FIO_OPEN UINT32_C(0xe07e1e4b)
#define CAPTURE_FIO_CREATE UINT32_C(0xe07e1f0b)
#define CAPTURE_FIO_REMOVE UINT32_C(0xe07e207d)
#define CAPTURE_FIO_WRITE64 UINT32_C(0xe07e283f)
#define CAPTURE_FIO_CLOSE UINT32_C(0xe07e2d6f)
#define CAPTURE_FIO_FLUSH UINT32_C(0xe07e40ef)
#define CAPTURE_FIO_MOUNT UINT32_C(0xe07e4861)
#define CAPTURE_MAP_CPU_BUFFER UINT32_C(0xe07f068b)
#define CAPTURE_UNMAP_CPU_BUFFER UINT32_C(0xe07f0719)

/* Canon's log timestamp (COMBO_LOG_TIMER_REGISTER, combo_observe.h owns the
 * evidence). Its width is >= 28 bits (largest lab value 0x8E5400F); the
 * start stamp is kept raw, each stage as its offset from it, and the host
 * derives the per-stage times. */
#define CAPTURE_TIMER_REGISTER COMBO_LOG_TIMER_REGISTER

/* Canon's job state, read before any FIO (build-05 busy-card refusal).
 * O (ROM 2.2.1): jobUpdateLastJobState E07F7AB6 keeps the main bits at
 * [0xA084 + 0x50] and the sub bits at [0xA084 + 0x54] (literal 0xA084 at
 * E07F7B74) and publishes main | sub as PROP_LAST_JOB_STATE 0x80030012
 * (literal E07F7D28) on every change. O (Canon logs returned-05..08, four
 * single shots): 0 -> 0x16 at CreateJob, 0x14, 0x10 at develop end, and 0
 * only after fwCompleteJob / fssCompleteJob, i.e. once the file is written;
 * a playback Resize (INDEV) job leaves it 0. D: a burst keeps it nonzero
 * until its last file is written; whether movie finalisation sets it is U
 * (hence CAPTURE_MOVIE_QUIET_TICKS). Aligned single-word reads, no lock. */
#define CAPTURE_JOB_STATE_MAIN UINT32_C(0x0000a0d4)
#define CAPTURE_JOB_STATE_SUB UINT32_C(0x0000a0d8)
/* Fallback without a Canon signal: refuse for 5 s after the worker last saw
 * the recording state nonzero (returned-05: a capture right after a 5 s
 * movie spent 0.92 s in its data write). The window length is D. */
#define CAPTURE_MOVIE_QUIET_TICKS 250u
#define CAPTURE_REC_NEVER UINT32_C(0xfffffffe) /* rec_idle: no recording seen since boot */
#define CAPTURE_STAGE_NOT_REACHED UINT32_MAX
typedef uint32_t (*capture_timer_fn)(void);
typedef int32_t (*capture_open_fn)(const char *path, uint32_t mode);
typedef int32_t (*capture_path_fn)(const char *path);
typedef int32_t (*capture_write64_fn)(int32_t fd, uint32_t unused, uint32_t phys_lo, uint32_t phys_hi, uint32_t bytes);
typedef int32_t (*capture_close_fn)(int32_t fd);
typedef int32_t (*capture_map_fn)(uint32_t out64[2], uint32_t vaddr, uint32_t bytes, uint32_t op, uint32_t mode);
typedef int32_t (*capture_unmap_fn)(uint32_t vaddr, uint32_t bytes, uint32_t op, uint32_t mode);
struct capture_api {
    capture_open_fn open;      /* Open(path, 0): >= 3 exists (then Close), -1 absent, else refuse */
    capture_path_fn create;    /* CreateSC(path): fd >= 3 owned */
    capture_path_fn remove;    /* Remove(path) */
    capture_write64_fn write64;
    capture_close_fn close;
    capture_path_fn flush;     /* Flush("B:/") */
    capture_path_fn mount;     /* MountCheck("B:/") == 1 when mounted */
    capture_map_fn map;
    capture_unmap_fn unmap;
    capture_timer_fn timer_us; /* Canon's log timer (above) */
};
extern const struct capture_api capture_api;

enum capture_outcome {
    CAPTURE_NONE = 0,
    CAPTURE_OK = 1,
    CAPTURE_REFUSED_BUSY = 2,
    CAPTURE_REFUSED_RECORDING = 3, /* never sound */
    CAPTURE_REFUSED_SHOT = 4,      /* shot counter changed < 2 s ago, or unknown */
    CAPTURE_REFUSED_DISPLAY = 5,   /* geometry, descriptor or MapCpuBuffer alignment */
    CAPTURE_REFUSED_CARD = 6,      /* MountCheck(B:/) != 1 */
    CAPTURE_REFUSED_NAMESPACE = 7, /* no free SS_nnnnn within the probe budget, or a probe error */
    CAPTURE_FAILED_CREATE = 8,     /* CreateSC < 3 (also: DCIM/R5SHOTS missing) */
    CAPTURE_FAILED_WRITE = 9,      /* MapCpuBuffer failure or short write: file closed and removed */
    CAPTURE_FAILED_CLOSE = 10,
    CAPTURE_FAILED_FLUSH = 11,
    CAPTURE_REFUSED_BOOT = 12,     /* within CAPTURE_BOOT_GUARD_TICKS of the worker start */
    CAPTURE_REFUSED_WRITING = 13,  /* Canon's job state nonzero: a shot is still being written */
    CAPTURE_REFUSED_MOVIE = 14,    /* within CAPTURE_MOVIE_QUIET_TICKS of the last recording */
    CAPTURE_REFUSED_DEVELOP = 15,  /* --hw-jpeg-y22: a Resize save (H8) or its H3 holds the guard */
    CAPTURE_OUTCOMES = 16
};

struct capture_result {
    uint32_t seq, outcome, number, tick, known, pass, gui_mode, probes;
    uint32_t width, height, marv, data, format, bytes, sel, duration; /* duration: worker ticks spent */
    int32_t rc_mount, rc_create, rc_header, rc_data, rc_close, rc_flush, rc_remove, rc_map_header;
    int32_t rc_map_data;
    /* Canon log timer (us), raw at the attempt start, then the offset from it
     * after the checks and name probes (MountCheck, Open) returned, and after
     * Create, header write, data write, Close and Flush returned;
     * CAPTURE_STAGE_NOT_REACHED for a stage not run. */
    uint32_t us_start, us_create, us_header, us_data, us_close, us_flush, us_probed;
    /* Canon job state words (main, sub; Canon publishes main | sub) and the
     * ticks since the recording state was last nonzero (CAPTURE_REC_NEVER),
     * all read at the writer check; CAPTURE_STAGE_NOT_REACHED when an earlier
     * check refused. */
    uint32_t job_main, job_sub, rec_idle;
    /* Stage A2 with --screenshot-png: the raw copy HWJPG.YCA after a CAPTURE_OK
     * PNG (enum capture_yca). rc_yca_header / rc_yca_data: bytes the card accepted
     * for the header sector and the display buffer; rc_yca_cleanup: Remove of a
     * failed file. us_yca_*: offsets from us_start after the old file's Remove
     * (start), Create, header write, data write and Flush. Without the PNG the
     * capture file itself (SS_nnnnn.YCA) is the source and yca_outcome stays 0. */
    uint32_t yca_outcome;
    int32_t rc_yca_remove, rc_yca_create, rc_yca_header, rc_yca_data, rc_yca_close, rc_yca_flush, rc_yca_cleanup;
    uint32_t us_yca_start, us_yca_create, us_yca_header, us_yca_data, us_yca_flush;
};
enum capture_yca { /* --hw-jpeg-y22 with --screenshot-png (also exported to the decoder) */
    CAPTURE_YCA_NONE = 0,          /* not attempted: the PNG was not CAPTURE_OK (or no PNG build) */
    CAPTURE_YCA_OK = 1,            /* written, closed, flushed: y22_pending set */
    CAPTURE_YCA_FAILED_CREATE = 2,
    CAPTURE_YCA_FAILED_WRITE = 3,  /* short write or Map failure: file removed */
    CAPTURE_YCA_FAILED_CLOSE = 4,  /* file removed */
    CAPTURE_YCA_FAILED_FLUSH = 5   /* file removed */
};
_Static_assert(sizeof(struct capture_result) == 192u, "capture result ABI (with the YCA stage)");

/* Single writer: GuiMainTask (combo_capture_gui). The worker reads results by
 * seq with the same publication rule as the H2 ring. */
struct capture_state {
    uint32_t held, presses, repeats, debounced, attempts, busy, any, last_tick;
    uint32_t next_number, published;
    uint32_t outcomes[CAPTURE_OUTCOMES];
    struct capture_result results[CAPTURE_RESULTS];
    uint32_t held_tick, stale_held; /* last press-code tick; presses after a lost release */
};
extern struct capture_state capture_state;

/* Single writer: the worker (capture_feedback_step), one aligned word each;
 * GuiMainTask reads rec_seen with acquire, then rec_last_tick. */
struct capture_watch { uint32_t rec_seen, rec_last_tick; };
extern struct capture_watch capture_watch;

/* Called by combo_observe_hook.S on GuiMainTask for a plain class-0 event
 * whose code is CAPTURE_TRIGGER_CODE (press) or + 1 (release). */
void combo_capture_gui(const uint32_t *slot);

/* --- feedback cues (worker) ------------------------------------------------ */
enum { CAPTURE_CUE_CLICK = 0, CAPTURE_CUE_BIP = 1, CAPTURE_CUES = 2 };
struct capture_cue { uint32_t segment_first, segment_count, offset_samples, padded_samples, bytes; };
/* Generated by build.py from tone_ref.VARIANTS["click"] and ["bip_low"]. */
extern const struct tone_segment capture_cue_segments[];
extern const struct capture_cue capture_cues[CAPTURE_CUES];
extern const uint32_t capture_cue_segment_total, capture_cue_capacity;
extern int16_t capture_cue_pcm[];
extern uint32_t capture_cue_descriptor[CAPTURE_CUES][16];

#define CAPTURE_CUE_TONE_UNREADY 32u
#define CAPTURE_CUE_TONE_BUSY 33u
#define CAPTURE_CUE_RECORDING 34u
#define CAPTURE_CUE_UNPUBLISHED 35u
struct capture_feedback {
    uint32_t publish_attempted, ready, fault, seen, lost;
    uint32_t clicks, bips, silent, skipped, last_reason;
    int32_t last_rc;
    struct capture_result last[CAPTURE_RESULTS];
    uint32_t kept;
    uint32_t bips_owed, bip_tick, bip_waits, refusals_seen, cancels_seen; /* the one-press bips (combo_capture.c) */
};
extern struct capture_feedback capture_feedback;
/* Worker: publish the cues once, then drain new results and sound them. */
void capture_feedback_step(void);
#endif
