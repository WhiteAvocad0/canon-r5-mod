#ifndef R5_COMBO_OBSERVE_H
#define R5_COMBO_OBSERVE_H

/* Issue #15 passive button-combo observer (opt-in --screenshot). Owner of the
 * H2 ring ABI (combo_observe_hook.S is its only writer), the worker drain, the
 * combo detector, the display-state sampler and the R5K1/R5X1/R5Y1/R5C1 report
 * rows (also the R5C5 capture and R5C6 inject summary rows, whose owners are
 * combo_capture.c and hw_jpeg_inject.c).
 *
 * The hook (GuiMainTask, class-0 handler E04A8028) copies one 16-byte record
 * per class-0 GUI event, publishes it with one release store of its seq word,
 * then replays the 8 original bytes. It calls nothing, writes nothing Canon
 * owns and never changes the event. Everything else runs in the r5shutter
 * worker (20 ms tick): no FIO, no GUI or event calls. */

/* GUI context and UiOpe words read by the hook (ROM: literal E04A820C =
 * 0x5474; [0x5474+0] / [0x5474+8] are the queue-clear generation pair the
 * class-0 handler compares at E04A8034; [0x92D0] is the UiOpe route mode). */
#define COMBO_GUI_CONTEXT 0x00005474
#define COMBO_UIOPE_MODE 0x000092d0
#define COMBO_HOOK_VA 0xe04a8028
#define COMBO_HOOK_RESUME 0xe04a8031

#define COMBO_RING_ENTRIES 64
#define COMBO_RING_MASK 63
#define COMBO_RING_WRITTEN_OFFSET 1024

/* Hook record flags (bits 31:24 of the context word). A *_WIDE flag says the
 * source word had higher bits set (the narrow field is then a projection). */
#define COMBO_F_GEN_UNEQUAL 1   /* full [0x5474] != [0x547C] (Canon then applies its keep table) */
#define COMBO_F_CODE_WIDE 2
#define COMBO_F_ARG2_WIDE 4
#define COMBO_F_ARG3_WIDE 8
#define COMBO_F_MODE_WIDE 16
#define COMBO_F_CLASS_NONZERO 32 /* slot+0 != 0 (never expected: table entry 0) */

/* Trigger press code (RATE 50 by owner decision). Plain integer: also used by
 * combo_observe_hook.S. */
#ifndef COMBO_TRIGGER_CODE
#define COMBO_TRIGGER_CODE 50
#endif
/* The INFO release (press + 1 of COMBO_BUTTONS INFO): besides the trigger press and
 * release, the only code the hook passes to combo_capture_gui; a deferred screenshot may start there (one_press.h).
 * Not the SET release (11): SET confirms playback dialogs. Plain integer: also used
 * by combo_observe_hook.S. */
#define COMBO_INFO_RELEASE 15

/* Canon's log timestamp: the logger's record writer stores
 * ~*(volatile uint32_t *)0xC021000C as each record's timer word (ROM 2.2.1
 * E0124B1E-E0124B28: ldr r0,=0xC0210000; ldr r0,[r0,#12]; mvns r0,r0;
 * str r0,[r4,#4]). It counts microseconds (on camera, Create to Close took
 * 2,323,330 in Canon's log against 116 worker ticks). Read only; both log
 * partitions carry it on one time base. Users: the capture stage timer
 * (combo_capture.c) and the inject freshness check (hw_jpeg_inject.c). */
#define COMBO_LOG_TIMER_REGISTER 0xc021000cu

#ifndef __ASSEMBLER__
#include <stdint.h>

/* One hook record (16 B). seq is written last, after a DMB: it is the single
 * release store that publishes the record. */
struct combo_ring_entry {
    uint32_t seq;        /* 1, 2, ... (0 = never written) */
    uint32_t code_tick;  /* [15:0] event code (slot+4), [31:16] worker tick low 16 */
    uint32_t args;       /* [15:0] slot+8 (arg2), [31:16] slot+12 (arg3) */
    uint32_t context;    /* [7:0] [0x5474], [15:8] [0x547C], [23:16] [0x92D0], [31:24] flags */
};

struct combo_hook_ring {
    struct combo_ring_entry entries[COMBO_RING_ENTRIES];
    uint32_t written; /* hook-private count of records written */
};
_Static_assert(sizeof(struct combo_ring_entry) == 16u, "ring entry ABI");
_Static_assert(__builtin_offsetof(struct combo_hook_ring, written) == COMBO_RING_WRITTEN_OFFSET, "ring ABI");

/* Single writer: the hook. Reader: the worker (seqlock re-check per record). */
extern struct combo_hook_ring combo_hook_ring;
/* Single writer: the worker (one increment per tick). Read by the hook. */
extern volatile uint32_t combo_worker_tick;
void combo_gui_dispatch_entry(void);

/* Physical-button press/release codes (Sw*CBR callbacks, ROM E0108400-E0109D80).
 * release = press + 1 for every pair below. */
#define COMBO_BUTTONS(X) \
    X(SET, 10) X(MENU, 12) X(INFO, 14) X(DISP, 16) X(PLAY, 18) X(ERASE, 20) X(FRAMING_ASSIST, 22) \
    X(ZOOM_TELE, 24) X(ZOOM_WIDE, 26) X(SCALE, 28) X(PROTECT, 30) X(M_FN, 34) X(M_FN2, 36) \
    X(DIAL_FUNC, 38) X(UNAVI, 43) X(MOVIE_REC, 45) X(RATING, 50) X(AV_COMP, 54) X(QUALITY, 56) \
    X(WIFI, 58) X(CROSS_RIGHT, 60) X(CROSS_LEFT, 62) X(CROSS_UP, 64) X(CROSS_DOWN, 66) X(MODE, 68) \
    X(AF_ON, 70) X(AE_LOCK, 72) X(AF_POINT, 74) X(EL, 76) X(AF_STOP, 78) X(ISO, 80) X(AF_MODE, 82) \
    X(DRIVE, 84) X(FA_LOCK, 88) X(LENS_TELE, 90) X(LENS_WIDE, 92) X(AF_AREA, 94) \
    X(SPDN, 96) X(WB, 98) X(COMP, 100) X(STB_RIGHT, 102) X(STB_LEFT, 104) X(EXP_DIAL, 137) X(LOCK, 179)
#define COMBO_BUTTON_COUNT_X(name, code) +1
#define COMBO_BUTTON_COUNT (0 COMBO_BUTTONS(COMBO_BUTTON_COUNT_X))

/* Primary detector: one dedicated button, candidate 0. Owner decision:
 * RATE (50) everywhere, including playback, where Canon also applies a rating.
 * AE lock (72) was dropped: with it set OFF in Custom Controls it still never
 * reached E04A8028 on camera. One fire per press, at most one per debounce
 * window. */
/* GUI mode word, observed only: getter E07C659E (1158 BL callers) is
 * `ldr r0,=0x8ED0; ldr r0,[r0,#0x70]`, and Canon's Extend-script gate
 * (E05A7314) requires 0x2002 there (the playback SET script path). */
#define COMBO_GUI_MODE_WORD UINT32_C(0x00008f40)
#define COMBO_GUI_MODE_PLAYBACK_SCRIPT UINT32_C(0x2002)
#define COMBO_TRIGGER_DEBOUNCE_TICKS 100u /* 2 s */
/* Secondary (diagnostic) candidate pairs, unordered. Candidate 1
 * (AF-ON + AE lock) is retired: AF-ON and AE lock never reached E04A8028 as
 * 70..73 on camera. Candidate 2 must not fire in
 * playback (Rate writes a rating there); the playback context is UNKNOWN to
 * this build, so its verdict bit stays unknown. */
#define COMBO_PAIRS(X) X(2, 14, 50) X(3, 179, 14)
#define COMBO_PAIR_COUNT 2u
#define COMBO_WINDOW_MS 150u
#define COMBO_TICK_MS 20u /* SOUND_WORKER_POLL_MS */
/* t(B) - t(A) <= 150 ms in whole ticks: dt <= 7 ticks. Quantisation: a true
 * skew within about +/-20 ms of the bound can fall either side. */
#define COMBO_WINDOW_TICKS (COMBO_WINDOW_MS / COMBO_TICK_MS)
#define COMBO_SHOT_QUIET_TICKS 100u  /* 2 s */
#define COMBO_DEBOUNCE_TICKS 100u    /* 2 s */
#define COMBO_DISPLAY_PERIOD_TICKS 10u /* sample display state at most every 200 ms (5 Hz) */

/* Live-record budgets per boot (the KUWA RAM log holds about 3100 records in
 * total, shared with Canon). Past a budget, records are counted, not logged.
 * Worst case about 384 + 128 + 8 x 31 + 32 x 4 = 888 rows (about 29 %); the
 * lost rows share the other-row budget. live_rows reports the actual use. */
#define COMBO_KEY_ROW_BUDGET 384u
#define COMBO_OTHER_ROW_BUDGET 128u
#define COMBO_SNAPSHOT_BUDGET 8u
#define COMBO_CAPTURE_ROW_BUDGET 32u
#define COMBO_OTHER_CODES 16u  /* distinct non-button codes counted in the summary */
#define COMBO_OVERLAPS 32u     /* latest press-while-held pairs kept for the summary */
#define COMBO_CAPTURES 8u      /* latest would_capture verdicts kept for the summary */
/* Resident census: it is emitted inside the export
 * bundle, so it survives Canon overwriting the live rows within seconds. */
#define COMBO_HIST_CODES 256u    /* class-0 event counts by code (u16, saturating) */
#define COMBO_SEQ_EVENTS 512u    /* last events: code u8 | min(dt ticks, 255) u8 */
#define COMBO_SEQ_ANCHOR 64u     /* absolute tick kept for every 64th event */
#define COMBO_SEQ_ESCAPE 255u    /* code byte for codes >= 255, wide or classed events */
#define COMBO_MATRIX 16u         /* first 16 distinct buttons pressed: overlap count / min skew */
#define COMBO_MATRIX_NONE 255u

/* Precondition verdict bits (known mask and pass mask). */
#define COMBO_V_NOT_RECORDING (1u << 0) /* [0x16DD4] == 0 */
#define COMBO_V_SHOT_QUIET (1u << 1)    /* shot counter unchanged for >= 2 s */
#define COMBO_V_GEOMETRY (1u << 2)      /* output 0 (w,h) in {1024x682, 1600x1200} */
#define COMBO_V_MARV (1u << 3)          /* selected MARV: magic, fmt 0x11060200, w/h equal the table */
#define COMBO_V_ALIGN (1u << 4)         /* MARV data and 3*w*h 32-byte aligned */
#define COMBO_V_DEBOUNCE (1u << 5)      /* no would_capture in the previous 2 s */
#define COMBO_V_CONTEXT (1u << 6)       /* candidate allowed in the current context */
#define COMBO_V_CARD (1u << 7)          /* card mounted: never known here (no FIO) */
/* combo_capture.c only: Canon's writer state before any FIO. */
#define COMBO_V_JOB_IDLE (1u << 8)      /* Canon job state (PROP_LAST_JOB_STATE source words) == 0 */
#define COMBO_V_MOVIE_QUIET (1u << 9)   /* recording state 0 for >= CAPTURE_MOVIE_QUIET_TICKS */
#define COMBO_V_ALL 0x3ffu

#define COMBO_REC_STATE_WORD UINT32_C(0x00016dd4)
#define COMBO_VMIX_STATE UINT32_C(0x000199c8)
#define COMBO_VMIX_OUTPUTS UINT32_C(0x00150b80)
#define COMBO_VMIX_OUTPUT_BYTES 52u
#define COMBO_VMIX_OUTPUT_COUNT 4u
#define COMBO_RENDERER_SLOTS UINT32_C(0x00150aa8)
#define COMBO_RENDERER_SLOT_BYTES 12u
#define COMBO_RENDERER_SLOT_COUNT 6u
#define COMBO_MARV_MAGIC UINT32_C(0x5652414d)
#define COMBO_YCCA_FORMAT UINT32_C(0x11060200)
/* A descriptor pointer is dereferenced only inside this range, 4-aligned. */
#define COMBO_POINTER_MIN UINT32_C(0x00100000)
#define COMBO_POINTER_END UINT32_C(0xc0000000) /* MapCpuBuffer asserts at or above (E091BF76) */

typedef uint32_t (*combo_read_fn)(uint32_t address);
typedef unsigned (*combo_counter_fn)(unsigned *context_out, unsigned *value_out);
struct combo_observe_api {
    combo_read_fn read;            /* aligned u32 load from Canon RAM */
    combo_counter_fn read_counter; /* the shutter-feedback capture counter (observer_read_counter) */
};
extern const struct combo_observe_api combo_observe_api;

struct combo_marv { uint32_t pointer, magic, data, format, width, height; };
struct combo_display {
    uint32_t vmix[5];                                  /* 0x199C8 +0 +8 +12 +16(cur) +20 */
    uint32_t output[COMBO_VMIX_OUTPUT_COUNT][9];       /* +0 +4 +16 +20 +32 +36 +40 +44 +48 */
    uint32_t renderer[COMBO_RENDERER_SLOT_COUNT][3];
    struct combo_marv output_marv[COMBO_VMIX_OUTPUT_COUNT][2]; /* +40 (A) and +44 (B) */
    struct combo_marv renderer_marv[COMBO_RENDERER_SLOT_COUNT];
};

struct combo_overlap { uint32_t first, second, ticks; };
struct combo_capture {
    uint32_t candidate, first, second, ticks, known, pass, tick, gui_mode;
};
struct combo_held { uint32_t held, press_tick, press_index; };

struct combo_observe_state {
    uint32_t steps, steps_armed_init, next_seq, drained, lost, torn, unread_after_step;
    uint32_t key_events, other_events, gen_unequal, wide;
    uint32_t key_rows, other_rows, suppressed_rows, live_rows;
    uint32_t press_index, releases_unmatched, presses_repeated;
    struct combo_held held[COMBO_BUTTON_COUNT];
    uint32_t press_count[COMBO_BUTTON_COUNT], release_count[COMBO_BUTTON_COUNT];
    uint32_t other_code[COMBO_OTHER_CODES], other_count[COMBO_OTHER_CODES], other_overflow;
    uint32_t armed[COMBO_PAIR_COUNT], fired[COMBO_PAIR_COUNT];
    uint32_t trigger_presses, trigger_fired, trigger_debounced, trigger_last_tick, trigger_any;
    uint32_t hist_overflow, max_held, seq_count, seq_last_tick, matrix_used, matrix_overflow;
    uint16_t hist[COMBO_HIST_CODES];
    uint16_t seq[COMBO_SEQ_EVENTS];
    uint32_t seq_anchor[COMBO_SEQ_EVENTS / COMBO_SEQ_ANCHOR];
    uint8_t matrix_code[COMBO_MATRIX];
    uint8_t matrix_count[COMBO_MATRIX][COMBO_MATRIX], matrix_skew[COMBO_MATRIX][COMBO_MATRIX];
    uint32_t overlaps, outside_window, extra_keys;
    struct combo_overlap overlap[COMBO_OVERLAPS];
    uint32_t captures, capture_rows;
    struct combo_capture capture[COMBO_CAPTURES];
    uint32_t last_capture_tick, any_capture;
    /* shot counter */
    uint32_t counter_known, counter_context, counter_value, counter_change_tick, counter_faults;
    /* display sampler */
    uint32_t display_samples, snapshots, snapshots_suppressed, cur_flips, last_cur, cur_seen, display_valid, display_countdown;
    struct combo_display last_display;
};
extern struct combo_observe_state combo_observe_state;

/* Capture feedback cues (combo_capture.c) submit only in AudioCtrl state 1
 * (stock idle); reasons 1..11 are rec_tone_preflight's, 32.. combo_capture.h. */
#define COMBO_CUE_ACTRL_MASK (1u << 1)

/* Worker only: one bounded step per 20 ms tick (drain <= 64 records, detector,
 * at most one display sample). Never sleeps, never blocks. */
void combo_observe_step(void);
/* R5C1 summary rows through observer_api.debug, inside the boot_mmu bundle. */
void combo_observe_report(unsigned reports);
/* Pure helpers (host tests and the Python model share their semantics). */
uint32_t combo_button_index(uint32_t code, uint32_t *is_release);
uint32_t combo_display_verdict(const struct combo_display *d, uint32_t *known, uint32_t *geometry);
void combo_sample_display(struct combo_display *d);

/* Row formats: the single owner of their text. Every row is one 84-byte KUWA
 * record (text + 4 bytes per argument <= 72). */
#define CK_FMT_EVENT "R5K1 E=%u T=%u C=%u A=%u B=%u F=%08X"
#define CK_FMT_LOST "R5K1 L=%u N=%u"
#define CW_FMT_HEAD "R5X1 W=%u T=%u X=%u P=%u Q=%u D=%u"
#define CW_FMT_VERDICT "R5X1 W=%u K=%08X V=%08X R=%u S=%u G=%08X"
#define CW_FMT_GEOMETRY "R5X1 W=%u O=%u %u C=%u M=%08X P=%08X"
#define CW_FMT_MARV "R5X1 W=%u G=%08X F=%08X %u %u"
#define CD_FMT_BEGIN "R5Y1 B=%u T=%u C=%u F=%u R=%u"
#define CD_FMT_VMIX "R5Y1 V=%08X %08X %08X %08X"
#define CD_FMT_OUTPUT "R5Y1 O=%u %08X %08X %u %u %08X"
#define CD_FMT_OUTPUT_PTRS "R5Y1 P=%u %08X %08X %08X %08X"
#define CD_FMT_RENDERER "R5Y1 G=%u %08X %08X %08X"
#define CD_FMT_MARV "R5Y1 M=%u %08X %08X %08X %08X %u %u"
#define CD_FMT_END "R5Y1 E=%u"
#define CC_FMT_BEGIN "R5C1 S=" OBSERVER_SOURCE_ID " Q=%u"
#define CC_FMT_RING "R5C1 H=%u L=%u Z=%u U=%u N=%u"
#define CC_FMT_EVENTS "R5C1 K=%u O=%u G=%u W=%u S=%u"
#define CC_FMT_KEYS "R5C1 I=%u R=%u M=%u T=%u"
#define CC_FMT_BUTTON "R5C1 P=%u %u %u H=%u"
#define CC_FMT_OTHER "R5C1 U=%u %u"
#define CC_FMT_PAIRS "R5C1 Y=%u X=%u Z=%u"
#define CC_FMT_OVERLAP "R5C1 Y=%u %u D=%u"
#define CC_FMT_CAPTURES "R5C1 W=%u %u %u %u"
#define CC_FMT_CAPTURE "R5C1 W=%u X=%u D=%u K=%08X V=%08X G=%08X"
#define CC_FMT_DISPLAY "R5C1 D=%u B=%u S=%u F=%u"
#define CC_FMT_SHOT "R5C1 A=%u C=%08X V=%u T=%u F=%u"
#define CC_FMT_CUE "R5C1 Q=%u %u %u R=%u E=%d"
#define CC_FMT_TRIGGER "R5C1 X=%u P=%u F=%u D=%u"
#define CC_FMT_CAP_COUNTS "R5C5 C=%u %u %u %u %u %u"
#define CC_FMT_CAP_OUT_A "R5C5 O=%u %u %u %u %u %u"
#define CC_FMT_CAP_OUT_B "R5C5 P=%u %u %u %u %u N=%u"
#define CC_FMT_CAP_RESULT "R5C5 R=%u X=%u N=%u K=%08X V=%08X G=%08X"
#define CC_FMT_CAP_RC "R5C5 F=%u %d %d %d %d %d %d"
#define CC_FMT_CAP_DESC "R5C5 D=%u %08X %08X %08X %u %u"
#define CC_FMT_CAP_MISC "R5C5 M=%u %u %u %d %u %d"
#define CC_FMT_CAP_TIME "R5C5 T=%u %u %d"
#define CC_FMT_CAP_HELD "R5C5 H=%u B=%u"
#define CC_FMT_CAP_US "R5C5 U=%u %u %u %u %u %u %u %u"
#define CC_FMT_CAP_OUT_C "R5C5 Q=%u %u"
#define CC_FMT_CAP_JOB "R5C5 J=%u %08X %08X %u"
#define CC_FMT_PROBE_HEAD "R5C6 N=%u H=%u %u"
#define CC_FMT_PROBE_ARGS "R5C6 S=%u K=%u %08X %08X %08X %08X %08X"
#define CC_FMT_PROBE_CONTEXT "R5C6 L=%u %08X %u %u %08X %08X"
#define CC_FMT_PROBE_Q "R5C6 Q=%u %08X %08X"
#define CC_FMT_PROBE_DESC "R5C6 D=%u %u %08X %08X %08X %08X %08X"
#define CC_FMT_PROBE_TORN "R5C6 T=%u"
#define CC_FMT_INJECT_HEAD "R5C6 I=%u %u %u %u %u %u %u"
#define CC_FMT_INJECT_ENTRY "R5C6 J=%u %u %08X %u %u %08X %08X"
#define CC_FMT_INJECT_DESC "R5C6 G=%u %08X %08X %u %u %u %u %u"
#define CC_FMT_INJECT_FIO "R5C6 R=%u %d %u %u %d %d %d"
#define CC_FMT_INJECT_US "R5C6 U=%u %u %u %u %u"
#define CC_FMT_INJECT_TORN "R5C6 Z=%u"
#define CC_FMT_INJECT_PENDING "R5C6 P=%u %u %u %u %u %u %u %u"
#define CC_FMT_INJECT_GUARD "R5C6 W=%u %u %u %u %u %u"
#define CC_FMT_INJECT_CONV "R5C6 C=%u %u %d %u %u %u %08X"
#define CC_FMT_CAP_YCA "R5C5 Y=%u %u %d %d %d %d %d %d %d"
#define CC_FMT_CAP_YCA_US "R5C5 Z=%u %u %u %u %u %u"
#define CC_FMT_CAP_OUT_D "R5C5 X=%u"
#define CC_FMT_INDEV_HEAD "R5C7 N=%u %u %u %u"
#define CC_FMT_INDEV_ARGS "R5C7 S=%u %u %08X %08X %08X %08X %08X"
#define CC_FMT_INDEV_CONTEXT "R5C7 L=%u %08X %08X %u %u %08X %08X %08X"
#define CC_FMT_INDEV_BLOCK "R5C7 B=%u %u %08X %08X %08X %08X %08X"
#define CC_FMT_INDEV_TORN "R5C7 T=%u"
#define CC_FMT_INJECT_BUILD "R5C6 V=%u %u %u %u %d %u %u %u"
#define CC_FMT_INJECT_BUILD2 "R5C6 X=%08X %u %d"
#define CC_FMT_INJECT_BUILD3 "R5C6 Y=%u %u %u %u %u %u"
#define CC_FMT_INJECT_BAND "R5C6 B=%u %u %08X %u"
#define CC_FMT_OP_STATE "R5CA S=%u %u %u %u %u %u %u %u"
#define CC_FMT_OP_MORE "R5CA M=%u %u %u %08X"
#define CC_FMT_OP_LAST "R5CA L=%u %u %08X %u %u %08X %u"
#define CC_FMT_OP_CHECK "R5CA C=%d %d %d %u %u %u %u %u"
#define CC_FMT_OP_FM "R5CA F=%08X %08X %08X %08X %08X"
#define CC_FMT_OP_JOB "R5CA J=%u %u %u %08X %u %u %u %08X"
#define CC_FMT_OP_VIEW "R5CA V=%u %08X %d %08X %08X"
#define CC_FMT_OP_DEFER "R5CA D=%u %u %u %u %u %u %08X %d" /* one_press.h struct one_press_defer */
#define CC_FMT_OP_AUTO "R5CA A=%u %u %08X %u %u"        /* its automatic INFO words */
#define CC_FMT_PL_HEAD "R5C9 N=%u"
#define CC_FMT_PL_ENTRY "R5C9 E=%u %u %u %08X %08X %08X"
#define CC_FMT_PL_OUT "R5C9 O=%u %u %u %u %u %u"
#define CC_FMT_PL_ADDR "R5C9 A=%u %u %08X %08X %08X %08X"
#define CC_FMT_PL_INFO "R5C9 I=%u %u %08X %08X %08X %08X %08X"
#define CC_FMT_PL_RC "R5C9 R=%u %d %d %d %d %d %d"
#define CC_FMT_PL_US "R5C9 U=%u %u %u %u %u"
#define CC_FMT_PL_TORN "R5C9 T=%u"
#define CC_FMT_HIST "R5C2 H=%08X %08X %08X %08X %08X"
#define CC_FMT_HIST_TAIL "R5C2 O=%u M=%u"
#define CC_FMT_SEQ_HEAD "R5C3 B=%u N=%u"
#define CC_FMT_SEQ_ANCHOR "R5C3 A=%u T=%u"
#define CC_FMT_SEQ "R5C3 Q=%u %08X %08X %08X %08X %08X"
#define CC_FMT_MATRIX_MAP "R5C4 M=%08X %08X %08X %08X U=%u O=%u"
#define CC_FMT_MATRIX_COUNT "R5C4 N=%u %08X %08X %08X %08X"
#define CC_FMT_MATRIX_SKEW "R5C4 K=%u %08X %08X %08X %08X"
#define CC_FMT_END "R5C1 E=%u"
/* Live-row and summary-row inventory, emission order, argument names. */
#define COMBO_LIVE_ROWS(X) \
    X(event, CK_FMT_EVENT, "seq tick code arg2 arg3 context") \
    X(lost, CK_FMT_LOST, "lost_total next_seq") \
    X(capture_head, CW_FMT_HEAD, "capture tick candidate first second ticks") \
    X(capture_verdict, CW_FMT_VERDICT, "capture known pass rec_state shot_quiet_ticks gui_mode") \
    X(capture_geometry, CW_FMT_GEOMETRY, "capture width height cur marv_pointer data") \
    X(capture_marv, CW_FMT_MARV, "capture magic format marv_width marv_height") \
    X(display_begin, CD_FMT_BEGIN, "snapshot tick cur cur_flips rec_state") \
    X(display_vmix, CD_FMT_VMIX, "vmix_0 vmix_8 vmix_12 vmix_20") \
    X(display_output, CD_FMT_OUTPUT, "output w0 w4 width height dirty") \
    X(display_output_ptrs, CD_FMT_OUTPUT_PTRS, "output w32 w36 marv_a marv_b") \
    X(display_renderer, CD_FMT_RENDERER, "slot w0 w4 w8") \
    X(display_marv, CD_FMT_MARV, "id pointer magic data format width height") \
    X(display_end, CD_FMT_END, "snapshot")
#define COMBO_SUMMARY_ROWS(X) \
    X(begin, CC_FMT_BEGIN, "report") \
    X(ring, CC_FMT_RING, "written drained lost torn unread") \
    X(events, CC_FMT_EVENTS, "key_events other_events gen_unequal wide steps") \
    X(keys, CC_FMT_KEYS, "presses_repeated releases_unmatched suppressed_rows live_rows") \
    X(button, CC_FMT_BUTTON, "code presses releases held") \
    X(other, CC_FMT_OTHER, "code count") \
    X(pairs, CC_FMT_PAIRS, "overlaps outside_window extra_keys") \
    X(overlap, CC_FMT_OVERLAP, "first second ticks") \
    X(captures, CC_FMT_CAPTURES, "captures fired_trigger fired_2 fired_3") \
    X(capture, CC_FMT_CAPTURE, "capture candidate ticks known pass gui_mode") \
    X(display, CC_FMT_DISPLAY, "samples snapshots suppressed cur_flips") \
    X(shot, CC_FMT_SHOT, "known context value change_tick faults") \
    X(cue, CC_FMT_CUE, "clicks bips skipped reason last_rc") \
    X(trigger, CC_FMT_TRIGGER, "code presses fired debounced") \
    X(cap_counts, CC_FMT_CAP_COUNTS, "presses repeats debounced attempts published lost") \
    X(cap_out_a, CC_FMT_CAP_OUT_A, "ok busy recording shot display card") \
    X(cap_out_b, CC_FMT_CAP_OUT_B, "namespace create write close flush next_number") \
    X(cap_held, CC_FMT_CAP_HELD, "stale_held boot") \
    X(cap_out_c, CC_FMT_CAP_OUT_C, "writing movie") \
    X(cap_result, CC_FMT_CAP_RESULT, "seq outcome number known pass gui_mode") \
    X(cap_rc, CC_FMT_CAP_RC, "seq rc_create rc_header rc_data rc_close rc_flush rc_remove") \
    X(cap_desc, CC_FMT_CAP_DESC, "seq marv data format width height") \
    X(cap_misc, CC_FMT_CAP_MISC, "seq probes bytes rc_map_header sel rc_mount") \
    X(cap_time, CC_FMT_CAP_TIME, "seq duration_ticks rc_map_data") \
    X(cap_us, CC_FMT_CAP_US, "seq us_start us_probed us_create us_header us_data us_close us_flush") \
    X(cap_job, CC_FMT_CAP_JOB, "seq job_main job_sub rec_idle_ticks") \
    X(probe_head, CC_FMT_PROBE_HEAD, "claimed encode develop_end") \
    X(probe_args, CC_FMT_PROBE_ARGS, "seq hook r0 r1 r2 r3 sp") \
    X(probe_context, CC_FMT_PROBE_CONTEXT, "seq lr us tick mpidr flags") \
    X(probe_q, CC_FMT_PROBE_Q, "seq q0 q1") \
    X(probe_desc, CC_FMT_PROBE_DESC, "seq index w0 w1 w2 w3 w4") \
    X(probe_torn, CC_FMT_PROBE_TORN, "seq") \
    X(inject_head, CC_FMT_INJECT_HEAD, "claimed encode_main encode_other develop_save develop_cancel develop_other armed") \
    X(inject_entry, CC_FMT_INJECT_ENTRY, "seq outcome flags us age_us mpidr sp") \
    X(inject_desc, CC_FMT_INJECT_DESC, "seq lo hi base_w xoff yoff width height") \
    X(inject_fio, CC_FMT_INJECT_FIO, "seq rc_size size_lo size_hi fd rd rc_close") \
    X(inject_us, CC_FMT_INJECT_US, "seq us_size us_open us_read us_close") \
    X(inject_torn, CC_FMT_INJECT_TORN, "seq") \
    X(inject_pending, CC_FMT_INJECT_PENDING, "pending set_tick tick sets clears consumed expired not_pending") \
    X(inject_guard, CC_FMT_INJECT_GUARD, "capture busy develop develop_tick refused_capture refused_develop") \
    X(inject_conv, CC_FMT_INJECT_CONV, "seq stage rc source_bytes y22_written us_conv source_wh") \
    X(cap_yca, CC_FMT_CAP_YCA, "seq yca_outcome rc_yca_remove rc_yca_create rc_yca_header rc_yca_data rc_yca_close rc_yca_flush rc_yca_cleanup") \
    X(cap_yca_us, CC_FMT_CAP_YCA_US, "seq us_yca_start us_yca_create us_yca_header us_yca_data us_yca_flush") \
    X(cap_out_d, CC_FMT_CAP_OUT_D, "develop") \
    X(indev_head, CC_FMT_INDEV_HEAD, "claimed start done end") \
    X(indev_args, CC_FMT_INDEV_ARGS, "seq hook r0 r1 r2 r3 lr") \
    X(indev_context, CC_FMT_INDEV_CONTEXT, "seq sp arg5 us tick mpidr flags gui") \
    X(indev_block, CC_FMT_INDEV_BLOCK, "seq index w0 w1 w2 w3 w4") \
    X(indev_torn, CC_FMT_INDEV_TORN, "seq") \
    X(pl_head, CC_FMT_PL_HEAD, "claimed") \
    X(pl_entry, CC_FMT_PL_ENTRY, "seq number tick gui_mode dvctrl mask") \
    X(pl_out, CC_FMT_PL_OUT, "seq out outcome info_index stable bytes") \
    X(pl_addr, CC_FMT_PL_ADDR, "seq slot tft_lo tft_hi evf_lo evf_hi") \
    X(pl_info, CC_FMT_PL_INFO, "seq index w0 w1 w2 w3 w4") \
    X(pl_rc, CC_FMT_PL_RC, "seq rc_create rc_header rc_data rc_close rc_flush rc_remove") \
    X(pl_us, CC_FMT_PL_US, "seq us_start us_create us_data us_close") \
    X(pl_torn, CC_FMT_PL_TORN, "seq") \
    X(inject_build, CC_FMT_INJECT_BUILD, "builds failures number stage rc source_bytes y22_written us") \
    X(inject_build2, CC_FMT_INJECT_BUILD2, "source_wh pic_status pic_rc") \
    X(inject_build3, CC_FMT_INJECT_BUILD3, "us_yca us_pic us_comp us_fill us_map us_write") \
    X(inject_band, CC_FMT_INJECT_BAND, "band us_band band_addr band_size") \
    X(op_state, CC_FMT_OP_STATE, "state latched attempts jobs saves cancels ends timeouts") \
    X(op_more, CC_FMT_OP_MORE, "refused late start_tick end_arg") \
    X(op_last, CC_FMT_OP_LAST, "outcome number gui_mode dir file handle pending_tick") \
    X(op_check, CC_FMT_OP_CHECK, "rc_open rc_read fm_rc develop_state gui_busy indev_busy pic_outcome pic_bytes") \
    X(op_fm, CC_FMT_OP_FM, "i0 i1 i2 i3 i4") \
    X(op_job, CC_FMT_OP_JOB, "us_start us_done us_end done_result end_reason dry_seen dry_ok dry_wh") \
    X(op_view, CC_FMT_OP_VIEW, "source view_handle view_fm_rc view_i0 view_i2") \
    X(op_defer, CC_FMT_OP_DEFER, "defer why xpan marked starts fallbacks last_good last_good_rc") \
    X(op_auto, CC_FMT_OP_AUTO, "auto_for auto_why auto_gui auto_posts auto_ticks") \
    X(hist, CC_FMT_HIST, "h0 h1 h2 h3 h4") \
    X(hist_tail, CC_FMT_HIST_TAIL, "overflow max_held") \
    X(seq_head, CC_FMT_SEQ_HEAD, "first count") \
    X(seq_anchor, CC_FMT_SEQ_ANCHOR, "index tick") \
    X(seq, CC_FMT_SEQ, "index s0 s1 s2 s3 s4") \
    X(matrix_map, CC_FMT_MATRIX_MAP, "m0 m1 m2 m3 used overflow") \
    X(matrix_count, CC_FMT_MATRIX_COUNT, "slot n0 n1 n2 n3") \
    X(matrix_skew, CC_FMT_MATRIX_SKEW, "slot k0 k1 k2 k3") \
    X(end, CC_FMT_END, "report")
#endif /* __ASSEMBLER__ */
#endif
