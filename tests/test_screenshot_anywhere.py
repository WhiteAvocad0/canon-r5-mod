"""Host tests for the screenshot anywhere stage 1 (--screenshot rate).

A RATE outside playback (live view, menus) saves its files and leaves its
HWJPG.Y22 pending as a deferred screenshot; the one-press job starts on
GuiMainTask at the next INFO release in settled playback (never SET, the confirm
key of playback dialogs). The source is the shown photo unless FM refuses it or
the playback cache says it is an XPan photo; then the last good source of this
boot (the last view whose job saved), re-checked with FM; otherwise two bips and
the screenshot stays pending. A deferred start older than 9.5 min is dropped.

The actual combo_capture.c, combo_observe.c, hw_jpeg_inject.c, y22_stream.c,
picture_layer.c and one_press.c run against the fakes of screenshot_host.py. No
ROM, camera, card or ARM toolchain; synthetic inputs only.
"""
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshot_host as H  # noqa: E402

U, OP = H.U, H.OP
GUI = H.GUI_MODE_WORD
VIEW = H.PHOTO                                          # 100/0013, a 3:2 JPEG L
XPAN = (2 << 30) | (1 << 28) | (100 << 18) | (14 << 4)  # 100/0014, an XPan JPEG L
OTHER = (2 << 30) | (1 << 28) | (100 << 18) | (15 << 4)  # 100/0015, another 3:2 JPEG L
SET_UP = 11                                             # the SET release (COMBO_BUTTONS SET 10 + 1)
SETTLE = OP["DEFER_SETTLE_TICKS"]
MAX_AGE = H.INJ["PENDING_TICKS"] - OP["TIMEOUT_TICKS"]  # one_press.h OP_DEFER_MAX_AGE_TICKS


class Anywhere(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = H.capture_lib(False)

    def setUp(self):
        self.c = H.CaptureCamera(self.lib, False)
        self.c.lcd(data=H.DATA)
        self.c.display(out=0)
        self.worker(1)

    def worker(self, n):
        self.c.step(n)

    def finish(self, save=True):
        """INDEV's dry run, done and end callbacks for the running job."""
        c = self.c
        self.lib.one_press_dry(1 if save else 0, 2400, 1600 if save else 1344)
        c.done(0x302CDCD0)
        c.end(0)
        self.assertEqual(c.op()["state"], OP["IDLE"])
        c.guard().develop = 0  # past the H8 develop window for the next RATE

    def capture_outside(self, tick, gui=0):
        """A RATE in live view (GUI mode 0) or a menu: a plain capture, HWJPG.Y22 pending, deferred."""
        c = self.c
        c.mem[GUI] = gui
        c.tap(tick)
        self.assertEqual(c.op()["outcome"], OP["NOT_PLAYBACK"])
        self.assertEqual(c.pending()["pending"], 1)
        return c.op()["pending_tick"]

    def enter_playback(self, view=VIEW, xpan=OP["XPAN_NO"], settle=True):
        c = self.c
        c.playback(handle=view)
        c.xpan(xpan)
        self.worker(SETTLE + 1 if settle else 1)

    def last_good_job(self, tick=200):
        """A RATE in playback on the 3:2 view that saves: VIEW becomes the last good source."""
        c = self.c
        c.good(VIEW)
        c.playback(handle=VIEW)
        c.tap(tick)
        self.assertEqual((c.op()["outcome"], c.op()["source"]), (OP["START"], OP["SOURCE_VIEW"]))
        self.finish()
        self.assertEqual(c.defer()["last_good"], VIEW)

    # ---- deferred delivery -----------------------------------------------------------
    def test_live_view_capture_starts_at_the_next_info_release_in_playback(self):
        c = self.c
        c.good(VIEW)
        pt = self.capture_outside(200)
        d = c.defer()
        self.assertEqual((d["defer"], d["marked"]), (OP["DEFER_WAIT"], 1))
        self.assertEqual((c.g("op_start_calls"), c.op_log()), (0, ""), "nothing starts outside playback")
        c.key_up()
        self.assertEqual((c.defer()["why"], c.g("op_start_calls")), (OP["DW_NOT_PLAYBACK"], 0), "an INFO release in live view waits")
        self.enter_playback(settle=False)
        c.key_up()  # the playback press's own release region: not settled
        self.assertEqual((c.defer()["why"], c.g("op_start_calls"), c.op_log()), (OP["DW_SETTLING"], 0, ""))
        self.worker(SETTLE)
        c.key_up()
        d, op = c.defer(), c.op()
        self.assertEqual((d["why"], d["defer"], d["starts"], d["fallbacks"]), (OP["DW_STARTED"], OP["DEFER_NONE"], 1, 0))
        self.assertEqual(c.op_log(), "FRGIS", "FM on the view, then Canon's order: release, busy flags, start")
        self.assertEqual((c.fm_seen(), c.g("op_start_handle")), ([VIEW | 2], VIEW))
        self.assertEqual((op["outcome"], op["source"], op["state"], op["pending_tick"], op["gui_mode"]),
                         (OP["START"], OP["SOURCE_VIEW"], OP["STARTED"], pt, 0),
                         "the job carries the live-view capture's pending tick and GUI mode")
        self.finish()
        self.assertEqual((c.op()["saves"], c.defer()["last_good"]), (1, VIEW))
        c.key_up()
        self.assertEqual(c.g("op_start_calls"), 1, "consumed: one job per deferred capture")
        self.worker(15)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 0), "the capture's click; no refusal bips")

    def test_a_menu_capture_is_deferred_too(self):
        c = self.c
        c.good(VIEW)
        self.capture_outside(200, gui=0x2003)
        self.enter_playback()
        c.key_up()
        self.assertEqual((c.defer()["why"], c.g("op_start_handle"), c.op()["gui_mode"]), (OP["DW_STARTED"], VIEW, 0x2003))

    def test_only_the_info_release_starts_never_set_or_other_releases(self):
        """SET is the confirm key of playback dialogs, so it never starts a job."""
        c = self.c
        c.good(VIEW)
        self.capture_outside(200)
        self.enter_playback()
        for code in (19, 13, H.TRIGGER + 1, 61, 35, 10, 14, SET_UP):  # releases of playback, MENU, RATE, others; SET / INFO presses; SET release
            c.key_up(code)
        self.assertEqual((c.g("op_start_calls"), c.defer()["decisions"]), (0, 1), "filtered codes: no decision (only the capture's arm)")
        c.key_up(H.INFO_UP)
        self.assertEqual((c.g("op_start_calls"), c.defer()["why"]), (1, OP["DW_STARTED"]))

    def test_the_hook_passes_the_info_release_and_never_set(self):
        """combo_observe_hook.S forwards exactly the trigger press, the INFO release and the trigger release."""
        text = (H.XPAN_SRC / "combo_observe_hook.S").read_text()
        code = "\n".join(line.split("@")[0] for line in text.splitlines() if not line.lstrip().startswith(("/*", "*")))
        self.assertEqual(re.findall(r"cmp r1, #(\(?COMBO_\w+(?: \+ 1\))?)", code),
                         ["COMBO_TRIGGER_CODE", "COMBO_INFO_RELEASE", "(COMBO_TRIGGER_CODE + 1)"])
        self.assertEqual(H.INFO_UP, 15)
        self.assertNotIn("SET_RELEASE", text)

    def test_xpan_view_uses_the_last_good_source(self):
        c = self.c
        self.last_good_job(200)
        c.mem[GUI] = 0
        self.worker(1)
        self.capture_outside(600, gui=0x2003)  # a menu
        c.good(VIEW, XPAN)  # FM would even accept the XPan view: the pre-check must keep it out
        n = c.g("op_fm_calls")
        self.enter_playback(view=XPAN, xpan=OP["XPAN_YES"])
        c.key_up()
        d, op = c.defer(), c.op()
        self.assertEqual((d["why"], d["xpan"], d["fallbacks"], d["starts"]), (OP["DW_STARTED"], OP["XPAN_YES"], 1, 1))
        self.assertEqual((op["source"], c.g("op_start_handle")), (OP["SOURCE_LAST_GOOD"], VIEW))
        self.assertEqual(c.fm_seen()[n:], [VIEW | 2], "no FM call on the XPan view; the last good source is re-checked")
        self.finish()
        self.assertEqual(c.defer()["last_good"], VIEW, "a last-good job does not replace the last good source")

    def test_unusable_view_without_last_good_bips_once_per_visit_and_keeps_pending(self):
        c = self.c
        self.capture_outside(200)
        self.enter_playback(view=XPAN, xpan=OP["XPAN_YES"])
        c.key_up()
        d, op = c.defer(), c.op()
        self.assertEqual((d["why"], d["defer"], op["outcome"], c.g("op_start_calls")),
                         (OP["DW_NO_SOURCE"], OP["DEFER_WAIT"], OP["VIEW_XPAN"], 0))
        self.assertEqual(c.pending()["pending"], 1, "the screenshot stays pending")
        c.key_up()
        self.assertEqual(c.defer()["why"], OP["DW_HELD"], "no second refusal in the same playback visit")
        self.worker(30)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 2), "the capture's click, then two bips once")
        # leave playback, come back on a 3:2 photo: the deferred screenshot is delivered
        c.mem[GUI] = 0
        self.worker(1)
        c.good(OTHER)
        self.enter_playback(view=OTHER, xpan=OP["XPAN_NO"])
        c.key_up()
        self.assertEqual((c.defer()["why"], c.g("op_start_handle"), c.op()["source"]), (OP["DW_STARTED"], OTHER, OP["SOURCE_VIEW"]))

    def test_refused_view_without_last_good(self):
        for name, view, good, outcome in (("no view", 0, (), OP["NO_VIEW"]),
                                          ("FM refuses the view", OTHER, (VIEW,), OP["VIEW_INVALID"])):
            with self.subTest(name):
                self.setUp()
                c = self.c
                c.good(*good)
                self.capture_outside(200)
                self.enter_playback(view=view)
                c.key_up()
                self.assertEqual((c.defer()["why"], c.op()["outcome"], c.g("op_start_calls")), (OP["DW_NO_SOURCE"], outcome, 0))
                self.assertEqual(c.pending()["pending"], 1)

    def test_leaving_playback_before_the_release_never_starts(self):
        c = self.c
        c.good(VIEW)
        self.capture_outside(200)
        self.enter_playback()
        c.mem[GUI] = 0  # the press left playback; the worker has not ticked yet
        c.key_up()
        self.assertEqual((c.defer()["why"], c.g("op_start_calls"), c.op_log()), (OP["DW_NOT_PLAYBACK"], 0, ""),
                         "GUI mode re-read at the release")
        self.worker(1)
        self.assertEqual(c.defer()["play_since"], 0)
        c.mem[GUI] = H.PLAYBACK
        c.key_up()  # back in playback but the worker has not re-seen it: not settled
        self.assertEqual((c.defer()["why"], c.g("op_start_calls")), (OP["DW_NOT_PLAYBACK"], 0))
        self.assertEqual(c.defer()["defer"], OP["DEFER_WAIT"])

    def test_busy_canon_only_waits(self):
        c = self.c
        c.good(VIEW)
        self.capture_outside(200)
        self.enter_playback()
        busy = {"develop state": lambda v: c.mem.__setitem__(H.GUI_DEVELOP_STATE, v),
                "GUI busy": lambda v: setattr(U.in_dll(self.lib, "op_gui_busy"), "value", v),
                "INDEV busy": lambda v: setattr(U.in_dll(self.lib, "op_indev_busy"), "value", v)}
        for name, set_busy in busy.items():
            with self.subTest(name):
                set_busy(1)
                c.key_up()
                self.assertEqual((c.defer()["why"], c.g("op_start_calls"), c.g("op_fm_calls"), c.op()["refusals"]),
                                 (OP["DW_BUSY"], 0, 0, 0), "no FM call, no start, no bips")
                set_busy(0)
        c.key_up()
        self.assertEqual((c.defer()["why"], c.g("op_start_calls")), (OP["DW_STARTED"], 1))

    def test_a_running_job_and_a_lost_or_old_y22(self):
        c = self.c
        c.good(VIEW)
        pt = self.capture_outside(200)
        self.enter_playback()
        st = H.OnePressState.in_dll(self.lib, "one_press_state")
        st.state = OP["STARTED"]  # a job in progress
        c.key_up()
        self.assertEqual((c.defer()["why"], c.defer()["defer"]), (OP["DW_BUSY"], OP["DEFER_WAIT"]))
        st.state = OP["IDLE"]
        H.InjectPending.in_dll(self.lib, "inject_pending").set_tick = pt + 1  # HWJPG.Y22 now holds another capture
        c.key_up()
        self.assertEqual((c.defer()["why"], c.defer()["defer"], c.g("op_start_calls")), (OP["DW_LOST"], OP["DEFER_NONE"], 0))
        # too old for H3's 10-minute expiry (the job's timeout included)
        self.setUp()
        c = self.c
        c.good(VIEW)
        pt = self.capture_outside(200)
        self.lib.host_set_tick_raw(pt + MAX_AGE - SETTLE)  # + the settle steps = max age + 1 at the release
        self.enter_playback()
        c.key_up()
        self.assertEqual((c.defer()["why"], c.defer()["defer"], c.g("op_start_calls")), (OP["DW_EXPIRED"], OP["DEFER_NONE"], 0))
        self.assertEqual(MAX_AGE + OP["TIMEOUT_TICKS"], H.INJ["PENDING_TICKS"])
        self.assertEqual(MAX_AGE * 20 // 1000, 570, "9.5 min of 20 ms ticks")

    def test_the_oldest_accepted_deferral_still_starts(self):
        c = self.c
        c.good(VIEW)
        pt = self.capture_outside(200)
        self.lib.host_set_tick_raw(pt + MAX_AGE - SETTLE - 1)  # exactly the max age at the release
        self.enter_playback()
        c.key_up()
        self.assertEqual((c.defer()["why"], c.g("op_start_calls")), (OP["DW_STARTED"], 1))

    def test_a_latched_one_press_drops_the_deferral(self):
        c = self.c
        c.good(VIEW)
        self.capture_outside(200)
        self.enter_playback()
        H.OnePressState.in_dll(self.lib, "one_press_state").latched = 1  # after a timeout this boot
        c.key_up()
        self.assertEqual((c.defer()["why"], c.defer()["defer"], c.g("op_start_calls")), (OP["DW_LATCHED"], OP["DEFER_NONE"], 0))

    def test_playback_rate_replaces_a_deferred_capture(self):
        c = self.c
        c.good(VIEW)
        self.capture_outside(200)
        self.enter_playback()
        c.tap(400)  # RATE in playback: the playback path on its own capture
        op = c.op()
        self.assertEqual((op["outcome"], op["gui_mode"], c.g("op_start_calls")), (OP["START"], H.PLAYBACK, 1))
        self.assertEqual((c.defer()["defer"], c.defer()["starts"]), (OP["DEFER_NONE"], 0), "latest only: the deferred one is replaced")
        self.finish()
        c.key_up()
        self.assertEqual(c.g("op_start_calls"), 1)

    def test_a_newer_capture_outside_playback_replaces_the_deferred_one(self):
        c = self.c
        c.good(VIEW)
        self.capture_outside(200)
        pt2 = self.capture_outside(400)
        self.assertEqual((c.defer()["defer"], c.defer()["marked"]), (OP["DEFER_WAIT"], 2))
        self.enter_playback()
        c.key_up()
        self.assertEqual((c.defer()["why"], c.op()["pending_tick"], c.g("op_start_calls")), (OP["DW_STARTED"], pt2, 1))

    def test_playback_rate_on_xpan_uses_the_last_good_source(self):
        c = self.c
        self.last_good_job(200)
        c.view(XPAN)
        c.xpan(OP["XPAN_YES"])
        c.tap(600)
        op = c.op()
        self.assertEqual((op["outcome"], op["source"], c.g("op_start_handle")), (OP["START"], OP["SOURCE_LAST_GOOD"], VIEW))
        self.finish()
        self.worker(30)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (2, 0), "two saved jobs: clicks only")
        self.setUp()  # no last good: two bips, kept pending (instead of a job that cancels with three)
        c = self.c
        c.good(VIEW, XPAN)
        c.playback(handle=XPAN)
        c.xpan(OP["XPAN_YES"])
        c.tap(200)
        self.assertEqual((c.op()["outcome"], c.g("op_start_calls"), c.g("op_fm_calls"), c.pending()["pending"]),
                         (OP["VIEW_XPAN"], 0, 0, 1))
        self.worker(15)
        self.assertEqual((c.feedback()["clicks"], c.feedback()["bips"]), (1, 2))

    def test_a_cancelled_or_last_good_job_is_never_a_last_good_source(self):
        c = self.c
        c.good(VIEW)
        c.playback(handle=VIEW)
        c.tap(200)
        self.finish(save=False)  # the dry run was not S2 3:2: cancelled
        self.assertEqual((c.op()["cancels"], c.defer()["last_good"]), (1, 0))

    def test_unknown_xpan_precheck_checks_the_view_with_fm(self):
        c = self.c
        c.good(VIEW)
        self.capture_outside(200)
        self.enter_playback(xpan=OP["XPAN_UNKNOWN"])
        c.key_up()
        self.assertEqual((c.defer()["why"], c.defer()["xpan"], c.op()["source"], c.fm_seen()),
                         (OP["DW_STARTED"], OP["XPAN_UNKNOWN"], OP["SOURCE_VIEW"], [VIEW | 2]),
                         "no admitted tuple: FM and the dry run decide")

    def test_a_deferred_capture_refused_by_latch_or_without_y22_is_not_marked(self):
        c = self.c
        H.OnePressState.in_dll(self.lib, "one_press_state").latched = 1  # latched after a timeout
        c.mem[GUI] = 0
        c.tap(200)
        self.assertEqual((c.defer()["defer"], c.defer()["marked"]), (OP["DEFER_NONE"], 0))
        self.setUp()
        c = self.c
        U.in_dll(self.lib, "host_conv_fail").value = 1
        c.mem[GUI] = 0
        c.tap(200)
        self.assertEqual((c.pending()["pending"], c.defer()["defer"], c.defer()["marked"]), (0, OP["DEFER_NONE"], 0))
        U.in_dll(self.lib, "host_conv_fail").value = 0

    def test_no_view_resets_the_xpan_precheck(self):
        c = self.c
        self.capture_outside(200)
        self.enter_playback(view=XPAN, xpan=OP["XPAN_YES"])
        c.key_up()
        self.assertEqual(c.defer()["xpan"], OP["XPAN_YES"])
        c.mem[GUI] = 0
        self.worker(1)
        self.enter_playback(view=0, xpan=OP["XPAN_YES"])
        c.key_up()
        self.assertEqual((c.defer()["why"], c.defer()["xpan"]), (OP["DW_NO_SOURCE"], OP["XPAN_UNKNOWN"]), "no view: not checked")


if __name__ == "__main__":
    unittest.main()
