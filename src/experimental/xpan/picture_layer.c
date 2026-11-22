/* P0 sampling, Stage I selection and the report (picture_layer.h). Reads Canon
 * RAM only through combo_observe_api.read (aligned u32, guarded ranges). */
#include "picture_layer.h"
#include "combo_observe.h"
#include "observer.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

/* The recorded words of each info: kind, OutKind, IBUS lo, hi, WholeW, WholeH,
 * InX, InY, OutX, OutY, DispW, DispH, colour space, range, +0x3C, init flag. */
static const uint16_t pl_infos[4] DATA = {PL_INFO_TFT_A, PL_INFO_TFT_B, PL_INFO_EVF_A, PL_INFO_EVF_B};

#define RD(a) combo_observe_api.read(a)

CODE static uint32_t ram(uint32_t p, uint32_t span)
{
    return !(p & 3u) && p >= PL_RAM_MIN && p < PL_RAM_END - span;
}

CODE static void addresses(const struct pl_entry *e, uint32_t *slot, uint32_t tft, uint32_t evf)
{
    slot[0] = RD(e->dvctrl + tft);
    slot[1] = RD(e->dvctrl + tft + 4u);
    slot[2] = RD(e->dvctrl + evf);
    slot[3] = RD(e->dvctrl + evf + 4u);
}

CODE void pl_sample(struct pl_entry *e)
{
    uint32_t dv = RD(PL_DVCTRL_POINTER), mp = RD(PL_MASK_POINTER);
    e->dvctrl = dv;
    e->mask = ram(mp, PL_MASK_OFFSET + 4u) ? RD(mp + PL_MASK_OFFSET) : 0xffffffffu;
    e->stable = 2u; /* not measured: set only around a PIC write (build-12 printed 0 for skips) */
    if (!ram(dv, PL_DVCTRL_SPAN))
        return;
    addresses(e, e->addr[0], PL_READY_TFT, PL_READY_EVF);
    addresses(e, e->addr[1], PL_ACTIVE_TFT, PL_ACTIVE_EVF);
}

CODE static void put32(uint8_t *p, uint32_t v)
{
    for (uint32_t i = 0; i < 4u; ++i)
        p[i] = (uint8_t)(v >> (8u * i));
}

CODE uint32_t pl_select(struct pl_entry *e, uint32_t width, uint32_t height, uint32_t *lo, uint32_t *hi, uint32_t *bytes,
                        uint8_t header[PL_HEADER_BYTES])
{
    uint32_t dv = e->dvctrl;
    if (!ram(dv, PL_DVCTRL_SPAN))
        return PL_SKIP_DVCTRL;
    addresses(e, e->addr[2], PL_ACTIVE_TFT, PL_ACTIVE_EVF);
    if (width == 1024u && height == 682u)
        e->out = 0u;
    else if (width == 1600u && height == 1200u)
        e->out = 1u;
    else
        return PL_SKIP_OUTPUT;
    if (e->mask == 0xffffffffu || !(e->mask & (e->out ? PL_MASK_EVF : PL_MASK_TFT)))
        return PL_SKIP_MASK;
    /* Active now (just read) must be the one sampled before the YCA write: a
     * changed buffer is a moving live view; the dump is still the current one. */
    uint32_t a_lo = e->addr[2][2u * e->out], a_hi = e->addr[2][2u * e->out + 1u];
    uint32_t pick = 4u;
    for (uint32_t k = 2u * e->out; k < 2u * e->out + 2u; ++k) {
        /* re-read now: the info that holds the current Active address */
        uint32_t base = dv + pl_infos[k];
        uint32_t kind = RD(base), out = RD(base + 4u), ilo = RD(base + 8u), ihi = RD(base + 12u);
        if (kind == 1u && out == e->out && ilo == a_lo && ihi == a_hi) {
            pick = k;
            break;
        }
    }
    e->info_index = pick;
    if (pick == 4u)
        return PL_SKIP_INFO;
    if (a_hi - 1u >= 5u || (a_lo & 7u))
        return PL_SKIP_ADDRESS;
    uint32_t base = dv + pl_infos[pick];
    uint32_t ww = RD(base + 0x14u), wh = RD(base + 0x18u), ix = RD(base + 0x1cu), iy = RD(base + 0x20u);
    uint32_t dw = RD(base + 0x2cu), dh = RD(base + 0x30u), cs = RD(base + 0x34u), range = RD(base + 0x38u);
    e->whole_w = ww;
    if (!ww || !wh || ww > PL_MAX_DIM || wh > PL_MAX_DIM || (ww & 31u) || cs >= 4u || (range != 3u && range != 5u) || ix > ww ||
        dw > ww - ix || iy > wh || dh > wh - iy)
        return PL_SKIP_FORMAT;
    uint32_t n = (ww * wh * 5u) >> 1; /* 20 bits per pixel; ww % 32 == 0 keeps it whole */
    if (n > PL_MAX_BYTES || (n & 511u))
        return PL_SKIP_FORMAT;
    e->bytes = n;
    *lo = a_lo;
    *hi = a_hi;
    *bytes = n;
    for (uint32_t i = 0; i < PL_HEADER_BYTES; ++i)
        header[i] = 0u;
    header[0] = 'R';
    header[1] = '5';
    header[2] = 'P';
    header[3] = 'I';
    header[4] = (uint8_t)PL_HEADER_VERSION;
    header[6] = (uint8_t)(PL_HEADER_BYTES & 0xffu);
    header[7] = (uint8_t)(PL_HEADER_BYTES >> 8);
    put32(header + 8, e->out);
    put32(header + 12, e->number);
    put32(header + 16, e->tick);
    put32(header + 20, e->gui_mode);
    put32(header + 24, e->mask);
    put32(header + 28, a_lo);
    put32(header + 32, a_hi);
    put32(header + 36, e->addr[0][2u * e->out]);
    put32(header + 40, e->addr[0][2u * e->out + 1u]);
    put32(header + 44, n);
    put32(header + 48, ww);
    put32(header + 52, wh);
    put32(header + 56, pick);
    put32(header + 60, dv);
    for (uint32_t i = 0; i < PL_INFO_BYTES; i += 4u) /* the whole DISPIMAGEINFO */
        put32(header + 64 + i, RD(base + i));
    return PL_OK;
}

