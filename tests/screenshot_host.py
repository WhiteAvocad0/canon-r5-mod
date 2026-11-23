"""Shared host harness for the opt-in --screenshot tests (tests/test_screenshot*.py).

No ROM, camera, card, device or ARM toolchain: the public sources in
src/experimental/xpan are compiled with the host C compiler through their
COMBO_OBSERVE_HOST seam and driven against fake Canon RAM, FIO, RscMgr and INDEV
layers defined here. Every input is synthetic.

The C shims and fakes are ported from the maintainer's private lab harness for the
same code; the reference Y22 converter below is ported from the lab's converter
script. Neither needs anything outside this repository at runtime.
"""
from __future__ import annotations

import ctypes
import functools
import operator
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
XPAN_SRC = ROOT / "src" / "experimental" / "xpan"
XPAN_TOOLS = XPAN_SRC / "tools"
R5_221_SRC = ROOT / "src" / "r5_221"
for _p in (ROOT / "tools", ROOT / "tests", XPAN_TOOLS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import record_indicator as ri  # noqa: E402
import screenshot as sc  # noqa: E402
from contracts import MANIFEST  # noqa: E402
from host_cc import find_host_cc  # noqa: E402

AUDIO = MANIFEST["audio"]
SOURCE = "0123456789abcdef"
U = ctypes.c_uint32
I = ctypes.c_int32


# --- constants, read from the C headers that own them ------------------------

def _header(name: str) -> str:
    return (XPAN_SRC / name).read_text()


def defines(name: str, prefix: str) -> dict[str, int]:
    """Plain integer #defines NAME value[u] of a header, keyed without the prefix."""
    pat = r"^#define " + re.escape(prefix) + r"(\w+) (0x[0-9a-fA-F]+|\d+)u?\b"
    return {k: int(v, 0) for k, v in re.findall(pat, _header(name), re.M)}


def enum_values(name: str, prefix: str) -> dict[str, int]:
    """NAME = value enumerators of a header, keyed without the prefix."""
    return {k: int(v) for k, v in re.findall(r"\b" + re.escape(prefix) + r"(\w+) = (\d+)", _header(name))}


O = enum_values("combo_capture.h", "CAPTURE_")                      # capture outcomes (OK, REFUSED_*, ...)
V = {k: 1 << int(b) for k, b in re.findall(r"^#define COMBO_V_(\w+) \(1u << (\d+)\)", _header("combo_observe.h"), re.M)}
CAP = defines("combo_capture.h", "CAPTURE_")
OP = defines("one_press.h", "OP_")
INJ = defines("hw_jpeg_inject.h", "INJECT_")
REC_STATE_WORD = 0x16DD4
GUI_MODE_WORD = 0x8F40
JOB_MAIN, JOB_SUB = 0xA0D4, 0xA0D8
GUI_DEVELOP_STATE = INJ["GUI_DEVELOP_STATE"]
PLAYBACK = OP["GUI_PLAYBACK"]
VIEW_WORD = OP["GUI_VIEW_HANDLE"]
PHOTO = (2 << 30) | (1 << 28) | (100 << 18) | (13 << 4)  # 100/0013, group handle (sub 0)
FM_NOT_FOUND = ctypes.c_int32(0x82100019).value            # Canon FileMgr, sub 0 ("FileType Not Found")
# The block Canon's GUI passes to INDEV_DevelopStart for a stock S2 Resize (one_press.c op_block_*).
P1_BLOCK = {0x04: 4, 0x1C: 0x157C, 0x20: 1, 0x24: 0x81, 0x2C: 4, 0x38: 0xFFFF, 0x3C: 0xFFFF, 0x40: 2, 0x44: 3, 0x4C: 1,
            0x58: 3, 0x5C: 0x00FF0F00, 0x60: 3, 0x64: 1, 0x68: 1, 0x70: 1, 0x78: 3, 0xD4: 1, 0x104: 1, 0x108: 1}
SS = "B:/DCIM/R5SHOTS/SS_%05d.YCA"
Y22_PATH = "B:/DCIM/R5SHOTS/HWJPG.Y22"
Y22_BYTES = 7_680_000
STRIP = 38_400
BAND_WRITE = 768_000


# --- ctypes mirrors of the shared ABI structs (sizes checked against the C) --

def _u(names: str):
    return [(n, U) for n in names.split()]


def _i(names: str):
    return [(n, I) for n in names.split()]


class CaptureResult(ctypes.Structure):
    _fields_ = (_u("seq outcome number tick known pass gui_mode probes width height marv data format bytes sel duration")
                + _i("rc_mount rc_create rc_header rc_data rc_close rc_flush rc_remove rc_map_header rc_map_data")
                + _u("us_start us_create us_header us_data us_close us_flush us_probed job_main job_sub rec_idle yca_outcome")
                + _i("rc_yca_remove rc_yca_create rc_yca_header rc_yca_data rc_yca_close rc_yca_flush rc_yca_cleanup")
                + _u("us_yca_start us_yca_create us_yca_header us_yca_data us_yca_flush"))


class CaptureState(ctypes.Structure):
    _fields_ = (_u("held presses repeats debounced attempts busy any last_tick next_number published")
                + [("outcomes", U * 16), ("results", CaptureResult * 8)] + _u("held_tick stale_held"))


class CaptureFeedback(ctypes.Structure):
    _fields_ = (_u("publish_attempted ready fault seen lost clicks bips silent skipped last_reason") + _i("last_rc")
                + [("last", CaptureResult * 8)] + _u("kept bips_owed bip_tick bip_waits refusals_seen cancels_seen"))


class OnePressState(ctypes.Structure):
    _fields_ = (_u("state latched start_tick attempts jobs saves cancels ends timeouts refused late "
                   "outcome number gui_mode dir file handle pending_tick") + _i("rc_open rc_read fm_rc")
                + [("fm_info", U * 5)]
                + _u("develop_state gui_busy indev_busy pic_outcome pic_bytes us_start us_done us_end done_result end_arg "
                     "end_reason dry_seen dry_ok dry_wh armed refusals source view_handle") + _i("view_fm_rc")
                + _u("view_info0 view_info2"))


class OnePressDefer(ctypes.Structure):
    _fields_ = (_u("defer why xpan marked starts fallbacks last_good") + _i("last_good_rc")
                + _u("decisions play_since held_since auto_for auto_why auto_gui auto_posts auto_ticks"))


class InjectBuild(ctypes.Structure):
    _fields_ = (_u("builds failures number stage") + _i("rc") + _u("source_bytes y22_written us source_wh pic_status")
                + _i("pic_rc") + _u("us_yca us_pic us_comp us_fill us_map us_write band us_band band_addr band_size"))


class InjectPending(ctypes.Structure):
    _fields_ = _u("pending set_tick sets clears consumed expired not_pending") + [("path", ctypes.c_char * 32)]


class InjectGuard(ctypes.Structure):
    _fields_ = _u("capture busy develop develop_tick refused_capture refused_develop")


class InjectEntry(ctypes.Structure):
    _fields_ = (_u("seq outcome flags us age_us mpidr sp lo hi base_w xoff yoff w h") + _i("rc_size")
                + _u("size_lo size_hi") + _i("fd rd rc_close") + _u("us_size us_open us_read us_close"))


class InjectRing(ctypes.Structure):
    _fields_ = [("entries", InjectEntry * 16)] + _u("claimed encode_main encode_other develop_save develop_cancel "
                                                    "develop_other armed arm_us")


SIZED = (("capture_state", CaptureState), ("capture_feedback", CaptureFeedback), ("one_press_state", OnePressState),
         ("inject_build", InjectBuild), ("inject_pending", InjectPending), ("inject_guard", InjectGuard),
         ("inject_ring", InjectRing), ("one_press_defer", OnePressDefer))


def as_dict(s: ctypes.Structure) -> dict:
    out = {}
    for name, _ in s._fields_:
        v = getattr(s, name)
        out[name] = v if isinstance(v, (int, bytes)) else list(v)
    return out


def check_abi(lib) -> None:
    """The ctypes mirrors must match the compiled structs (host_sizeof in the shims)."""
    for i, (name, cls) in enumerate(SIZED):
        got = lib.host_sizeof(i)
        if got and got != ctypes.sizeof(cls):
            raise AssertionError(f"{name}: C {got} B, ctypes {ctypes.sizeof(cls)} B")


SIZEOF_C = r'''
uint32_t host_sizeof(uint32_t which)
{
    switch (which) {
    case 0: return HOST_SIZE_0; case 1: return HOST_SIZE_1; case 2: return (uint32_t)sizeof(struct one_press_state);
    case 3: return (uint32_t)sizeof(struct inject_build); case 4: return (uint32_t)sizeof(struct inject_pending);
    case 5: return (uint32_t)sizeof(struct inject_guard); case 6: return (uint32_t)sizeof(struct inject_ring);
    case 7: return (uint32_t)sizeof(struct one_press_defer);
    default: return 0u;
    }
}
'''


# --- reference converter -------------------------------------------------------
# Ported (stdlib only, same integer formulas) from the maintainer's private lab
# converter make_y22.py, the byte-for-byte reference y22_stream.c and
# hw_jpeg_inject.c were developed against. y22_stream.h documents the mapping.

OUT_W, OUT_H = 2400, 1600
OUT_PITCH = 2 * OUT_W
GEOMETRY = {(1024, 682): (2400, 0), (1600, 1200): (2132, 134)}  # (w, h) -> (active columns, left pillar)
PIC_HEADER = 512


def source_row(dy: int, h: int = 682) -> int:
    return dy * h // OUT_H


def source_col(x: int, w: int = 1024, h: int = 682) -> int | None:
    """Source column of output x, or None inside a pillar."""
    active, pillar = GEOMETRY[(w, h)]
    if not pillar <= x < pillar + active:
        return None
    return (x - pillar) * w // active


@functools.lru_cache(maxsize=None)
def column_map(w: int, h: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Per output byte of a row: its source byte inside a YCA row (-1 for a pillar byte, which is 0) and XOR mask."""
    idx, mask = [], []
    for j in range(OUT_W // 2):
        sx0, sx1 = source_col(2 * j, w, h), source_col(2 * j + 1, w, h)
        if sx0 is None:  # pillars are whole pairs (134 is even)
            idx += [-1] * 4
            mask += [0] * 4
            continue
        p0, p1 = (sx0 >> 1) * 6, (sx1 >> 1) * 6
        idx += [p0, p0 + 1 + 2 * (sx0 & 1), p0 + 2, p1 + 1 + 2 * (sx1 & 1)]
        mask += [0x80, 0, 0x80, 0]
    return tuple(idx), tuple(mask)


def r5ss_geometry(yca: bytes) -> tuple[int, int]:
    """The R5SS v3 header combo_capture.c fill_header writes (only what the mapping needs)."""
    if yca[:4] != b"R5SS":
        raise ValueError("not an R5SS file")
    version, size, w, h, pitch, length, fourcc = struct.unpack_from("<HHIIII4s", yca, 4)
    if (version, size, fourcc) != (3, 512, b"UYVA") or pitch != 3 * w or length != pitch * h or len(yca) != 512 + length:
        raise ValueError("R5SS header")
    return w, h


def convert(yca: bytes) -> bytes:
    """SS_nnnnn.YCA -> HWJPG.Y22 (make_y22.py --no-bars)."""
    w, h = r5ss_geometry(yca)
    if (w, h) not in GEOMETRY:
        raise ValueError(f"source {w}x{h}: only 1024x682 and 1600x1200 are mapped")
    idx, mask = column_map(w, h)
    # Pillar bytes gather a trailing zero byte (index -1), whose mask is 0.
    gather = operator.itemgetter(*idx)
    pitch, pixels = 3 * w, yca[512:]
    rows: dict[int, bytes] = {}
    out = bytearray()
    for dy in range(OUT_H):
        sy = source_row(dy, h)
        row = rows.get(sy)
        if row is None:
            src = pixels[sy * pitch:(sy + 1) * pitch] + b"\0"
            row = rows[sy] = bytes(v ^ m for v, m in zip(gather(src), mask))
        out += row
    assert len(out) == Y22_BYTES
    return bytes(out)


def _u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "little")


def pic_status(yca: bytes, pic: bytes | None) -> str:
    """hw_jpeg_inject.c open_pic's checks: "OK" or the INJECT_PIC_* reason it falls back with."""
    w, h = r5ss_geometry(yca)
    if pic is None:
        return "MISSING"
    plane = w * h * 5 // 2
    if len(pic) != PIC_HEADER + plane:
        return "SIZE"
    i = 64  # the DISPIMAGEINFO copy (picture_layer.c pl_select)
    out = int(w == 1600)
    number = _u32(yca, 28) >> 16
    ok = (pic[:4] == b"R5PI" and pic[4] == 1 and pic[5] == 0 and _u32(pic, 4) >> 16 == 512 and _u32(pic, 8) == out
          and _u32(pic, 12) & 0xFFFF == number and _u32(pic, 44) == plane and _u32(pic, 48) == w and _u32(pic, 52) == h
          and _u32(pic, i) == 1 and _u32(pic, i + 4) == out and _u32(pic, i + 0x14) == w and _u32(pic, i + 0x18) == h
          and not any(_u32(pic, i + o) for o in (0x1C, 0x20, 0x24, 0x28)) and _u32(pic, i + 0x2C) == w
          and _u32(pic, i + 0x30) == h and _u32(pic, i + 0x34) == 1 and _u32(pic, i + 0x38) == 3)
    return "OK" if ok else "HEADER"


def _d255(x: int) -> int:
    return (x + 127) // 255


def composite_row(osd: bytearray, pic: bytes, w: int) -> None:
    """One row in place (y22_stream.h y22_composite_row): osd is w/2 UYVA pairs (6 B), pic the plane row (5 B a pair)."""
    for j in range(w // 2):
        v = int.from_bytes(pic[5 * j:5 * j + 5], "little")
        cb, y0, cr, y1 = [((v >> s) & 1023) >> 2 for s in (0, 10, 20, 30)]
        k = 6 * j
        u, oy0, vv, oy1, a0, a1 = osd[k:k + 6]
        b = 255 - ((a0 + a1 + 1) >> 1)
        base = _d255(b * 128)
        osd[k + 1] = min(255, oy0 + _d255((255 - a0) * y0))
        osd[k + 3] = min(255, oy1 + _d255((255 - a1) * y1))
        osd[k] = max(0, min(255, u + _d255(b * cb) - base))
        osd[k + 2] = max(0, min(255, vv + _d255(b * cr) - base))


def composite(yca: bytes, pic: bytes) -> bytes:
    """The YCA with its OSD composited over the PIC's picture layer (A kept)."""
    w, h = r5ss_geometry(yca)
    out = bytearray(yca)
    pitch, prow = 3 * w, w * 5 // 2
    for y in range(h):
        row = bytearray(out[512 + y * pitch:512 + (y + 1) * pitch])
        composite_row(row, pic[PIC_HEADER + y * prow:PIC_HEADER + (y + 1) * prow], w)
        out[512 + y * pitch:512 + (y + 1) * pitch] = row
    return bytes(out)


# --- synthetic inputs ------------------------------------------------------------

def r5ss(w: int, h: int, body: bytes, number: int = 0, sel: int = 0) -> bytes:
    """A v3 R5SS file laid out as combo_capture.c fill_header writes it."""
    hdr = bytearray(512)
    hdr[:4] = b"R5SS"
    struct.pack_into("<HHIIII4sI", hdr, 4, 3, 512, w, h, 3 * w, 3 * w * h, b"UYVA", (number & 0xFFFF) << 16 | sel << 8)
    assert len(body) == 3 * w * h
    return bytes(hdr) + body


def _table(values) -> bytes:
    return bytes(values[i % len(values)] for i in range(256))


@functools.lru_cache(maxsize=None)
def synthetic_yca(w: int, h: int, number: int = 1, seed: int = 0) -> bytes:
    """A deterministic OSD: chroma near neutral, alpha from {0, 0, 37, 128, 255} (Y, U, V, Y, A, A per pair)."""
    rnd = random.Random(f"yca:{w}x{h}:{number}:{seed}")
    body = bytearray(rnd.randbytes(3 * w * h))
    chroma = _table(range(88, 168))
    alpha = _table((0, 0, 37, 128, 255, 0, 255))
    for k in (0, 2):
        body[k::6] = body[k::6].translate(chroma)
    for k in (4, 5):
        body[k::6] = body[k::6].translate(alpha)
    return r5ss(w, h, bytes(body), number)


def info_words(kind, out, lo, hi, w, h, cs=1, rng=3) -> dict[int, int]:
    """A DISPIMAGEINFO (picture_layer.h) of the whole w x h panel plane at 0,0."""
    return {0x00: kind, 0x04: out, 0x08: lo, 0x0C: hi, 0x14: w, 0x18: h, 0x2C: w, 0x30: h, 0x34: cs, 0x38: rng, 0x74: 1}


@functools.lru_cache(maxsize=None)
def synthetic_pic(w: int, h: int, number: int = 1, over: tuple = (), seed: int = 0) -> bytes:
    """A .PIC as picture_layer.c pl_select writes it: 512-byte header, then the 10-bit plane (random)."""
    rnd = random.Random(f"pic:{w}x{h}:{seed}")
    out = int(w == 1600)
    hdr = bytearray(512)
    hdr[:4] = b"R5PI"
    struct.pack_into("<HH", hdr, 4, 1, 512)
    struct.pack_into("<14I", hdr, 8, out, number, 1200, 0x2002, 0x11, 0, 3, 0, 0, w * h * 5 // 2, w, h, 1, 0x300000)
    info = info_words(1, out, 0, 3, w, h)
    info.update(dict(over))
    for off, v in info.items():
        struct.pack_into("<I", hdr, 64 + off, v)
    return bytes(hdr) + rnd.randbytes(w * h * 5 // 2)


@functools.lru_cache(maxsize=None)
def reference_y22(yca: bytes, pic: bytes | None = None) -> bytes:
    """What the camera should write: the OSD over an admitted .PIC (else alone), mapped."""
    if pic is not None and pic_status(yca, pic) == "OK":
        yca = composite(yca, pic)
    return convert(yca)


# --- host compilation ------------------------------------------------------------

_TMP = tempfile.TemporaryDirectory(prefix="r5-screenshot-")
_LIBS: dict = {}


def _scratch(name: str) -> Path:
    root = Path(_TMP.name)
    d = root / name
    if root.is_symlink() or d.exists():
        raise RuntimeError(f"unexpected scratch path {d}")
    d.mkdir()
    return d


def compile_lib(name: str, sources: list[Path], generated: dict[str, str], defines: tuple[str, ...] = ()):
    """One shared library from public sources plus generated shims, built in a fresh scratch folder."""
    d = _scratch(name)
    (d / "observer-source.h").write_text(
        f'#define OBSERVER_SOURCE_ID "{SOURCE}"\n#define OBSERVER_CLASS 0x84u\n#define OBSERVER_LEVEL 0x3u\n'
        "#define OBSERVER_MAX_NOTIFICATIONS_LOGGED 64u\n#define OBSERVER_MAX_DUMPS 8u\n")
    for file_name, text in generated.items():
        (d / file_name).write_text(text)
    lib = d / (name + ".so")
    cmd = [find_host_cc(), "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-DCOMBO_OBSERVE_HOST",
           *defines, "-I", str(d), "-I", str(XPAN_SRC), "-I", str(R5_221_SRC),
           *map(str, sources), *(str(d / n) for n in generated if n.endswith(".c")), "-o", str(lib)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        raise AssertionError("host compile failed:\n" + proc.stderr)
    # ctypes loads with RTLD_NOW: an unresolved symbol fails here, on every host.
    return ctypes.CDLL(str(lib))


def host_cue_bindings() -> str:
    """screenshot.py's capture-cue-bindings.c without the ELF section placement (host objects)."""
    src = sc.capture_cue_bindings(sc.facts(sc.RATE))
    return src.replace(' __attribute__((section(".resident_rodata")))', "").replace('section(".resident_data"), ', "")


# --- the RATE world: combo_capture.c and everything it calls -------------------

CAPTURE_SHIM = r'''
#include "combo_observe.h"
#include "combo_capture.h"
#include "hw_jpeg_inject.h"
#include "one_press.h"
#include "observer.h"
#include "rec_tone.h"
#include <string.h>
typedef uint32_t (*py_read_fn)(uint32_t);
typedef uint32_t (*py_counter_fn)(uint32_t *, uint32_t *);
typedef int32_t (*py_path_fn)(uint32_t op, const char *path);
typedef int32_t (*py_io_fn)(uint32_t op, uint32_t a, uint32_t b, uint32_t c, uint32_t d, uint32_t e);
typedef int32_t (*py_query_fn)(uint32_t handle);
static py_read_fn py_read; static py_counter_fn py_counter; static py_path_fn py_path; static py_io_fn py_io;
static py_query_fn py_query;
void host_set(py_read_fn r, py_counter_fn c, py_path_fn p, py_io_fn io, py_query_fn q)
{ py_read = r; py_counter = c; py_path = p; py_io = io; py_query = q; }
static uint32_t h_read(uint32_t a) { return py_read(a); }
static unsigned h_counter(unsigned *c, unsigned *v) { return py_counter((uint32_t *)c, (uint32_t *)v); }
void combo_host_after_copy(uint32_t seq) { (void)seq; }
const struct combo_observe_api combo_observe_api = {h_read, h_counter};
/* capture_api: the RATE writer's FIO, answered by the Python fake card. */
static int32_t f_open(const char *p, uint32_t m) { return py_io(1, (uint32_t)py_path(0, p), m, 0, 0, 0); }
static int32_t f_create(const char *p) { return py_path(2, p); }
static int32_t f_remove(const char *p) { return py_path(3, p); }
static int32_t f_write(int32_t fd, uint32_t u, uint32_t lo, uint32_t hi, uint32_t n) { return py_io(4, (uint32_t)fd, u, lo, hi, n); }
static int32_t f_close(int32_t fd) { return py_io(5, (uint32_t)fd, 0, 0, 0, 0); }
static int32_t f_flush(const char *p) { return py_path(6, p); }
static int32_t f_mount(const char *p) { return py_path(7, p); }
static int32_t f_map(uint32_t out[2], uint32_t va, uint32_t n, uint32_t op, uint32_t mode)
{ out[0] = va ^ 0x80000000u; out[1] = 0x1u; return py_io(8, va, n, op, mode, 0); }
static int32_t f_unmap(uint32_t va, uint32_t n, uint32_t op, uint32_t mode) { return py_io(9, va, n, op, mode, 0); }
uint32_t host_us, host_us_step = 1000u;
static uint32_t h_timer(void) { uint32_t t = host_us; host_us += host_us_step; return t; }
const struct capture_api capture_api = {f_open, f_create, f_remove, f_write, f_close, f_flush, f_mount, f_map, f_unmap, h_timer};
uint32_t host_rows;
static void host_debug(unsigned c, unsigned l, const char *fmt, ...) { (void)c; (void)l; (void)fmt; host_rows++; }
const struct observer_api observer_api = {0, 0, host_debug, 0, 0};
/* rec_tone_api: SE_BeepStart and the cache clean are counted; read / query see the fake RAM. */
uint32_t host_beeps, host_beep_id, host_cleans; uintptr_t host_beep_desc;
static int32_t h_query(uint32_t h) { return py_query(h); }
static int32_t h_beep(uint32_t id, const uint32_t *d) { host_beep_id = id; host_beeps++; host_beep_desc = (uintptr_t)d; return 0; }
static void h_clean(const volatile void *p, uint32_t n) { (void)p; (void)n; host_cleans++; }
const struct rec_tone_api rec_tone_api = {h_read, h_query, h_beep, h_clean};
extern void (*capture_host_after_copy)(uint32_t);
void host_after_copy(void (*fn)(uint32_t)) { capture_host_after_copy = fn; }
uintptr_t host_desc(uint32_t i) { return (uintptr_t)capture_cue_descriptor[i]; }
uintptr_t host_pcm(void) { return (uintptr_t)capture_cue_pcm; }
void host_op_reset(void);
void host_tone_reset(void);
void host_reset(void)
{
    memset(&combo_observe_state, 0, sizeof combo_observe_state);
    memset(&combo_hook_ring, 0, sizeof combo_hook_ring);
    memset(&capture_state, 0, sizeof capture_state);
    memset(&capture_feedback, 0, sizeof capture_feedback);
    memset(&capture_watch, 0, sizeof capture_watch);
    memset(&inject_pending, 0, sizeof inject_pending);
    memset(&inject_guard, 0, sizeof inject_guard);
    for (uint32_t i = 0; i < CAPTURE_CUES; i++) capture_cue_descriptor[i][1] = 0u;
    combo_worker_tick = 0u; host_rows = host_beeps = host_beep_id = host_cleans = 0u; host_beep_desc = 0;
    capture_host_after_copy = 0; host_us = 0x7ffff000u; host_us_step = 1000u;
    host_tone_reset(); host_op_reset();
}
/* Test ticks are offset past the 5 s boot guard; host_set_tick_raw sets the true tick. */
#define HOST_TICK_BASE 1000u
void host_set_tick(uint32_t t) { combo_worker_tick = t + HOST_TICK_BASE; }
void host_set_tick_raw(uint32_t t) { combo_worker_tick = t; }
uint32_t host_tick(void) { return combo_worker_tick; }
void host_counter(uint32_t known, uint32_t change)
{ combo_observe_state.counter_known = known; combo_observe_state.counter_change_tick = change + HOST_TICK_BASE; }
#define HOST_SIZE_0 (uint32_t)sizeof(struct capture_state)
#define HOST_SIZE_1 (uint32_t)sizeof(struct capture_feedback)
''' + SIZEOF_C

# -DR5_RECORD_INDICATOR=1: combo_capture.c also gates on rec_tone_state; the preflight is a stub.
INDICATOR_TONE = r'''
#include "rec_tone.h"
#include <string.h>
struct rec_tone_state rec_tone_state;
uint32_t host_preflight_reason, host_preflight_mask;
uint32_t rec_tone_preflight(struct rec_tone_attempt *a, uint32_t mask) { (void)a; host_preflight_mask = mask; return host_preflight_reason; }
void host_tone(uint32_t ready, uint32_t mode, uint32_t pending) { rec_tone_state.ready = ready; rec_tone_state.mode = mode; rec_tone_state.pending = pending; }
void host_tone_fault(uint32_t fault) { rec_tone_state.fault = fault; }
void host_tone_reset(void) { memset(&rec_tone_state, 0, sizeof rec_tone_state); host_preflight_reason = host_preflight_mask = 0u; }
'''

# Screenshot only: the real rec_tone.c (its preflight alone) with the build's own config words.
PREFLIGHT_TONE = r'''
#include "rec_tone.h"
const struct rec_tone_config rec_tone_config = {@CONFIG@};
void host_tone_reset(void) {}
'''

# The RATE-time Y22 build's FIO (inject_y22_build on "GuiMainTask"), faked in C: the
# source is a valid all-zero YCA of the size GetFileSize64 reports, no .PIC, and every
# FIO_ReadFile buffer must come from inject_api.uncached (Canon asserts on a cacheable
# one); host_cached_reads counts violations.
Y22_FIO_FAKE = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "hw_jpeg_inject.h"
uint32_t host_fz_w = 1024u, host_fz_h = 682u, host_cached_reads, host_conv_reads, host_conv_writes, host_conv_fail;
static uint32_t fz_reads; static uintptr_t uc_lo[16], uc_hi[16]; static uint32_t uc_n;
static uint32_t fz(void) { return 0; }
uint32_t host_y22_us, host_y22_us_step;
static uint32_t f_tm(void) { uint32_t t = host_y22_us; host_y22_us += host_y22_us_step; return t; }
static void *f_uncached(void *b, uint32_t n) { if (uc_n < 16) { uc_lo[uc_n] = (uintptr_t)b; uc_hi[uc_n] = (uintptr_t)b + n; uc_n++; } return b; }
static int uc_ok(const void *b, uint32_t n) { for (uint32_t i = 0; i < uc_n; i++) if ((uintptr_t)b >= uc_lo[i] && (uintptr_t)b + n <= uc_hi[i]) return 1; return 0; }
static int ends(const char *p, const char *x) { size_t a = strlen(p), b = strlen(x); return a >= b && !strcmp(p + a - b, x); }
static int32_t f_size(const char *p, uint32_t o[2]) { o[1] = 0; if (ends(p, ".PIC")) { o[0] = 0; return -1; } o[0] = ends(p, ".Y22") ? 7680000u : 512u + 3u * host_fz_w * host_fz_h; return 0; }
static int32_t f_open(const char *p, uint32_t m) { (void)m; fz_reads = 0; return ends(p, ".PIC") ? -1 : 9; }
static int32_t f_read(int32_t fd, void *b, uint32_t n)
{
    (void)fd; host_conv_reads++;
    if (!uc_ok(b, n)) { host_cached_reads++; return -77; }
    memset(b, 0, n);
    if (fz_reads++ == 0) {
        uint8_t *h = b; uint32_t w = host_fz_w, ht = host_fz_h, v[5] = {w, ht, 3u * w, 3u * w * ht, 0};
        memcpy(h, "R5SS", 4); h[4] = 3; h[6] = 0; h[7] = 2; memcpy(h + 8, v, 16); memcpy(h + 24, "UYVA", 4);
    }
    return (int32_t)n;
}
static int32_t f_ibus(int32_t fd, uint32_t u, uint32_t lo, uint32_t hi, uint32_t n) { (void)fd; (void)u; (void)lo; (void)hi; return (int32_t)n; }
static int32_t f_close(int32_t fd) { (void)fd; return 0; }
static int32_t f_path(const char *p) { (void)p; return host_conv_fail ? -1 : 10; }
static int32_t f_ok(const char *p) { (void)p; return 0; }
static int32_t f_w64(int32_t fd, uint32_t u, uint32_t lo, uint32_t hi, uint32_t n) { (void)fd; (void)u; (void)lo; (void)hi; host_conv_writes++; return (int32_t)n; }
static int32_t f_map(uint32_t o[2], uint32_t va, uint32_t n, uint32_t op, uint32_t mode) { (void)n; (void)op; (void)mode; o[0] = va; o[1] = 1; return 0; }
static int32_t f_unmap(uint32_t va, uint32_t n, uint32_t op, uint32_t mode) { (void)va; (void)n; (void)op; (void)mode; return 0; }
static void f_bar(void) {}
static uint32_t f_rd(uint32_t a) { (void)a; return 0; }
const struct inject_api inject_api = {f_rd, f_tm, fz, f_size, f_open, f_ibus, f_close, f_bar, f_read, f_path, f_ok, f_ok, f_w64, f_map, f_unmap, f_uncached};
void host_band_reset(void);
void host_fz_reset(void) { host_band_reset(); uc_n = 0; host_y22_us = host_y22_us_step = 0; host_cached_reads = host_conv_reads = host_conv_writes = host_conv_fail = 0; memset(&inject_build, 0, sizeof inject_build); }
'''

# Canon's semaphore and RscMgr suite calls (hw_jpeg_inject.h inject_band_api), appended
# to a fake that defines f_uncached. host_band_mode: 0 the callback returns no suite
# (the default: other tests keep the strip path); 1 a suite with one chunk of
# host_band_size bytes at a token address; 2 no callback before the timeout (it
# arrives with host_band_arrive); 3 a 16-aligned chunk address (Canon's chunks are
# only 16-aligned; the layout starts at the next 4 KiB boundary, host_band_ptr);
# 4 the callback runs after the first take timed out, before the CAS; 5 as 4, but its
# give lands only at host_band_release_give (a stale give).
BAND_FAKE = r'''
uint8_t *host_band_mem, *host_band_ptr; uint32_t host_band_size = 1179648u, host_band_mode, host_band_allocs, host_band_frees, host_band_req, host_band_type;
uint32_t host_band_takes, host_band_freed_suite; static int band_sem_count; static inject_band_cbr band_late; static uint32_t band_late_ctx;
void inject_host_band_reset(void);
static uint32_t b_create(const char *n, uint32_t i) { (void)n; (void)i; return 0x5e3u; }
static int band_hold_give, band_held_give; void host_band_arrive(void);
static int32_t b_take(uint32_t s, uint32_t t)
{
    (void)s; (void)t; host_band_takes++;
    if (band_sem_count) { band_sem_count = 0; return 0; }
    if ((host_band_mode == 4 || host_band_mode == 5) && band_late) { band_hold_give = host_band_mode == 5; host_band_arrive(); band_hold_give = 0; }
    return 9;
}
static int32_t b_give(uint32_t s) { (void)s; if (band_hold_give) band_held_give = 1; else band_sem_count = 1; return 0; }
void host_band_release_give(void) { if (band_held_give) { band_held_give = 0; band_sem_count = 1; } }
static void b_alloc(uint32_t n, inject_band_cbr cbr, uint32_t ctx, uint32_t type)
{
    host_band_allocs++; host_band_req = n; host_band_type = type;
    if (host_band_mode == 2 || host_band_mode == 4 || host_band_mode == 5) { band_late = cbr; band_late_ctx = ctx; return; }
    cbr(ctx, host_band_mode ? 0x5517eu : 0u);
}
static void b_free(uint32_t s, uint32_t c, uint32_t x) { (void)c; (void)x; host_band_frees++; host_band_freed_suite = s; }
static uint32_t b_first(uint32_t s) { return s == 0x5517eu ? 0xc0u : 0u; }
static uint32_t b_addr(uint32_t c) { (void)c; return host_band_mode == 3 ? 0x40000070u : 0x40000000u; }
static uint32_t b_size(uint32_t c) { (void)c; return host_band_size; }
uint8_t *inject_host_band(uint32_t a)
{
    free(host_band_mem); host_band_mem = malloc(host_band_size + 8192u);
    uint8_t *m = (uint8_t *)(((uintptr_t)host_band_mem + 4095u) & ~(uintptr_t)4095u); /* 4 KiB-aligned, like the token */
    uint8_t *chunk = m + (a & 4095u);
    host_band_ptr = m + ((a & 4095u) ? 4096u : 0u); /* where a layout rounded to 4 KiB starts */
    memset(chunk, 0xa5, host_band_size); f_uncached(chunk, host_band_size); return chunk; /* exactly the chunk is readable */
}
void host_band_arrive(void) { if (band_late) { inject_band_cbr c = band_late; band_late = 0; c(band_late_ctx, 0x5517eu); } }
void host_band_reset(void) { host_band_mode = host_band_allocs = host_band_frees = host_band_req = host_band_type = host_band_takes = host_band_freed_suite = 0u;
                             host_band_size = 1179648u; band_sem_count = 0; band_late = 0; host_band_ptr = 0; band_hold_give = band_held_give = 0; inject_host_band_reset(); }
const struct inject_band_api inject_band_api = {b_create, b_take, b_give, b_alloc, b_free, b_first, b_addr, b_size};
'''

# one_press_api: Canon's FM / GUI / INDEV calls. Like ctgGetObject, the fake FM answers
# only the JPEG sub-handle (sub 2); any other sub is "FileType Not Found". With a list of
# good group handles (op_fm_good), only those photos are found. op_xpan is the playback
# cache's AspectInfo pre-check (one_press.c op_view_xpan).
OP_STUB = r'''
#include <stdint.h>
#include <string.h>
#include "one_press.h"
#include "hw_jpeg_inject.h"
#include "combo_observe.h"
extern uint32_t host_us;
void host_fz_reset(void);
static uint32_t rd(uint32_t a) { return combo_observe_api.read(a); }
static uint32_t tm(void) { return host_us; }
char op_log[512]; uint32_t op_log_n;
static void lg(char c) { if (op_log_n < sizeof op_log - 1) { op_log[op_log_n++] = c; op_log[op_log_n] = 0; } }
uint32_t op_fm_words[8], op_fm_handle, op_fm_calls, op_fm_subs, op_fm_good[4], op_fm_seen[8], op_xpan, op_fm_sets_busy; int32_t op_fm_rc;
extern uint32_t op_indev_busy;
static uint32_t found(uint32_t h)
{
    uint32_t any = 0u;
    for (int i = 0; i < 4; i++) { any |= op_fm_good[i]; if (op_fm_good[i] && op_fm_good[i] == (h & ~15u)) return 1u; }
    return !any;
}
static int32_t o_fm(uint32_t h, uint32_t *info)
{
    lg('F'); if (op_fm_calls < 8) op_fm_seen[op_fm_calls] = h;
    op_fm_calls++; op_fm_subs = op_fm_subs << 4 | (h & 15u); op_fm_handle = h;
    if (op_fm_sets_busy) op_indev_busy = 1u; /* Canon became busy during the FM call */
    if ((h & 15u) != 2u || !found(h)) { for (int i = 0; i < 5; i++) info[i] = 0; return (int32_t)0x82100019u; }
    for (int i = 0; i < 5; i++) info[i] = op_fm_words[i];
    return op_fm_rc;
}
static uint32_t o_xpan(void) { return op_xpan; }
/* E04A787A(class, code, arg2, arg3): queued for GuiMainTask; the tests dispatch the events. */
uint32_t op_posts[16][4], op_post_calls;
static void o_post(uint32_t c, uint32_t code, uint32_t a2, uint32_t a3)
{
    lg('P');
    if (op_post_calls < 16) { op_posts[op_post_calls][0] = c; op_posts[op_post_calls][1] = code; op_posts[op_post_calls][2] = a2; op_posts[op_post_calls][3] = a3; }
    op_post_calls++;
}
static void o_release(void) { lg('R'); }
uint32_t op_gui_busy, op_indev_busy;
static void o_set_gui(uint32_t v) { lg(v ? 'G' : 'g'); op_gui_busy = v; }
static void o_set_indev(uint32_t v) { lg(v ? 'I' : 'i'); op_indev_busy = v; }
static uint32_t o_gui(void) { return op_gui_busy; }
static uint32_t o_indev(void) { return op_indev_busy; }
uint32_t op_start_handle, op_start_remote, op_start_calls; uint8_t op_start_block[284]; uintptr_t op_start_cb, op_done_cb;
static void o_start(uint32_t h, void *blk, op_cb_fn s, op_cb_fn d, uint32_t remote)
{ lg('S'); op_start_calls++; op_start_handle = h; memcpy(op_start_block, blk, 284); op_start_cb = (uintptr_t)s; op_done_cb = (uintptr_t)d; op_start_remote = remote; }
uint32_t op_end_save, op_end_calls; uint8_t op_end_block[284]; uintptr_t op_end_cb;
static void o_end(uint32_t save, void *blk, op_cb_fn e)
{
    lg('E');
    uint32_t saved[INJECT_SAVED_WORDS] = {save, 0x0013f804u, 0, 0, 4, 5, 12, 0}; /* the patched entry: our H8 first */
    inject_record(INJECT_HOOK_DEVELOP_END, saved, 0x00328ce8u);
    op_end_calls++; op_end_save = save; memcpy(op_end_block, blk, 284); op_end_cb = (uintptr_t)e;
}
const struct one_press_api one_press_api = {rd, tm, o_fm, o_release, o_set_gui, o_set_indev, o_gui, o_indev, o_start, o_end,
                                            o_xpan, o_post};
uintptr_t host_done_ptr(void) { return (uintptr_t)one_press_done; }
uintptr_t host_end_ptr(void) { return (uintptr_t)one_press_end; }
void host_op_reset(void)
{
    memset(&one_press_state, 0, sizeof one_press_state); memset(one_press_block, 0, sizeof one_press_block);
    memset(&one_press_defer, 0, sizeof one_press_defer); memset(op_fm_good, 0, sizeof op_fm_good);
    memset(op_fm_seen, 0, sizeof op_fm_seen); op_xpan = 0u; op_fm_sets_busy = 0u;
    memset(op_posts, 0, sizeof op_posts); op_post_calls = 0u; one_press_auto = one_press_clear_visit = 0u;
    op_log_n = 0; op_log[0] = 0; op_end_calls = op_start_calls = op_fm_calls = op_fm_subs = op_fm_handle = op_start_handle = 0;
    op_gui_busy = op_indev_busy = 0; op_fm_rc = 0;
    memset(op_fm_words, 0, sizeof op_fm_words); memset(&inject_ring, 0, sizeof inject_ring);
    host_fz_reset();
}
'''

CAPTURE_C = ("combo_observe.c", "combo_capture.c", "hw_jpeg_inject.c", "y22_stream.c", "picture_layer.c", "one_press.c",
             "tone_gen.c")


def capture_lib(indicator: bool):
    """combo_capture.c with everything it calls; indicator selects -DR5_RECORD_INDICATOR=1.

    Without the indicator the real rec_tone.c is linked: a screenshot-only build has
    only its preflight (no rec_tone_state, no record-cue engine)."""
    key = ("capture", indicator)
    if key not in _LIBS:
        sources = [XPAN_SRC / n for n in CAPTURE_C]
        if indicator:
            tone = INDICATOR_TONE
        else:
            words = ri.config_words({"rec_state_word": ri.REC_STATE_WORD}, AUDIO)
            tone = PREFLIGHT_TONE.replace("@CONFIG@", ", ".join(hex(v) + "u" for v in words))
            sources.append(XPAN_SRC / "rec_tone.c")
        lib = compile_lib("capture-indicator" if indicator else "capture-screenshot-only", sources,
                          {"capture-cue-bindings.c": host_cue_bindings(), "shim.c": CAPTURE_SHIM, "tone.c": tone,
                           "fio.c": Y22_FIO_FAKE + BAND_FAKE, "op.c": OP_STUB},
                          ("-DR5_RECORD_INDICATOR=1",) if indicator else ())
        for n in ("host_pcm", "host_desc", "host_done_ptr", "host_end_ptr"):
            getattr(lib, n).restype = ctypes.c_size_t
        lib.capture_host_header.restype = ctypes.c_void_p
        lib.host_tick.restype = U
        check_abi(lib)
        _LIBS[key] = lib
    return _LIBS[key]


READ = ctypes.CFUNCTYPE(U, U)
COUNTER = ctypes.CFUNCTYPE(U, ctypes.POINTER(U), ctypes.POINTER(U))
PATH = ctypes.CFUNCTYPE(I, U, ctypes.c_char_p)
IO = ctypes.CFUNCTYPE(I, U, U, U, U, U, U)
QUERY = ctypes.CFUNCTYPE(I, U)
CALLBACK = ctypes.CFUNCTYPE(None, U)
TRIGGER = 50  # RATE (combo_observe.h COMBO_TRIGGER_CODE)
INFO_UP = int(re.search(r"^#define COMBO_INFO_RELEASE (\d+)$", _header("combo_observe.h"), re.M).group(1))
TFT_LO, TFT_HI = 0x2A000000, 3
EVF_LO, EVF_HI = 0x2B000000, 3
DV, MASKP = 0x00300000, 0x00310000   # fake dvctrl object; *(0x10684) -> MASKP, mask at +0x18
DATA = 0x60877100                     # the YCCA display buffer of the one-press tests


class CaptureCamera:
    """Canon RAM, ABeep / AudioCtrl, the shot counter, a FAT folder B:/DCIM/R5SHOTS and
    the FM / INDEV fakes, around one host build of combo_capture.c."""

    CTX, ABH, AC, ACH, IC, OUTC, AKT = 0x30000, 0x8001, 0x31000, 0x9002, 0x32000, 0x33000, 0x34000

    def __init__(self, lib, indicator: bool):
        self.lib, self.indicator = lib, indicator
        lib.host_reset()
        self.mem, self.files, self.calls, self.fds, self.reads = {}, {}, [], {}, []
        self.dir_exists, self.mounted, self.create_rc = True, 1, None
        self.short, self.close_rc, self.flush_rc = {}, 0, 0
        self.next_fd, self.writes, self.maps = 5, 0, 0
        self.open_rc, self.open_once, self.map_rc = {}, {}, {}
        self.on_write = self.on_flush = self.on_create = self.on_close = self.on_open = None
        self.states = {self.ABH: 1, self.ACH: 1}  # ABeep idle, AudioCtrl stock idle (state 1)
        self.lcd()
        a = AUDIO
        audio = {a["singleton_slot"]: self.CTX, self.CTX: 1, self.CTX + 4: self.ABH, self.CTX + 0x48: 3, self.CTX + 0x4C: 0,
                 a["audioctrl_slot"]: self.AC, self.AC + a["audioctrl_handle_offset"]: self.ACH,
                 a["audio_ic_slot"]: self.IC, self.IC + a["audio_ic_init_offset"]: 1, a["output_context_slot"]: self.OUTC,
                 a["icu_cache_base"] + a["icu_cache_init_offset"]: 1,
                 a["icu_cache_base"] + a["icu_cache_kind_offset"]: a["icu_kind_ak"], a["ak_param_slot"]: self.AKT}
        assert not set(audio) & set(self.mem)
        self.mem.update(audio)
        if indicator:
            lib.host_tone(1, 0, 0)
        self._cb = (READ(self._read), COUNTER(self._counter), PATH(self._path), IO(self._io), QUERY(self._query))
        lib.host_set(*self._cb)
        lib.host_counter(1, 0)

    # -- fakes -------------------------------------------------------------------
    def _read(self, a):
        self.reads.append(a)
        return self.mem.get(a, 0)

    def _query(self, h):
        return self.states.get(h, -1)

    @staticmethod
    def _counter(c, v):
        c[0], v[0] = 0x1000, 7
        return 1

    def _path(self, op, p):
        p = p.decode()
        if op == 0:  # open: remember the name for io(1)
            self.calls.append(("open", p))
            self.last_open = p
            return 0
        if op == 2:
            self.calls.append(("create", p))
            if self.on_create:
                self.on_create()
            assert p not in self.files, "CreateSC over an existing name"
            if self.create_rc is not None:
                return self.create_rc
            if not self.dir_exists:
                return -1
            fd, self.next_fd = self.next_fd, self.next_fd + 1
            self.fds[fd] = p
            self.files[p] = b""
            return fd
        if op == 3:
            self.calls.append(("remove", p))
            return 0 if self.files.pop(p, None) is not None else -1
        if op == 6:
            self.calls.append(("flush", p))
            if self.on_flush:
                self.on_flush()
            return self.flush_rc
        if op == 7:
            self.calls.append(("mount", p))
            return self.mounted
        raise AssertionError(op)

    def _io(self, op, a, b, c, d, e):
        if op == 1:
            p = self.last_open
            if self.on_open:
                self.on_open()
            if p in self.open_once:
                return self.open_once.pop(p)
            if p in self.open_rc:
                return self.open_rc[p]
            if p in self.files:
                fd, self.next_fd = self.next_fd, self.next_fd + 1
                self.fds[fd] = p
                self.calls.append(("open-hit", p))
                return fd
            return -1
        if op == 4:
            fd, unused, lo, hi, n = a, b, c, d, e
            if self.on_write:
                self.on_write(self.writes)
            self.calls.append(("write", fd, unused, lo, hi, n))
            assert fd in self.fds, "write on an fd we did not open"
            got = self.short.get(self.writes, n)
            self.writes += 1
            self.files[self.fds[fd]] += b"x" * got
            return got
        if op == 5:
            self.calls.append(("close", a))
            if self.on_close:
                self.on_close()
            assert a in self.fds and a >= 3, "close of an fd we do not own"
            del self.fds[a]
            return self.close_rc
        if op == 8:
            va, n, opc, mode = a, b, c, d
            self.calls.append(("map", va, n, opc, mode))
            if va < 0x40000000:
                assert va % 32 == 0 and (va + n) % 32 == 0, "MapCpuBuffer would assert"
            k, self.maps = self.maps, self.maps + 1
            return self.map_rc.get(k, 0)
        if op == 9:
            self.calls.append(("unmap", a, b, c, d))
            return 0
        raise AssertionError(op)

    # -- Canon state -----------------------------------------------------------
    def lcd(self, w=1024, h=682, cur=0, data=0x22000000, fmt=0x11060200, marv=0x23000000, rec=0, gui=0, magic=0x5652414D):
        """VMIX output 0 and its two MARV descriptors (buffer 1 - cur is the shown one)."""
        self.mem[REC_STATE_WORD] = rec
        self.mem[GUI_MODE_WORD] = gui
        for off, v in zip((0, 8, 12, 16, 20), (1, 1, 1, cur, 7)):
            self.mem[0x199C8 + off] = v
        base = 0x150B80
        self.mem.update({base + 16: w, base + 20: h, base + 40: marv, base + 44: marv + 0x100})
        for m, dptr in ((marv, data), (marv + 0x100, data + 0x400000)):  # A and B buffers differ
            self.mem.update({m: magic, m + 4: dptr, m + 24: fmt, m + 28: w, m + 32: h})

    def display(self, out=0, kind=1, mask=0x11):
        """The display driver's Active picture-layer plane (picture_layer.h) for output `out`."""
        m = self.mem
        m[0x106A4], m[0x10684], m[MASKP + 0x18] = DV, MASKP, mask
        lo, hi = (TFT_LO, TFT_HI) if out == 0 else (EVF_LO, EVF_HI)
        w, h = (1024, 682) if out == 0 else (1600, 1200)
        m[DV + 0x128], m[DV + 0x12C] = TFT_LO + 0x200000, TFT_HI
        m[DV + 0x130], m[DV + 0x134] = TFT_LO, TFT_HI
        m[DV + 0x138], m[DV + 0x13C] = EVF_LO + 0x400000, EVF_HI
        m[DV + 0x140], m[DV + 0x144] = EVF_LO, EVF_HI
        base_a, base_b = (0xAB0, 0xB48) if out == 0 else (0xBE0, 0xC78)
        for base, words in ((base_a, info_words(0, out, lo + 0x200000, hi, w, h)), (base_b, info_words(kind, out, lo, hi, w, h))):
            for off in range(0, 0x98, 4):
                m[DV + base + off] = words.get(off, 0)

    def playback(self, handle=PHOTO, size=14, type_bits=4, rc=0):
        """Playback (GUI mode 0x2002) showing `handle`, whose JPEG sub FM reports (type, size, rc)."""
        self.mem[GUI_MODE_WORD] = PLAYBACK
        self.mem[VIEW_WORD] = handle
        self.fm(type_bits, size, rc)

    def view(self, handle):
        """The photo playback shows (0 = none)."""
        self.mem[VIEW_WORD] = handle

    def good(self, *handles):
        """The only group handles whose JPEG sub FM finds (none given: every handle)."""
        arr = (U * 4).in_dll(self.lib, "op_fm_good")
        for i in range(4):
            arr[i] = handles[i] if i < len(handles) else 0

    def xpan(self, v):
        """The playback cache's AspectInfo pre-check of the shown photo (OP_XPAN_*)."""
        U.in_dll(self.lib, "op_xpan").value = v

    def fm_seen(self):
        return list((U * 8).in_dll(self.lib, "op_fm_seen"))[:min(self.g("op_fm_calls"), 8)]

    def fm(self, type_bits=4, size=14, rc=0):
        (U * 8).in_dll(self.lib, "op_fm_words")[0] = size << 16 | type_bits
        I.in_dll(self.lib, "op_fm_rc").value = rc

    def abeep_busy(self, busy: bool):
        """The stock beep (our click) still playing: rec_tone_preflight answers REC_TONE_PF_BUSY."""
        if self.indicator:
            U.in_dll(self.lib, "host_preflight_reason").value = 2 if busy else 0
        else:
            self.states[self.ABH] = 2 if busy else 1

    # -- the button ----------------------------------------------------------------
    def press(self, tick=None, code=TRIGGER):
        if tick is not None:
            self.lib.host_set_tick(tick)
        self.lib.combo_capture_gui((U * 4)(0, code, 0, 1))

    def release(self):
        self.lib.combo_capture_gui((U * 4)(0, TRIGGER + 1, 0, 1))

    def key_up(self, code=INFO_UP):
        """A class-0 release the H2 hook passes to combo_capture_gui (only INFO on camera)."""
        self.lib.combo_capture_gui((U * 4)(0, code, 0, 1))

    def tap(self, tick):
        self.press(tick)
        self.release()

    def step(self, n=1):
        for _ in range(n):
            self.lib.combo_observe_step()

    # -- state ---------------------------------------------------------------------
    def _s(self, cls, name):
        return cls.in_dll(self.lib, name)

    def state(self):
        return self._s(CaptureState, "capture_state")

    def result(self, seq):
        return as_dict(self.state().results[(seq - 1) % 8])

    def feedback(self):
        return as_dict(self._s(CaptureFeedback, "capture_feedback"))

    def op(self):
        return as_dict(self._s(OnePressState, "one_press_state"))

    def defer(self):
        return as_dict(self._s(OnePressDefer, "one_press_defer"))

    def pending(self):
        return as_dict(self._s(InjectPending, "inject_pending"))

    def guard(self):
        return self._s(InjectGuard, "inject_guard")

    def built(self):
        b = as_dict(self._s(InjectBuild, "inject_build"))
        b["cached_reads"] = self.g("host_cached_reads")
        return b

    def g(self, name):
        return U.in_dll(self.lib, name).value

    def beeps(self):
        return self.g("host_beeps")

    def beep_desc(self):
        return ctypes.c_size_t.in_dll(self.lib, "host_beep_desc").value

    def op_log(self):
        return ctypes.string_at(ctypes.addressof(ctypes.c_char.in_dll(self.lib, "op_log"))).decode()

    def header(self):
        return ctypes.string_at(self.lib.capture_host_header(), 512)

    def done(self, result):
        CALLBACK(self.lib.host_done_ptr())(result)

    def end(self, arg):
        CALLBACK(self.lib.host_end_ptr())(arg)


# --- the converter world: hw_jpeg_inject.c + y22_stream.c over a fake FAT -----------

INJECT_SHIM = r'''
#include "hw_jpeg_inject.h"
#include "combo_observe.h"
#include "observer.h"
#include "one_press.h"
#include "y22_stream.h"
#include <stdlib.h>
#include <string.h>
typedef uint32_t (*py_read_fn)(uint32_t);
typedef void (*py_hook_fn)(uint32_t kind);
static py_read_fn py_read; static py_hook_fn py_hook;
volatile uint32_t combo_worker_tick;
struct combo_observe_state combo_observe_state; /* one_press.c reads the drain's event-sequence words */
uint32_t host_us = 1000u, host_us_step = 0u, host_cpu = 0x80000000u;
void host_set(py_read_fn r, py_hook_fn h) { py_read = r; py_hook = h; }
static uint32_t h_read(uint32_t a) { return py_read(a); }
static uint32_t h_timer(void) { uint32_t t = host_us; host_us += host_us_step; return t; }
static uint32_t h_cpu(void) { return host_cpu; }
/* A fake FAT: a few named files and fds with a position; one log letter per call. */
#define FILES 6
struct hf { char name[40]; uint8_t *data; uint32_t n, cap; int used; };
struct hf files[FILES];
struct { int file; uint32_t pos; int used; } fds[16];
char host_log[4096]; uint32_t host_log_n;
uint32_t host_writes[512], host_nwrites, host_bad_phys, host_create_over_existing;
int32_t host_create_rc = 0, host_close_rc_y22 = 0, host_flush_rc = 0, host_map_fail_at = -1, host_short_at = -1, host_short_rc = 0;
int32_t host_read_fail_at = -1, host_read_fail_rc = -7, host_reads;
extern uint8_t *host_band_mem, *host_band_ptr;
uint8_t *host_ibus; uint32_t host_ibus_n, host_ibus_lo, host_ibus_hi;
uintptr_t host_uc_lo[16], host_uc_hi[16]; uint32_t host_uc_n, host_cached_reads;
static void *f_uncached(void *b, uint32_t n) { if (host_uc_n < 16) { host_uc_lo[host_uc_n] = (uintptr_t)b; host_uc_hi[host_uc_n] = (uintptr_t)b + n; host_uc_n++; } return b; }
static int uc_ok(const void *b, uint32_t n) { for (uint32_t i = 0; i < host_uc_n; i++) if ((uintptr_t)b >= host_uc_lo[i] && (uintptr_t)b + n <= host_uc_hi[i]) return 1; return 0; }
static void lg(char c) { if (host_log_n < sizeof host_log - 1) host_log[host_log_n++] = c; host_log[host_log_n] = 0; if (py_hook) py_hook((uint32_t)c); }
static int find(const char *p) { for (int i = 0; i < FILES; i++) if (files[i].used && !strcmp(files[i].name, p)) return i; return -1; }
static int new_file(const char *p)
{
    for (int i = 0; i < FILES; i++)
        if (!files[i].used) {
            memset(&files[i], 0, sizeof files[i]);
            for (int k = 0; k < 39 && p[k]; k++) files[i].name[k] = p[k];
            files[i].used = 1;
            return i;
        }
    return -1;
}
static int new_fd(int f) { for (int i = 3; i < 16; i++) if (!fds[i].used) { fds[i].used = 1; fds[i].file = f; fds[i].pos = 0; return i; } return -1; }
static void append(struct hf *f, const uint8_t *b, uint32_t n)
{ if (f->n + n > f->cap) { f->cap = f->n + n + (1u << 20); f->data = realloc(f->data, f->cap); } memcpy(f->data + f->n, b, n); f->n += n; }
void host_put(const char *p, const uint8_t *b, uint32_t n) { int i = find(p); if (i < 0) i = new_file(p); files[i].n = 0; append(&files[i], b, n); }
uint32_t host_size_of(const char *p) { int i = find(p); return i < 0 ? 0xffffffffu : files[i].n; }
const uint8_t *host_data_of(const char *p) { int i = find(p); return i < 0 ? 0 : files[i].data; }
uint32_t host_open_fds(void) { uint32_t n = 0; for (int i = 3; i < 16; i++) n += fds[i].used; return n; }
static int32_t f_size(const char *p, uint32_t out[2]) { lg('S'); int i = find(p); if (i < 0) { out[0] = out[1] = 0; return -1; } out[0] = files[i].n; out[1] = 0; return 0; }
static int32_t f_open(const char *p, uint32_t m) { lg('O'); (void)m; int i = find(p); return i < 0 ? -1 : new_fd(i); }
static int32_t f_read(int32_t fd, void *b, uint32_t n)
{
    lg('r');
    if (!uc_ok(b, n)) { host_cached_reads++; return -77; } /* Canon: "Please Designate Uncacheable Addr", assert */
    if (host_read_fail_at >= 0 && host_reads++ == host_read_fail_at) return host_read_fail_rc;
    struct hf *f = &files[fds[fd].file]; uint32_t k = f->n - fds[fd].pos < n ? f->n - fds[fd].pos : n;
    memcpy(b, f->data + fds[fd].pos, k); fds[fd].pos += k; return (int32_t)k;
}
static int32_t f_read_ibus(int32_t fd, uint32_t u, uint32_t lo, uint32_t hi, uint32_t n)
{
    lg('R'); (void)u; host_ibus_lo = lo; host_ibus_hi = hi;
    struct hf *f = &files[fds[fd].file]; uint32_t k = f->n < n ? f->n : n;
    free(host_ibus); host_ibus = malloc(k ? k : 1); memcpy(host_ibus, f->data, k); host_ibus_n = k; return (int32_t)k;
}
static int32_t f_close(int32_t fd)
{
    lg('C'); int f = fds[fd].file; fds[fd].used = 0;
    return !strcmp(files[f].name, "B:/DCIM/R5SHOTS/HWJPG.Y22") ? host_close_rc_y22 : 0;
}
static int32_t f_create(const char *p)
{
    lg('K'); if (host_create_rc) return host_create_rc;
    if (find(p) >= 0) { host_create_over_existing++; return -99; }
    return new_fd(new_file(p));
}
static int32_t f_remove(const char *p) { lg('X'); int i = find(p); if (i < 0) return -1; free(files[i].data); files[i].data = 0; files[i].n = files[i].cap = 0; files[i].used = 0; return 0; }
static int32_t f_flush(const char *p) { lg('F'); (void)p; return host_flush_rc; }
static int maps;
static int32_t f_map(uint32_t out[2], uint32_t va, uint32_t n, uint32_t op, uint32_t mode)
{ lg('M'); (void)n; out[0] = va ^ 0x80000000u; out[1] = 1u; if (op || mode != 1u) return -9; return maps++ == host_map_fail_at ? 7 : 0; }
static int32_t f_unmap(uint32_t va, uint32_t n, uint32_t op, uint32_t mode) { lg('U'); (void)va; (void)n; (void)op; (void)mode; return 0; }
static int32_t f_write64(int32_t fd, uint32_t u, uint32_t lo, uint32_t hi, uint32_t n)
{
    lg('W'); (void)u;
    const uint8_t *from = inject_shared_strip; /* or the fake's band (BAND_FAKE) */
    uint32_t va = lo ^ 0x80000000u;
    if (host_band_ptr && va == (uint32_t)(uintptr_t)host_band_ptr) from = host_band_ptr; /* the band start, rounded to 4 KiB */
    else if (va != (uint32_t)(uintptr_t)inject_shared_strip) host_bad_phys++;
    if (hi != 1u) host_bad_phys++;
    if (host_nwrites < 512) host_writes[host_nwrites] = n;
    uint32_t k = (int32_t)host_nwrites++ == host_short_at ? (uint32_t)host_short_rc : n;
    append(&files[fds[fd].file], from, k);
    return (int32_t)k;
}
static void f_barrier(void) { lg('B'); }
void host_band_reset(void);
const struct inject_api inject_api = {h_read, h_timer, h_cpu, f_size, f_open, f_read_ibus, f_close, f_barrier,
                                      f_read, f_create, f_remove, f_flush, f_write64, f_map, f_unmap, f_uncached};
static void host_debug(unsigned c, unsigned l, const char *fmt, ...) { (void)c; (void)l; (void)fmt; }
const struct observer_api observer_api = {0, 0, host_debug, 0, 0};
void host_reset(void)
{
    memset(&inject_ring, 0, sizeof inject_ring); memset(&inject_pending, 0, sizeof inject_pending); memset(&inject_guard, 0, sizeof inject_guard);
    for (int i = 0; i < FILES; i++) { free(files[i].data); files[i].data = 0; files[i].used = 0; }
    memset(fds, 0, sizeof fds); host_log_n = 0; host_log[0] = 0; host_nwrites = host_bad_phys = host_create_over_existing = 0;
    host_create_rc = host_close_rc_y22 = host_flush_rc = 0; host_map_fail_at = host_short_at = -1; host_short_rc = 0; maps = 0;
    host_read_fail_at = -1; host_read_fail_rc = -7; host_reads = 0; host_ibus_n = 0;
    host_us = 1000u; host_us_step = 0u; combo_worker_tick = 1000u; host_uc_n = 0; host_cached_reads = 0;
    memset(&inject_build, 0, sizeof inject_build); memset(&one_press_state, 0, sizeof one_press_state); host_band_reset();
}
void host_call(uint32_t hook, uint32_t r0, uint32_t r2)
{
    uint32_t saved[INJECT_SAVED_WORDS] = {r0, 0x00358628u, r2, 0x01ad3950u, 4, 5, 12, 0xe0ccb705u};
    inject_record(hook, saved, 0x00358570u);
}
void host_tick(uint32_t t) { combo_worker_tick = t; }
/* one_press_api: only the dry-run record is exercised here. */
static uint32_t zr(uint32_t a) { (void)a; return 0; }
static int32_t zm(uint32_t h, uint32_t *i) { (void)h; (void)i; return 1; }
static void zv(void) {}
static void zs(uint32_t v) { (void)v; }
static uint32_t zg(void) { return 0; }
static void zst(uint32_t h, void *b, op_cb_fn s, op_cb_fn d, uint32_t r) { (void)h; (void)b; (void)s; (void)d; (void)r; }
static void zen(uint32_t s, void *b, op_cb_fn e) { (void)s; (void)b; (void)e; }
static void zp(uint32_t c, uint32_t k, uint32_t a, uint32_t b) { (void)c; (void)k; (void)a; (void)b; }
const struct one_press_api one_press_api = {zr, h_timer, zm, zv, zs, zs, zg, zg, zst, zen, zg, zp};
void host_op_state(uint32_t st) { one_press_state.state = st; one_press_state.dry_seen = one_press_state.dry_ok = one_press_state.dry_wh = 0; }
void host_composite(uint8_t *osd, const uint8_t *pic, uint32_t w) { y22_composite_row(osd, pic, w); }
#define HOST_SIZE_0 0u
#define HOST_SIZE_1 0u
''' + SIZEOF_C

STREAM_SHIM = r'''
#include "y22_stream.h"
static struct y22_stream s; static uint8_t row[4801];
uint32_t host_init(uint32_t w, uint32_t h, uint32_t row_bytes, y22_fetch_fn f) { return y22_stream_init(&s, w, h, row, row_bytes, f, 0); }
uint32_t host_fill(uint8_t *out) { return y22_stream_fill(&s, out); }
int32_t host_error(void) { return s.error; }
'''


def inject_lib():
    if "inject" not in _LIBS:
        lib = compile_lib("inject", [XPAN_SRC / n for n in ("hw_jpeg_inject.c", "y22_stream.c", "one_press.c")],
                          {"shim.c": INJECT_SHIM + BAND_FAKE})
        lib.host_data_of.restype = ctypes.c_void_p
        lib.host_size_of.restype = U
        check_abi(lib)
        _LIBS["inject"] = lib
    return _LIBS["inject"]


def stream_lib():
    if "stream" not in _LIBS:
        _LIBS["stream"] = compile_lib("stream", [XPAN_SRC / "y22_stream.c"], {"s.c": STREAM_SHIM})
    return _LIBS["stream"]
