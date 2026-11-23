"""Host tests for the automatic INFO and the S4 guard of screenshot anywhere (--screenshot rate).

While a screenshot taken outside playback waits, the worker posts one INFO press through
Canon's GUI event post E04A787A(0, 14, 0, 1) once playback has been stable for 2 s and no
class-0 event went through the H2 hook for 0.5 s (with every one_press_key precheck,
Canon's UiOpe key handler and the GUI queue clear), then the release (0, 15, 0, 1) at least
5 worker ticks later into a quiet GUI queue (after a 1 s wait, any queue below the post
limit), with a verdict: OK only when not delayed, for the same visit and mode, no S4 sign,
a quiet queue, idle and not latched, [0x92D4] == E08ABFF5, our press seen by the hook and
no user key since it (drained or not). A held verdict holds the whole playback visit: no
INFO release starts a job there, and the next visit delivers. One release of ours is out
at a time. A user key after playback entry (any button, the joystick / direction-key codes
118..136, lost hook records; only the playback press on the entry tick is exempt)
cancels the post; INFO by hand then works as before. The worker claims the deferral
before posting, so an INFO by hand that consumed it first wins. A playback RATE's kept
deferral is never posted for; one post per deferral. The S4 guard makes INFO (posted or
by hand) wait in a playback visit with any sign of a GUI queue clear: drained, not
drained yet, pending, or [0x5474] changed since the visit began.

The actual combo_observe.c, combo_capture.c, hw_jpeg_inject.c, y22_stream.c,
picture_layer.c and one_press.c run against the fakes of screenshot_host.py; posted
events are dispatched by the test through the H2 hook's ring, as GuiMainTask would. No
ROM, camera, card or ARM toolchain; synthetic inputs only.

Mutation check: python3 -B tests/test_screenshot_auto_info.py --mutations builds each
mutation below from a scratch copy of src/experimental/xpan (the tree is only read). A
mutation counts as caught only when a test assertion fails; a mutant that does not
compile or import is reported as invalid, not caught. The unmutated copy must pass.
"""
from pathlib import Path
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshot_host as H  # noqa: E402

_MUTANT = os.environ.get("R5_AUTO_INFO_MUTANT_SRC")  # --mutations only: a scratch copy of src/experimental/xpan
if _MUTANT:
    H.XPAN_SRC = Path(_MUTANT)

U, OP = H.U, H.OP
GUI = H.GUI_MODE_WORD
VIEW = H.PHOTO
XPAN = (2 << 30) | (1 << 28) | (100 << 18) | (14 << 4)
OTHER = (2 << 30) | (1 << 28) | (100 << 18) | (15 << 4)
LIVE_VIEW, MENU_MODE = 0x0, 0x2003
INFO, INFO_UP, PLAY, MENU, CROSS_RIGHT = OP["INFO_PRESS"], OP["INFO_RELEASE"], 18, 12, 60
HOOK_PASSES = (H.TRIGGER, H.TRIGGER + 1, H.INFO_UP)  # combo_observe_hook.S
UIOPE, POST_DIRECT, KEYS_KEPT = OP["UIOPE_KEY_HANDLER"], OP["UIOPE_POST_DIRECT"], 0xE08ABE5D
QUEUE = OP["GUI_QUEUE"]
SETTLE, ASETTLE, AQUIET = OP["DEFER_SETTLE_TICKS"], OP["AUTO_SETTLE_TICKS"], OP["AUTO_QUIET_TICKS"]
ARELEASE, RWAIT = OP["AUTO_RELEASE_TICKS"], OP["AUTO_RELEASE_WAIT_TICKS"]
MAX_AGE = H.INJ["PENDING_TICKS"] - OP["TIMEOUT_TICKS"]
PRESS, RELEASE = (0, INFO, 0, 1), (0, INFO_UP, 0, 1)
GEN_UNEQUAL = 1 << 24       # COMBO_F_GEN_UNEQUAL in the hook record's context word
CLASS_NONZERO = 32 << 24    # COMBO_F_CLASS_NONZERO
AI = {k[3:]: v for k, v in OP.items() if k.startswith("AI_")}
LIMIT, CAPACITY = OP["GUI_QUEUE_POST_LIMIT"], OP["GUI_QUEUE_CAPACITY"]
DEVELOP = H.GUI_DEVELOP_STATE
DW = {k[3:]: v for k, v in OP.items() if k.startswith("DW_")}


class Camera(H.CaptureCamera):
    """CaptureCamera plus the H2 hook's ring and the GUI post (E04A787A) the worker calls."""

    def __init__(self, lib):
        super().__init__(lib, False)
        self.dispatched = 0
        self.mem[UIOPE] = POST_DIRECT  # UiOpe mode 2: keys are posted directly
        self.mem[QUEUE] = self.mem[QUEUE + 8] = 7
        self.mem[QUEUE + 4] = 0

    def posts(self):
        n = min(self.g("op_post_calls"), 16)
        a = ctypes.addressof(U.in_dll(self.lib, "op_posts"))
        return [tuple(U.from_address(a + 16 * i + 4 * j).value for j in range(4)) for i in range(n)]

    def auto(self):
        return self.g("one_press_auto")

    def held(self):
        """The current playback visit is held (an automatic release failed its verdict)."""
        return self.defer()["play_since"] != 0 and self.g("one_press_held_visit") == self.defer()["play_since"]

    def post_depths(self):
        return list((U * 16).in_dll(self.lib, "op_post_depth"))[:min(self.g("op_post_calls"), 16)]

    def record(self, code, arg3=1, context=0, tick=None):
        """The H2 hook's ring record of one class-0 event (stamped with the current worker tick, or `tick`)."""
        ring = ctypes.addressof(U.in_dll(self.lib, "combo_hook_ring"))
        seq = (U.from_address(ring + 1024).value + 1) & 0xFFFFFFFF
        tick = self.g("combo_worker_tick") if tick is None else tick
        e = ring + 16 * ((seq - 1) & 63)
        for i, v in ((1, code | (tick & 0xFFFF) << 16), (2, arg3 << 16), (3, context), (0, seq)):
            U.from_address(e + 4 * i).value = v
        U.from_address(ring + 1024).value = seq

    def hook(self, code, arg3=1):
        """GuiMainTask dispatching one class-0 event: the ring record, then combo_capture_gui for the codes
        the hook passes on."""
        self.record(code, arg3)
        if code in HOOK_PASSES:
            self.lib.combo_capture_gui((U * 4)(0, code, 0, arg3))

    def clear_record(self, code=166):
        """A class-0 event the hook recorded while a GUI queue clear was pending."""
        self.record(code, 1, GEN_UNEQUAL | 8 << 8 | 7)

    def dispatch(self):
        """GuiMainTask receiving the events the worker posted (Canon's queue is FIFO)."""
        for cls, code, a2, a3 in self.posts()[self.dispatched:]:
            assert (cls, a2) == (0, 0)
            self.hook(code, a3)
            self.dispatched += 1

    def info_by_hand(self):
        self.hook(INFO)
        self.hook(INFO_UP)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = H.capture_lib(False)

    def setUp(self):
        self.c = Camera(self.lib)
        self.c.lcd(data=H.DATA)
        self.c.display(out=0)
        self.worker(1)

    def worker(self, n):
        self.c.step(n)

    def tick(self):
        return self.c.g("combo_worker_tick")

    def finish(self):
        self.lib.one_press_dry(1, 2400, 1600)
        self.c.done(0x302CDCD0)
        self.c.end(0)
        self.assertEqual(self.c.op()["state"], OP["IDLE"])
        self.c.guard().develop = 0

    def capture_outside(self, tick=200, gui=LIVE_VIEW):
        c = self.c
        c.mem[GUI] = gui
        c.tap(tick)
        self.assertEqual((c.op()["outcome"], c.pending()["pending"], c.defer()["defer"]), (OP["NOT_PLAYBACK"], 1, OP["DEFER_WAIT"]))
        return c.op()["pending_tick"]

    def enter(self, view=VIEW, xpan=OP["XPAN_NO"], ticks=1, good=True):
        """The user's playback press (live view), then playback on `view` for `ticks` worker steps."""
        c = self.c
        if good:
            c.good(view)
        c.hook(PLAY)
        c.playback(handle=view)
        c.xpan(xpan)
        self.worker(ticks)
        return c.defer()["play_since"]

    def leave(self):
        self.c.mem[GUI] = LIVE_VIEW
        self.worker(3)

    def until_press(self, limit=400):
        n = len(self.c.posts())
        for _ in range(limit):
            if len(self.c.posts()) > n:
                return self.tick()
            self.worker(1)
        self.fail("no press posted")

    def to_release(self, seen=True):
        """Capture outside, playback, our press; dispatched (seen) or not; returns the press tick."""
        self.capture_outside()
        self.enter()
        press = self.until_press()
        self.assertEqual(self.c.posts(), [PRESS])
        if seen:
            self.c.dispatch()
        return press

    def verdict(self):
        """The release, ARELEASE ticks after the press into a quiet queue: posted; returns "held" or "ok"."""
        self.worker(ARELEASE)
        self.assertEqual(self.c.posts(), [PRESS, RELEASE], "the release is posted")
        self.assertEqual((self.c.g("host_post_asserts"), self.c.auto()), (0, OP["AUTO_RELEASED"]))
        return "held" if self.c.held() else "ok"

    def assert_no_post(self, why, msg=None):
        c, d = self.c, self.c.defer()
        self.assertEqual((c.posts(), d["auto_why"], d["defer"], c.g("op_start_calls")), ([], why, OP["DEFER_WAIT"], 0), msg)

    def assert_started(self, handle=VIEW):
        self.assertEqual((self.c.defer()["why"], self.c.g("op_start_calls"), self.c.g("op_start_handle")), (DW["STARTED"], 1, handle))

    def assert_held_visit(self):
        """Our held release and INFO by hand in this visit start nothing; the next visit delivers."""
        c = self.c
        fm = c.g("op_fm_calls")
        c.dispatch()
        self.assertEqual(c.g("op_fm_calls"), fm, "a held visit is refused before any Resize check")
        for who in ("our held release", "INFO by hand in the held visit"):
            d = c.defer()
            self.assertEqual((d["why"], d["defer"], c.g("op_start_calls"), c.op()["refusals"]), (DW["AUTO_HELD"], OP["DEFER_WAIT"], 0, 0),
                             who + " starts nothing: no job, no bips, the deferral kept")
            c.info_by_hand()
        self.leave()
        self.enter(ticks=SETTLE + 1)
        c.info_by_hand()
        self.assert_started()


class AutoPost(Base):
    def test_press_after_2_s_release_5_ticks_later_and_the_release_starts_the_job(self):
        c = self.c
        pt = self.capture_outside()
        self.worker(50)
        self.assert_no_post(AI["NOT_PLAYBACK"], "live view: no post")
        since = self.enter()
        self.worker(ASETTLE - 1)
        self.assert_no_post(AI["SETTLING"], "1.98 s of playback: no post")
        self.worker(1)
        d = c.defer()
        self.assertEqual((c.posts(), self.tick() - since), ([PRESS], ASETTLE), "exactly Canon's INFO key press (E08ABFF4)")
        self.assertEqual((d["auto_why"], d["auto_for"], d["marked"], d["auto_gui"], d["auto_posts"], d["auto_ticks"]),
                         (AI["POSTED"], 1, 1, H.PLAYBACK, 1, ASETTLE))
        self.assertEqual((c.auto() & 3, c.auto() >> 4), (OP["AUTO_PRESSED"], self.tick()))
        c.dispatch()
        self.worker(1)
        self.assertEqual(c.auto() & 0xF, OP["AUTO_PRESSED"] | OP["AUTO_SEEN"], "the hook saw our press")
        self.worker(ARELEASE - 2)
        self.assertEqual(c.posts(), [PRESS], "no release before ARELEASE ticks")
        self.worker(1)
        self.assertEqual((c.posts(), c.held(), c.auto()), ([PRESS, RELEASE], False, OP["AUTO_RELEASED"]))
        self.assertEqual((c.g("op_start_calls"), c.op_log()), (0, "PP"), "the worker only posts; it never starts the job")
        c.dispatch()
        op = c.op()
        self.assertEqual(c.op_log(), "PPFRGIS", "the posted INFO release: FM on the view, then Canon's order")
        self.assert_started()
        self.assertEqual((op["pending_tick"], c.defer()["defer"]), (pt, OP["DEFER_NONE"]))
        self.worker(1)
        self.assertEqual(c.auto(), 0, "the drain saw our release: no release of ours is out")
        self.finish()
        self.worker(300)
        self.assertEqual((len(c.posts()), c.op()["saves"], c.feedback()["bips"], c.g("host_post_asserts"), c.post_depths()), (2, 1, 0, 0, [0, 0]),
                         "one post per deferral; saved; into a quiet queue")

    def test_info_by_hand_keeps_its_half_second_settle(self):
        c = self.c
        self.capture_outside()
        self.enter(ticks=5)
        c.info_by_hand()
        self.assertEqual((c.defer()["why"], c.g("op_start_calls")), (DW["SETTLING"], 0))
        self.worker(SETTLE)
        c.info_by_hand()
        self.assert_started()
        self.assertEqual((c.posts(), c.defer()["auto_why"]), ([], AI["KEY"]), "the user's INFO press cancelled the post")

    def test_never_posts_outside_playback(self):
        c = self.c
        self.capture_outside(gui=MENU_MODE)
        for gui in (LIVE_VIEW, MENU_MODE, 0x4035, 0x2001):
            c.mem[GUI] = gui
            self.worker(ASETTLE * 2)
            self.assert_no_post(AI["NOT_PLAYBACK"], hex(gui))

    def test_a_playback_flicker_restarts_the_settle(self):
        c = self.c
        self.capture_outside()
        self.enter(ticks=ASETTLE - 1)
        c.mem[GUI] = MENU_MODE
        self.worker(1)
        c.mem[GUI] = H.PLAYBACK
        self.worker(ASETTLE)
        self.assert_no_post(AI["SETTLING"])
        self.worker(1)
        self.assertEqual(c.posts(), [PRESS])

    def test_the_gui_must_be_quiet_for_half_a_second(self):
        """Canon's playback-entry events late in the settle delay the press to AQUIET ticks after the latest record."""
        c = self.c
        self.capture_outside()
        since = self.enter()
        self.worker(ASETTLE - 10)
        c.record(167)
        self.worker(1)
        c.record(0)
        last = self.tick()
        self.worker(AQUIET - 1)
        self.assert_no_post(AI["GUI_EVENTS"])
        press = self.until_press()
        self.assertEqual((press - last, c.posts()), (AQUIET, [PRESS]))
        self.assertGreaterEqual(press - since, ASETTLE)

    def test_prechecks_only_wait(self):
        c = self.c
        self.capture_outside()
        lib = self.lib
        cases = (("our job", lambda v: setattr(H.OnePressState.in_dll(lib, "one_press_state"), "state", OP["STARTED"] if v else OP["IDLE"]), AI["BUSY"]),
                 ("develop state", lambda v: c.mem.__setitem__(H.GUI_DEVELOP_STATE, v), AI["CANON_BUSY"]),
                 ("GUI busy", lambda v: setattr(U.in_dll(lib, "op_gui_busy"), "value", v), AI["CANON_BUSY"]),
                 ("INDEV busy", lambda v: setattr(U.in_dll(lib, "op_indev_busy"), "value", v), AI["CANON_BUSY"]),
                 ("UiOpe keeps keys", lambda v: c.mem.__setitem__(UIOPE, KEYS_KEPT if v else POST_DIRECT), AI["KEYS_HELD"]),
                 ("queue clear pending", lambda v: c.mem.__setitem__(QUEUE + 8, 8 if v else 7), AI["QUEUE_BUSY"]),
                 ("queue depth 5", lambda v: c.mem.__setitem__(QUEUE + 4, 5 if v else 4), AI["QUEUE_BUSY"]),
                 ("queue depth wrapped", lambda v: c.mem.__setitem__(QUEUE + 4, 0xFFFFFFFF if v else 4), AI["QUEUE_BUSY"]))
        self.enter()
        for name, spoil, why in cases:
            with self.subTest(name):
                spoil(1)
                self.worker(ASETTLE)
                self.assert_no_post(why, name)
                spoil(0)
        self.worker(1)
        self.assertEqual((c.posts(), c.mem[QUEUE + 4]), ([PRESS], 4), "depth 4 still posts")

    def test_latched_lost_expired_and_held_never_post(self):
        def latched():
            H.OnePressState.in_dll(self.lib, "one_press_state").latched = 1

        def lost():
            H.InjectPending.in_dll(self.lib, "inject_pending").set_tick += 1  # HWJPG.Y22 now holds another capture

        def held():
            H.OnePressDefer.in_dll(self.lib, "one_press_defer").held_since = self.c.defer()["play_since"]
        for name, spoil, why in (("latched", latched, AI["LATCHED"]), ("lost", lost, AI["LOST"]), ("held", held, AI["HELD"])):
            with self.subTest(name):
                self.setUp()
                self.capture_outside()
                self.enter()
                spoil()
                self.worker(ASETTLE * 2)
                self.assert_no_post(why, name)
        with self.subTest("expired"):
            self.setUp()
            pt = self.capture_outside()
            self.lib.host_set_tick_raw(pt + MAX_AGE - ASETTLE)
            self.enter(ticks=ASETTLE + 1)
            self.assert_no_post(AI["EXPIRED"])

    def test_the_oldest_accepted_deferral_still_posts(self):
        pt = self.capture_outside()
        self.lib.host_set_tick_raw(pt + MAX_AGE - ASETTLE - 1)
        self.enter(ticks=ASETTLE + 1)
        self.assertEqual(self.c.posts(), [PRESS])

    # ---- the user first ------------------------------------------------------------------------
    def test_a_user_key_after_entry_cancels_the_post_and_info_by_hand_starts(self):
        def lost(c):
            for _ in range(70):  # the ring laps before the worker drains it
                c.record(200)
        for name, act in (("cross right", lambda c: c.record(CROSS_RIGHT)), ("MENU", lambda c: c.record(MENU)),
                          ("playback again", lambda c: c.record(PLAY)), ("an INFO press", lambda c: c.record(INFO)),
                          ("direction key state 0 (118)", lambda c: c.record(118)), ("direction key 119", lambda c: c.record(119)),
                          ("multi-controller 129", lambda c: c.record(129)), ("multi-controller 136", lambda c: c.record(136)),
                          ("lost hook records", lost)):
            with self.subTest(name):
                self.setUp()
                c = self.c
                self.capture_outside()
                self.enter(ticks=10)
                act(c)
                self.worker(1)
                d = c.defer()
                self.assertEqual((d["auto_why"], d["auto_for"], d["marked"]), (AI["KEY"], 1, 1))
                self.worker(ASETTLE * 2)
                self.assert_no_post(AI["KEY"], name)
                c.info_by_hand()
                self.assert_started()

    def test_codes_outside_the_joystick_range_and_class_nonzero_records_do_not_cancel(self):
        for name, code, context in (("code 117", 117, 0), ("a Canon event 167", 167, 0), ("class-nonzero 128", 128, CLASS_NONZERO)):
            with self.subTest(name):
                self.setUp()
                c = self.c
                self.capture_outside()
                self.enter(ticks=3)
                c.record(code, 1, context)
                self.worker(ASETTLE + AQUIET)
                self.assertEqual((c.posts()[:1], c.defer()["auto_posts"]), ([PRESS], 1), "not cancelled")

    def test_keys_before_playback_and_the_playback_press_do_not_count(self):
        c = self.c
        self.capture_outside()
        for code in (CROSS_RIGHT, MENU, MENU + 1, INFO, INFO_UP, 129):  # live-view keys while the screenshot waits
            c.hook(code)
            self.worker(3)
        c.hook(PLAY)
        c.good(VIEW)
        c.playback(handle=VIEW)
        self.worker(1)  # play_since = this tick
        c.record(PLAY)  # stamped with the visit's first tick: the entering press, not counted
        self.worker(1)
        c.record(PLAY + 1)  # its release
        for code in (166, 183, 167, 0):  # Canon's own class-0 events around playback entry
            c.record(code)
        self.worker(ASETTLE)
        self.assertEqual((c.posts(), c.defer()["auto_why"]), ([PRESS], AI["POSTED"]))

    # ---- one post per deferral -------------------------------------------------------------------
    def test_one_post_per_deferral_even_if_never_dispatched(self):
        c = self.c
        self.to_release(seen=False)
        self.worker(ARELEASE)
        self.leave()
        self.enter(ticks=ASETTLE * 3)
        d = c.defer()
        self.assertEqual((len(c.posts()), d["defer"], d["auto_why"], d["auto_posts"]), (2, OP["DEFER_WAIT"], AI["RELEASE_HELD"], 1))
        c.info_by_hand()  # from another slot: an INFO by hand, judged on its own
        self.assert_started()
        self.finish()
        self.leave()
        self.capture_outside(1000)  # a new deferral gets its own post
        self.enter()
        self.until_press()
        d = c.defer()
        self.assertEqual((len(c.posts()), d["auto_posts"], d["auto_for"], d["marked"]), (3, 2, 2, 2))

    def test_a_kept_playback_deferral_is_never_posted_for(self):
        """A playback RATE on an XPan photo without a last good photo is refused at its release (two bips) and
        kept; no automatic INFO for it, in this visit or a later one; INFO by hand on a 3:2 photo delivers it."""
        c = self.c
        c.good(OTHER)
        c.playback(handle=XPAN)
        c.xpan(OP["XPAN_YES"])
        self.worker(SETTLE + 1)
        c.tap(400)
        pt = c.op()["pending_tick"]
        self.assertEqual((c.op()["outcome"], c.defer()["defer"], c.defer()["marked"]), (OP["VIEW_XPAN"], OP["DEFER_WAIT"], 1))
        self.worker(ASETTLE * 2)
        self.assert_no_post(AI["KEPT"], "this visit")
        self.leave()
        self.enter(view=XPAN, xpan=OP["XPAN_YES"], ticks=ASETTLE * 2, good=False)
        self.assert_no_post(AI["KEPT"], "a later visit")
        c.view(OTHER)
        c.xpan(OP["XPAN_NO"])
        c.info_by_hand()
        self.assert_started(OTHER)
        self.assertEqual(c.op()["pending_tick"], pt)

    def test_a_refused_automatic_start_bips_once_and_waits(self):
        """The posted INFO meets an XPan photo without a last good photo: two bips once, the screenshot waits,
        no second post for this deferral."""
        c = self.c
        self.capture_outside()
        c.good()
        self.enter(view=XPAN, xpan=OP["XPAN_YES"], good=False)
        self.until_press()
        c.dispatch()
        self.worker(ARELEASE)
        c.dispatch()
        d = c.defer()
        self.assertEqual((d["why"], d["defer"], c.op()["refusals"], c.g("op_start_calls")), (DW["NO_SOURCE"], OP["DEFER_WAIT"], 1, 0))
        self.worker(ASETTLE * 3)
        self.assertEqual((len(c.posts()), c.feedback()["bips"]), (2, 2))


    def test_a_key_or_clear_hooked_before_the_visit_is_published_belongs_to_the_visit(self):
        """Review 2 P2#3: the worker publishes the visit before the drain on the entry tick."""
        for name in ("joystick", "clear"):
            with self.subTest(name):
                self.setUp()
                c = self.c
                self.capture_outside()
                c.good(VIEW)
                c.playback(handle=VIEW)
                c.xpan(OP["XPAN_NO"])
                entry = self.tick() + 1  # the tick the next worker step publishes
                if name == "joystick":
                    c.record(129, tick=entry)
                else:
                    c.mem[QUEUE] = c.mem[QUEUE + 8] = 8  # requested and finished before the step
                    c.record(166, 1, GEN_UNEQUAL | 8 << 8 | 7, tick=entry)
                self.worker(1)
                self.assertEqual(c.defer()["play_since"], entry)
                self.worker(ASETTLE * 2)
                self.assert_no_post(AI["KEY"] if name == "joystick" else AI["LEAVING"], name)

    def test_only_the_playback_press_is_exempt_on_the_entry_tick(self):
        """Review #5: a joystick move stamped with the visit's first tick cancels the post."""
        c = self.c
        self.capture_outside()
        since = self.enter()
        self.assertEqual(self.tick(), since)
        c.record(129)  # the same tick as the visit's first
        self.worker(ASETTLE * 2)
        self.assert_no_post(AI["KEY"])

    def test_an_info_by_hand_that_consumed_the_deferral_first_wins(self):
        """Review #6: GuiMainTask starts the job between the worker's last check and its claim: no post."""
        c = self.c
        self.capture_outside()
        self.enter(ticks=ASETTLE)
        fired = []

        def interleave():
            if not fired:
                fired.append(1)
                c.info_by_hand()
        cb = ctypes.CFUNCTYPE(None)(interleave)
        self.lib.host_set_before_claim(cb)
        try:
            self.worker(1)
        finally:
            self.lib.host_set_before_claim(None)
        self.assertEqual(fired, [1])
        self.assert_started()
        self.assertEqual((c.posts(), c.defer()["auto_why"], c.defer()["auto_posts"]), ([], AI["CONSUMED"], 0))


class ReleaseVerdict(Base):
    def test_ok_needs_our_press_seen_by_the_hook(self):
        self.to_release(seen=False)
        self.assertEqual(self.verdict(), "held", "never dispatched: held")
        self.assertEqual(self.c.defer()["auto_why"], AI["RELEASE_HELD"])
        self.assert_held_visit()

    def test_a_user_key_in_the_gap_holds_the_visit(self):
        def lost(c):
            for _ in range(70):
                c.record(200)
        for name, act in (("playback", lambda c: c.record(PLAY)), ("MENU", lambda c: c.record(MENU)),
                          ("joystick 129", lambda c: c.record(129)), ("direction 119", lambda c: c.record(119)),
                          ("direction state 0 (118)", lambda c: c.record(118)), ("a second INFO press", lambda c: c.record(INFO)),
                          ("lost records", lost)):
            with self.subTest(name):
                self.setUp()
                c = self.c
                self.to_release()
                self.worker(1)
                act(c)
                self.worker(1)
                self.assertTrue(c.auto() & OP["AUTO_USER_KEY"])
                self.assertEqual(self.verdict(), "held")
                self.assert_held_visit()

    def test_a_code_below_the_joystick_range_in_the_gap_does_not_hold(self):
        c = self.c
        self.to_release()
        self.worker(1)
        c.record(117)
        self.assertEqual(self.verdict(), "ok")

    def test_a_failed_recheck_holds_the_visit(self):
        lib = self.lib

        def s4(c):
            U.in_dll(lib, "one_press_clear_visit").value = c.defer()["play_since"]

        def gen(c):
            c.mem[QUEUE] = c.mem[QUEUE + 8] = 8  # a clear requested and finished since the visit began

        def job(c):
            H.OnePressState.in_dll(lib, "one_press_state").state = OP["STARTED"]
        cases = (("UiOpe keeps keys", lambda c: c.mem.__setitem__(UIOPE, KEYS_KEPT), lambda c: c.mem.__setitem__(UIOPE, POST_DIRECT)),
                 ("playback left", lambda c: c.mem.__setitem__(GUI, MENU_MODE), None),
                 ("queue clear in this visit", s4, None),
                 ("queue clear generation changed", gen, None),
                 ("latched", lambda c: setattr(H.OnePressState.in_dll(lib, "one_press_state"), "latched", 1), None),
                 ("a job running", job, None))
        for name, spoil, undo in cases:
            with self.subTest(name):
                self.setUp()
                c = self.c
                self.to_release()
                spoil(c)
                self.worker(ARELEASE)
                self.assertEqual((c.posts(), c.defer()["auto_why"]), ([PRESS, RELEASE], AI["RELEASE_HELD"]), name)
                if undo:
                    self.assertTrue(c.held())
                    undo(c)
                    self.assert_held_visit()

    def test_a_visit_newer_than_the_press_is_held(self):
        """Playback left and re-entered between the press and the release."""
        c = self.c
        self.to_release()
        c.mem[GUI] = LIVE_VIEW
        self.worker(1)
        c.mem[GUI] = H.PLAYBACK
        self.assertEqual(self.verdict(), "held")

    def test_info_by_hand_between_press_and_release(self):
        """It starts the job and leaves our pending press alone; our release still follows and starts nothing more."""
        c = self.c
        self.to_release(seen=False)
        c.hook(INFO_UP)
        self.assert_started()
        self.assertEqual(c.auto() & 3, OP["AUTO_PRESSED"], "the press is still pending")
        self.worker(ARELEASE)
        self.assertEqual(c.posts(), [PRESS, RELEASE])
        c.dispatch()
        self.assertEqual(c.g("op_start_calls"), 1, "no second job")

    def test_a_held_release_dispatched_late_still_starts_nothing(self):
        """No time limit: Canon dispatches our held release 3 s after it was posted."""
        c = self.c
        self.to_release()
        c.record(MENU)  # a key in the gap
        self.assertEqual(self.verdict(), "held")
        self.worker(150)
        self.assert_held_visit()

    def test_one_release_of_ours_at_a_time(self):
        """While our release is out (not yet seen by the hook) no press is posted, even for an eligible deferral."""
        c = self.c
        self.to_release()
        self.assertEqual(self.verdict(), "ok")
        H.OnePressDefer.in_dll(self.lib, "one_press_defer").auto_for = 0  # as if a new deferral were waiting
        self.worker(ASETTLE * 2)
        self.assertEqual((c.posts(), c.auto()), ([PRESS, RELEASE], OP["AUTO_RELEASED"]), "no press while ours is out")
        c.dispatch()  # GuiMainTask dispatches our release (it starts the job)
        self.assertEqual(c.g("op_start_calls"), 1)
        self.worker(1)
        self.assertEqual(c.auto(), 0, "seen by the hook: no release of ours is out")

    def test_no_press_in_a_held_visit(self):
        c = self.c
        self.to_release(seen=False)
        self.assertEqual(self.verdict(), "held")
        c.dispatch()
        self.worker(1)
        self.assertEqual(c.auto(), 0)
        H.OnePressDefer.in_dll(self.lib, "one_press_defer").auto_for = 0  # as if a new deferral were waiting
        self.worker(ASETTLE * 2)
        self.assertEqual((len(c.posts()), c.defer()["auto_why"]), (2, AI["HELD"]))

    def test_leaving_playback_ends_the_wait_for_our_release(self):
        c = self.c
        self.to_release(seen=False)
        self.assertEqual(self.verdict(), "held")
        self.leave()
        self.assertEqual(c.auto(), 0)

    # ---- the release queue (review 2 P1#2) ------------------------------------------------------
    def test_the_release_waits_1_s_for_a_quiet_queue_then_goes_held(self):
        c = self.c
        press = self.to_release()
        c.mem[QUEUE + 4] = 5
        self.worker(ARELEASE + RWAIT - 1)
        self.assertEqual((c.posts(), c.defer()["auto_why"]), ([PRESS], AI["RELEASE_WAIT"]), "waits for a quiet queue")
        self.worker(1)
        self.assertEqual((c.posts(), self.tick() - press, c.post_depths()[-1], c.held(), c.g("host_post_asserts")),
                         ([PRESS, RELEASE], ARELEASE + RWAIT, 5, True, 0), "bounded: after 1 s it goes, held")
        c.mem[QUEUE + 4] = 0
        self.assert_held_visit()

    def test_never_posted_at_or_above_the_post_limit(self):
        c = self.c
        self.to_release()
        c.mem[QUEUE + 4] = LIMIT
        self.worker(ARELEASE + RWAIT * 3)
        self.assertEqual((c.posts(), c.defer()["auto_why"]), ([PRESS], AI["RELEASE_WAIT"]))
        c.mem[QUEUE + 4] = LIMIT - 1
        self.worker(1)
        self.assertEqual((c.posts(), c.post_depths()[-1], c.held(), c.g("host_post_asserts")), ([PRESS, RELEASE], LIMIT - 1, True, 0))
        self.assertLess(LIMIT, CAPACITY)

    def test_after_the_bound_a_pending_clear_does_not_block_the_release(self):
        c = self.c
        press = self.to_release()
        c.mem[QUEUE + 8] = 8  # a clear stays pending; depth 0
        self.worker(ARELEASE + RWAIT - 1)
        self.assertEqual(c.posts(), [PRESS])
        self.worker(1)
        self.assertEqual((c.posts(), self.tick() - press, c.held()), ([PRESS, RELEASE], ARELEASE + RWAIT, True))

    def test_a_short_busy_spell_delays_and_holds(self):
        c = self.c
        self.to_release()
        c.mem[QUEUE + 4] = 5
        self.worker(ARELEASE)
        c.mem[QUEUE + 4] = 0
        self.worker(1)
        self.assertEqual((c.posts(), c.held()), ([PRESS, RELEASE], True))

    # ---- keys and events the drain has not handled ------------------------------------------------
    def test_a_key_recorded_after_an_ok_verdict_holds_the_visit(self):
        for drained in (False, True):
            with self.subTest("drained" if drained else "not drained yet"):
                self.setUp()
                c = self.c
                self.to_release()
                self.assertEqual(self.verdict(), "ok")
                c.record(129)  # the joystick, after the verdict
                if drained:
                    self.worker(1)
                    self.assertTrue(c.held(), "the drain holds the visit")
                self.assert_held_visit()

    def test_a_hold_published_during_the_info_dispatch_is_seen_before_the_start(self):
        """Review 3 P2#1: GuiMainTask has read the old hold; the worker then drains a joystick (holding the visit)
        and our already-hooked release (retiring it) before GuiMainTask reads the release state. The re-check
        after the claim gives the deferral back."""
        c = self.c
        self.to_release()
        self.assertEqual(self.verdict(), "ok")
        c.record(129)  # the joystick, hooked just before our release
        ran = []

        def worker_between_loads():
            if not ran:
                ran.append(1)
                self.worker(1)
        cb = ctypes.CFUNCTYPE(None)(worker_between_loads)
        self.lib.host_set_in_key(cb)
        try:
            c.dispatch()
        finally:
            self.lib.host_set_in_key(None)
        d = c.defer()
        self.assertEqual((ran, c.held(), c.auto()), ([1], True, 0), "held and retired between the two loads")
        self.assertEqual((d["why"], d["defer"], c.g("op_start_calls"), c.op()["refusals"]), (DW["AUTO_HELD"], OP["DEFER_WAIT"], 0, 0))
        self.leave()
        self.enter(ticks=SETTLE + 1)
        c.info_by_hand()
        self.assert_started()

    def test_an_event_not_drained_at_the_press_tick_stops_the_post(self):
        for name, code, why in (("joystick", 129, AI["KEY"]), ("a Canon event", 167, AI["POSTED"])):
            with self.subTest(name):
                self.setUp()
                c = self.c
                self.capture_outside()
                self.enter(ticks=ASETTLE)
                c.record(code)  # after this tick's drain
                self.lib.one_press_step(self.tick() + 1)  # the worker's decision at the press tick, without a new drain
                self.assert_no_post(AI["GUI_EVENTS"], name)
                self.worker(AQUIET + 1)
                self.assertEqual(c.defer()["auto_why"], why, "the quiet period restarts from that event")

    def test_a_lapped_or_torn_scan_fails_closed(self):
        """64 undrained records: the oldest slot may be being rewritten (the drain's torn rule); 70: lapped."""
        for n in (64, 70):
            with self.subTest(n):
                self.setUp()
                c = self.c
                self.capture_outside()
                self.enter(ticks=ASETTLE)
                for _ in range(n):
                    c.record(167)
                self.lib.one_press_step(self.tick() + 1)
                self.assert_no_post(AI["LEAVING"], "every sign reported, a clear included")

    def test_hook_sequence_zero_after_wrap_is_a_real_record(self):
        """Review 2 P3: a joystick at hook seq 0 (after 2^32 records) is drained, not skipped."""
        c = self.c
        state = ctypes.addressof(U.in_dll(self.lib, "combo_observe_state"))
        ring = ctypes.addressof(U.in_dll(self.lib, "combo_hook_ring"))
        U.from_address(ring + 1024).value = 0xFFFFFFBE  # 2^32 - 66 records so far, all drained
        U.from_address(state + 8).value = 0xFFFFFFBF
        self.capture_outside()
        for _ in range(64):  # fill every slot with a real sequence number before the wrap
            c.record(167)
            self.worker(1)
        self.enter(ticks=10)  # its playback press is seq 0xFFFFFFFF
        self.assertEqual((U.from_address(ring + 1024).value, U.from_address(state + 8).value), (0xFFFFFFFF, 0))
        c.record(129)  # seq 0
        self.worker(1)
        self.assertEqual(c.defer()["auto_why"], AI["KEY"])


class S4(Base):
    def setUp(self):
        super().setUp()
        self.c.mem[UIOPE] = KEYS_KEPT  # no automatic post: INFO by hand alone

    def assert_waiting(self, msg):
        c, d = self.c, self.c.defer()
        self.assertEqual((d["why"], d["defer"], c.g("op_start_calls"), c.op()["refusals"]), (DW["NOT_PLAYBACK"], OP["DEFER_WAIT"], 0, 0),
                         msg + ": no job, no bips, kept")

    def test_a_queue_clear_in_this_visit_makes_info_wait_until_the_next_visit(self):
        c = self.c
        self.capture_outside()
        self.enter(ticks=SETTLE + 1)
        c.clear_record()
        self.worker(1)
        self.assertEqual(c.g("one_press_clear_visit"), c.defer()["play_since"], "the visit is marked")
        c.info_by_hand()
        self.assert_waiting("INFO after a queue clear in this visit")
        self.worker(SETTLE * 4)
        c.info_by_hand()
        self.assert_waiting("still this visit")
        self.leave()
        self.enter(ticks=SETTLE + 1)
        c.info_by_hand()
        self.assert_started()

    def test_a_pending_queue_clear_at_the_info_makes_it_wait(self):
        c = self.c
        self.capture_outside()
        self.enter(ticks=SETTLE + 1)
        c.mem[QUEUE + 8] = 8
        c.info_by_hand()
        self.assert_waiting("INFO while a queue clear is pending")
        c.mem[QUEUE + 8] = 7
        c.info_by_hand()
        self.assert_started()

    def test_no_automatic_post_after_a_queue_clear_in_this_visit(self):
        c = self.c
        c.mem[UIOPE] = POST_DIRECT
        self.capture_outside()
        self.enter(ticks=5)
        c.clear_record()
        self.worker(ASETTLE * 2)
        self.assert_no_post(AI["LEAVING"], "no post for the rest of the visit")
        self.leave()
        self.enter()
        self.until_press()
        c.dispatch()
        self.worker(ARELEASE)
        c.dispatch()
        self.assert_started()

    def test_a_clear_recorded_but_not_drained_makes_info_wait(self):
        """Review #2: the hook records a clear, Canon finishes it, INFO comes before the next drain."""
        c = self.c
        self.capture_outside()
        self.enter(ticks=SETTLE + 1)
        c.clear_record()
        self.assertEqual((c.mem[QUEUE], c.mem[QUEUE + 8], c.g("one_press_clear_visit")), (7, 7, 0), "finished, not drained")
        c.info_by_hand()
        self.assert_waiting("INFO before the drain")

    def test_a_clear_finished_between_drains_makes_info_wait(self):
        """[0x5474] moved since the visit began, with no hook record at all."""
        c = self.c
        self.capture_outside()
        self.enter(ticks=SETTLE + 1)
        c.mem[QUEUE] = c.mem[QUEUE + 8] = 8
        c.info_by_hand()
        self.assert_waiting("INFO after a silent clear")
        self.leave()
        self.enter(ticks=SETTLE + 1)
        c.info_by_hand()
        self.assert_started()

    def test_no_automatic_post_after_a_silent_clear(self):
        c = self.c
        c.mem[UIOPE] = POST_DIRECT
        self.capture_outside()
        self.enter(ticks=5)
        c.mem[QUEUE] = c.mem[QUEUE + 8] = 8
        self.worker(ASETTLE * 2)
        self.assert_no_post(AI["LEAVING"])

    def test_a_clear_recorded_before_the_visit_is_not_its_own(self):
        """Review 3 P3#3: a clear hooked and finished before the worker first sees playback, drained on the entry
        tick, does not hold the new visit (its generation is the visit's baseline)."""
        c = self.c
        self.capture_outside()
        c.good(VIEW)
        c.playback(handle=VIEW)
        c.xpan(OP["XPAN_NO"])
        c.mem[QUEUE] = c.mem[QUEUE + 8] = 8
        c.record(166, 1, GEN_UNEQUAL | 8 << 8 | 7)  # stamped with the tick before the visit
        self.worker(SETTLE + 1)
        self.assertEqual(c.g("one_press_clear_visit"), 0)
        c.info_by_hand()
        self.assert_started()

    def test_a_queue_clear_outside_playback_marks_nothing(self):
        c = self.c
        c.mem[UIOPE] = POST_DIRECT
        self.capture_outside()
        c.clear_record()
        self.worker(2)
        self.assertEqual(c.g("one_press_clear_visit"), 0)
        self.enter()
        self.until_press()


# ---- mutation checks -------------------------------------------------------------------------------
MUTATIONS = {
    "auto settle back to the human 0.5 s": ("one_press.c",
        "(int32_t)(tick - since) < (int32_t)OP_AUTO_SETTLE_TICKS)", "(int32_t)(tick - since) < (int32_t)OP_DEFER_SETTLE_TICKS)"),
    "no GUI-quiet gate": ("one_press.c",
        "    } else if (undrained || (combo_observe_state.seq_count &&", "    } else if (undrained || (0 &&"),
    "an undrained event does not restart the quiet period": ("one_press.c",
        "    } else if (undrained || (combo_observe_state.seq_count &&", "    } else if ((combo_observe_state.seq_count &&"),
    "no queue gate before the press": ("one_press.c", "        why = OP_AI_QUEUE_BUSY;", "        why = OP_AI_QUEUE_BUSY; why = OP_AI_POSTED;"),
    "no UiOpe gate before the press": ("one_press.c", "        why = OP_AI_KEYS_HELD;", "        why = OP_AI_POSTED;"),
    "no Canon busy gate before the press": ("one_press.c", "        why = OP_AI_CANON_BUSY;", "        why = OP_AI_POSTED;"),
    "no held gate before the press": ("one_press.c", "        why = OP_AI_HELD;", "        why = OP_AI_POSTED;"),
    "a held visit does not stop the press": ("one_press.c",
        "    } else if (since == D.held_since || since == one_press_held_visit) {", "    } else if (since == D.held_since) {"),
    "no lost gate before the press": ("one_press.c", "        why = OP_AI_LOST;", "        why = OP_AI_POSTED;"),
    "no expiry gate before the press": ("one_press.c", "        why = OP_AI_EXPIRED;", "        why = OP_AI_POSTED;"),
    "a kept playback deferral is posted for": ("one_press.c", "        why = OP_AI_KEPT;", "        why = OP_AI_POSTED;"),
    "posts while latched": ("one_press.c", "        why = OP_AI_LATCHED;", "        why = OP_AI_POSTED;"),
    "posts while a job runs": ("one_press.c", "        why = OP_AI_BUSY;", "        why = OP_AI_POSTED;"),
    "posts outside playback": ("one_press.c", "        why = OP_AI_NOT_PLAYBACK;", "        why = OP_AI_POSTED;"),
    "the deferral is not handled by the post": ("one_press.c",
        "        D.auto_for = marked; /* handled before the post", "        (void)marked; /* handled before the post"),
    "no claim before the post": ("one_press.c",
        "    if (!__atomic_compare_exchange_n(&D.defer, &expect, OP_DEFER_WAIT | OP_DEFER_CLAIM, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {",
        "    if (0) {"),
    "several releases of ours out (review 2 P1#1)": ("one_press.c",
        "        if (op_info_releases() == op_rel_seen && since == op_rel_visit)\n            return;\n", ""),
    "release without the delay": ("one_press.c", "    if (waited < OP_AUTO_RELEASE_TICKS)\n        return;", "    (void)0;"),
    "release posted into a busy queue": ("one_press.c",
        "    if (!quiet && (waited < OP_AUTO_RELEASE_TICKS + OP_AUTO_RELEASE_WAIT_TICKS || depth >= OP_GUI_QUEUE_POST_LIMIT)) {",
        "    if (0) {"),
    "release posted at or above the post limit (review 2 P1#2)": ("one_press.c",
        "OP_AUTO_RELEASE_WAIT_TICKS || depth >= OP_GUI_QUEUE_POST_LIMIT)) {", "OP_AUTO_RELEASE_WAIT_TICKS)) {"),
    "the release waits forever for depth 4 (review 2 P1#2)": ("one_press.c",
        "OP_AUTO_RELEASE_WAIT_TICKS || depth >= OP_GUI_QUEUE_POST_LIMIT)) {", "OP_AUTO_RELEASE_WAIT_TICKS || depth > OP_GUI_QUEUE_DEPTH_MAX)) {"),
    "a delayed release can be OK": ("one_press.c",
        "    uint32_t ok = quiet && waited == OP_AUTO_RELEASE_TICKS &&", "    uint32_t ok = quiet &&"),
    "release re-check always OK": ("one_press.c", "    if (!ok) {\n        D.auto_why = OP_AI_RELEASE_HELD;", "    ok = 1u;\n    if (!ok) {\n        D.auto_why = OP_AI_RELEASE_HELD;"),
    "a held verdict does not hold the visit": ("one_press.c",
        "        __atomic_store_n(&one_press_held_visit, since, __ATOMIC_RELEASE);\n    }\n    op_rel_seen", "    }\n    op_rel_seen"),
    "INFO ignores the held visit": ("one_press.c",
        "    uint32_t held = __atomic_load_n(&one_press_held_visit, __ATOMIC_ACQUIRE) == since;", "    uint32_t held = 0u;"),
    "OK without our press seen": ("one_press.c",
        "(state & (OP_AUTO_SEEN | OP_AUTO_USER_KEY)) == OP_AUTO_SEEN &&", "!(state & OP_AUTO_USER_KEY) &&"),
    "a user key in the gap is ignored": ("one_press.c",
        "(state & (OP_AUTO_SEEN | OP_AUTO_USER_KEY)) == OP_AUTO_SEEN &&", "(state & OP_AUTO_SEEN) &&"),
    "no UiOpe re-check at the release": ("one_press.c",
        "A.read(OP_UIOPE_KEY_HANDLER) == OP_UIOPE_POST_DIRECT && A.read(COMBO_GUI_MODE_WORD) == OP_GUI_PLAYBACK &&",
        "A.read(COMBO_GUI_MODE_WORD) == OP_GUI_PLAYBACK &&"),
    "no visit order at the release": ("one_press.c",
        "                  since && ((press - since) & OP_AUTO_TICK_MASK) - 1u < (OP_AUTO_TICK_MASK >> 1) &&", "                  since &&"),
    "no generation check at the release": ("one_press.c",
        "                  A.read(OP_GUI_QUEUE) == __atomic_load_n(&one_press_visit_gen, __ATOMIC_RELAXED) &&\n", ""),
    "a drained key while our release is out is ignored": ("one_press.c",
        "    if (st == OP_AUTO_RELEASED && since) {\n        __atomic_store_n(&one_press_held_visit, since, __ATOMIC_RELEASE);",
        "    if (st == OP_AUTO_RELEASED && since) {\n        (void)0;"),
    "an undrained key while our release is out is ignored": ("one_press.c",
        "            (undrained & COMBO_UNDRAINED_USER_KEY);", "            (0 && undrained);"),
    "our press counts as a user key": ("one_press.c",
        "        st |= (code == OP_INFO_PRESS && !(st & OP_AUTO_SEEN)) ? OP_AUTO_SEEN : OP_AUTO_USER_KEY;", "        st |= OP_AUTO_USER_KEY;"),
    "press and release in one tick": ("one_press.c",
        "        __atomic_store_n(&one_press_auto, (tick << OP_AUTO_TICK_SHIFT) | OP_AUTO_PRESSED, __ATOMIC_RELEASE);\n"
        "        A.gui_post(0u, OP_INFO_PRESS, 0u, 1u); /* exactly Canon's mode-2 key post (E08ABFF4) */\n",
        "        A.gui_post(0u, OP_INFO_PRESS, 0u, 1u); /* exactly Canon's mode-2 key post (E08ABFF4) */\n"
        "        A.gui_post(0u, OP_INFO_RELEASE, 0u, 1u);\n"),
    "every key on the entry tick is exempt": ("one_press.c", "age < 0 || (age == 0 && code == OP_PLAY_PRESS))", "age <= 0)"),
    "the visit is published after the drain (review 2 P2#3)": ("combo_observe.c",
        "    one_press_visit(now); /* before the drain: a key or a clear read on the entry tick belongs to the visit */\n    drain(now);",
        "    drain(now);\n    one_press_visit(now);"),
    "a user key before the post is ignored": ("one_press.c", "    D.auto_why = OP_AI_KEY;\n", "    D.auto_for = 0u;\n"),
    "the drain reports no user key": ("combo_observe.c",
        "    if (user_key(code, flags))\n        one_press_user_key(tick, code);", "    if (0)\n        one_press_user_key(tick, code);"),
    "joysticks are not user keys": ("combo_observe.c",
        "           (code >= OP_DIRECTION_FIRST && code <= OP_MULTI_CONTROLLER_LAST);", "           0;"),
    "joystick range from 119": ("one_press.h", "#define OP_DIRECTION_FIRST 118u", "#define OP_DIRECTION_FIRST 119u"),
    "joystick range from 117": ("one_press.h", "#define OP_DIRECTION_FIRST 118u", "#define OP_DIRECTION_FIRST 117u"),
    "class-nonzero records count": ("combo_observe.c",
        "    if (flags & (COMBO_F_CODE_WIDE | COMBO_F_CLASS_NONZERO))\n        return 0u;\n    uint32_t release = 0u;",
        "    if (0 && (flags & (COMBO_F_CODE_WIDE | COMBO_F_CLASS_NONZERO)))\n        return 0u;\n    uint32_t release = 0u;"),
    "lost records are not reported": ("combo_observe.c", "{\n    one_press_user_key(now, 0u);\n}", "{\n    (void)now;\n}"),
    "a torn or lapped scan is trusted (review 2 P2#4)": ("combo_observe.c",
        "            return COMBO_UNDRAINED_ALL; /* rewritten (or being rewritten) while copied */", "            continue;"),
    "hook seq 0 skipped after wrap (review 2 P3)": ("combo_observe.c",
        "CODE static void drain(uint32_t now)\n{\n", "CODE static void drain(uint32_t now)\n{\n    if (!S.next_seq)\n        S.next_seq = 1u;\n"),
    "S4: one_press_key ignores the marked visit": ("one_press.c",
        "    return (since && __atomic_load_n(&one_press_clear_visit, __ATOMIC_RELAXED) == since) ||",
        "    return (0 && since && __atomic_load_n(&one_press_clear_visit, __ATOMIC_RELAXED) == since) ||"),
    "S4: one_press_key ignores a pending clear": ("one_press.c",
        "           gen != A.read(OP_GUI_QUEUE + 8u) || gen != __atomic_load_n", "           gen != __atomic_load_n"),
    "S4: one_press_key ignores the visit generation": ("one_press.c",
        "|| gen != __atomic_load_n(&one_press_visit_gen, __ATOMIC_RELAXED) ||\n", "||\n"),
    "S4: one_press_key ignores undrained clears": ("one_press.c",
        "           (undrained & COMBO_UNDRAINED_CLEAR);\n}", "           (0 && undrained);\n}"),
    "S4: the automatic INFO ignores the marked visit": ("one_press.c",
        "    } else if (__atomic_load_n(&one_press_clear_visit, __ATOMIC_RELAXED) == since || (undrained & COMBO_UNDRAINED_CLEAR) ||",
        "    } else if ((undrained & COMBO_UNDRAINED_CLEAR) ||"),
    "S4: the automatic INFO ignores the visit generation": ("one_press.c",
        "               A.read(OP_GUI_QUEUE) != __atomic_load_n(&one_press_visit_gen, __ATOMIC_RELAXED)) {", "               0) {"),
    "S4: the release ignores the marked visit": ("one_press.c",
        "                  __atomic_load_n(&one_press_clear_visit, __ATOMIC_RELAXED) != since &&\n", ""),
    "S4: the drain never reports a queue clear": ("combo_observe.c", "        one_press_queue_clear(tick); /* S4", "        (void)tick; /* S4"),
    "S4: a clear outside playback marks the next visit": ("one_press.c",
        "    if (since && (int32_t)(tick - since) >= 0)\n        __atomic_store_n(&one_press_clear_visit, since, __ATOMIC_RELAXED);",
        "    (void)tick;\n    __atomic_store_n(&one_press_clear_visit, since ? since : 1u, __ATOMIC_RELAXED);"),
    "S4: a clear before the visit marks it (review 3 P3#3)": ("one_press.c",
        "    if (since && (int32_t)(tick - since) >= 0)", "    if (since && tick)"),
    "no hold re-check after the claim (review 3 P2#1)": ("one_press.c",
        "        } else if (__atomic_load_n(&one_press_held_visit, __ATOMIC_SEQ_CST) == since) {", "        } else if (0) {"),
    "S4: the wait drops the deferral": ("one_press.c",
        "        why = OP_DW_NOT_PLAYBACK; /* S4", "        D.defer = OP_DEFER_NONE; why = OP_DW_NOT_PLAYBACK; /* S4"),
}


def classify(returncode: int, stderr: str) -> str:
    """'passed', 'caught' (a test assertion failed), or 'invalid' (the mutant did not compile or import)."""
    if returncode == 0:
        return "passed"
    if ("host compile failed" in stderr or "ImportError" in stderr or "ModuleNotFoundError" in stderr
            or any(line.startswith("ERROR: setUpClass") for line in stderr.splitlines())):
        return "invalid"
    return "caught" if any(line.startswith("FAIL:") for line in stderr.splitlines()) else "invalid"


def run_mutations() -> dict:
    """Each mutation in a scratch copy of src/experimental/xpan (copies, never links); the tree is only read."""
    root = Path(__file__).resolve().parents[1]
    src = root / "src" / "experimental" / "xpan"
    files = [p for p in src.iterdir() if p.is_file() and p.suffix in (".c", ".h", ".S")]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    results = {}
    for name, (fn, old, new) in [("control (no mutation)", ("one_press.c", "", ""))] + list(MUTATIONS.items()):
        with tempfile.TemporaryDirectory(prefix="r5-auto-info-mutant-") as d:
            d = Path(d)
            for p in files:
                shutil.copyfile(p, d / p.name)
            if old:
                text = (d / fn).read_text()
                assert text.count(old) == 1, name
                (d / fn).write_text(text.replace(old, new))
            env = dict(os.environ, R5_AUTO_INFO_MUTANT_SRC=str(d))
            r = subprocess.run([sys.executable, "-B", "-m", "unittest", "tests.test_screenshot_auto_info"], cwd=root,
                               capture_output=True, text=True, env=env)
            failing = sorted({line.split(" (")[0] for line in r.stderr.splitlines() if line.startswith(("FAIL:", "ERROR:"))})
            results[name] = {"file": fn, "result": classify(r.returncode, r.stderr), "failing": failing}
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert after == before, "source changed during the mutation run"
    return results


if __name__ == "__main__":
    if "--mutations" in sys.argv:
        res = run_mutations()
        print(json.dumps(res, indent=1))
        control = res.pop("control (no mutation)")["result"]
        counts = {k: sum(v["result"] == k for v in res.values()) for k in ("caught", "passed", "invalid")}
        print("mutations: %d caught, %d survived, %d invalid (of %d); control: %s"
              % (counts["caught"], counts["passed"], counts["invalid"], len(res), control))
        sys.exit(0 if control == "passed" and counts["caught"] == len(res) else 1)
    unittest.main()
