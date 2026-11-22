/* Screenshot Level B Stage A1: H8 arm, H3 injection (hook context) and the
 * export report. Host-testable through inject_api and observer_api
 * (COMBO_OBSERVE_HOST). hw_jpeg_inject.h holds the windows and the evidence. */
#include "hw_jpeg_inject.h"
#include "combo_observe.h"
#include "observer.h"
#include "y22_stream.h"
#include "one_press.h"
#ifdef OBSERVER_FIRMWARE
#define CODE __attribute__((section(".resident_text")))
#define DATA __attribute__((section(".resident_rodata")))
#else
#define CODE
#define DATA
#endif

_Static_assert((INJECT_ENTRIES & (INJECT_ENTRIES - 1u)) == 0u, "ring index is a mask");
_Static_assert(INJECT_REPORTED <= INJECT_ENTRIES - 1u, "reported entries stay below a full lap");
_Static_assert(INJECT_BYTES == 7680000u && INJECT_BYTES % 512u == 0u, "whole sectors");

struct inject_ring inject_ring __attribute__((aligned(32)));
_Static_assert(INJECT_SHARED_STRIP_BYTES == Y22_STRIP_BYTES, "one strip");
_Static_assert(INJECT_SHARED_ROW_BYTES >= Y22_SOURCE_MAX_ROW_BYTES && INJECT_SHARED_ROW_BYTES >= INJECT_YCA_HEADER_BYTES, "row");
struct inject_pending inject_pending;
struct inject_guard inject_guard;
uint8_t inject_shared_strip[INJECT_SHARED_STRIP_BYTES] __attribute__((aligned(32)));
uint8_t inject_shared_row[INJECT_SHARED_ROW_BYTES] __attribute__((aligned(32)));
uint8_t inject_dma_row[INJECT_SHARED_ROW_BYTES] __attribute__((aligned(32)));
_Static_assert(INJECT_SHARED_ROW_BYTES % 32u == 0u, "whole cache lines (review K N2)");
_Static_assert(INJECT_BAND_OUT_BYTES + INJECT_BAND_ROWS_LCD * (3u * 1024u + 1024u * 5u / 2u) <= INJECT_BAND_REQUEST, "LCD band fits");
_Static_assert(INJECT_BAND_OUT_BYTES + INJECT_BAND_ROWS_EVF * (3u * 1600u + 1600u * 5u / 2u) <= INJECT_BAND_REQUEST, "EVF band fits");
/* build-21: returned-23's 768,000 B Write64s from a 128-aligned band ran at 5.1 MB/s, the
 * capture's 2 MB Write64 from a 1 KiB-aligned buffer at 16-29 MB/s; the band start (every
 * Write64 source) is rounded to 4 KiB. The read regions are then 512-aligned for the LCD
 * layout; the EVF PIC rows stay 32-aligned (46 * 4800 is not a multiple of 512). */
#define BAND_ALIGN 4096u
_Static_assert(INJECT_BAND_OUT_BYTES + INJECT_BAND_ROWS_EVF * (3u * 1600u + 1600u * 5u / 2u) + BAND_ALIGN - 1u <= INJECT_BAND_REQUEST, "room to round the chunk up to BAND_ALIGN");
_Static_assert(INJECT_BAND_OUT_BYTES % 512u == 0u && (INJECT_BAND_ROWS_LCD * 3u * 1024u) % 512u == 0u, "LCD band regions 512-aligned");
_Static_assert(INJECT_BAND_OUT_BYTES % 32u == 0u && (INJECT_BAND_ROWS_LCD * 3u * 1024u) % 32u == 0u && (INJECT_BAND_ROWS_EVF * 3u * 1600u) % 32u == 0u,
               "band regions 32-aligned");
static struct y22_stream inject_y22;
struct inject_build inject_build;
uint8_t inject_pic_row[INJECT_PIC_ROW_BYTES] __attribute__((aligned(32)));
_Static_assert(INJECT_PIC_ROW_BYTES % 32u == 0u && INJECT_PIC_ROW_BYTES >= 512u && INJECT_PIC_ROW_BYTES >= 1600u * 5u / 2u, "PIC row");
static char inject_pic_path[INJECT_SOURCE_PATH_BYTES];

CODE void inject_y22_clear(void)
{
    __atomic_store_n(&inject_pending.pending, 0u, __ATOMIC_RELEASE);
    (void)__atomic_add_fetch(&inject_pending.clears, 1u, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}

CODE void inject_y22_ready(uint32_t tick, const char *path)
{
    uint32_t i = 0u;
    for (; i + 1u < INJECT_SOURCE_PATH_BYTES && path[i]; ++i)
        inject_pending.path[i] = path[i];
    inject_pending.path[i] = 0;
    __atomic_store_n(&inject_pending.set_tick, tick, __ATOMIC_RELAXED);
    (void)__atomic_add_fetch(&inject_pending.sets, 1u, __ATOMIC_RELAXED);
    __atomic_store_n(&inject_pending.pending, 1u, __ATOMIC_RELEASE); /* path and tick before pending */
}

/* GuiMainTask. Dekker half: own flag, full barrier, then the other side's. */
CODE uint32_t inject_capture_begin(void)
{
    __atomic_store_n(&inject_guard.capture, 1u, __ATOMIC_SEQ_CST);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    uint32_t busy = __atomic_load_n(&inject_guard.busy, __ATOMIC_SEQ_CST);
    uint32_t develop = __atomic_load_n(&inject_guard.develop, __ATOMIC_ACQUIRE);
    uint32_t age = __atomic_load_n(&combo_worker_tick, __ATOMIC_RELAXED) - __atomic_load_n(&inject_guard.develop_tick, __ATOMIC_RELAXED);
    if ((int32_t)age < 0) /* review K N1: a newer H8 stamp than our tick read is a live window */
        age = 0u;
    if (busy || (develop && age <= INJECT_DEVELOP_TICKS)) {
        __atomic_store_n(&inject_guard.capture, 0u, __ATOMIC_RELEASE);
        (void)__atomic_add_fetch(&inject_guard.refused_develop, 1u, __ATOMIC_RELAXED);
        return 1u;
    }
    return 0u;
}

CODE void inject_capture_end(void)
{
    __atomic_store_n(&inject_guard.capture, 0u, __ATOMIC_RELEASE); /* after the last file call returned */
}

#ifndef COMBO_OBSERVE_HOST
CODE static uint32_t inject_native_read(uint32_t address)
{
    return *(const volatile uint32_t *)(uintptr_t)address;
}
CODE static uint32_t inject_native_timer(void)
{
    return ~*(volatile const uint32_t *)COMBO_LOG_TIMER_REGISTER;
}
CODE static uint32_t inject_native_cpu(void)
{
    uint32_t mpidr;
    __asm__ volatile("mrc p15, 0, %0, c0, c0, 5" : "=r"(mpidr));
    return mpidr;
}
CODE static void inject_native_barrier(void)
{
    __asm__ volatile("dsb sy" ::: "memory");
}
/* Canon's AllocateUncacheableMemory without the malloc (O: E07F76FC -> E0808DD8,
 * E04E1FE0): drop the range's cache lines, then the 0x40000000 alias. The buffer
 * is 32-aligned and whole lines; the CPU must use only the returned alias. */
CODE void *inject_native_uncached(void *buffer, uint32_t bytes)
{
    ((void (*)(void *, uint32_t))INJECT_DCACHE_INVALIDATE)(buffer, bytes);
    return (void *)((uintptr_t)buffer | INJECT_UNCACHED_ALIAS);
}
const struct inject_api inject_api DATA = {
    inject_native_read, inject_native_timer, inject_native_cpu,
    (inject_size_fn)INJECT_FIO_GET_SIZE64, (inject_open_fn)INJECT_FIO_OPEN,
    (inject_read_ibus_fn)INJECT_FIO_READ_IBUS, (inject_close_fn)INJECT_FIO_CLOSE,
    inject_native_barrier,
    (inject_buffer_read_fn)INJECT_FIO_READ, (inject_path_fn)INJECT_FIO_CREATE, (inject_path_fn)INJECT_FIO_REMOVE,
    (inject_path_fn)INJECT_FIO_FLUSH, (inject_write64_fn)INJECT_FIO_WRITE64, (inject_map_fn)INJECT_MAP_CPU_BUFFER,
    (inject_unmap_fn)INJECT_UNMAP_CPU_BUFFER, inject_native_uncached,
};
const struct inject_band_api inject_band_api DATA = {
    (uint32_t (*)(const char *, uint32_t))INJECT_SEM_CREATE, (int32_t (*)(uint32_t, uint32_t))INJECT_SEM_TAKE,
    (int32_t (*)(uint32_t))INJECT_SEM_GIVE, (void (*)(uint32_t, inject_band_cbr, uint32_t, uint32_t))INJECT_RSC_ALLOC,
    (void (*)(uint32_t, uint32_t, uint32_t))INJECT_RSC_FREE, (uint32_t (*)(uint32_t))INJECT_RSC_FIRST,
    (uint32_t (*)(uint32_t))INJECT_RSC_ADDR, (uint32_t (*)(uint32_t))INJECT_RSC_SIZE,
};
#endif

static const char inject_path[] DATA = INJECT_PATH;
static const char i_head_fmt[] DATA = CC_FMT_INJECT_HEAD;
static const char i_entry_fmt[] DATA = CC_FMT_INJECT_ENTRY;
static const char i_desc_fmt[] DATA = CC_FMT_INJECT_DESC;
static const char i_fio_fmt[] DATA = CC_FMT_INJECT_FIO;
static const char i_us_fmt[] DATA = CC_FMT_INJECT_US;
static const char i_torn_fmt[] DATA = CC_FMT_INJECT_TORN;
static const char i_pending_fmt[] DATA = CC_FMT_INJECT_PENDING;
static const char i_guard_fmt[] DATA = CC_FMT_INJECT_GUARD;
static const char i_build_fmt[] DATA = CC_FMT_INJECT_BUILD;
static const char i_build2_fmt[] DATA = CC_FMT_INJECT_BUILD2;
static const char i_build3_fmt[] DATA = CC_FMT_INJECT_BUILD3;
static const char i_band_fmt[] DATA = CC_FMT_INJECT_BAND;
static const char inject_drive[] DATA = "B:/";

/* As hw_jpeg_probe.c: 1 inside the RAM range, 2 inside the caller frame above
 * the entry SP, 0 refused. */
CODE static uint32_t readable(uint32_t p, uint32_t bytes, uint32_t sp)
{
    if (p & 3u)
        return 0u;
    if (p >= INJECT_POINTER_MIN && p <= INJECT_POINTER_END - bytes)
        return 1u;
    return sp && !(sp & 3u) && p - sp <= INJECT_STACK_WINDOW - bytes ? 2u : 0u;
}

CODE static void develop_end(uint32_t save)
{
    if (save == 1u) {
        __atomic_store_n(&inject_ring.arm_us, inject_api.timer_us(), __ATOMIC_RELAXED);
        (void)__atomic_add_fetch(&inject_ring.develop_save, 1u, __ATOMIC_RELAXED);
        __atomic_store_n(&inject_guard.develop_tick, __atomic_load_n(&combo_worker_tick, __ATOMIC_RELAXED), __ATOMIC_RELAXED);
        __atomic_store_n(&inject_guard.develop, 1u, __ATOMIC_SEQ_CST); /* RATE bips from here */
        __atomic_store_n(&inject_ring.armed, 1u, __ATOMIC_RELEASE);
        return;
    }
    __atomic_store_n(&inject_guard.develop, 0u, __ATOMIC_RELEASE);
    /* 0 is the cancel; any other value (never observed) also disarms. */
    __atomic_store_n(&inject_ring.armed, 0u, __ATOMIC_RELEASE);
    (void)__atomic_add_fetch(save ? &inject_ring.develop_other : &inject_ring.develop_cancel, 1u, __ATOMIC_RELAXED);
}

/* One Map / Write64 / Unmap of the shared strip or a band, as combo_capture's write_buffer. */
CODE static int32_t write_buffer(int32_t fd, const uint8_t *buffer, uint32_t bytes)
{
    uint32_t phys[2] = {0u, 0u};
    uint32_t va = (uint32_t)(uintptr_t)buffer, t0 = inject_api.timer_us();
    if (inject_api.map(phys, va, bytes, 0u, 1u) != 0)
        return -1;
    uint32_t t1 = inject_api.timer_us();
    int32_t rc = inject_api.write64(fd, 0u, phys[0], phys[1], bytes);
    uint32_t t2 = inject_api.timer_us();
    (void)inject_api.unmap(va, bytes, 0u, 1u);
    inject_build.us_write += t2 - t1;
    inject_build.us_map += inject_api.timer_us() - t2 + (t1 - t0);
    return rc;
}

struct source_reader {
    int32_t fd;
    uint32_t next;
    uint8_t *dma;       /* inject_dma_row's uncacheable alias: the FIO_ReadFile buffer */
    int32_t pic;        /* the admitted .PIC (open_pic), else -1 */
    uint32_t pic_bytes; /* one 10-bit row: w * 5 / 2 */
    uint8_t *pic_dma;   /* inject_pic_row's uncacheable alias */
    /* build-19 band mode (rows != 0): `rows` source rows of each file per read */
    uint32_t rows, height, first, end;
    uint8_t *yca_band, *pic_band;
};

/* One timed FIO_ReadFile of exactly `bytes` into an uncacheable buffer: 0, or the rc (`short_rc` if short). */
CODE static int32_t read_exact(int32_t fd, uint8_t *buffer, uint32_t bytes, uint32_t *us, int32_t short_rc)
{
    uint32_t t = inject_api.timer_us();
    int32_t n = inject_api.read_file(fd, buffer, bytes);
    *us += inject_api.timer_us() - t;
    return n == (int32_t)bytes ? 0 : n < 0 ? n : short_rc;
}

/* y22_stream asks for every source row once, in order: a sequential read into
 * an uncacheable buffer (one row, or in band mode the next `rows` rows of each
 * file into the band), then one copy into the cached work row (and, with an
 * admitted PIC, the same row of the picture layer composited under it). */
CODE static int32_t fetch_row(void *ctx, uint32_t sy, uint8_t *row, uint32_t bytes)
{
    struct source_reader *c = ctx;
    if (sy != c->next)
        return -4;
    c->next = sy + 1u;
    const uint8_t *yca = c->dma;
    const uint8_t *pic = c->pic_dma;
    uint32_t pic_n = c->pic >= INJECT_FD_MIN ? c->pic_bytes : 0u;
    int32_t rc;
    if (c->rows) {
        if (sy == c->end) {
            uint32_t k = c->height - sy < c->rows ? c->height - sy : c->rows;
            if ((rc = read_exact(c->fd, c->yca_band, k * bytes, &inject_build.us_yca, -3)) != 0)
                return rc;
            if (pic_n && (rc = read_exact(c->pic, c->pic_band, k * pic_n, &inject_build.us_pic, -6)) != 0)
                return rc;
            c->first = sy;
            c->end = sy + k;
        }
        yca = c->yca_band + (sy - c->first) * bytes;
        pic = c->pic_band + (sy - c->first) * pic_n;
    } else if ((rc = read_exact(c->fd, c->dma, bytes, &inject_build.us_yca, -3)) != 0) {
        return rc;
    }
    const uint32_t *from = (const uint32_t *)yca;
    uint32_t *to = (uint32_t *)row;
    for (uint32_t i = 0; i < bytes / 4u; ++i) /* 3w is a multiple of 4 for both admitted widths */
        to[i] = from[i];
    if (pic_n) {
        if (!c->rows && (rc = read_exact(c->pic, c->pic_dma, pic_n, &inject_build.us_pic, -6)) != 0)
            return rc;
        uint32_t t = inject_api.timer_us();
        y22_composite_row(row, pic, bytes / 3u);
        inject_build.us_comp += inject_api.timer_us() - t;
    }
    return 0;
}

/* build-19 band buffer (hw_jpeg_inject.h INJECT_BAND_*): state machine shared with
 * the RscMgrTask callback. IDLE -> WAITING (request posted) -> DONE (callback
 * stored the suite; it gives the semaphore) or ABANDONED (timeout; the callback
 * frees the suite on arrival and returns to IDLE). */
enum { BAND_IDLE, BAND_WAITING, BAND_DONE, BAND_ABANDONED };
static uint32_t band_sem, band_suite, band_state;
#define BA inject_band_api
#ifdef COMBO_OBSERVE_HOST
uint8_t *inject_host_band(uint32_t a); /* the fake's arena for its chunk address token */
#define BAND_ALIAS_OK(a) ((a) != 0u)
#define BAND_POINTER(a) inject_host_band(a)
#else
#define BAND_ALIAS_OK(a) ((a) >= 0x40000000u && (a) < 0x80000000u) /* FIO_ReadFile's uncacheable test (build-15 §1) */
#define BAND_POINTER(a) ((uint8_t *)(uintptr_t)(a))
#endif

CODE static void band_cbr(uint32_t ctx, uint32_t suite)
{
    (void)ctx;
    band_suite = suite;
    if (__atomic_exchange_n(&band_state, BAND_DONE, __ATOMIC_ACQ_REL) == BAND_ABANDONED) {
        if (suite)
            BA.free(suite, 0u, 0u);
        __atomic_store_n(&band_state, BAND_IDLE, __ATOMIC_RELEASE);
        return;
    }
    (void)BA.sem_give(band_sem);
}

/* GuiMainTask: the band's CPU address (an uncacheable alias, rounded up to BAND_ALIGN), or 0 for the strip path.
 * Not inlined, so test_one_press_asm can run its linked words. */
CODE __attribute__((noinline)) static uint8_t *band_get(struct inject_build *b)
{
    if (__atomic_load_n(&band_state, __ATOMIC_ACQUIRE) != BAND_IDLE) {
        b->band = INJECT_BAND_LATE;
        return 0;
    }
    if (!band_sem && !(band_sem = BA.sem_create("R5LB", 0u))) {
        b->band = INJECT_BAND_SEM;
        return 0;
    }
    band_suite = 0u;
    __atomic_store_n(&band_state, BAND_WAITING, __ATOMIC_RELEASE);
    uint32_t t = inject_api.timer_us();
    BA.alloc(INJECT_BAND_REQUEST, band_cbr, 0u, INJECT_BAND_TYPE);
    int32_t took = BA.sem_take(band_sem, INJECT_BAND_TIMEOUT_MS);
    /* Still WAITING: no give within the timeout, or (build-22, review R N1) a stale give
     * left by an earlier callback that gave after band_get stopped waiting. Either way
     * abandon this request; its callback frees the suite on arrival and does not give. */
    uint32_t st = BAND_WAITING;
    if (__atomic_compare_exchange_n(&band_state, &st, BAND_ABANDONED, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        b->us_band = inject_api.timer_us() - t;
        b->band = INJECT_BAND_TIMEOUT;
        return 0;
    }
    if (took != 0) /* DONE meanwhile: its give follows (if later still, the next build meets it as stale) */
        (void)BA.sem_take(band_sem, INJECT_BAND_TIMEOUT_MS);
    b->us_band = inject_api.timer_us() - t;
    __atomic_store_n(&band_state, BAND_IDLE, __ATOMIC_RELEASE);
    uint32_t suite = band_suite;
    if (!suite) {
        b->band = INJECT_BAND_EMPTY;
        return 0;
    }
    uint32_t chunk = BA.first_chunk(suite), a = chunk ? BA.chunk_addr(chunk) : 0u;
    b->band_addr = a;
    b->band_size = chunk ? BA.chunk_size(chunk) : 0u;
    if (b->band_size < INJECT_BAND_REQUEST || !BAND_ALIAS_OK(a)) {
        BA.free(suite, 0u, 0u);
        b->band = INJECT_BAND_BAD;
        return 0;
    }
    b->band = INJECT_BAND_USED;
    /* Canon's chunk is only 16-aligned (returned-22/23: 0x476F4070). build-20 started the
     * layout at the next 32-byte boundary; build-21 at the next 4 KiB one (BAND_ALIGN). */
    return BAND_POINTER(a) + ((0u - a) & (BAND_ALIGN - 1u));
}

CODE static void band_put(void)
{
    BA.free(band_suite, 0u, 0u); /* before any INDEV start: RscMgrTask serves requests in order (D) */
}
#ifdef COMBO_OBSERVE_HOST
void inject_host_band_reset(void) { band_sem = band_suite = band_state = 0u; }
#endif

CODE static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* The source YCA's own .PIC (picture_layer.c pl_select writes its header): the
 * same capture number and output, the whole w x h panel plane at 0,0, BT.601,
 * range 3 (10-bit full range, the only format y22_composite_row decodes). Opens it
 * positioned at its plane and returns the fd, or -1 with b->pic_status set:
 * then the OSD alone is converted. The header is read into the uncacheable alias. */
CODE static int32_t open_pic(struct inject_build *b, const char *source, uint32_t w, uint32_t h, uint32_t number, const uint8_t *p)
{
    uint32_t n = 0u;
    for (; source[n] && n + 1u < INJECT_SOURCE_PATH_BYTES; ++n)
        inject_pic_path[n] = source[n];
    inject_pic_path[n] = 0;
    b->pic_status = INJECT_PIC_MISSING;
    if (n < 4u)
        return -1;
    for (uint32_t i = 0; i < 3u; ++i)
        inject_pic_path[n - 3u + i] = INJECT_PIC_PATH[i];
    uint32_t plane = (w * h * 5u) >> 1, size[2] __attribute__((aligned(8))) = {0u, 0u};
    b->pic_rc = inject_api.size(inject_pic_path, size);
    if (b->pic_rc != 0)
        return -1;
    b->pic_status = INJECT_PIC_SIZE;
    if (size[1] || size[0] != 512u + plane)
        return -1;
    b->pic_status = INJECT_PIC_OPEN;
    int32_t fd = inject_api.open(inject_pic_path, INJECT_OPEN_READ);
    if (fd < INJECT_FD_MIN) {
        b->pic_rc = fd;
        return -1;
    }
    b->pic_status = INJECT_PIC_HEADER;
    const uint8_t *i = p + 64u; /* the DISPIMAGEINFO copy */
    b->pic_rc = inject_api.read_file(fd, (void *)p, 512u);
    uint32_t out = w == 1600u;
    if (b->pic_rc != 512 || p[0] != 'R' || p[1] != '5' || p[2] != 'P' || p[3] != 'I' || p[4] != 1u || p[5] || le32(p + 4) >> 16 != 512u ||
        le32(p + 8) != out || (le32(p + 12) & 0xffffu) != number || le32(p + 44) != plane || le32(p + 48) != w || le32(p + 52) != h ||
        le32(i) != 1u || le32(i + 4) != out || le32(i + 0x14) != w || le32(i + 0x18) != h || le32(i + 0x1c) || le32(i + 0x20) ||
        le32(i + 0x24) || le32(i + 0x28) || le32(i + 0x2c) != w || le32(i + 0x30) != h || le32(i + 0x34) != 1u || le32(i + 0x38) != 3u) {
        (void)inject_api.close(fd);
        return -1;
    }
    b->pic_status = INJECT_PIC_OK;
    b->pic_rc = 0;
    return fd;
}

/* GuiMainTask, capture guard held (combo_capture.c, after the RATE's own files
 * were closed and flushed): the source .YCA (fill_header: R5SS v3, 512-byte
 * header, UYVA, pitch 3w) -> HWJPG.Y22, replaced (Remove, CreateSC) and written
 * in whole 38400-byte strips, closed and flushed. 0 on success; on any failure
 * the Y22 is removed. Every FIO_ReadFile buffer is an uncacheable alias. */
CODE static uint32_t build_y22(struct inject_build *b, const char *source)
{
    uint8_t *dma = inject_api.uncached(inject_dma_row, INJECT_SHARED_ROW_BYTES);
    {
        uint32_t src_size[2] __attribute__((aligned(8))) = {0u, 0u};
        b->stage = INJECT_CONV_SOURCE_SIZE;
        b->rc = inject_api.size(source, src_size);
        b->source_bytes = src_size[0];
        if (b->rc != 0 || src_size[1] ||
            (src_size[0] != INJECT_YCA_HEADER_BYTES + 3u * 1024u * 682u && src_size[0] != INJECT_YCA_HEADER_BYTES + 3u * 1600u * 1200u)) {
            if (!b->rc)
                b->rc = -2;
            return 1u;
        }
    }
    b->stage = INJECT_CONV_OPEN;
    int32_t src = inject_api.open(source, INJECT_OPEN_READ);
    if (src < INJECT_FD_MIN) {
        b->rc = src;
        return 1u;
    }
    b->stage = INJECT_CONV_HEADER;
    int32_t n = inject_api.read_file(src, dma, INJECT_YCA_HEADER_BYTES);
    const uint8_t *h = dma;
    uint32_t w = le32(h + 8), ht = le32(h + 12);
    b->source_wh = w << 16 | (ht & 0xffffu);
    b->number = le32(h + 28) >> 16; /* fill_header: capture number low 16 */
    if (n != (int32_t)INJECT_YCA_HEADER_BYTES || h[0] != 'R' || h[1] != '5' || h[2] != 'S' || h[3] != 'S' || h[4] != 3u || h[5] ||
        h[6] != (INJECT_YCA_HEADER_BYTES & 0xffu) || h[7] != INJECT_YCA_HEADER_BYTES >> 8 || le32(h + 16) != 3u * w ||
        le32(h + 20) != 3u * w * ht || h[24] != 'U' || h[25] != 'Y' || h[26] != 'V' || h[27] != 'A' ||
        b->source_bytes != INJECT_YCA_HEADER_BYTES + 3u * w * ht ||
        !y22_stream_init(&inject_y22, w, ht, inject_shared_row, INJECT_SHARED_ROW_BYTES, fetch_row, 0)) {
        b->rc = n != (int32_t)INJECT_YCA_HEADER_BYTES ? n : -2;
        (void)inject_api.close(src);
        return 1u;
    }
    uint8_t *pic_dma = inject_api.uncached(inject_pic_row, INJECT_PIC_ROW_BYTES);
    struct source_reader reader; /* field by field: an aggregate initializer becomes a memset call (no libc in the link) */
    reader.fd = src;
    reader.next = reader.rows = reader.first = reader.end = 0u;
    reader.dma = dma;
    reader.height = ht;
    reader.yca_band = reader.pic_band = 0;
    reader.pic = open_pic(b, source, w, ht, b->number, pic_dma);
    reader.pic_bytes = (w * 5u) >> 1;
    reader.pic_dma = pic_dma;
    inject_y22.ctx = &reader;
    b->stage = INJECT_CONV_CREATE;
    (void)inject_api.remove(inject_path); /* absent is fine */
    int32_t out = inject_api.create(inject_path);
    if (out < INJECT_FD_MIN) {
        b->rc = out;
        (void)inject_api.close(src);
        if (reader.pic >= INJECT_FD_MIN)
            (void)inject_api.close(reader.pic);
        return 1u;
    }
    b->stage = INJECT_CONV_STRIPS;
    uint8_t *band = band_get(b);
    if (band) { /* [ output: 20 strips | YCA rows | PIC rows ] */
        reader.rows = w == 1024u ? INJECT_BAND_ROWS_LCD : INJECT_BAND_ROWS_EVF;
        reader.yca_band = band + INJECT_BAND_OUT_BYTES;
        reader.pic_band = reader.yca_band + reader.rows * 3u * w;
    }
    uint32_t bytes, t, held = 0u;
    int32_t rc = 0;
    while (t = inject_api.timer_us(), (bytes = y22_stream_fill(&inject_y22, inject_shared_strip)) != 0u) {
        b->us_fill += inject_api.timer_us() - t;
        const uint8_t *from = inject_shared_strip;
        if (band) { /* the strip joins the band's output; one Write64 per 20 strips or at the end */
            uint32_t *to = (uint32_t *)(band + held);
            for (uint32_t i = 0; i < bytes / 4u; ++i)
                to[i] = ((const uint32_t *)inject_shared_strip)[i];
            held += bytes;
            if (held < INJECT_BAND_OUT_BYTES && b->y22_written + held < Y22_BYTES)
                continue;
            from = band;
            bytes = held;
            held = 0u;
        }
        rc = write_buffer(out, from, bytes);
        if (rc != (int32_t)bytes)
            break;
        b->y22_written += bytes;
        rc = 0;
    }
    if (band)
        band_put();
    (void)inject_api.close(src);
    if (reader.pic >= INJECT_FD_MIN)
        (void)inject_api.close(reader.pic);
    if (!rc && inject_y22.error)
        rc = inject_y22.error;
    if (!rc && b->y22_written != Y22_BYTES)
        rc = -5;
    if (!rc)
        b->stage = INJECT_CONV_CLOSE;
    int32_t cl = inject_api.close(out);
    int32_t fl = inject_api.flush(inject_drive);
    if (!rc)
        rc = cl < 0 ? cl : (fl < 0 ? fl : 0);
    if (rc) {
        b->rc = rc;
        (void)inject_api.remove(inject_path); /* our own failed file only */
        return 1u;
    }
    b->stage = INJECT_CONV_DONE;
    return 0u;
}

CODE uint32_t inject_y22_build(const char *source)
{
    struct inject_build *b = &inject_build;
    uint32_t builds = b->builds + 1u, failures = b->failures, us = inject_api.timer_us();
    volatile uint32_t *z = (volatile uint32_t *)b;
    for (uint32_t i = 0; i < sizeof(*b) / 4u; ++i)
        z[i] = 0u;
    b->builds = builds;
    uint32_t failed = build_y22(b, source);
    b->failures = failures + failed;
    b->us = inject_api.timer_us() - us;
    return failed;
}

/* Fills e (a private copy). Returns the outcome. Every return before
 * INJECT_F_READ leaves desc[0] untouched. */
/* The one S2 3:2 MAIN-buffer rule, for the armed save and the one-press dry run. */
#define INJECT_DESC_BAD(e)                                                                                                  \
    ((e)->w != INJECT_WIDTH || (e)->h != INJECT_HEIGHT || (e)->base_w != INJECT_WIDTH || (e)->xoff || (e)->yoff ||          \
     (e)->hi - 1u >= INJECT_IBUS_BANKS || ((e)->lo & INJECT_ALIGN_MASK))

CODE static uint32_t encode_main(struct inject_entry *e, uint32_t desc, uint32_t entry_sp)
{
    if (!__atomic_exchange_n(&inject_ring.armed, 0u, __ATOMIC_ACQ_REL)) {
        /* A one-press job's dry run (before its done callback): the same reads, no FIO, no write. */
        if (one_press_dry_wanted()) {
            uint32_t ok = 0u, r = readable(desc, 4u * INJECT_DESC_WORDS, entry_sp);
            if (r) {
                e->flags |= INJECT_F_DESC | (r == 2u ? INJECT_F_STACK : 0u);
                e->lo = inject_api.read(desc);
                e->hi = inject_api.read(desc + 4u);
                e->base_w = inject_api.read(desc + 8u);
                e->xoff = inject_api.read(desc + 12u);
                e->yoff = inject_api.read(desc + 16u);
                e->w = inject_api.read(desc + 20u);
                e->h = inject_api.read(desc + 24u);
                ok = !INJECT_DESC_BAD(e);
            } else {
                e->flags |= INJECT_F_DESC_REFUSED;
            }
            one_press_dry(ok, e->w, e->h);
        }
        return INJECT_SKIP_UNARMED;
    }
    e->age_us = e->us - __atomic_load_n(&inject_ring.arm_us, __ATOMIC_RELAXED);
    if (e->age_us > INJECT_FRESH_US) /* also a timer wrap: refused */
        return INJECT_REFUSE_STALE;
    uint32_t r = readable(desc, 4u * INJECT_DESC_WORDS, entry_sp);
    if (!r) {
        e->flags |= INJECT_F_DESC_REFUSED;
        return INJECT_REFUSE_GEOM;
    }
    e->flags |= INJECT_F_DESC | (r == 2u ? INJECT_F_STACK : 0u);
    e->lo = inject_api.read(desc);
    e->hi = inject_api.read(desc + 4u);
    e->base_w = inject_api.read(desc + 8u);
    e->xoff = inject_api.read(desc + 12u);
    e->yoff = inject_api.read(desc + 16u);
    e->w = inject_api.read(desc + 20u);
    e->h = inject_api.read(desc + 24u);
    if (INJECT_DESC_BAD(e))
        return INJECT_REFUSE_GEOM;
    /* Dekker half (busy was stored and fenced in inject_record): a capture that
     * holds the guard owns the buffers and its files. */
    uint32_t held = __atomic_load_n(&inject_guard.capture, __ATOMIC_SEQ_CST);
    if (held) {
        (void)__atomic_add_fetch(&inject_guard.refused_capture, 1u, __ATOMIC_RELAXED);
        return INJECT_REFUSE_CAPTURE;
    }
    /* No FIO at all for an ordinary S2 Resize: the pending screenshot first. */
    if (!__atomic_load_n(&inject_pending.pending, __ATOMIC_ACQUIRE)) {
        (void)__atomic_add_fetch(&inject_pending.not_pending, 1u, __ATOMIC_RELAXED);
        return INJECT_REFUSE_NOT_PENDING;
    }
    if (__atomic_load_n(&combo_worker_tick, __ATOMIC_RELAXED) - __atomic_load_n(&inject_pending.set_tick, __ATOMIC_RELAXED) >
        INJECT_PENDING_TICKS) {
        if (__atomic_exchange_n(&inject_pending.pending, 0u, __ATOMIC_ACQ_REL))
            (void)__atomic_add_fetch(&inject_pending.expired, 1u, __ATOMIC_RELAXED);
        return INJECT_REFUSE_EXPIRED;
    }
    /* Consumed before the read: one screenshot, one attempt. A clear that raced
     * in since the check above wins. build-15: HWJPG.Y22 is already complete (it
     * was built at RATE); this H3 makes only the A1 calls below. */
    if (!__atomic_exchange_n(&inject_pending.pending, 0u, __ATOMIC_ACQ_REL)) {
        (void)__atomic_add_fetch(&inject_pending.not_pending, 1u, __ATOMIC_RELAXED);
        return INJECT_REFUSE_NOT_PENDING;
    }
    (void)__atomic_add_fetch(&inject_pending.consumed, 1u, __ATOMIC_RELAXED);
    uint32_t size[2] __attribute__((aligned(8))) = {0u, 0u}; /* Canon stores it with strd */
    e->flags |= INJECT_F_SIZE;
    e->rc_size = inject_api.size(inject_path, size);
    e->us_size = inject_api.timer_us() - e->us;
    e->size_lo = size[0];
    e->size_hi = size[1];
    if (e->rc_size != 0 || size[0] != INJECT_BYTES || size[1])
        return INJECT_REFUSE_FILE;
    e->flags |= INJECT_F_OPEN;
    e->fd = inject_api.open(inject_path, INJECT_OPEN_READ);
    e->us_open = inject_api.timer_us() - e->us;
    if (e->fd < INJECT_FD_MIN)
        return INJECT_FAIL_OPEN;
    e->flags |= INJECT_F_READ;
    e->rd = inject_api.read_ibus(e->fd, 0u, e->lo, e->hi, INJECT_BYTES);
    e->us_read = inject_api.timer_us() - e->us;
    e->rc_close = inject_api.close(e->fd);
    e->us_close = inject_api.timer_us() - e->us;
    inject_api.barrier();
    return e->rd == (int32_t)INJECT_BYTES ? INJECT_OK : INJECT_FAIL_READ;
}

CODE static void publish(const struct inject_entry *local)
{
    uint32_t seq = __atomic_add_fetch(&inject_ring.claimed, 1u, __ATOMIC_RELAXED);
    struct inject_entry *e = &inject_ring.entries[(seq - 1u) & (INJECT_ENTRIES - 1u)];
    __atomic_store_n(&e->seq, 0u, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    volatile uint32_t *dst = (volatile uint32_t *)e;
    const uint32_t *src = (const uint32_t *)local;
    for (uint32_t i = 1u; i < sizeof(*e) / 4u; ++i)
        dst[i] = src[i];
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    __atomic_store_n(&e->seq, seq, __ATOMIC_RELAXED);
}

CODE void inject_record(uint32_t hook, const uint32_t saved[INJECT_SAVED_WORDS], uint32_t entry_sp)
{
    if (hook == INJECT_HOOK_DEVELOP_END) {
        develop_end(saved[0]);
        return;
    }
    if (hook != INJECT_HOOK_ENCODE)
        return;
    if (saved[2] != 0u) { /* MPF / THM: never touched (THM is resized from desc[0]) */
        (void)__atomic_add_fetch(&inject_ring.encode_other, 1u, __ATOMIC_RELAXED);
        return;
    }
    (void)__atomic_add_fetch(&inject_ring.encode_main, 1u, __ATOMIC_RELAXED);
    __atomic_store_n(&inject_guard.busy, 1u, __ATOMIC_SEQ_CST); /* Dekker half: then the capture flag */
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    struct inject_entry e;
    volatile uint32_t *z = (volatile uint32_t *)&e;
    for (uint32_t i = 0u; i < sizeof(e) / 4u; ++i)
        z[i] = 0u;
    e.us = inject_api.timer_us();
    e.mpidr = inject_api.cpu();
    e.sp = entry_sp;
    e.outcome = encode_main(&e, saved[0], entry_sp);
    publish(&e);
    __atomic_store_n(&inject_guard.develop, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&inject_guard.busy, 0u, __ATOMIC_RELEASE); /* after the last file call */
}

#define DEBUG(...) observer_api.debug(OBSERVER_CLASS, OBSERVER_LEVEL, __VA_ARGS__)

static struct inject_entry inject_copy; /* single reader: the export bundle */

CODE void inject_report(void)
{
    uint32_t claimed = __atomic_load_n(&inject_ring.claimed, __ATOMIC_ACQUIRE);
    DEBUG(i_head_fmt, claimed, __atomic_load_n(&inject_ring.encode_main, __ATOMIC_RELAXED),
          __atomic_load_n(&inject_ring.encode_other, __ATOMIC_RELAXED),
          __atomic_load_n(&inject_ring.develop_save, __ATOMIC_RELAXED),
          __atomic_load_n(&inject_ring.develop_cancel, __ATOMIC_RELAXED),
          __atomic_load_n(&inject_ring.develop_other, __ATOMIC_RELAXED),
          __atomic_load_n(&inject_ring.armed, __ATOMIC_RELAXED));
    DEBUG(i_pending_fmt, __atomic_load_n(&inject_pending.pending, __ATOMIC_ACQUIRE), inject_pending.set_tick,
          __atomic_load_n(&combo_worker_tick, __ATOMIC_RELAXED), inject_pending.sets, inject_pending.clears,
          inject_pending.consumed, inject_pending.expired, inject_pending.not_pending);
    DEBUG(i_guard_fmt, __atomic_load_n(&inject_guard.capture, __ATOMIC_RELAXED), __atomic_load_n(&inject_guard.busy, __ATOMIC_RELAXED),
          __atomic_load_n(&inject_guard.develop, __ATOMIC_RELAXED), inject_guard.develop_tick, inject_guard.refused_capture,
          inject_guard.refused_develop);
    {
        const struct inject_build *b = &inject_build;
        DEBUG(i_build_fmt, b->builds, b->failures, b->number, b->stage, b->rc, b->source_bytes, b->y22_written, b->us);
        DEBUG(i_build2_fmt, b->source_wh, b->pic_status, b->pic_rc);
        DEBUG(i_build3_fmt, b->us_yca, b->us_pic, b->us_comp, b->us_fill, b->us_map, b->us_write);
        DEBUG(i_band_fmt, b->band, b->us_band, b->band_addr, b->band_size);
    }
    uint32_t first = claimed > INJECT_REPORTED ? claimed - INJECT_REPORTED + 1u : 1u;
    for (uint32_t k = first; k <= claimed && k; ++k) {
        const struct inject_entry *slot = &inject_ring.entries[(k - 1u) & (INJECT_ENTRIES - 1u)];
        uint32_t s1 = __atomic_load_n(&slot->seq, __ATOMIC_ACQUIRE);
        volatile uint32_t *dst = (volatile uint32_t *)&inject_copy;
        const volatile uint32_t *src = (const volatile uint32_t *)slot;
        for (uint32_t i = 0; i < sizeof(inject_copy) / 4u; ++i)
            dst[i] = src[i];
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
        uint32_t s2 = __atomic_load_n(&slot->seq, __ATOMIC_RELAXED);
        if (s1 != k || s2 != k) {
            DEBUG(i_torn_fmt, k);
            continue;
        }
        const struct inject_entry *e = &inject_copy;
        DEBUG(i_entry_fmt, k, e->outcome, e->flags, e->us, e->age_us, e->mpidr, e->sp);
        if (e->flags & INJECT_F_DESC)
            DEBUG(i_desc_fmt, k, e->lo, e->hi, e->base_w, e->xoff, e->yoff, e->w, e->h);
        if (e->flags & INJECT_F_SIZE) {
            DEBUG(i_fio_fmt, k, e->rc_size, e->size_lo, e->size_hi, e->fd, e->rd, e->rc_close);
            DEBUG(i_us_fmt, k, e->us_size, e->us_open, e->us_read, e->us_close);
        }
    }
}
