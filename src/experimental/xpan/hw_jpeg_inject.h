#ifndef R5_HW_JPEG_INJECT_H
#define R5_HW_JPEG_INJECT_H
/* Screenshot JPEG injection (opt-in --screenshot). Owner of the inject ring
 * ABI shared by hw_jpeg_inject_hook.S (entries) and hw_jpeg_inject.c (H8 arm,
 * H3 injection, report; row formats in combo_observe.h). The two windows
 * (ROM 2.2.1):
 *
 *  H3 E0CCB284 ShtVfxEncJpg_Encode, 8 bytes 2de9f0479a460c46 (CRC32 0xfbc88d71):
 *     push.w {r4-r10,lr}; mov sl,r3; mov r4,r1. Resume E0CCB28D.
 *     r0 = encode descriptor (desc[0] for MAIN), r2 = kind (0 MAIN from
 *     ShtVfxEncJpg E0CCB700; 1 MPF E0CCB7A6; 2 THM E0CCB86E).
 *  H8 E0549258 INDEV_DevelopEnd, 10 bytes 2de9f84306464ff49278 (CRC32 0xfb287bb8):
 *     push.w {r3-r9,lr}; mov r6,r0; mov.w r8,#292. Resume E0549263.
 *     r0 = 1 on the save (seen on camera, and Canon's "INDEV_DevelopEnd 1"
 *     log); ROM E05492BC branches only on zero / nonzero.
 *
 * Descriptor words read at r0 (builder E0CCB488, and seen on camera):
 *   +0 IBUS address low, +4 high (bank), +8 BaseW (pixels), +C Xoff, +10 Yoff,
 *   +14 W, +18 H. ShtVfxEncJpg later resizes desc[0] into the THM buffer
 *   (E0CCB862 Resize(&desc[0], &desc[2], 2, sl)), so one MAIN replacement
 *   supplies the thumbnail too.
 *
 * H8 only arms (or disarms) and stamps Canon's log timer: no FIO, no memory
 * read. H3 acts only on kind 0, consumes the arm atomically, and fills desc[0]
 * from INJECT_PATH only when the arm is fresh, the geometry is exactly 3:2 S2
 * and the file is exactly INJECT_BYTES. Every refusal falls through unchanged
 * to Canon's encode. The file is never written or removed. */
#define INJECT_H3_VA 0xe0ccb284
#define INJECT_H3_RESUME 0xe0ccb28d
#define INJECT_H8_VA 0xe0549258
#define INJECT_H8_RESUME 0xe0549263
#define INJECT_HOOK_ENCODE 3      /* H3 */
#define INJECT_HOOK_DEVELOP_END 8 /* H8 */

/* Canon FIO, Thumb entries (ROM 2.2.1). Open and
 * Close are combo_capture.h's CAPTURE_FIO_OPEN / CAPTURE_FIO_CLOSE (hardware-run
 * there). Canon's own Open(path, 0) -> GetFileSize64 -> ReadFile_IBUS(fd, -,
 * lo, hi, size) -> Close sequence: E0161812..E016185A. */
#define INJECT_FIO_GET_SIZE64 0xe07e2f87u /* (path, uint64 *out): 0 ok, -1 error */
#define INJECT_FIO_OPEN 0xe07e1e4bu       /* (path, 0): fd >= 0, -1 error */
#define INJECT_FIO_READ_IBUS 0xe07e2429u  /* (fd, unused, lo, hi, bytes): bytes read or < 0 */
#define INJECT_FIO_CLOSE 0xe07e2d6fu      /* (fd) */
#define INJECT_FIO_READ 0xe07e2111u      /* FIO_ReadFile (fd, buffer, bytes) */
#define INJECT_FIO_CREATE 0xe07e1f0bu    /* = CAPTURE_FIO_CREATE */
#define INJECT_FIO_REMOVE 0xe07e207du    /* = CAPTURE_FIO_REMOVE */
#define INJECT_FIO_WRITE64 0xe07e283fu   /* = CAPTURE_FIO_WRITE64 */
#define INJECT_FIO_FLUSH 0xe07e40efu     /* = CAPTURE_FIO_FLUSH */
#define INJECT_MAP_CPU_BUFFER 0xe07f068bu
#define INJECT_UNMAP_CPU_BUFFER 0xe07f0719u
#define INJECT_OPEN_READ 0u
#define INJECT_FD_MIN 3

#define INJECT_WIDTH 2400u
#define INJECT_HEIGHT 1600u
#define INJECT_BYTES (INJECT_WIDTH * INJECT_HEIGHT * 2u) /* signed-chroma UYVY, 7680000 */
#define INJECT_FRESH_US 5000000u  /* H8 -> H3 at most 5 s (72 ms on camera) */
#define INJECT_IBUS_BANKS 5u      /* valid bank 1..5 (Edmac check E07F0CE6) */
#define INJECT_ALIGN_MASK 0x1ffu  /* 512-byte aligned destination */
#define INJECT_DESC_WORDS 7u
#define INJECT_PATH "B:/DCIM/R5SHOTS/HWJPG.Y22"

#define INJECT_ENTRIES 16u
#define INJECT_REPORTED 8u
#define INJECT_POINTER_MIN 0x00100000u
#define INJECT_POINTER_END 0xc0000000u
#define INJECT_STACK_WINDOW 0x400u /* caller frame above the entry SP (desc[0] is sp+24) */

/* outcomes */
#define INJECT_OK 1
#define INJECT_SKIP_UNARMED 2
#define INJECT_REFUSE_STALE 3
#define INJECT_REFUSE_GEOM 4
#define INJECT_REFUSE_FILE 5
#define INJECT_FAIL_OPEN 6
#define INJECT_FAIL_READ 7
#define INJECT_REFUSE_NOT_PENDING 8 /* a valid descriptor but no fresh screenshot */
#define INJECT_REFUSE_EXPIRED 9
#define INJECT_REFUSE_CAPTURE 10    /* a RATE capture holds the guard: stock Resize */
#define INJECT_FAIL_CONVERT 11      /* unused (no conversion in H3); the value stays reserved */

/* flags */
#define INJECT_F_DESC 1u         /* the 7 descriptor words were read */
#define INJECT_F_DESC_REFUSED 2u /* r0 unaligned or outside both windows: not read */
#define INJECT_F_STACK 4u        /* admitted by the entry-SP window */
#define INJECT_F_SIZE 8u         /* GetFileSize64 called */
#define INJECT_F_OPEN 16u        /* Open called */
#define INJECT_F_READ 32u        /* ReadFile_IBUS and Close called (desc[0] may have changed) */

#ifndef __ASSEMBLER__
#include <stdint.h>
#define INJECT_SAVED_WORDS 8u /* r0 r1 r2 r3 r4 r5 r12 lr, as hw_jpeg_inject_hook.S saves them */

struct inject_entry {
    uint32_t seq; /* 0 while the slot is written; then its claim number */
    uint32_t outcome, flags, us, age_us, mpidr, sp;
    uint32_t lo, hi, base_w, xoff, yoff, w, h;
    int32_t rc_size;
    uint32_t size_lo, size_hi;
    int32_t fd, rd, rc_close;
    uint32_t us_size, us_open, us_read, us_close; /* offsets from us, after each call */
};
/* H3 only reads the ready Y22 (the conversion runs at RATE). */
_Static_assert(sizeof(struct inject_entry) == 96u, "inject entry ABI");

struct inject_ring {
    struct inject_entry entries[INJECT_ENTRIES];
    uint32_t claimed;                                  /* atomic: H3 kind-0 records */
    uint32_t encode_main, encode_other;                /* atomic: H3 kind 0 / other kinds */
    uint32_t develop_save, develop_cancel, develop_other; /* atomic: H8 r0 == 1 / 0 / other */
    uint32_t armed;                                    /* 1 after H8 save, exchanged to 0 by H3 kind 0 */
    uint32_t arm_us;                                   /* Canon log timer at the arm (published before armed) */
};
_Static_assert(sizeof(struct inject_ring) <= 2048u, "inject ring <= 2 KB");
extern struct inject_ring inject_ring;

typedef uint32_t (*inject_read_fn)(uint32_t address);
typedef uint32_t (*inject_word_fn)(void);
typedef int32_t (*inject_size_fn)(const char *path, uint32_t size_out[2]);
typedef int32_t (*inject_open_fn)(const char *path, uint32_t mode);
typedef int32_t (*inject_read_ibus_fn)(int32_t fd, uint32_t unused, uint32_t ibus_lo, uint32_t ibus_hi, uint32_t bytes);
typedef int32_t (*inject_close_fn)(int32_t fd);
typedef void (*inject_void_fn)(void);
typedef int32_t (*inject_buffer_read_fn)(int32_t fd, void *buffer, uint32_t bytes);
typedef int32_t (*inject_path_fn)(const char *path);
typedef int32_t (*inject_write64_fn)(int32_t fd, uint32_t unused, uint32_t phys_lo, uint32_t phys_hi, uint32_t bytes);
typedef int32_t (*inject_map_fn)(uint32_t out64[2], uint32_t vaddr, uint32_t bytes, uint32_t op, uint32_t mode);
typedef int32_t (*inject_unmap_fn)(uint32_t vaddr, uint32_t bytes, uint32_t op, uint32_t mode);
typedef void *(*inject_uncached_fn)(void *buffer, uint32_t bytes);
struct inject_api {
    inject_read_fn read; /* aligned u32 load */
    inject_word_fn timer_us;
    inject_word_fn cpu; /* MPIDR */
    inject_size_fn size;
    inject_open_fn open;
    inject_read_ibus_fn read_ibus;
    inject_close_fn close;
    inject_void_fn barrier; /* dsb sy */
    /* The converter's calls (GuiMainTask): combo_capture.h's
     * CAPTURE_FIO_* entries (hardware-run there) plus FIO_ReadFile E07E2110, whose
     * buffer must be an uncacheable address (below). */
    inject_buffer_read_fn read_file; /* (fd, buffer, bytes): bytes read or < 0 */
    inject_path_fn create, remove, flush;
    inject_write64_fn write64;
    inject_map_fn map;
    inject_unmap_fn unmap;
    inject_uncached_fn uncached;     /* a 32-aligned static buffer as Canon's uncacheable alias */
};
extern const struct inject_api inject_api;

/* One transient contiguous suite from Canon's RscMgr pool (type 4, the
 * pool Canon's GUI dialogs take from GuiMainTask: guiSyncAllocateContinuousMemoryResource
 * E0ADA6C6 = Create binary semaphore, E099D296(size, cbr, ctx, 4), take 1000,
 * E07EA5C8 / E07EA36A, free E099D2C4(suite, 0, 0)). The
 * build reads both source files and writes the Y22 in bands through it instead of
 * 1364 row reads and 200 strip writes (on camera, writes took 3156 ms of 4493 ms,
 * 15.8 ms per Write64).
 * The callback runs on RscMgrTask: it only stores, frees an abandoned suite, and
 * gives our semaphore (created once, never deleted). */
typedef void (*inject_band_cbr)(uint32_t ctx, uint32_t suite);
struct inject_band_api {
    uint32_t (*sem_create)(const char *name, uint32_t initial);
    int32_t (*sem_take)(uint32_t sem, uint32_t timeout_ms);
    int32_t (*sem_give)(uint32_t sem);
    void (*alloc)(uint32_t bytes, inject_band_cbr cbr, uint32_t ctx, uint32_t type);
    void (*free)(uint32_t suite, uint32_t cbr, uint32_t ctx);
    uint32_t (*first_chunk)(uint32_t suite);
    uint32_t (*chunk_addr)(uint32_t chunk);
    uint32_t (*chunk_size)(uint32_t chunk);
};
extern const struct inject_band_api inject_band_api;
#define INJECT_SEM_CREATE 0xe0809e39u  /* CreateBinarySemaphore(name, initial) */
#define INJECT_SEM_TAKE 0xe0809f35u    /* (sem, timeout): 0 taken */
#define INJECT_SEM_GIVE 0xe0809fafu
#define INJECT_RSC_ALLOC 0xe099d297u   /* AllocateContinuousMemoryResource(bytes, cbr, ctx, type) */
#define INJECT_RSC_FREE 0xe099d2c5u    /* FreeMemoryResource(suite, cbr, ctx) */
#define INJECT_RSC_FIRST 0xe07ea5c9u   /* GetFirstChunkFromSuite */
#define INJECT_RSC_ADDR 0xe07ea36bu    /* GetMemoryAddressOfMemoryChunk */
#define INJECT_RSC_SIZE 0xe07ea32fu    /* GetSizeOfMemoryChunk */
#define INJECT_BAND_TYPE 4u
#define INJECT_BAND_TIMEOUT_MS 200u
#define INJECT_BAND_REQUEST 1179648u /* 1.125 MiB */
#define INJECT_BAND_OUT_STRIPS 20u   /* 160 rows, 768000 B per Write64 */
#define INJECT_BAND_OUT_BYTES (INJECT_BAND_OUT_STRIPS * INJECT_SHARED_STRIP_BYTES)
/* Source rows per band read, without a divide: (REQUEST - OUT) / (YCA + PIC row). */
#define INJECT_BAND_ROWS_LCD 70u /* 70 * (3072 + 2560) = 394240 */
#define INJECT_BAND_ROWS_EVF 46u /* 46 * (4800 + 4000) = 404800 */
/* inject_build.band */
#define INJECT_BAND_NONE 0     /* not tried */
#define INJECT_BAND_USED 1
#define INJECT_BAND_EMPTY 2    /* the callback returned no suite */
#define INJECT_BAND_TIMEOUT 3  /* no callback within the timeout: abandoned, freed on arrival */
#define INJECT_BAND_BAD 4      /* no chunk, too small, or not an uncacheable alias: freed */
#define INJECT_BAND_LATE 5     /* an abandoned request is still outstanding: not asked again */
#define INJECT_BAND_SEM 6      /* the semaphore could not be created */

/* Canon's GUI develop state (RequestPlayDevelopStart E071950C, literal 0x17438
 * at E0719914, state at +0x1C: 0 idle, 1 started, 2 dry run done, 3 saving). */
#define INJECT_GUI_DEVELOP_STATE 0x00017454u
#define INJECT_PENDING_TICKS 30000u /* 10 min at 20 ms (below) */
#define INJECT_DEVELOP_TICKS 250u   /* RATE refused for 5 s after an H8 save */
#define INJECT_YCA_PATH "B:/DCIM/R5SHOTS/HWJPG.YCA" /* unused here */
#define INJECT_SOURCE_PATH_BYTES 32u
#define INJECT_YCA_HEADER_BYTES 512u
#define INJECT_SHARED_STRIP_BYTES 38400u /* = Y22_STRIP_BYTES */
/* >= 3 * 1600; a multiple of 32 and 32-aligned, so cache maintenance on it
 * never shares a line with a neighbour. */
#define INJECT_SHARED_ROW_BYTES 4832u
/* Reading the source rows into cacheable BSS (0x002xxxxx) failed with Err 70
 * on camera. FIO_ReadFile E07E2110 reaches the storage layer's CPU block read
 * E01D346E, which first calls
 * E01D32FC; that tests the buffer with E04E2046 (1 only for 0x40000000 <= addr <
 * 0xC0000000) and otherwise logs "Please Designate Uncacheable Addr!!!!!! ... %#x"
 * and asserts (E01D3398, Storage::FSysUtilRWMainSeq.c line 0x70). Canon's own readers use
 * AllocateUncacheableMemory E07F7728 (e.g. tomGetTOFInfo E038666E..E038668C): an
 * aligned malloc, DCIMVAC over the range (E0808DD8), then addr | 0x40000000
 * (E04E1FE0). inject_api.uncached does the same two steps on our own 32-aligned
 * static buffers; every FIO_ReadFile buffer is such an alias, and the CPU then
 * reads it only through that alias. */
#define INJECT_DCACHE_INVALIDATE 0xe0808dd9u /* (start, bytes): DCIMVAC by 32-byte line, DSB */
#define INJECT_UNCACHED_ALIAS 0x40000000u
/* Conversion stage reached (row V=): the call that failed, or DONE. */
#define INJECT_CONV_SOURCE_SIZE 1
#define INJECT_CONV_OPEN 2
#define INJECT_CONV_HEADER 3
#define INJECT_CONV_CREATE 4
#define INJECT_CONV_STRIPS 5
#define INJECT_CONV_CLOSE 6
#define INJECT_CONV_DONE 7
/* The picture layer under the OSD. The source
 * SS_nnnnn.YCA's own SS_nnnnn.PIC (picture_layer.h header, 10-bit plane) is
 * composited row by row under the premultiplied OSD before the Y22 mapping
 * (y22_stream.h y22_composite_row). Any doubt
 * about the PIC falls back to the OSD alone (status below); a read failure after
 * the PIC was admitted fails the conversion. */
#define INJECT_PIC_NONE 0     /* not reached */
#define INJECT_PIC_OK 1       /* composited */
#define INJECT_PIC_MISSING 2  /* GetFileSize64 failed: no .PIC (its write skipped or failed) */
#define INJECT_PIC_SIZE 3     /* not 512 + w*h*5/2 bytes */
#define INJECT_PIC_OPEN 4
#define INJECT_PIC_HEADER 5   /* short read, or the header differs from this YCA (number, output, geometry, 601, range 3) */
#define INJECT_PIC_PATH "PIC" /* the pending path with its YCA suffix replaced */
#define INJECT_PIC_ROW_BYTES 4000u /* 1600 * 5 / 2: one 10-bit row of the widest output; >= the 512-byte header */
/* RATE writes a raw display-layer file (SS_nnnnn.YCA: 512-byte R5SS v3 header
 * + 3*w*h bytes, one direct write). The same RATE, still on GuiMainTask under
 * its capture guard, converts that file (over its .PIC) into HWJPG.Y22
 * (inject_y22_build, y22_stream.c) and only then marks it pending. H3 only
 * reads HWJPG.Y22 into desc[0]. Converting inside H3 on ShootVfxStage failed
 * with Err 70 (above).
 *
 * Pending (1: HWJPG.Y22 holds this screenshot): written by GuiMainTask (clear at
 * every admitted RATE, ready after HWJPG.Y22 is closed and flushed), consumed by
 * H3 (atomic exchange). Its age
 * is in r5shutter worker ticks (20 ms, 32-bit; Canon's microsecond timer is
 * only known to be >= 28 bits wide). BSS: zero at every boot.
 *
 * Guard (both directions). capture: GuiMainTask
 * holds it from RATE admission to the end of its last file call. busy: H3
 * holds it from the start of a kind-0 call to its end. develop: set by the H8
 * save, cleared by the next kind-0 H3 end or an H8 cancel; RATE treats it as
 * live for INJECT_DEVELOP_TICKS. Each side stores its own flag seq_cst, issues
 * a full barrier, then loads the other's (Dekker): at most one side proceeds,
 * so the shared buffers and the source / Y22 files have one user at a time. A
 * refused H3 falls through to stock Resize; a refused RATE bips. */
struct inject_pending {
    uint32_t pending;  /* 1: HWJPG.Y22 of this screenshot waits for one S2 Resize */
    uint32_t set_tick; /* worker tick at "ready" (published before pending) */
    uint32_t sets, clears, consumed, expired, not_pending;
    char path[INJECT_SOURCE_PATH_BYTES]; /* the source .YCA (written before pending) */
};
extern struct inject_pending inject_pending;
struct inject_guard {
    uint32_t capture, busy, develop, develop_tick;
    uint32_t refused_capture; /* H3 refused because a capture held the guard */
    uint32_t refused_develop; /* RATE refused because a develop / H3 held it */
};
extern struct inject_guard inject_guard;
/* The strip and row buffers: used by the converter, only while the capture
 * holds its guard flag. inject_shared_row is
 * the cached work row; inject_dma_row and inject_pic_row are FIO_ReadFile
 * buffers, used only through their uncacheable alias. */
extern uint8_t inject_shared_strip[INJECT_SHARED_STRIP_BYTES];
extern uint8_t inject_shared_row[INJECT_SHARED_ROW_BYTES];
extern uint8_t inject_dma_row[INJECT_SHARED_ROW_BYTES];
extern uint8_t inject_pic_row[INJECT_PIC_ROW_BYTES];
/* The latest conversion (rows R5C6 V= / X=): one writer, GuiMainTask. */
struct inject_build {
    uint32_t builds, failures, number, stage; /* stage INJECT_CONV_* reached */
    int32_t rc;                               /* rc of the call that failed, else 0 */
    uint32_t source_bytes, y22_written, us;   /* us: Canon timer, the conversion's duration */
    uint32_t source_wh, pic_status;           /* INJECT_PIC_* */
    int32_t pic_rc;
    /* Row R5C6 Y=: where us goes, summed over the build. fill = all of
     * y22_stream_fill (it includes yca, copy, pic and comp); map = Map + Unmap of
     * each strip, write = its Write64. */
    uint32_t us_yca, us_pic, us_comp, us_fill, us_map, us_write;
    /* Row R5C6 B=: the band buffer (INJECT_BAND_*): status, the wait for
     * it (post to semaphore), and its chunk's CPU address and size. */
    uint32_t band, us_band, band_addr, band_size;
};
extern struct inject_build inject_build;
/* GuiMainTask, holding the capture guard, after the source .YCA (and .PIC) were
 * closed and flushed: 0 when HWJPG.Y22 is complete; on failure it is removed. */
uint32_t inject_y22_build(const char *source);
void *inject_native_uncached(void *buffer, uint32_t bytes); /* firmware inject_api.uncached (one_press.c uses it too) */
void inject_y22_clear(void);                             /* GuiMainTask */
void inject_y22_ready(uint32_t tick, const char *path);  /* GuiMainTask, after Close + Flush */
uint32_t inject_capture_begin(void);                     /* GuiMainTask: 0 admitted (capture held), 1 refused */
void inject_capture_end(void);

/* Patch destinations of H3 / H8 (hw_jpeg_inject_hook.S). */
void inject_encode_entry(void);
void inject_develop_end_entry(void);
/* Hook context: entry_sp is the SP before the hook's own push (saved + 32). */
void inject_record(uint32_t hook, const uint32_t saved[INJECT_SAVED_WORDS], uint32_t entry_sp);
/* Export bundle (combo_observe_report): R5C6 inject rows. */
void inject_report(void);
#endif /* __ASSEMBLER__ */
#endif
