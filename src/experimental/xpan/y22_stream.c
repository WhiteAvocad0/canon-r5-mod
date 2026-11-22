/* Display buffer -> HWJPG.Y22 strips (y22_stream.h). */
#include "y22_stream.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#else
#define CODE
#endif

CODE uint32_t y22_stream_init(struct y22_stream *s, uint32_t width, uint32_t height, uint8_t *row, uint32_t row_bytes,
                              y22_fetch_fn fetch, void *ctx)
{
    uint32_t active, pillar;
    if (width == 1024u && height == 682u) {
        active = Y22_WIDTH;
        pillar = 0u;
    } else if (width == 1600u && height == 1200u) {
        active = Y22_EVF_ACTIVE;
        pillar = Y22_EVF_PILLAR;
    } else {
        return 0u;
    }
    if (!row || !fetch || row_bytes < 3u * width)
        return 0u;
    s->fetch = fetch;
    s->ctx = ctx;
    s->row = row;
    s->error = 0;
    s->width = width;
    s->height = height;
    s->pitch = 3u * width;
    s->active = active;
    s->pillar = pillar;
    s->dy = s->sy = s->sy_rem = s->cached_sy = s->cached = 0u;
    return Y22_BYTES;
}

/* One output row from the cached source row. */
CODE static void convert_row(const struct y22_stream *s, uint8_t *o)
{
    const uint8_t *r = s->row;
    uint32_t j = 0u, pairs_left = s->pillar / 2u;
    for (; j < pairs_left; ++j, o += 4) /* left pillar */
        o[0] = o[1] = o[2] = o[3] = 0u;
    uint32_t sx = 0u, rem = 0u; /* sx = floor((x - pillar) * width / active), walked per pixel */
    for (uint32_t k = 0u; k < s->active / 2u; ++k, o += 4) {
        uint32_t sx0 = sx;
        rem += s->width;
        while (rem >= s->active) {
            rem -= s->active;
            ++sx;
        }
        uint32_t sx1 = sx;
        rem += s->width;
        while (rem >= s->active) {
            rem -= s->active;
            ++sx;
        }
        const uint8_t *p0 = r + (sx0 >> 1) * 6u, *p1 = r + (sx1 >> 1) * 6u;
        o[0] = (uint8_t)(p0[0] ^ 0x80u);
        o[1] = p0[1u + 2u * (sx0 & 1u)];
        o[2] = (uint8_t)(p0[2] ^ 0x80u);
        o[3] = p1[1u + 2u * (sx1 & 1u)];
    }
    for (j = 0u; j < pairs_left; ++j, o += 4) /* right pillar */
        o[0] = o[1] = o[2] = o[3] = 0u;
}

CODE uint32_t y22_stream_fill(struct y22_stream *s, uint8_t *out)
{
    if (s->dy >= Y22_HEIGHT || s->error)
        return 0u;
    for (uint32_t i = 0u; i < Y22_STRIP_ROWS; ++i, ++s->dy) {
        if (!s->cached || s->cached_sy != s->sy) {
            int32_t rc = s->fetch(s->ctx, s->sy, s->row, s->pitch);
            if (rc) {
                s->error = rc;
                return 0u;
            }
            s->cached_sy = s->sy;
            s->cached = 1u;
        }
        convert_row(s, out + i * Y22_ROW_BYTES);
        s->sy_rem += s->height; /* sy = floor((dy + 1) * height / 1600) for the next row */
        while (s->sy_rem >= Y22_HEIGHT) {
            s->sy_rem -= Y22_HEIGHT;
            ++s->sy;
        }
    }
    return Y22_STRIP_BYTES;
}

/* floor((x + 127) / 255) for x <= 65025 without a divide: exact for t < 65535. */
CODE static uint32_t div255(uint32_t x)
{
    uint32_t t = x + 127u;
    return (t + 1u + (t >> 8)) >> 8;
}

CODE static uint8_t clamp255(int32_t v)
{
    return (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v);
}

CODE void y22_composite_row(uint8_t *osd, const uint8_t *picture, uint32_t width)
{
    for (uint32_t j = 0; j < width / 2u; ++j, osd += 6, picture += 5) {
        uint32_t v = (uint32_t)picture[0] | (uint32_t)picture[1] << 8 | (uint32_t)picture[2] << 16 | (uint32_t)picture[3] << 24;
        uint32_t cb = (v & 1023u) >> 2, y0 = ((v >> 10) & 1023u) >> 2, cr = ((v >> 20) & 1023u) >> 2;
        uint32_t y1 = ((v >> 30) | (uint32_t)picture[4] << 2) >> 2;
        uint32_t a0 = osd[4], a1 = osd[5], b = 255u - ((a0 + a1 + 1u) >> 1), base = div255(b * 128u);
        uint32_t t0 = osd[1] + div255((255u - a0) * y0), t1 = osd[3] + div255((255u - a1) * y1);
        osd[1] = (uint8_t)(t0 > 255u ? 255u : t0);
        osd[3] = (uint8_t)(t1 > 255u ? 255u : t1);
        osd[0] = clamp255((int32_t)(osd[0] + div255(b * cb)) - (int32_t)base);
        osd[2] = clamp255((int32_t)(osd[2] + div255(b * cr)) - (int32_t)base);
    }
}
