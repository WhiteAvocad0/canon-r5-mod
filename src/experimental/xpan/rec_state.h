#ifndef R5_REC_STATE_H
#define R5_REC_STATE_H
#include <stdint.h>
#include <stdatomic.h>
/* Issue #14 record indicator (opt-in --record-indicator). Owner of the
 * GUI_SetRecordingState E07C6FBC entry hook (r0 = new state) and of the
 * confirmed REC-press fact. Counter only: nothing audio-related runs here.
 *
 * RecState word 0x16DD4 (getter E07C6FB8 -> E0A98D0E reads [0x16DD0+4]; single
 * writer E0A98D2C). REC press: old 0 -> new 1 from the BL at E0B0B87E (LR
 * E0B0B883; ROM E0B0B87C movs r0,#1, E0B0B87E bl GUI_SetRecordingState),
 * inside handleMovieButton (E0B0B758). Other writers of 1 (E0B0B730 restate,
 * E0B0C5D6 timelapse) and every other transition are ignored. */
#define REC_STATE_WORD UINT32_C(0x00016dd4)
#define REC_STATE_PRESSED UINT32_C(1)
#define REC_PRESS_CALLER UINT32_C(0xe0b0b883)
/* Monotonic count of REC presses. Single writer (the hook, GUI task), release
 * increment; the only consumer (rec_tone worker step) loads with acquire. */
extern _Atomic uint32_t rec_press_seq;
/* saved[0..5] = caller r0,r1,r2,r3,r12,lr at GUI_SetRecordingState entry. */
void rec_state_observe(const uint32_t *saved);
void rec_state_entry(void);
#endif
