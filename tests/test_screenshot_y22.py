"""Host tests for the --screenshot HWJPG.Y22 build (y22_stream.c, hw_jpeg_inject.c).

RATE builds HWJPG.Y22 (2400x1600 signed-chroma UYVY) from the capture's
SS_nnnnn.YCA, composited over its SS_nnnnn.PIC picture layer, either through one
borrowed RscMgr band or, on any band fallback, in 38,400-byte strips. Each output is
compared byte for byte with the pure-Python reference converter in
screenshot_host.py. The fake FAT refuses any FIO_ReadFile buffer that
inject_api.uncached did not return, as Canon's storage layer asserts on a cacheable
one. Synthetic inputs only; no ROM, camera, card or ARM toolchain.
"""
import ctypes
import itertools
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshot_host as H  # noqa: E402

U, I = H.U, H.I
INJ = H.INJ
ST = {k[len("CONV_"):]: v for k, v in INJ.items() if k.startswith("CONV_")}
PS = {k[len("PIC_"):]: v for k, v in INJ.items() if k.startswith("PIC_")}
BS = {k[len("BAND_"):]: v for k, v in INJ.items() if k in ("BAND_" + s for s in ("NONE", "USED", "EMPTY", "TIMEOUT", "BAD", "LATE", "SEM"))}
H3, H8 = INJ["HOOK_ENCODE"], INJ["HOOK_DEVELOP_END"]
SS1 = H.SS % 1
PIC1 = SS1[:-3] + "PIC"
DESC = 0x00358588
LO, HI = 0x1C2B8000, 3
GOOD = [LO, HI, 2400, 0, 0, 2400, 1600]  # desc[0]: IBUS lo, hi, BaseW, Xoff, Yoff, W, H of an S2 3:2 encode
READ = ctypes.CFUNCTYPE(U, U)
HOOK = ctypes.CFUNCTYPE(None, U)
FETCH = ctypes.CFUNCTYPE(I, ctypes.c_void_p, U, ctypes.c_void_p, U)
LCD, EVF = (1024, 682), (1600, 1200)
BAND_ROWS = {LCD: 70, EVF: 46}  # source rows of each file per band read (hw_jpeg_inject.h INJECT_BAND_ROWS_*)


class ReferenceConverter(unittest.TestCase):
    """The host reference itself, against an independent reading of the mapping."""

    def test_mapping_is_the_integer_form(self):
        self.assertTrue(all(H.source_row(dy) == (dy * 341) // 800 for dy in range(1600)))
        self.assertTrue(all(H.source_col(x) == (x * 32) // 75 for x in range(2400)))
        self.assertEqual((H.source_row(1599), H.source_col(2399)), (681, 1023))
        self.assertTrue(all(H.source_row(dy, 1200) == dy * 3 // 4 for dy in range(1600)))
        self.assertEqual([H.source_col(x, *EVF) for x in (133, 134, 2265, 2266)], [None, 0, 1599, None])
        self.assertTrue(all(H.source_col(x, *EVF) == (x - 134) * 1600 // 2132 for x in range(134, 2266)))

    def test_every_byte_matches_an_independent_reading(self):
        src = H.synthetic_yca(*LCD)
        out = H.convert(src)
        self.assertEqual(len(out), H.Y22_BYTES)
        px = src[512:]
        for dy in (0, 1, 97, 800, 1598, 1599):
            sy = (dy * 682) // 1600
            row = px[sy * 3072:(sy + 1) * 3072]
            want = bytearray()
            for j in range(1200):
                sx0, sx1 = (2 * j * 1024) // 2400, ((2 * j + 1) * 1024) // 2400
                p = (sx0 >> 1) * 6
                y0 = row[p + (1 if sx0 % 2 == 0 else 3)]
                y1 = row[(sx1 >> 1) * 6 + (1 if sx1 % 2 == 0 else 3)]
                want += bytes((row[p] ^ 0x80, y0, row[p + 2] ^ 0x80, y1))
            self.assertEqual(out[dy * 4800:(dy + 1) * 4800], bytes(want), dy)

    def test_neutral_chroma_becomes_signed_zero_alpha_is_dropped_and_pillars_are_black(self):
        out = H.convert(H.r5ss(1024, 682, bytes((0x80, 0x29, 0x80, 0x83, 0xFF, 0xFF)) * (512 * 682)))
        self.assertEqual(set(out[0::4]) | set(out[2::4]), {0})
        self.assertEqual(set(out[1::4]) | set(out[3::4]), {0x29, 0x83})
        out = H.convert(H.r5ss(1600, 1200, bytes((0x90, 0x11, 0x70, 0x22, 0, 0)) * (800 * 1200)))
        for dy in (0, 1599):
            row = out[dy * 4800:(dy + 1) * 4800]
            self.assertEqual((row[:268], row[-268:]), (bytes(268), bytes(268)), "134-pixel pillars (67 pairs)")
            # x = 134 and 135 both map to source pixel 0 (1600 / 2132 < 1): Y0 twice.
            self.assertEqual(row[268:272], bytes((0x10, 0x11, 0xF0, 0x11)))

    def test_composite_keeps_an_opaque_osd_and_shows_the_picture_under_a_clear_one(self):
        pic = (512 << 30 | 300 << 20 | 400 << 10 | 100).to_bytes(5, "little")  # Cb 100, Y0 400, Cr 300, Y1 512 (10-bit)
        opaque = bytearray((10, 20, 30, 40, 255, 255))
        H.composite_row(opaque, pic, 2)
        self.assertEqual(bytes(opaque), bytes((10, 20, 30, 40, 255, 255)))
        clear = bytearray((128, 0, 128, 0, 0, 0))
        H.composite_row(clear, pic, 2)
        self.assertEqual(bytes(clear), bytes((100 >> 2, 400 >> 2, 300 >> 2, 512 >> 2, 0, 0)))

    def test_pic_status_reasons(self):
        yca, pic = H.synthetic_yca(*LCD), H.synthetic_pic(*LCD)
        self.assertEqual(H.pic_status(yca, pic), "OK")
        self.assertEqual(H.pic_status(yca, None), "MISSING")
        self.assertEqual(H.pic_status(yca, pic[:-512]), "SIZE")
        for over in ({0x38: 5}, {0x34: 0}, {0x2C: 1000}, {0x24: 8}, {0x00: 2}):
            self.assertEqual(H.pic_status(yca, H.synthetic_pic(*LCD, over=tuple(over.items()))), "HEADER", over)
        self.assertEqual(H.pic_status(yca, H.synthetic_pic(*LCD, number=2)), "HEADER", "another capture's .PIC")


class Stream(unittest.TestCase):
    """y22_stream.c alone, fed row by row as hw_jpeg_inject.c's reader does."""

    @classmethod
    def setUpClass(cls):
        cls.lib = H.stream_lib()

    def run_stream(self, yca, fail_at=None):
        w, h = H.r5ss_geometry(yca)
        body, pitch, asked = yca[512:], 3 * w, []

        def fetch(ctx, sy, row, n):
            asked.append(sy)
            if sy == fail_at:
                return -7
            ctypes.memmove(row, body[sy * pitch:(sy + 1) * pitch], n)
            return 0
        self._f = FETCH(fetch)
        self.assertEqual(self.lib.host_init(w, h, 4801, self._f), H.Y22_BYTES)
        out, strip, sizes = bytearray(), ctypes.create_string_buffer(H.STRIP), []
        while n := self.lib.host_fill(strip):
            sizes.append(n)
            out += strip.raw[:n]
        if fail_at is None:
            self.assertEqual(asked, list(range(h)), "every source row once, in order: a sequential file read")
            self.assertEqual(sizes, [H.STRIP] * 200)
        return bytes(out), sizes

    def test_lcd_and_evf_equal_the_reference(self):
        for geometry in (LCD, EVF):
            with self.subTest(geometry=geometry):
                yca = H.synthetic_yca(*geometry)
                self.assertEqual(self.run_stream(yca)[0], H.convert(yca))

    def test_fetch_failure_stops_the_stream(self):
        _, sizes = self.run_stream(H.synthetic_yca(*LCD), fail_at=300)
        self.assertEqual(self.lib.host_error(), -7)
        self.assertLess(len(sizes), 200)
        self.assertEqual(self.lib.host_fill(ctypes.create_string_buffer(H.STRIP)), 0, "stays stopped")

    def test_refusals(self):
        f = FETCH(lambda *a: 0)
        for w, h, rb in ((1024, 680, 4801), (1600, 1080, 4801), (720, 480, 4801), (1024, 682, 3071), (1600, 1200, 4799)):
            with self.subTest(w=w, h=h, row_bytes=rb):
                self.assertEqual(self.lib.host_init(w, h, rb, f), 0)
        self.assertEqual(self.lib.host_init(1024, 682, 4801, None), 0)


class InjectBase(unittest.TestCase):
    """hw_jpeg_inject.c (inject_y22_build at RATE, H3 / H8) over the fake FAT."""

    @classmethod
    def setUpClass(cls):
        cls.lib = H.inject_lib()

    def setUp(self):
        self.lib.host_reset()
        self.mem = {DESC + 4 * i: w for i, w in enumerate(GOOD)}
        self.on_call = None
        self._r = READ(lambda a: self.mem.get(a, 0xBAD0BAD0))
        self._h = HOOK(lambda c: self.on_call(chr(c)) if self.on_call else None)
        self.lib.host_set(self._r, self._h)

    def put(self, name, data):
        self.lib.host_put(name.encode(), data, len(data))

    def file(self, name):
        n = self.lib.host_size_of(name.encode())
        if n == 0xFFFFFFFF:
            return None
        return ctypes.string_at(self.lib.host_data_of(name.encode()), n) if n else b""

    def log(self):
        return ctypes.string_at(ctypes.addressof(ctypes.c_char.in_dll(self.lib, "host_log"))).decode()

    def var(self, name, value=None, kind=U):
        v = kind.in_dll(self.lib, name)
        if value is not None:
            v.value = value
        return v.value

    def writes(self):
        return list((U * 512).in_dll(self.lib, "host_writes"))[:self.var("host_nwrites")]

    def built(self):
        return H.as_dict(H.InjectBuild.in_dll(self.lib, "inject_build"))

    def pending(self):
        return H.as_dict(H.InjectPending.in_dll(self.lib, "inject_pending"))

    def entry(self, seq):
        return H.as_dict(H.InjectRing.in_dll(self.lib, "inject_ring").entries[(seq - 1) % 16])

    def uncached_spans(self):
        n = self.var("host_uc_n")
        hi = list((ctypes.c_size_t * 16).in_dll(self.lib, "host_uc_hi"))[:n]
        lo = list((ctypes.c_size_t * 16).in_dll(self.lib, "host_uc_lo"))[:n]
        return sorted(h - l for h, l in zip(hi, lo))

    def save(self, yca, pic=None, name=SS1):
        """The RATE side: the capture's files, then inject_y22_build of the YCA."""
        self.put(name, yca)
        if pic is not None:
            self.put(name[:-3] + "PIC", pic)
        rc = self.lib.inject_y22_build(name.encode())
        self.assertEqual(self.var("host_cached_reads"), 0, "every FIO_ReadFile buffer is an uncacheable alias")
        return rc

    def assert_y22(self, yca, pic=None):
        self.assertEqual(self.file(H.Y22_PATH), H.reference_y22(yca, pic), "byte-equal to the reference converter")
        self.assertEqual(self.lib.host_open_fds(), 0, "source, picture and Y22 all closed")

    def resize(self, tick=None, kind=0):
        """Canon's S2 Resize: our DevelopEnd(1) (H8), then the encode of desc[0] (H3)."""
        if tick is not None:
            self.lib.host_tick(tick)
        self.lib.host_call(H8, 1, 0)
        self.lib.host_call(H3, DESC, kind)


class StripBuild(InjectBase):
    def test_osd_only_lcd_and_evf_are_byte_equal_in_200_strips(self):
        for geometry in (LCD, EVF):
            with self.subTest(geometry=geometry):
                self.setUp()
                yca = H.synthetic_yca(*geometry)
                self.assertEqual(self.save(yca), 0)
                self.assert_y22(yca)
                log = self.log()
                self.assertEqual(log[:7], "SOrSXKr", "source size, open, header, .PIC size (none), old Y22 removed, created, row")
                self.assertEqual(log.count("MWU"), 200)
                self.assertTrue(log.endswith("CCF"), log[-6:])
                self.assertEqual(self.writes(), [H.STRIP] * 200, "whole 75-sector strips")
                self.assertEqual(self.var("host_bad_phys"), 0, "every write is the mapped shared strip")
                b = self.built()
                self.assertEqual((b["builds"], b["stage"], b["rc"], b["source_bytes"], b["y22_written"], b["source_wh"]),
                                 (1, ST["DONE"], 0, len(yca), H.Y22_BYTES, geometry[0] << 16 | geometry[1]))
                self.assertEqual((b["pic_status"], b["band"]), (PS["MISSING"], BS["EMPTY"]))
                self.assertEqual(self.file(SS1), yca, "the source is only read")

    def test_picture_layer_lcd_and_evf_are_byte_equal_in_200_strips(self):
        for geometry in (LCD, EVF):
            with self.subTest(geometry=geometry):
                self.setUp()
                yca, pic = H.synthetic_yca(*geometry), H.synthetic_pic(*geometry)
                self.assertEqual(self.save(yca, pic), 0)
                self.assertEqual((self.built()["pic_status"], self.built()["stage"]), (PS["OK"], ST["DONE"]))
                self.assert_y22(yca, pic)
                self.assertNotEqual(self.file(H.Y22_PATH), H.convert(yca), "the picture layer is really under the OSD")
                self.assertEqual(self.writes(), [H.STRIP] * 200)

    def test_read_buffers_are_the_uncacheable_aliases(self):
        self.assertEqual(self.save(H.synthetic_yca(*LCD), H.synthetic_pic(*LCD)), 0)
        self.assertEqual(self.uncached_spans(), [4000, 4832], "inject_pic_row and inject_dma_row aliases only")

    def test_stage_times(self):
        """Each timed span reads the timer twice: with a 1 us step the sums count the calls."""
        self.var("host_us_step", 1)
        self.save(H.synthetic_yca(*LCD), H.synthetic_pic(*LCD))
        b = self.built()
        self.assertEqual((b["us_yca"], b["us_pic"], b["us_comp"], b["us_write"], b["us_map"]), (682, 682, 682, 200, 400))
        self.assertGreaterEqual(b["us_fill"], b["us_yca"] + b["us_pic"] + b["us_comp"])

    def test_fallbacks_to_the_osd_alone(self):
        yca, pic = H.synthetic_yca(*LCD), H.synthetic_pic(*LCD)
        bad = {
            "MISSING": None,
            "SIZE": pic[:-512],
            "HEADER number": H.synthetic_pic(*LCD, number=2),
            "HEADER range 5": H.synthetic_pic(*LCD, over=((0x38, 5),)),
            "HEADER 709": H.synthetic_pic(*LCD, over=((0x34, 0),)),
            "HEADER window": H.synthetic_pic(*LCD, over=((0x2C, 1000),)),
            "HEADER offset": H.synthetic_pic(*LCD, over=((0x24, 8),)),
            "HEADER kind 2": H.synthetic_pic(*LCD, over=((0x00, 2),)),
            "HEADER magic": b"R5PX" + pic[4:],
        }
        for label, p in bad.items():
            with self.subTest(label):
                self.setUp()
                self.assertEqual(self.save(yca, p), 0)
                status = label.split()[0]
                self.assertEqual(self.built()["pic_status"], PS[status])
                self.assertEqual(H.pic_status(yca, p), status, "the reference agrees")
                self.assertEqual(self.file(H.Y22_PATH), H.convert(yca), "the OSD alone")
                self.assertEqual(self.lib.host_open_fds(), 0)

    def test_picture_read_failure_fails_closed(self):
        self.var("host_read_fail_at", 1 + 1 + 100, I)  # YCA header, PIC header, then inside the rows
        self.assertEqual(self.save(H.synthetic_yca(*LCD), H.synthetic_pic(*LCD)), 1)
        self.assertEqual(self.built()["stage"], ST["STRIPS"])
        self.assertIsNone(self.file(H.Y22_PATH), "the failed Y22 is removed")
        self.assertEqual(self.lib.host_open_fds(), 0)

    def test_conversion_failures_remove_the_y22(self):
        good = H.synthetic_yca(*LCD)
        for name, src in (("magic", b"X" + good[1:]), ("version", good[:4] + b"\x02" + good[5:]),
                          ("geometry", good[:8] + (1000).to_bytes(4, "little") + good[12:])):
            with self.subTest(header=name):
                self.setUp()
                self.assertEqual(self.save(src), 1)
                self.assertEqual((self.built()["stage"], self.built()["rc"]), (ST["HEADER"], -2))
                self.assertIsNone(self.file(H.Y22_PATH))
                self.assertEqual(self.lib.host_open_fds(), 0)
        for name, setup, stage in (
                ("create", lambda: self.var("host_create_rc", -1, I), ST["CREATE"]),
                ("short strip", lambda: (self.var("host_short_at", 57, I), self.var("host_short_rc", 100, I)), ST["STRIPS"]),
                ("map", lambda: self.var("host_map_fail_at", 3, I), ST["STRIPS"]),
                ("row read", lambda: self.var("host_read_fail_at", 300, I), ST["STRIPS"]),
                ("close", lambda: self.var("host_close_rc_y22", -1, I), ST["CLOSE"]),
                ("flush", lambda: self.var("host_flush_rc", -5, I), ST["CLOSE"])):
            with self.subTest(failure=name):
                self.setUp()
                setup()
                self.assertEqual(self.save(good), 1)
                b = self.built()
                self.assertEqual((b["stage"], b["failures"]), (stage, 1))
                self.assertNotEqual(b["rc"], 0)
                self.assertIsNone(self.file(H.Y22_PATH), "partial Y22 removed")
                self.assertEqual(self.file(SS1), good)
                self.assertEqual(self.lib.host_open_fds(), 0)
        self.setUp()
        self.assertEqual(self.lib.inject_y22_build(SS1.encode()), 1)  # no source file
        self.assertEqual((self.built()["stage"], self.built()["rc"], self.log()), (ST["SOURCE_SIZE"], -1, "S"))
        self.assertEqual(self.save(good[:-512]), 1)
        self.assertEqual(self.built()["stage"], ST["SOURCE_SIZE"])

    def test_existing_y22_is_replaced(self):
        self.put(H.Y22_PATH, b"old")
        yca = H.synthetic_yca(*LCD)
        self.assertEqual(self.save(yca), 0)
        self.assertEqual(self.var("host_create_over_existing"), 0)
        self.assert_y22(yca)

    def test_h3_reads_the_built_frame_into_the_encoder_once(self):
        yca, pic = H.synthetic_yca(*LCD), H.synthetic_pic(*LCD)
        self.assertEqual(self.save(yca, pic), 0)
        self.lib.inject_y22_ready(1000, SS1.encode())
        n = len(self.log())
        self.resize(1200)
        self.assertEqual(self.entry(1)["outcome"], INJ["OK"])
        self.assertEqual(self.log()[n:], "SORCB", "H3: only size, open, ReadFile_IBUS, close (no FIO_ReadFile)")
        ibus = ctypes.string_at(self.var("host_ibus", kind=ctypes.c_void_p), self.var("host_ibus_n"))
        self.assertEqual(ibus, H.reference_y22(yca, pic), "desc[0] gets the composited frame")
        self.assertEqual((self.var("host_ibus_lo"), self.var("host_ibus_hi")), (LO, HI))
        self.assertEqual((self.pending()["pending"], self.pending()["consumed"]), (0, 1))
        self.resize()
        self.assertEqual(self.entry(2)["outcome"], INJ["REFUSE_NOT_PENDING"], "one screenshot, one encode")

    def test_composite_row_matches_the_reference_for_every_alpha(self):
        w, rnd = 512, random.Random(7)
        for a in range(256):
            osd = bytearray()
            for _ in range(w // 2):
                osd += bytes((rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), a,
                              rnd.randrange(256)))
            pic = rnd.randbytes(w * 5 // 2)
            buf = (ctypes.c_uint8 * len(osd)).from_buffer_copy(osd)
            self.lib.host_composite(buf, pic, w)
            H.composite_row(osd, pic, w)
            self.assertEqual(bytes(buf), bytes(osd), "alpha %d" % a)


class Band(InjectBase):
    """The Y22 build through one RscMgr type-4 suite (hw_jpeg_inject.h INJECT_BAND_*)
    and every way of falling back to the strip path (BAND_FAKE in screenshot_host.py)."""

    def band(self, mode, size=None):
        self.var("host_band_mode", mode)
        if size is not None:
            self.var("host_band_size", size)

    def test_band_with_picture_layer_is_byte_equal_in_ten_writes(self):
        """Also from a 16-aligned chunk (mode 3), laid out from the next 4 KiB boundary."""
        for geometry, mode in itertools.product((LCD, EVF), (1, 3)):
            with self.subTest(geometry=geometry, mode=mode):
                self.setUp()
                yca, pic = H.synthetic_yca(*geometry), H.synthetic_pic(*geometry)
                self.band(mode)
                self.assertEqual(self.save(yca, pic), 0)
                self.assert_y22(yca, pic)
                self.assertEqual(self.writes(), [H.BAND_WRITE] * 10, "20 strips per Write64")
                self.assertEqual(self.var("host_bad_phys"), 0, "every write maps the (rounded) band")
                reads = -(-geometry[1] // BAND_ROWS[geometry])
                self.assertEqual(self.log().count("r"), 2 + 2 * reads, "both headers, then one read of each file per band")
                b = self.built()
                self.assertEqual((b["band"], b["band_addr"], b["band_size"], b["pic_status"], b["stage"]),
                                 (BS["USED"], 0x40000070 if mode == 3 else 0x40000000, H.sc.BAND_REQUEST, PS["OK"], ST["DONE"]))
                self.assertEqual((self.var("host_band_allocs"), self.var("host_band_req"), self.var("host_band_type")),
                                 (1, H.sc.BAND_REQUEST, 4))
                self.assertEqual((self.var("host_band_frees"), self.var("host_band_freed_suite")), (1, 0x5517E))

    def test_band_osd_only(self):
        yca = H.synthetic_yca(*LCD)
        self.band(1)
        self.assertEqual(self.save(yca), 0)
        self.assert_y22(yca)
        self.assertEqual(self.writes(), [H.BAND_WRITE] * 10)
        self.assertEqual(self.log().count("r"), 1 + 10, "the header, then 70 source rows per read")

    def test_fallbacks_are_byte_equal_on_the_strip_path(self):
        yca, pic = H.synthetic_yca(*LCD), H.synthetic_pic(*LCD)
        for name, (mode, size, status, frees) in {"EMPTY": (0, None, "EMPTY", 0), "BAD": (1, H.sc.BAND_REQUEST - 1, "BAD", 1),
                                                  "TIMEOUT": (2, None, "TIMEOUT", 0)}.items():
            with self.subTest(name):
                self.setUp()
                self.band(mode, size)
                self.assertEqual(self.save(yca, pic), 0)
                self.assertEqual(self.built()["band"], BS[status])
                self.assertEqual(self.writes(), [H.STRIP] * 200)
                self.assertEqual(self.var("host_band_frees"), frees)
                self.assert_y22(yca, pic)
        with self.subTest("LATE"):  # the TIMEOUT request is still outstanding: not asked again
            n = self.var("host_nwrites")
            self.assertEqual(self.save(yca, pic), 0)
            self.assertEqual((self.built()["band"], self.var("host_band_allocs")), (BS["LATE"], 1))
            self.assertEqual(self.writes()[n:], [H.STRIP] * 200)
            self.assert_y22(yca, pic)

    def test_abandoned_request_is_freed_on_arrival_and_not_repeated(self):
        yca = H.synthetic_yca(*LCD)
        self.band(2)
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_takes")), (BS["TIMEOUT"], 1))
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_allocs")), (BS["LATE"], 1))
        self.lib.host_band_arrive()  # RscMgrTask, later
        self.assertEqual((self.var("host_band_frees"), self.var("host_band_freed_suite")), (1, 0x5517E), "the callback frees it")
        self.band(1)
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_allocs"), self.writes()[-1]), (BS["USED"], 2, H.BAND_WRITE))
        self.assert_y22(yca)

    def test_callback_between_the_timeout_and_the_abandon_is_used(self):
        """The first take times out, the callback completes before the CAS: the second
        take collects its give and the band is used, leaving no token behind."""
        yca = H.synthetic_yca(*LCD)
        self.band(4)
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_takes"), self.var("host_band_frees")), (BS["USED"], 2, 1))
        self.assert_y22(yca)
        self.band(1)
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_takes")), (BS["USED"], 3), "no token left behind")

    def test_stale_give_costs_one_build_and_leaks_nothing(self):
        """The callback's give lands after both takes timed out. The next build's take
        returns that stale give while its own request is still WAITING: it is abandoned
        like a timeout, its callback frees it, and the build after is USED again."""
        yca = H.synthetic_yca(*LCD)
        self.band(5)
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_takes"), self.var("host_band_frees")), (BS["USED"], 2, 1))
        self.lib.host_band_release_give()  # the late give: a stale token now
        self.band(2)  # the next request's callback is slow
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_allocs"), self.var("host_band_takes")), (BS["TIMEOUT"], 2, 3))
        self.assertEqual(self.writes()[-1], H.STRIP, "strip path")
        self.assert_y22(yca)
        self.lib.host_band_arrive()
        self.assertEqual((self.var("host_band_frees"), self.var("host_band_freed_suite")), (2, 0x5517E), "freed on arrival")
        self.band(1)
        self.assertEqual(self.save(yca), 0)
        self.assertEqual((self.built()["band"], self.var("host_band_allocs"), self.var("host_band_takes"),
                          self.var("host_band_frees")), (BS["USED"], 3, 4, 3))
        self.assert_y22(yca)

    def test_read_failure_in_band_mode_removes_the_y22_and_frees(self):
        self.band(1)
        self.var("host_read_fail_at", 3, I)
        self.assertEqual(self.save(H.synthetic_yca(*LCD), H.synthetic_pic(*LCD)), 1)
        self.assertIsNone(self.file(H.Y22_PATH))
        self.assertEqual((self.var("host_band_frees"), self.lib.host_open_fds()), (1, 0))


if __name__ == "__main__":
    unittest.main()
