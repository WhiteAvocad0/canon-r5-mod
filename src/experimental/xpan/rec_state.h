#ifndef R5_REC_STATE_H
#define R5_REC_STATE_H
#include <stdint.h>
#include <stdatomic.h>
/* Issue #14 record indicator (opt-in --record-indicator and/or
 * --record-stop-indicator). Owner of the GUI_SetRecordingState E07C6FBC entry
 * hook (r0 = new state) and of the REC-press and ended-take facts. Counters
 * only: nothing audio-related runs here.
 *
 * RecState word 0x16DD4 (getter E07C6FB8 -> E0A98D0E reads [0x16DD0+4]; single
 * writer E0A98D2C). REC press: old 0 -> new 1 from the BL at E0B0B87E (LR
 * E0B0B883; ROM E0B0B87C movs r0,#1, E0B0B87E bl GUI_SetRecordingState),
 * inside handleMovieButton (E0B0B758). Other writers of 1 (E0B0B730 restate,
 * E0B0C5D6 timelapse) and every other transition are ignored. */
#define REC_STATE_WORD UINT32_C(0x00016dd4)
#define REC_STATE_PRESSED UINT32_C(1)
#define REC_PRESS_CALLER UINT32_C(0xe0b0b883)
/* Confirmed normal-movie start: old 2 -> new 3 from the BL at E0B0BD28 (LR
 * E0B0BD2D). Timelapse (E06011EB) and streaming (E0B0C2B5) reach 3 from other
 * callers and never confirm a take. */
#define REC_STATE_RECORDING UINT32_C(3)
#define REC_CONFIRMED_CALLER UINT32_C(0xe0b0bd2d)
/* Take ended: any GUI_SetRecordingState(0) (RecState back to idle) after a
 * confirmed start in the same take, from any caller. Every literal-addressed
 * write of [0x16DD4] goes through the hooked setter (single store E0A98D2C,
 * entered only by the b.w at E07C6FD2), and all 26 setter callers pass an
 * immediate 0, 1, 2, 3 or 5; 14 of them pass 0, among them
 * LOCAL_MOVIE_RECORD_STOP E0B10900. Private hardware logs: manual and 1:00
 * auto-stops both run 3 -> 5 (E0B0B5BF), 5 -> 5 (E0B10977), then idle. A start
 * that never reached 3 (1 -> 0, 2 -> 0, 2 -> 5 -> 0) is no ended take. */
#define REC_STATE_IDLE UINT32_C(0)
/* Monotonic counts of REC presses and of ended takes. Single writer (the hook,
 * GUI task), release increments; the only consumer (rec_tone worker step)
 * loads with acquire. */
extern _Atomic uint32_t rec_press_seq;
extern _Atomic uint32_t rec_stop_seq;
/* saved[0..5] = caller r0,r1,r2,r3,r12,lr at GUI_SetRecordingState entry. */
void rec_state_observe(const uint32_t *saved);
void rec_state_entry(void);
#endif
