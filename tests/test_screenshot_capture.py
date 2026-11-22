"""Host tests for the --screenshot RATE writer and the one-press job.

The actual combo_capture.c (SS_nnnnn.YCA / .PIC writer on GuiMainTask, click and
bip feedback on the worker), combo_observe.c, hw_jpeg_inject.c, y22_stream.c,
picture_layer.c and one_press.c are compiled with the host C compiler, against
fake Canon RAM, FIO, ABeep / AudioCtrl, FM and INDEV layers (screenshot_host.py).

Two host builds, like the two firmware builds that can carry the screenshot:
screenshot only (the real rec_tone.c, which then has its preflight alone) and
-DR5_RECORD_INDICATOR=1 (combo_capture.c also defers to the record cue). No ROM,
camera, card or ARM toolchain; synthetic inputs only.
"""
import ctypes
import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshot_host as H  # noqa: E402

U, I = H.U, H.I
O, V, CAP, OP = H.O, H.V, H.CAP, H.OP
SS1 = H.SS % 1
LCD_BYTES = 3 * 1024 * 682
PIC_BYTES = 1024 * 682 * 5 // 2
PF_BUSY, PF_ACTRL_STATE = 2, 7  # rec_tone.h REC_TONE_PF_*


class CaptureBase(unittest.TestCase):
    INDICATOR = False

    @classmethod
    def setUpClass(cls):
        cls.lib = H.capture_lib(cls.INDICATOR)

    def setUp(self):
        self.c = H.CaptureCamera(self.lib, self.INDICATOR)

    def no_fio(self):
        """Nothing past MountCheck: no name probe, Create, Map or Write."""
        self.assertFalse(any(x[0] in ("create", "write", "map", "open") for x in self.c.calls), self.c.calls)


class Writer(CaptureBase):
    def test_lcd_capture_writes_the_header_sector_then_the_display_buffer(self):
        c = self.c
        c.tap(200)
        r = c.result(1)
        self.assertEqual((r["outcome"], r["number"], r["width"], r["height"], r["bytes"]), (O["OK"], 1, 1024, 682, LCD_BYTES))
        self.assertEqual(len(c.files[SS1]), 512 + LCD_BYTES)
        self.assertEqual([x[0] for x in c.calls],
                         ["mount", "open", "open", "create", "map", "write", "unmap", "map", "write", "unmap", "close", "flush",
                          "remove"])  # a stale SS_00001.PIC goes even when no picture layer is written
        hdr_va = self.lib.capture_host_header() & 0xFFFFFFFF
        self.assertEqual(c.calls[4], ("map", hdr_va, 512, 0, 1), "one whole sector")
        self.assertEqual(c.calls[5][1:], (5, 0, hdr_va ^ 0x80000000, 1, 512))
        self.assertEqual(c.calls[7], ("map", 0x22400000, LCD_BYTES, 0, 1), "selected buffer 1 - cur (B)")
        self.assertEqual(c.calls[8][1:], (5, 0, 0x22400000 ^ 0x80000000, 1, LCD_BYTES))
        self.assertEqual(c.calls[10:], [("close", 5), ("flush", "B:/"), ("remove", "B:/DCIM/R5SHOTS/SS_00001.PIC")])
        h = c.header()
        self.assertEqual(h[:4], b"R5SS")
        self.assertEqual(struct.unpack("<HHIIII4sI", h[4:32]), (3, 512, 1024, 682, 3072, LCD_BYTES, b"UYVA", 1 << 16 | 1 << 8))
        self.assertEqual(h[32:], bytes(480), "padding is zero")
        self.assertEqual(H.r5ss_geometry(h + bytes(LCD_BYTES)), (1024, 682), "what the converter reads")

    def test_evf_and_uncached_descriptor(self):
        c = self.c
        c.lcd(w=1600, h=1200, cur=1, data=0x48000004)  # uncached alias: no alignment rule
        c.tap(200)
        r = c.result(1)
        self.assertEqual((r["outcome"], r["width"], r["height"], r["bytes"]), (O["OK"], 1600, 1200, 5_760_000))
        self.assertIn(("map", 0x48000004, 5_760_000, 0, 1), c.calls)

    def test_numbering_never_overwrites_and_continues(self):
        c = self.c
        for n in (1, 2, 3, 4, 5, 7):
            c.files[H.SS % n] = b"old"
        c.tap(200)
        self.assertEqual(c.result(1)["number"], 6)
        self.assertIn(H.SS % 6, c.files)
        c.tap(300)
        self.assertEqual(c.result(2)["number"], 8, "7 exists: probed and skipped")
        self.assertEqual(c.files[H.SS % 7], b"old")
        c.tap(400)
        self.assertEqual((c.result(3)["number"], c.result(3)["probes"]), (9, 1))

    def test_many_existing_files_are_found_within_the_probe_budget(self):
        c = self.c
        for n in range(1, 1201):
            c.files[H.SS % n] = b"o"
        c.tap(200)
        self.assertEqual(c.result(1)["number"], 1201)
        self.assertLessEqual(c.result(1)["probes"], CAP["PROBE_LIMIT"])

    def test_probe_budget_exhausted_refuses(self):
        c = self.c
        c.tap(200)
        for n in range(2, 200):  # copied in behind our back: the linear search runs out at 40 probes
            c.files[H.SS % n] = b"o"
        c.calls.clear()
        c.tap(300)
        r = c.result(2)
        self.assertEqual((r["outcome"], r["probes"]), (O["REFUSED_NAMESPACE"], CAP["PROBE_LIMIT"]))
        self.assertFalse(any(x[0] == "create" for x in c.calls))

    def test_number_space_ends_at_99999(self):
        c = self.c
        c.state().next_number = 99999
        c.files[H.SS % 99999] = b"o"
        c.tap(200)
        self.assertEqual((c.result(1)["outcome"], c.result(1)["probes"]), (O["REFUSED_NAMESPACE"], 1))
        del c.files[H.SS % 99999]
        c.tap(300)
        self.assertEqual((c.result(2)["outcome"], c.result(2)["number"]), (O["OK"], 99999))
        self.assertIn("B:/DCIM/R5SHOTS/SS_99999.YCA", c.files)

    def test_probe_errors_refuse_and_never_close(self):
        for rc in (-5, 0, 2):
            with self.subTest(rc=rc):
                self.setUp()
                self.c.open_rc[SS1] = rc
                self.c.tap(200)
                self.assertEqual(self.c.result(1)["outcome"], O["REFUSED_NAMESPACE"])
                self.assertFalse(any(x[0] in ("create", "close") for x in self.c.calls), self.c.calls)

    def test_map_failure_writes_nothing_and_removes(self):
        for idx, field in ((0, "rc_map_header"), (1, "rc_map_data")):
            with self.subTest(map=idx):
                self.setUp()
                c = self.c
                c.map_rc = {idx: 3}
                c.tap(200)
                r = c.result(1)
                self.assertEqual((r["outcome"], r[field]), (O["FAILED_WRITE"], 3))
                kinds = [x[0] for x in c.calls]
                self.assertEqual((kinds.count("write"), kinds.count("unmap")), (idx, idx), "no Write or Unmap after a failed Map")
                self.assertEqual(kinds[-3:], ["close", "flush", "remove"])
                self.assertNotIn(SS1, c.files)

    def test_short_writes_are_closed_removed_and_the_number_reused(self):
        for idx, field in ((0, "rc_header"), (1, "rc_data")):
            with self.subTest(write=idx):
                self.setUp()
                c = self.c
                c.short = {idx: 10}
                c.tap(200)
                r = c.result(1)
                self.assertEqual((r["outcome"], r[field]), (O["FAILED_WRITE"], 10))
                self.assertEqual([x[0] for x in c.calls][-3:], ["close", "flush", "remove"])
                self.assertNotIn(SS1, c.files)
                c.short = {}
                c.tap(300)
                self.assertEqual((c.result(2)["outcome"], c.result(2)["number"]), (O["OK"], 1))

    def test_create_failure_never_closes(self):
        c = self.c
        c.dir_exists = False
        c.tap(200)
        self.assertEqual((c.result(1)["outcome"], c.result(1)["rc_create"]), (O["FAILED_CREATE"], -1))
        self.assertFalse(any(x[0] in ("close", "write", "map") for x in c.calls))
        c.dir_exists, c.create_rc = True, 2
        c.tap(300)
        self.assertEqual(c.result(2)["outcome"], O["FAILED_CREATE"])
        self.assertFalse(any(x[0] == "close" for x in c.calls))

    def test_close_and_flush_failures_are_reported(self):
        c = self.c
        c.close_rc = -1
        c.tap(200)
        self.assertEqual(c.result(1)["outcome"], O["FAILED_CLOSE"])
        c.close_rc, c.flush_rc = 0, -5
        c.tap(300)
        self.assertEqual((c.result(2)["outcome"], c.result(2)["number"]), (O["FAILED_FLUSH"], 2))

    def test_reentered_attempt_is_refused_busy(self):
        c = self.c

        def reenter(k):
            if k == 1:
                c.release()
                c.press(300)
        c.on_write = reenter
        c.tap(200)
        self.assertEqual([c.result(i)["outcome"] for i in (1, 2)], [O["REFUSED_BUSY"], O["OK"]])
        c.on_write = None
        c.tap(400)
        self.assertEqual(c.result(3)["outcome"], O["OK"], "busy cleared")

    def test_stage_offsets_follow_each_call(self):
        c = self.c
        c.tap(200)
        r = c.result(1)
        self.assertEqual([r[k] for k in ("us_probed", "us_create", "us_header", "us_data", "us_close", "us_flush")],
                         [1000, 2000, 3000, 4000, 5000, 6000], "one timer read after each stage")
        c.mounted = 0
        c.tap(300)
        r = c.result(2)
        self.assertEqual([r[k] for k in ("us_probed", "us_create", "us_header", "us_data", "us_close", "us_flush")],
                         [0xFFFFFFFF] * 6, "refused before the probes: not reached")

    def test_picture_layer_is_saved_beside_the_screenshot(self):
        c = self.c
        c.display(out=0)
        sectors = {}
        c.on_write = lambda k: sectors.setdefault(k, c.header())  # the shared sector as handed to each Write
        c.tap(200)
        pic = SS1[:-3] + "PIC"
        self.assertEqual(c.result(1)["outcome"], O["OK"])
        self.assertEqual(len(c.files[pic]), 512 + PIC_BYTES)
        tail = c.calls[c.calls.index(("flush", "B:/")) + 1:]
        self.assertEqual([x[0] for x in tail], ["remove", "create", "map", "write", "unmap", "write", "close", "flush"])
        self.assertEqual(tail[5][3:], (H.TFT_LO, H.TFT_HI, PIC_BYTES), "the Active IBUS plane, written without a CPU copy")
        self.assertEqual((c.op()["pic_outcome"], c.op()["pic_bytes"]), (1, PIC_BYTES))
        c.tap(300)
        self.assertEqual(sectors[2][:4], b"R5PI", "a .PIC header filled the shared sector in between")
        for k in (0, 4):  # SS_00001.YCA and SS_00002.YCA headers
            self.assertEqual((sectors[k][:4], sectors[k][32:]), (b"R5SS", bytes(480)), "the YCA header is zero-padded")

    def test_a_stale_picture_layer_is_removed_even_without_a_new_one(self):
        """A .PIC left behind (its .YCA deleted by hand) must never be composited into a
        new screenshot of the same number when the picture layer cannot be written."""
        c = self.c
        pic = SS1[:-3] + "PIC"
        c.files[pic] = b"an older photo layer"
        c.tap(200)  # no picture layer is set up in this harness state: pl_select refuses
        self.assertEqual(c.result(1)["outcome"], O["OK"])
        self.assertNotEqual(c.op()["pic_outcome"], 1)
        self.assertNotIn(pic, c.files)
        self.assertEqual(c.calls[-1], ("remove", pic))

    def test_ok_capture_builds_the_y22_and_marks_it_pending(self):
        c = self.c
        c.tap(200)
        r, p, b = c.result(1), c.pending(), c.built()
        self.assertEqual((p["pending"], p["path"], p["set_tick"], p["sets"], p["clears"]), (1, SS1.encode(), r["tick"], 1, 1))
        self.assertEqual((b["builds"], b["failures"], b["stage"], b["y22_written"], b["cached_reads"]),
                         (1, 0, H.INJ["CONV_DONE"], H.Y22_BYTES, 0), "every FIO_ReadFile buffer uncacheable")
        self.assertEqual(c.guard().capture, 0, "the capture guard is released")
        c.tap(300)
        self.assertEqual(c.pending()["path"], (H.SS % 2).encode(), "the latest capture")

    def test_failed_y22_build_or_capture_leaves_nothing_pending(self):
        c = self.c
        U.in_dll(self.lib, "host_conv_fail").value = 1  # CreateSC of HWJPG.Y22 fails
        c.tap(200)
        self.assertEqual(c.result(1)["outcome"], O["OK"], "the screenshot itself is saved")
        self.assertEqual((c.pending()["pending"], c.built()["failures"], c.built()["stage"]), (0, 1, H.INJ["CONV_CREATE"]))
        U.in_dll(self.lib, "host_conv_fail").value = 0
        c.tap(300)
        self.assertEqual(c.pending()["pending"], 1)
        c.short = {c.writes + 1: 4096}
        c.tap(400)
        self.assertEqual((c.result(3)["outcome"], c.pending()["pending"]), (O["FAILED_WRITE"], 0), "cleared at admission")


class Refusals(CaptureBase):
    def test_recording_shot_and_unknown_counter(self):
        c = self.c
        c.lcd(rec=3)
        c.tap(200)
        self.assertEqual(c.result(1)["outcome"], O["REFUSED_RECORDING"])
        c.lcd()
        self.lib.host_counter(1, 250)
        c.tap(300)
        self.assertEqual(c.result(2)["outcome"], O["REFUSED_SHOT"])
        self.lib.host_counter(0, 0)
        c.tap(400)
        self.assertEqual(c.result(3)["outcome"], O["REFUSED_SHOT"])
        self.no_fio()

    def test_boot_guard_refuses_for_five_seconds(self):
        c = self.c
        for raw in (0, 100, CAP["BOOT_GUARD_TICKS"] - 1):
            self.lib.host_set_tick_raw(raw)
            c.press()
            c.release()
            self.lib.host_set_tick_raw(raw + 60)  # keep the debounce out of the way
        self.assertEqual([c.result(i)["outcome"] for i in (1, 2, 3)], [O["REFUSED_BOOT"]] * 3)
        self.no_fio()
        self.lib.host_counter(1, -900 & 0xFFFFFFFF)  # counter known since tick 100
        self.lib.host_set_tick_raw(CAP["BOOT_GUARD_TICKS"] + 60)
        c.tap(None)
        self.assertEqual(c.result(4)["outcome"], O["OK"], "past the guard")
        self.setUp()
        self.c.lcd(rec=3)
        self.lib.host_set_tick_raw(10)
        self.c.press()
        self.assertEqual(self.c.result(1)["outcome"], O["REFUSED_BOOT"], "the boot guard comes first")

    def test_display_refusals(self):
        for kw in (dict(magic=0x12345678), dict(fmt=0x05040100), dict(w=1920, h=1080), dict(data=0x22000010), dict(cur=5),
                   dict(marv=0), dict(data=0xC0000000)):
            with self.subTest(**kw):
                self.setUp()
                self.c.lcd(**kw)
                self.c.tap(200)
                self.assertEqual(self.c.result(1)["outcome"], O["REFUSED_DISPLAY"])
                self.assertFalse(any(x[0] in ("mount", "create", "map") for x in self.c.calls))

    def test_card_absent(self):
        self.c.mounted = 0
        self.c.tap(200)
        self.assertEqual((self.c.result(1)["outcome"], self.c.result(1)["rc_mount"]), (O["REFUSED_CARD"], 0))
        self.no_fio()

    def test_debounce_held_repeats_and_a_lost_release(self):
        c = self.c
        c.tap(200)
        c.tap(200 + CAP["DEBOUNCE_TICKS"] - 1)
        self.assertEqual(c.state().attempts, 1, "debounced")
        c.tap(200 + CAP["DEBOUNCE_TICKS"])
        self.assertEqual(c.state().attempts, 2)
        c.press(400)
        c.press(430)   # held: a repeat
        c.press(479)   # 49 ticks after the last press code: still a repeat
        c.press(529)   # 50 ticks of silence: the release was lost
        s = c.state()
        self.assertEqual((s.repeats, s.stale_held, s.attempts), (2, 1, 4))

    def test_other_codes_are_ignored(self):
        self.c.press(200, code=72)
        self.c.press(201, code=10)
        self.assertEqual((self.c.state().presses, self.c.calls), (0, []))

    def test_gui_mode_and_verdicts_are_recorded(self):
        c = self.c
        c.lcd(gui=0x2003)
        c.tap(200)
        r = c.result(1)
        want = 0
        for bit in ("NOT_RECORDING", "SHOT_QUIET", "CONTEXT", "DEBOUNCE", "GEOMETRY", "MARV", "ALIGN", "CARD", "JOB_IDLE",
                    "MOVIE_QUIET"):
            want |= V[bit]
        self.assertEqual((r["gui_mode"], r["known"], r["pass"]), (0x2003, want, want))
        self.assertEqual((r["job_main"], r["job_sub"], r["rec_idle"]), (0, 0, 0xFFFFFFFE), "no recording seen since boot")

    def test_canon_job_state_refuses_before_any_fio(self):
        for main, sub in ((0x10, 0), (0, 0x4), (0x16, 0x2)):
            with self.subTest(main=main, sub=sub):
                self.setUp()
                c = self.c
                c.mem[H.JOB_MAIN], c.mem[H.JOB_SUB] = main, sub
                c.tap(200)
                r = c.result(1)
                self.assertEqual((r["outcome"], r["job_main"], r["job_sub"]), (O["REFUSED_WRITING"], main, sub))
                self.assertEqual(c.calls, [], "not even MountCheck")
                c.mem[H.JOB_MAIN] = c.mem[H.JOB_SUB] = 0
                c.tap(300)
                self.assertEqual(c.result(2)["outcome"], O["OK"], "writes once Canon is idle")

    def record(self, tick, rec):
        self.c.mem[H.REC_STATE_WORD] = rec
        self.lib.host_set_tick(tick)
        self.lib.capture_feedback_step()

    def test_movie_quiet_window_runs_from_the_last_recording_sighting(self):
        c = self.c
        self.record(100, 1)
        self.record(200, 1)
        self.record(210, 0)
        c.tap(200 + CAP["MOVIE_QUIET_TICKS"] - 1)
        self.assertEqual((c.result(1)["outcome"], c.result(1)["rec_idle"]), (O["REFUSED_MOVIE"], CAP["MOVIE_QUIET_TICKS"] - 1))
        self.assertEqual(c.calls, [], "not even MountCheck")
        self.setUp()
        self.record(100, 1)
        self.record(110, 0)
        self.c.tap(100 + CAP["MOVIE_QUIET_TICKS"])
        self.assertEqual((self.c.result(1)["outcome"], self.c.result(1)["rec_idle"]), (O["OK"], CAP["MOVIE_QUIET_TICKS"]))

    def test_develop_guard_refuses_before_any_fio_and_keeps_the_pending_screenshot(self):
        c = self.c
        c.tap(100)
        self.assertEqual(c.pending()["pending"], 1)
        g = c.guard()
        g.develop, g.develop_tick = 1, 300 + 1000   # an H8 save at the press tick
        n = len(c.calls)
        c.tap(300 + H.INJ["DEVELOP_TICKS"])
        self.assertEqual((c.result(2)["outcome"], len(c.calls)), (O["REFUSED_DEVELOP"], n))
        self.assertEqual((c.pending()["pending"], g.capture, g.refused_develop), (1, 0, 1), "the pending screenshot is untouched")
        c.tap(301 + 2 * H.INJ["DEVELOP_TICKS"])  # the window ran out (no H3 came)
        self.assertEqual(c.result(3)["outcome"], O["OK"])
        g.busy = 1  # an H3 reading HWJPG.Y22
        c.tap(400 + 3 * H.INJ["DEVELOP_TICKS"])
        self.assertEqual(c.result(4)["outcome"], O["REFUSED_DEVELOP"])


class Feedback(CaptureBase):
    """Click on a saved screenshot, low bip on a refusal, nothing while recording."""

    def test_cues_are_published_once_bit_exact(self):
        c = self.c
        c.step()
        f = H.sc.cue_facts()
        pcm = ctypes.string_at(self.lib.host_pcm(), 2 * f["capacity_samples"])
        for cue in f["cues"]:
            got = pcm[2 * cue["offset_samples"]:2 * (cue["offset_samples"] + cue["samples"])]
            self.assertEqual(hashlib.sha256(got).hexdigest(), cue["pcm_sha256"], cue["name"])
        for i, cue in enumerate(f["cues"]):
            d = (U * 16).from_address(self.lib.host_desc(i))
            self.assertEqual((d[0], d[1] & 31, d[2]), (1, 0, cue["bytes"]))
            self.assertEqual(d[1], (self.lib.host_pcm() + 2 * cue["offset_samples"]) & 0xFFFFFFFF)
        cleans = c.g("host_cleans")
        c.step(5)
        self.assertEqual(c.g("host_cleans"), cleans, "published once")
        self.assertEqual(c.feedback()["ready"], 1)

    def test_click_on_success_bip_on_refusal_silence_while_recording(self):
        c = self.c
        c.step()
        c.tap(200)
        c.step()
        self.assertEqual((c.beeps(), c.beep_desc(), c.g("host_beep_id")), (1, self.lib.host_desc(0), 2), "click, SE id 2")
        c.mounted = 0
        c.tap(300)
        c.step()
        self.assertEqual((c.beeps(), c.beep_desc()), (2, self.lib.host_desc(1)), "low bip")
        c.lcd(rec=3)
        c.tap(400)
        c.step()
        self.assertEqual((c.beeps(), c.feedback()["silent"]), (2, 1), "never while recording")
        c.lcd()
        c.mounted = 1
        c.tap(500)   # captured before recording started...
        c.lcd(rec=3)  # ...but recording began before the worker drained it
        c.step()
        self.assertEqual((c.beeps(), c.feedback()["last_reason"]), (2, CAP["CUE_RECORDING"]))

    def test_boot_refusal_bips(self):
        c = self.c
        c.step()
        self.lib.host_set_tick_raw(100)
        c.press()
        c.step()
        self.assertEqual((c.beeps(), c.beep_desc()), (1, self.lib.host_desc(1)))

    def test_lapped_results_are_counted_not_guessed(self):
        c = self.c
        for i in range(10):
            c.tap(200 + 100 * i)
        c.step(2)
        f = c.feedback()
        self.assertEqual((f["lost"], f["kept"], f["seen"]), (3, 7, 10))
        self.assertEqual(c.beeps(), 7)

    def test_record_published_during_the_copy_is_torn(self):
        c = self.c
        for i in range(7):
            c.tap(200 + 100 * i)
        fired = []

        def lap(seq):
            if not fired:
                fired.append(seq)
                c.tap(1000)  # seq 8: the slot of seq 1 may now be in rewrite
        cb = H.CALLBACK(lap)
        self.lib.host_after_copy(cb)
        c.step(2)
        self.lib.host_after_copy(H.CALLBACK(0))
        f = c.feedback()
        self.assertEqual(fired, [1])
        self.assertEqual((f["lost"], f["kept"], f["seen"]), (1, 7, 8))


class FeedbackWithRecordIndicator(Feedback):
    """-DR5_RECORD_INDICATOR=1: the same cues, and never over the record cue."""
    INDICATOR = True

    def test_record_cue_gates(self):
        c = self.c
        c.step()
        tick = 200
        for name, arm, reason in (("preflight", lambda: setattr(U.in_dll(self.lib, "host_preflight_reason"), "value", 7), 7),
                                  ("record cue pending", lambda: self.lib.host_tone(1, 0, 1), CAP["CUE_TONE_BUSY"]),
                                  ("record cue observed", lambda: self.lib.host_tone(1, 1, 0), CAP["CUE_TONE_BUSY"]),
                                  ("tone fault", lambda: self.lib.host_tone_fault(1), CAP["CUE_TONE_UNREADY"]),
                                  ("tone unready", lambda: self.lib.host_tone(0, 0, 0), CAP["CUE_TONE_UNREADY"])):
            with self.subTest(name):
                arm()
                c.tap(tick)
                c.step()
                self.assertEqual((c.beeps(), c.feedback()["last_reason"]), (0, reason))
                U.in_dll(self.lib, "host_preflight_reason").value = 0
                self.lib.host_tone_fault(0)
                self.lib.host_tone(1, 0, 0)
                tick += 100
        c.tap(tick)
        c.step()
        self.assertEqual(c.beeps(), 1, "a clean state sounds")
        self.assertEqual(c.g("host_preflight_mask"), 1 << 1, "AudioCtrl stock idle only")


class ScreenshotOnlyBuild(CaptureBase):
    """Without the record indicator rec_tone.c is its preflight alone."""

    def test_links_the_preflight_without_the_record_indicator_engine(self):
        self.assertTrue(hasattr(self.lib, "rec_tone_preflight"))
        for name in ("rec_tone_state", "rec_tone_step", "rec_tone_report", "rec_tone_cues"):
            self.assertFalse(hasattr(self.lib, name), name)
        self.assertTrue(hasattr(H.capture_lib(True), "rec_tone_state"), "the indicator host build carries it")

    def test_the_real_preflight_admits_only_stock_idle_audioctrl(self):
        c = self.c
        c.step()
        c.states[c.ACH] = 5
        c.tap(200)
        c.step()
        self.assertEqual((c.beeps(), c.feedback()["last_reason"], c.feedback()["skipped"]), (0, PF_ACTRL_STATE, 1))
        c.states[c.ACH] = 1
        c.abeep_busy(True)
        c.tap(300)
        c.step()
        self.assertEqual((c.beeps(), c.feedback()["last_reason"]), (0, PF_BUSY), "a click never waits")
        c.abeep_busy(False)
        c.tap(400)
        c.step()
        self.assertEqual((c.beeps(), c.beep_desc(), c.g("host_beep_id")), (1, self.lib.host_desc(0), 2), "the click")


class OnePress(CaptureBase):
    """RATE in playback: the capture, then Canon's own Resize -> S2 job on the shown photo."""

    def setUp(self):
        super().setUp()
        self.c.lcd(data=H.DATA)
        self.c.display(out=0)

    def start(self, tick=200):
        self.c.playback()
        self.c.tap(tick)
        self.assertEqual(self.c.result(1)["outcome"], O["OK"])
        op = self.c.op()
        self.assertEqual((op["outcome"], op["state"]), (OP["START"], OP["STARTED"]))
        return op

    def dry(self, ok=1):
        self.lib.one_press_dry(ok, 2400 if ok else 4176, 1600 if ok else 2784)

    def h3_armed(self):
        return H.InjectRing.in_dll(self.lib, "inject_ring").armed

    def test_rate_in_playback_starts_canons_resize_on_the_shown_photo(self):
        op = self.start()
        c = self.c
        self.assertEqual(c.op_log(), "FRGIS", "at the release: FM check, then Canon's order: release, busy flags, start")
        self.assertEqual((c.g("op_fm_calls"), c.g("op_fm_subs"), c.g("op_fm_handle")), (1, 2, H.PHOTO | 2),
                         "the FM check on the JPEG sub")
        self.assertEqual((c.g("op_start_handle"), c.g("op_start_remote")), (H.PHOTO, 0), "INDEV gets the group handle, local job")
        self.assertEqual(ctypes.c_size_t.in_dll(self.lib, "op_done_cb").value, self.lib.host_done_ptr())
        self.assertNotEqual(ctypes.c_size_t.in_dll(self.lib, "op_start_cb").value, 0)
        want = bytearray(284)
        for off, v in H.P1_BLOCK.items():
            struct.pack_into("<I", want, off, v)
        self.assertEqual(bytes((ctypes.c_uint8 * 284).in_dll(self.lib, "op_start_block")), bytes(want), "Canon's S2 Resize block")
        self.assertEqual((op["number"], op["view_handle"], op["source"], op["view_fm_rc"], op["view_info0"]),
                         (1, H.PHOTO, OP["SOURCE_VIEW"], 0, 14 << 16 | 4))
        self.assertEqual((op["pic_outcome"], op["pic_bytes"]), (1, PIC_BYTES))
        self.assertEqual((op["dir"], op["file"], op["handle"], op["rc_open"], op["rc_read"], op["fm_rc"]), (0,) * 6)
        self.assertEqual(op["pending_tick"], c.pending()["set_tick"], "this job's own screenshot")
        self.assertEqual((c.g("op_gui_busy"), c.g("op_indev_busy"), c.pending()["pending"], c.guard().capture), (1, 1, 1, 0))
        self.assertEqual((c.built()["stage"], c.built()["cached_reads"]), (H.INJ["CONV_DONE"], 0), "HWJPG.Y22 built at the press")

    def test_armed_at_the_press_started_at_the_release(self):
        c = self.c
        c.playback()
        c.press(200)
        op = c.op()
        self.assertEqual((op["armed"], op["state"], c.g("op_start_calls"), c.op_log()), (1, OP["IDLE"], 0, ""))
        c.release()
        self.assertEqual((c.op()["armed"], c.op()["state"], c.g("op_start_calls")), (0, OP["STARTED"], 1))
        c.release()
        self.assertEqual(c.g("op_start_calls"), 1, "a second release starts nothing")

    def test_arms_only_in_playback(self):
        c = self.c
        c.step()
        c.playback()
        for i, gui in enumerate((0, 0x2003, 0x2001)):
            c.mem[H.GUI_MODE_WORD] = gui
            c.tap(200 + 100 * i)
        op = c.op()
        self.assertEqual((op["outcome"], op["attempts"], op["armed"], c.op_log()), (OP["NOT_PLAYBACK"], 0, 0, ""),
                         "no FM call, no INDEV call")
        c.step(40)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (3, 0), "a plain capture: its click only")
        self.assertEqual(c.pending()["pending"], 1, "pending for a manual S2 Resize")

    def test_refusals_click_then_bip_twice_and_keep_the_screenshot_pending(self):
        cases = {"no view": (lambda c: c.playback(handle=0), OP["NO_VIEW"], 0),
                 "FM error": (lambda c: c.playback(rc=H.FM_NOT_FOUND), OP["VIEW_INVALID"], 1),
                 "FM odd rc": (lambda c: c.playback(rc=17), OP["VIEW_INVALID"], 1),
                 "not a JPEG": (lambda c: c.playback(type_bits=1), OP["VIEW_INVALID"], 1),
                 "S2 (a screenshot)": (lambda c: c.playback(size=15), OP["VIEW_INVALID"], 1),
                 "S3": (lambda c: c.playback(size=16), OP["VIEW_INVALID"], 1),
                 "size 2": (lambda c: c.playback(size=2), OP["VIEW_INVALID"], 1),
                 "no size": (lambda c: c.playback(size=0xFF), OP["VIEW_INVALID"], 1),
                 "Canon develop state": (lambda c: (c.playback(), c.mem.__setitem__(H.GUI_DEVELOP_STATE, 1)), OP["BUSY"], 1),
                 "GUI busy": (lambda c: (c.playback(), setattr(U.in_dll(self.lib, "op_gui_busy"), "value", 1)), OP["BUSY"], 1),
                 "INDEV busy": (lambda c: (c.playback(), setattr(U.in_dll(self.lib, "op_indev_busy"), "value", 1)), OP["BUSY"], 1)}
        for name, (setup, outcome, fm_calls) in cases.items():
            with self.subTest(name):
                self.setUp()
                c = self.c
                c.step()
                setup(c)
                c.tap(200)
                op = c.op()
                self.assertEqual((c.result(1)["outcome"], op["outcome"], c.g("op_start_calls"), c.g("op_fm_calls"), op["refusals"]),
                                 (O["OK"], outcome, 0, fm_calls, 1), "the screenshot itself is saved; no INDEV call")
                self.assertNotIn("R", c.op_log(), "no GUI_ReleaseFileObject")
                self.assertEqual((op["state"], c.pending()["pending"]), (OP["IDLE"], 1), "pending for a manual S2")
                c.step()
                self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 0), "the capture's own click first")
                c.step()
                self.assertEqual(c.feedback()["bips"], 1, "then the first bip, on its own tick")
                c.step(12)
                self.assertEqual(c.feedback()["bips"], 2, "the second bip")
                c.step(40)
                self.assertEqual(c.feedback()["bips"], 2, "and no more")

    def test_no_y22_means_no_job(self):
        c = self.c
        c.step()
        c.playback()
        U.in_dll(self.lib, "host_conv_fail").value = 1
        c.tap(200)
        self.assertEqual((c.result(1)["outcome"], c.op()["outcome"], c.g("op_start_calls"), c.pending()["pending"]),
                         (O["OK"], OP["NO_Y22"], 0, 0))
        c.step(13)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 2))

    def test_playback_left_before_the_release_refuses(self):
        c = self.c
        c.step()
        c.playback()
        c.press(200)
        c.mem[H.GUI_MODE_WORD] = 0  # a queued button left playback during the capture
        c.release()
        op = c.op()
        self.assertEqual((op["outcome"], op["view_handle"], c.g("op_fm_calls"), c.g("op_start_calls")), (OP["NOT_PLAYBACK"], 0, 0, 0))
        c.step(13)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 2))

    def test_refusal_bips_wait_for_the_click_to_finish(self):
        c = self.c
        c.step()
        c.playback(handle=0)
        c.tap(200)  # no view: refused
        c.step()    # the click
        c.abeep_busy(True)
        c.step(5)
        fb = c.feedback()
        self.assertEqual((fb["clicks"], fb["bips"], fb["skipped"], fb["bip_waits"]), (1, 0, 0, 5), "waiting, not dropped")
        c.abeep_busy(False)
        c.step(13)
        fb = c.feedback()
        self.assertEqual((fb["clicks"], fb["bips"], fb["skipped"]), (1, 2, 0))

    def test_refusal_bip_gives_up_after_forty_ticks(self):
        c = self.c
        c.step()
        c.playback(handle=0)
        c.tap(200)
        c.step()
        c.abeep_busy(True)
        c.step(40)
        self.assertEqual((c.feedback()["bips"], c.feedback()["skipped"], c.feedback()["bip_waits"]), (0, 0, 40))
        c.step()
        self.assertEqual((c.feedback()["bips"], c.feedback()["skipped"]), (0, 1), "dropped after 40 ticks")

    def test_save_path(self):
        self.start()
        c = self.c
        self.dry(1)
        c.done(0x302CDCD0)
        self.assertEqual((c.g("op_end_calls"), c.g("op_end_save")), (1, 1))
        self.assertEqual(ctypes.c_size_t.in_dll(self.lib, "op_end_cb").value, self.lib.host_end_ptr())
        end_blk = bytes((ctypes.c_uint8 * 284).in_dll(self.lib, "op_end_block"))
        start_blk = bytes((ctypes.c_uint8 * 284).in_dll(self.lib, "op_start_block"))
        self.assertEqual([i for i in range(0, 284, 4) if end_blk[i:i + 4] != start_blk[i:i + 4]], [0x70, 0xD4],
                         "the two words Canon's RequestPlayDevelopEnd clears")
        self.assertEqual(self.h3_armed(), 1, "our H8 saw our own DevelopEnd(1) and armed H3")
        self.assertEqual(c.guard().develop, 1, "RATE refused from the save on")
        op = c.op()
        self.assertEqual((op["state"], op["end_reason"], op["saves"], op["done_result"]),
                         (OP["ENDING"], OP["END_SAVE"], 1, 0x302CDCD0))
        self.assertTrue(c.op_log().endswith("SiIE"), "done: INDEV busy 0 (Canon's doneCBR), 1 again (its End path), End")
        self.assertEqual((c.g("op_indev_busy"), c.g("op_gui_busy")), (1, 1), "both busy through the save")
        c.end(0)
        op = c.op()
        self.assertEqual((op["state"], op["ends"], c.g("op_gui_busy"), c.g("op_indev_busy")), (OP["IDLE"], 1, 0, 0))
        c.step(30)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 0), "a saved job: the click only")

    def test_cancelled_jobs_end_with_develop_end_0_and_three_bips(self):
        cases = {"result 0": (lambda: self.dry(1), 0, OP["END_RESULT_ZERO"]),
                 "no dry-run H3": (lambda: None, 1, OP["END_NO_DRY"]),
                 "dry run not S2": (lambda: self.dry(0), 1, OP["END_DRY_GEOMETRY"]),
                 "pending consumed": (lambda: (self.dry(1), setattr(self.c_pending(), "pending", 0)), 1, OP["END_PENDING"]),
                 "pending of another capture": (lambda: (self.dry(1), setattr(self.c_pending(), "set_tick", 12345)), 1,
                                                OP["END_PENDING"])}
        for name, (setup, result, reason) in cases.items():
            with self.subTest(name):
                self.setUp()
                c = self.c
                c.step()
                self.start(200)
                setup()
                c.done(result)
                self.assertEqual((c.g("op_end_calls"), c.g("op_end_save"), self.h3_armed(), c.guard().develop), (1, 0, 0, 0),
                                 "DevelopEnd(0); H3 not armed")
                op = c.op()
                self.assertEqual((op["end_reason"], op["cancels"], op["saves"]), (reason, 1, 0))
                c.end(0)
                self.assertEqual((c.op()["state"], c.g("op_gui_busy"), c.g("op_indev_busy")), (OP["IDLE"], 0, 0))
                c.step(30)
                self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 3))

    def c_pending(self):
        return H.InjectPending.in_dll(self.lib, "inject_pending")

    def test_rate_refused_while_the_job_runs_then_free(self):
        self.start(200)
        c = self.c
        n = len(c.calls)
        c.tap(400)
        self.assertEqual((c.result(2)["outcome"], len(c.calls), c.op()["refused"]), (O["REFUSED_DEVELOP"], n, 1),
                         "no FIO for a refused RATE")
        self.dry(1)
        c.done(1)
        c.tap(600)
        self.assertEqual(c.result(3)["outcome"], O["REFUSED_DEVELOP"], "ENDING still refuses")
        c.end(0)
        c.mem[H.GUI_MODE_WORD] = 0
        c.guard().develop = 0  # past the H8 window
        c.tap(800)
        self.assertEqual(c.result(4)["outcome"], O["OK"])

    def test_timeout_gives_up_keeps_flags_and_a_late_done_cancels(self):
        self.start(200)
        c = self.c
        st = c.op()["start_tick"]
        self.lib.host_set_tick_raw(st + OP["TIMEOUT_TICKS"] - 2)
        c.step()
        self.assertEqual(c.op()["state"], OP["STARTED"])
        c.step()
        op = c.op()
        self.assertEqual((op["state"], op["latched"], op["timeouts"], c.g("op_gui_busy"), c.g("op_indev_busy")),
                         (OP["GAVE_UP"], 1, 1, 1, 1), "both busy flags stay set")
        self.assertEqual(c.g("op_end_calls"), 0, "never DevelopEnd before done")
        n = len(c.calls)
        c.tap(st + OP["TIMEOUT_TICKS"] + 100 - 1000)
        self.assertEqual((c.result(2)["outcome"], len(c.calls)), (O["REFUSED_DEVELOP"], n))
        c.done(1)
        self.assertEqual((c.g("op_end_calls"), c.g("op_end_save"), c.op()["end_reason"], c.op()["late"]), (1, 0, OP["END_LATE"], 1))
        c.end(0)
        self.assertEqual((c.op()["state"], c.g("op_gui_busy"), c.g("op_indev_busy")), (OP["IDLE"], 0, 0), "a late end recovers")
        c.guard().develop = 0
        c.tap(st + OP["TIMEOUT_TICKS"] + 500 - 1000)
        self.assertEqual((c.op()["outcome"], c.g("op_start_calls")), (OP["LATCHED"], 1), "no second job this boot")

    def test_end_without_done_returns_to_idle(self):
        self.start()
        c = self.c
        c.end(0)
        self.assertEqual((c.op()["state"], c.g("op_gui_busy"), c.g("op_indev_busy"), c.g("op_end_calls")), (OP["IDLE"], 0, 0, 0))

    def test_y22_build_stage_times(self):
        """Each timed span reads the timer twice: with a 1 us step the sums count the calls."""
        c = self.c
        U.in_dll(self.lib, "host_y22_us_step").value = 1
        c.playback()
        c.tap(200)
        b = c.built()
        self.assertEqual((b["stage"], b["y22_written"], b["pic_status"]), (H.INJ["CONV_DONE"], H.Y22_BYTES, H.INJ["PIC_MISSING"]))
        self.assertEqual((b["us_yca"], b["us_pic"], b["us_comp"], b["us_write"], b["us_map"]), (682, 0, 0, 200, 400))
        self.assertGreaterEqual(b["us_fill"], b["us_yca"])
        self.assertGreater(b["us"], b["us_fill"] + b["us_map"] + b["us_write"])


if __name__ == "__main__":
    unittest.main()
