#ifndef R5_PICTURE_LAYER_H
#define R5_PICTURE_LAYER_H
/* P0 + Stage I (opt-in --picture-layer, build-12; issue-research/screenshot/
 * all-layers-research.md sections 1, 2, 4). Owner of the picture-layer record
 * ABI (ring, rows, the .PIC header) shared by combo_capture.c (the RATE writer,
 * its only producer), picture_layer.c (sampling, selection, report) and the host
 * tools (combo_observe_log.py, pic2png.py).
 *
 * Display driver state (O, ROM f317c69f..., re-verified in build-12/NOTES.md):
 *  dvctrl = *(u32 *)0x106A4 (literal E0260A5C). DISP_GetImageAddress(out)
 *  E07E0324 -> E0260A0A returns the Active u64 at dvctrl + 0x130 (TFT, out 0)
 *  or + 0x140 (EVF, out 1). The panel / EVF vsync handlers (E063A9BA, E063AAB4)
 *  promote Ready +0x128 / +0x138 to Active and copy the 0x98-byte infos
 *  +0x1C8/+0x2F8 -> +0xAB0/+0xB48 (TFT) and +0x390/+0x4C0 -> +0xBE0/+0xC78 (EVF).
 *  Active displays: *(*(u32 *)0x10684 + 0x18) (E016CC58): bit 0 TFT, bit 4 EVF.
 *  DISPIMAGEINFO (validator E07DF708): +0 kind, +4 OutKind, +8 IBUS u64, +0x14
 *  WholeW, +0x18 WholeH, +0x1C/+0x20 InputStart, +0x24/+0x28 OutputStart,
 *  +0x2C/+0x30 Disp size, +0x34 colour space, +0x38 range/depth, +0x3C, +0x74.
 *  Canon's VramRead (E016E140 writer) writes Active with FIO_WriteFile_IBUS
 *  E07E283E (fd, 0, lo, hi, bytes) straight from IBUS: no CPU copy. Stage I does
 *  the same for the selected output only.
 *
 * Format (build-13, O): +0x34 names (table E16DA910) 0 709, 1 601, 2 2020,
 * 3 C_GAMUT; +0x38 names (E16DA920) 0 0_255, 1 16_254, 2 16_235, 3 0_1023,
 * 4 64_1019, 5 64_940. The live panel / EVF planes are range 3 (returned-14) and
 * ▶'s own builder E05E0A6E writes +0x38 = 3 or 5 (E05E0B28). Ranges 3 and 5 are
 * 10-bit 4:2:2 at 20 bits per pixel: EngCalcLib's format table E09F67B2 gives
 * code 6 = 20 bpp, and both Canon dumps of these planes size them W*H*5/2
 * (VramRead E016E1A0..AA, RawPbTest "Yuv(Panel).yuv (%d x %d(10bit))"
 * E0A7A592..9C). Only ranges 3 and 5 are accepted. The bit layout inside each
 * 5-byte pixel pair and the chroma sign are U (no software packer in the ROM);
 * the host decoder chooses them from the data (pic2png.py). */
#define PL_DVCTRL_POINTER 0x000106a4u
#define PL_MASK_POINTER 0x00010684u
#define PL_MASK_OFFSET 0x18u
#define PL_READY_TFT 0x128u
#define PL_ACTIVE_TFT 0x130u
#define PL_READY_EVF 0x138u
#define PL_ACTIVE_EVF 0x140u
#define PL_INFO_TFT_A 0xab0u
#define PL_INFO_TFT_B 0xb48u
#define PL_INFO_EVF_A 0xbe0u
#define PL_INFO_EVF_B 0xc78u
#define PL_DVCTRL_SPAN 0xd10u /* the highest offset read (< +0xC78 + 0x98) */
#define PL_INFO_BYTES 0x98u
#define PL_MASK_TFT 0x1u
#define PL_MASK_EVF 0x10u
#define PL_RAM_MIN 0x00010000u
#define PL_RAM_END 0x40000000u
#define PL_MAX_BYTES 4800000u /* EVF 1600x1200 10-bit, the largest panel plane */
#define PL_MAX_DIM 4096u
#define PL_HEADER_BYTES 512u
#define PL_HEADER_VERSION 1u
#define PL_INFO_WORDS 16u /* recorded per info: see pl_info_offsets */
#define PL_ENTRIES 4u
#define PL_REPORTED 3u

/* outcomes (Stage I) */
#define PL_NONE 0
#define PL_OK 1
#define PL_SKIP_DVCTRL 2   /* dvctrl pointer unreadable */
#define PL_SKIP_OUTPUT 3   /* VMIX output not 1024x682 (TFT) or 1600x1200 (EVF) */
#define PL_SKIP_MASK 4     /* that output is not active */
#define PL_SKIP_INFO 5     /* no kind-1 info of that output holds Active */
#define PL_SKIP_ADDRESS 6  /* bank not 1..5 or address not 8-aligned */
#define PL_SKIP_FORMAT 7   /* not 8-bit 4:2:2, size, pitch or placement out of bounds */
#define PL_FAILED_CREATE 8
#define PL_FAILED_WRITE 9  /* header or data short: file removed */
#define PL_FAILED_CLOSE 10 /* close or flush < 0: file removed */

#ifndef __ASSEMBLER__
#include <stdint.h>
struct pl_entry {
    uint32_t seq, number, tick, gui_mode, dvctrl, mask, out, outcome, info_index, stable, bytes, whole_w;
    uint32_t addr[4][4]; /* [ready before, active before, active after the YCA, active after the PIC][tft lo, hi, evf lo, hi] */
    uint32_t info[4][PL_INFO_WORDS]; /* TFT A, TFT B, EVF A, EVF B */
    int32_t rc_create, rc_header, rc_data, rc_close, rc_flush, rc_remove;
    uint32_t us_start, us_create, us_data, us_close;
};
_Static_assert(sizeof(struct pl_entry) == 408u, "pl entry ABI");
struct pl_ring { struct pl_entry entries[PL_ENTRIES]; uint32_t claimed; };

/* GuiMainTask (combo_capture_gui), in order. S0 before the YCA write. */
void pl_sample(struct pl_entry *e);
/* After the YCA write: records Active (slot 2) and selects. Returns PL_OK with
 * *lo, *hi, *bytes and the 512-byte .PIC header filled, or the skip reason. */
uint32_t pl_select(struct pl_entry *e, uint32_t width, uint32_t height, uint32_t *lo, uint32_t *hi, uint32_t *bytes,
                   uint8_t header[PL_HEADER_BYTES]);
#endif
#endif
