#ifndef R5_Y22_STREAM_H
#define R5_Y22_STREAM_H
/* Stage A2 (opt-in --hw-jpeg-y22): a captured display layer (YCCA, UYVA 6
 * bytes per pixel pair, unsigned chroma) as HWJPG.Y22, the 2400x1600
 * signed-chroma UYVY frame hw_jpeg_inject.c loads into Canon's S2 encoder
 * (DESIGN-HANDOFF.md sections 1.2 and 2). The mapping is make_y22.py's
 * (its docstring owns the formulas; test_y22_stream.py checks byte identity):
 *   1024x682:  full frame, sy = dy*682/1600, sx = x*1024/2400 (floor)
 *   1600x1200: 2132 active columns, 134-pixel black (00 00 00 00) pillars,
 *              sy = dy*1200/1600, sx = (x-134)*1600/2132 (floor)
 * Y0 / Y1 from sx0 / sx1, Cb / Cr from the source pair of sx0, chroma ^ 0x80.
 * Division-free (no divide libcall in the freestanding link): rows and columns
 * advance with remainder counters. Each source row is fetched once into the
 * caller's row buffer, then every output row that maps to it is converted
 * from there. Both admitted heights are below 1600, so the fetches ask for
 * every source row exactly once, in order 0, 1, 2, ... (a file can be read
 * sequentially). build-11: the fetch reads HWJPG.YCA on ShootVfxStage
 * (hw_jpeg_inject.c). Output is whole strips of Y22_STRIP_ROWS rows:
 * 8 x 4800 = 38400 B = 75 sectors, so every FIO write is whole sectors. */
#include <stdint.h>

#define Y22_WIDTH 2400u
#define Y22_HEIGHT 1600u
#define Y22_ROW_BYTES (Y22_WIDTH * 2u)
#define Y22_BYTES (Y22_ROW_BYTES * Y22_HEIGHT) /* 7680000 */
#define Y22_STRIP_ROWS 8u
#define Y22_STRIP_BYTES (Y22_STRIP_ROWS * Y22_ROW_BYTES) /* 38400 */
#define Y22_SECTOR 512u
#define Y22_EVF_ACTIVE 2132u
#define Y22_EVF_PILLAR 134u
#define Y22_SOURCE_MAX_ROW_BYTES (3u * 1600u)

_Static_assert(Y22_STRIP_BYTES % Y22_SECTOR == 0u, "every strip write is whole sectors");
_Static_assert(Y22_HEIGHT % Y22_STRIP_ROWS == 0u, "whole strips");
_Static_assert(Y22_EVF_PILLAR % 2u == 0u && 2u * Y22_EVF_PILLAR + Y22_EVF_ACTIVE == Y22_WIDTH, "pillars are whole pairs");

/* Source row sy (3 * width bytes) into row; 0 on success, else a nonzero code
 * that stops the stream (y22_stream.error). */
typedef int32_t (*y22_fetch_fn)(void *ctx, uint32_t sy, uint8_t *row, uint32_t bytes);

struct y22_stream {
    y22_fetch_fn fetch;
    void *ctx;
    uint8_t *row;                                   /* caller's row buffer, >= 3 * width */
    uint32_t width, height, pitch, active, pillar;
    uint32_t dy, sy, sy_rem, cached_sy, cached;     /* row walk; cached: row holds source row cached_sy */
    int32_t error;                                  /* the first nonzero fetch code */
};

/* Returns Y22_BYTES, or 0 (nothing set up) for an unadmitted geometry, a
 * missing fetch or a short row buffer. */
uint32_t y22_stream_init(struct y22_stream *s, uint32_t width, uint32_t height, uint8_t *row, uint32_t row_bytes,
                         y22_fetch_fn fetch, void *ctx);
/* Fills the next strip (Y22_STRIP_BYTES) into out; returns its bytes, 0 when
 * the frame is complete or a fetch failed (then error is nonzero). */
uint32_t y22_stream_fill(struct y22_stream *s, uint8_t *out);
/* --one-press: one source row of the premultiplied OSD (UYVA, 6 bytes per pair)
 * over the same row of the picture layer (10-bit 4:2:2, 5 bytes per pair: a
 * little-endian 40-bit word Cb | Y0 << 10 | Cr << 20 | Y1 << 30, unsigned chroma;
 * D from returned-16, all five planes decode only this way, owner-confirmed
 * colours), in place; A is left as it is (the mapping ignores it). Per pair,
 * with P = picture sample >> 2, d(x) = floor((x + 127) / 255):
 *   Y  = min(255, Y_osd + d((255 - A) * P_Y))           for Y0 / A0 and Y1 / A1
 *   C  = clamp(C_osd + d(B * P_C) - d(B * 128)),  B = 255 - ((A0 + A1 + 1) >> 1)
 * make_y22.py --pic owns the same formula (byte-identical, test_build14.py). */
void y22_composite_row(uint8_t *osd, const uint8_t *picture, uint32_t width);
#endif
