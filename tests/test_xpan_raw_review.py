"""Host tests for the RAW-only XPan automatic review (default in xpan-crop).

The review gate's XPan test is aspect 7 and (a Large JPEG bit 0x101 or any RAW bit
0xF00000). The RAW test is Canon's own: E0840D84 ldr [CF0+0xD00]; E0840D88 tst.w
#0xF00000 forces the full 3:2 review tuple {0,0,0,0,1024,682} for any RAW shot,
which the review crop already turns into the XPan band. So a RAW-only, cRAW or
RAW + M/S JPEG XPan shot gets the band from the first simple-review frame instead
of only at Quick Review. The JPEG review token (issue #31) keeps its own test and
still refuses every RAW bit.

The actual xpan_authority.c, xpan_review_gate.c and review_crop.c run against the
fake Canon memory of test_xpan_review_fix.py. No ROM, camera or ARM toolchain.

The R5 flag word's RAW bits come from Canon's RAW decoder E058B386 (0x100000,
0x200000, 0x400000, 0x800000); which bit is cRAW was not recorded on camera, so
every bit is tested. The JPEG decoder E058B562 emits values below 0x10000 with
0x1/0x100 for Large; 0x4 below stands for "a JPEG bit other than Large" (the
M/S values were not recorded on camera).

Mutation check: python3 -B tests/test_xpan_raw_review.py --mutations builds each
mutation below from a scratch copy of src/experimental/xpan (copies, never links;
the tree is only read) and runs the three review suites against it. A mutation
counts as caught only when a test assertion fails; a mutant that does not compile
or import is reported as invalid, not caught. The unmutated copy must pass.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_xpan_review_fix as review  # noqa: E402

_MUTANT = os.environ.get("R5_XPAN_RAW_MUTANT_SRC")  # --mutations only: a scratch copy of src/experimental/xpan
if _MUTANT:
    review.XPAN_SRC = Path(_MUTANT)

U = ctypes.c_uint32
GATE, CROP = review.GATE, dict(review.CROP, LETTERBOX=13)
FULL, XPAN = review.FULL, review.XPAN
LETTERBOX_STOCK = [0, 53, 0, 53, 1024, 576]
RAW_BITS = (0x100000, 0x200000, 0x400000, 0x800000)
OTHER_JPEG = 0x4  # a JPEG bit other than Large (stands for M/S)


class RawOnlyReview(unittest.TestCase):
    _n = 0

    @classmethod
    def setUpClass(cls):
        cls.base = review.build_lib()

    def setUp(self):
        RawOnlyReview._n += 1
        path = self.base.with_name(f"raw{RawOnlyReview._n}.so")
        path.write_bytes(self.base.read_bytes())
        self.cam = review.Camera(path)
        lib = self.cam.lib
        lib.review_crop_jpeg_review.restype = U
        lib.xpan_review_gate_event_current.argtypes = [U]
        lib.xpan_review_gate_event_current.restype = U
        lib.xpan_review_gate_claim_ordinal.restype = U

    def xpan_review(self, flags, aspect=7, tuple6=FULL):
        """One shot and its automatic review: the SR-start outline call, then the descriptor crop."""
        cam = self.cam
        cam.shot(aspect=aspect, flags=flags)
        outline = cam.outline()
        ordinal = cam.lib.xpan_review_gate_claim_ordinal()
        rc, why, out = cam.crop(tuple6=tuple6)
        return outline, ordinal, why, out

    def assert_banded(self, flags):
        outline, ordinal, why, out = self.xpan_review(flags)
        self.assertEqual(outline, (0, 0), f"outline hidden for {flags:#x}")
        self.assertEqual(ordinal, 1, f"an open XPan claim for {flags:#x}")
        self.assertEqual((why, out), (CROP["APPLIED"], XPAN), f"band at the first SR frame for {flags:#x}")
        c = self.cam.counts()
        self.assertEqual((c.open[GATE["CLAIMED"]], c.crop_applied, c.outline_hidden), (1, 1, 1), hex(flags))
        # The JPEG review token: the full-source crop leaves it at 0, and the token's own test
        # refuses this RAW claim even when asked directly with its ordinal.
        self.assertEqual(self.cam.lib.review_crop_jpeg_review(), GATE["NO_CLAIM"], hex(flags))
        self.assertEqual(self.cam.lib.xpan_review_gate_event_current(1), GATE["NOT_XPAN"], hex(flags))

    def assert_stock(self, flags, aspect):
        outline, ordinal, why, out = self.xpan_review(flags, aspect=aspect)
        label = f"aspect {aspect} flags {flags:#x}"
        self.assertEqual(outline, (1, 0), label)
        self.assertEqual(ordinal, 0, label)
        self.assertEqual((why, out), (CROP["GATE"], FULL), label)
        c = self.cam.counts()
        self.assertEqual((c.open[GATE["NOT_XPAN"]], c.last_gate, c.crop_applied, c.outline_hidden),
                         (1, GATE["NOT_XPAN"], 0, 0), label)
        self.assertEqual(self.cam.lib.review_crop_jpeg_review(), GATE["NO_CLAIM"], label)

    def test_raw_only_xpan_gets_the_band_from_the_first_frame(self):
        for flags in RAW_BITS:
            with self.subTest(flags=hex(flags)):
                self.setUp()
                self.assert_banded(flags)

    def test_craw_and_raw_plus_m_s_jpeg_xpan_get_the_band(self):
        # Any RAW bit counts, with or without a non-Large JPEG bit: their SR and Quick Review
        # show the RAW display image, which Canon's own E0840D88 test sends to the full tuple.
        for flags in [b | OTHER_JPEG for b in RAW_BITS] + [0x100001, 0x100100]:
            with self.subTest(flags=hex(flags)):
                self.setUp()
                self.assert_banded(flags)

    def test_raw_only_3_2_and_other_aspects_keep_the_stock_review(self):
        for aspect in (0, 1, 2, 5, 6, 8):
            for flags in (0x100000, 0x800000):
                with self.subTest(aspect=aspect, flags=hex(flags)):
                    self.setUp()
                    self.assert_stock(flags, aspect)

    def test_xpan_without_large_jpeg_or_raw_stays_not_xpan(self):
        for flags in (0x0, OTHER_JPEG, 0x2, 0x10000, 0x80000, 0x1000000, 0xFFFFFFFF & ~0x101 & ~0xF00000):
            with self.subTest(flags=hex(flags)):
                self.setUp()
                self.assert_stock(flags, 7)

    def test_jpeg_token_refuses_raw_even_from_a_letterbox_crop(self):
        # Canon never letterboxes a RAW review (it forces the full tuple), but if a RAW claim
        # ever reached the 16:9 letterbox crop, the token's own test still refuses it.
        for flags in RAW_BITS + (0x100001, 0x100000 | OTHER_JPEG):
            with self.subTest(flags=hex(flags)):
                self.setUp()
                outline, ordinal, why, out = self.xpan_review(flags, tuple6=LETTERBOX_STOCK)
                self.assertEqual((outline, ordinal, why, out), ((0, 0), 1, CROP["LETTERBOX"], XPAN))
                self.cam.end_review()
                self.assertEqual(self.cam.lib.review_crop_jpeg_review(), GATE["NOT_XPAN"])

    def test_jpeg_only_review_and_token_are_unchanged(self):
        for flags in (0x1, 0x100):
            with self.subTest(flags=hex(flags)):
                self.setUp()
                outline, ordinal, why, out = self.xpan_review(flags, tuple6=LETTERBOX_STOCK)
                self.assertEqual((outline, ordinal, why, out), ((0, 0), 1, CROP["LETTERBOX"], XPAN))
                self.cam.end_review()
                self.assertEqual(self.cam.lib.review_crop_jpeg_review(), GATE["NONE"])
        # An M/S-only JPEG XPan shot keeps the stock (stretched) review, as documented.
        self.setUp()
        self.assertEqual(self.xpan_review(OTHER_JPEG, tuple6=LETTERBOX_STOCK)[2:], (CROP["GATE"], LETTERBOX_STOCK))
        self.assertEqual(self.cam.lib.review_crop_jpeg_review(), GATE["NO_CLAIM"])

    def test_event_current_contract_is_jpeg_only_xpan(self):
        # The token's documented test, asked directly for the latest event's ordinal: aspect 7,
        # a Large JPEG bit and no RAW bit. Through review_crop only XPan claims reach it, so
        # this pins the contract itself rather than relying on the claim's is_xpan.
        cases = [((7, 0x1), "NONE"), ((7, 0x100), "NONE"), ((7, 0x101), "NONE"),
                 ((0, 0x1), "NOT_XPAN"), ((2, 0x100), "NOT_XPAN"), ((7, 0x0), "NOT_XPAN"),
                 ((7, OTHER_JPEG), "NOT_XPAN"), ((7, 0x100000), "NOT_XPAN"), ((7, 0x100001), "NOT_XPAN"),
                 ((0, 0x100000), "NOT_XPAN")]
        for (aspect, flags), want in cases:
            with self.subTest(aspect=aspect, flags=hex(flags)):
                self.setUp()
                self.cam.shot(aspect=aspect, flags=flags)
                self.assertEqual(self.cam.lib.xpan_review_gate_event_current(1), GATE[want])

    def test_raw_only_claim_keeps_single_shot_and_stale_rules(self):
        cam = self.cam
        # A burst of RAW-only XPan shots: MULTIPLE, stock review.
        cam.shot(flags=0x100000)
        cam.shot(flags=0x100000, jobid=0x42)
        self.assertEqual(cam.outline(), (1, 0))
        self.assertEqual(cam.crop()[1:], (CROP["GATE"], FULL))
        self.assertEqual(cam.counts().open[GATE["MULTIPLE"]], 1)
        cam.end_review()
        # A later redisplay without a new shot stays stock.
        self.assertEqual(cam.outline(), (1, 0))
        self.assertEqual(cam.counts().open[GATE["NO_NEW_EVENT"]], 1)
        cam.end_review()
        # A newer event during a RAW-only review revokes the claim.
        cam.shot(flags=0x100000, jobid=0x43)
        self.assertEqual(cam.outline(), (0, 0))
        cam.shot(flags=0x100000, jobid=0x44)
        self.assertEqual(cam.crop()[1:], (CROP["GATE"], FULL))
        self.assertEqual(cam.counts().crop[GATE["STALE"]], 1)


# ---- mutation checks -------------------------------------------------------------------------------
GATE_C = "xpan_review_gate.c"
IS_XPAN = "(l->flags & XPAN_GATE_LARGE_JPEG) != 0u || (l->flags & XPAN_GATE_RAW_MASK) != 0u"
TOKEN_TEST = ("    if (s.latest.aspect != XPAN_GATE_ASPECT_XPAN || !(s.latest.flags & XPAN_GATE_LARGE_JPEG) ||\n"
              "        (s.latest.flags & XPAN_GATE_RAW_MASK))")
MUTATIONS = {
    "is_xpan drops the RAW branch (the old strict policy)": (GATE_C, IS_XPAN, "(l->flags & XPAN_GATE_LARGE_JPEG) != 0u"),
    "is_xpan drops the aspect test": (GATE_C, "return l->aspect == XPAN_GATE_ASPECT_XPAN &&", "return 1u &&"),
    "is_xpan admits only RAW bit 0x100000": (GATE_C, "(l->flags & XPAN_GATE_RAW_MASK) != 0u", "(l->flags & 0x100000u) != 0u"),
    "is_xpan admits any flag": (GATE_C, IS_XPAN, "l->flags != 0u"),
    "is_xpan admits any flag word, even 0": (GATE_C, IS_XPAN, "1u"),
    "is_xpan requires no Large JPEG with RAW (strict RAW-only)": (GATE_C, IS_XPAN,
        "(l->flags & XPAN_GATE_LARGE_JPEG) != 0u || ((l->flags & XPAN_GATE_RAW_MASK) != 0u && !(l->flags & 0xffffu))"),
    "RAW mask widened to 0xFF0000": ("xpan_review_gate.h", "UINT32_C(0x00f00000)", "UINT32_C(0x00ff0000)"),
    "RAW mask widened to 0x1F00000": ("xpan_review_gate.h", "UINT32_C(0x00f00000)", "UINT32_C(0x01f00000)"),
    "token drops its RAW refusal": (GATE_C, " ||\n        (s.latest.flags & XPAN_GATE_RAW_MASK))", ")"),
    "token uses is_xpan alone": (GATE_C, TOKEN_TEST, "    if (!is_xpan(&s.latest))"),
    "token drops its Large JPEG test": (GATE_C, " || !(s.latest.flags & XPAN_GATE_LARGE_JPEG) ||", " ||"),
    "token drops its aspect test": (GATE_C, "s.latest.aspect != XPAN_GATE_ASPECT_XPAN || !(s.latest.flags", "!(s.latest.flags"),
    "full crop arms the token": ("review_crop.c", "why == REVIEW_CROP_R_LETTERBOX ? xpan_review_gate_claim_ordinal() : 0u",
                                 "xpan_review_gate_claim_ordinal()"),
}
# Run from tests/ under their plain module names, so all three share the one test_xpan_review_fix module whose
# XPAN_SRC this module points at the mutant (it is imported first).
SUITES = ["test_xpan_raw_review", "test_xpan_review_fix", "test_xpan_jpeg_review"]


def classify(returncode: int, stderr: str) -> str:
    """'passed', 'caught' (a test assertion failed), or 'invalid' (the mutant did not compile or import)."""
    if returncode == 0:
        return "passed"
    lines = stderr.splitlines()
    if ("CalledProcessError" in stderr or "host compile failed" in stderr or "ImportError" in stderr
            or "ModuleNotFoundError" in stderr or any(l.startswith("ERROR: setUpClass") for l in lines)):
        return "invalid"
    return "caught" if any(l.startswith("FAIL:") for l in lines) else "invalid"


def run_mutations() -> dict:
    root = Path(__file__).resolve().parents[1]
    src = root / "src" / "experimental" / "xpan"
    files = [p for p in src.iterdir() if p.is_file() and p.suffix in (".c", ".h", ".S")]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    results = {}
    for name, (fn, old, new) in [("control (no mutation)", (GATE_C, "", ""))] + list(MUTATIONS.items()):
        with tempfile.TemporaryDirectory(prefix="r5-xpan-raw-mutant-") as d:
            d = Path(d)
            for p in files:
                shutil.copyfile(p, d / p.name)
            if old:
                text = (d / fn).read_text()
                assert text.count(old) == 1, name
                (d / fn).write_text(text.replace(old, new))
            env = dict(os.environ, R5_XPAN_RAW_MUTANT_SRC=str(d))
            r = subprocess.run([sys.executable, "-B", "-m", "unittest", *SUITES], cwd=root / "tests", capture_output=True,
                               text=True, env=env, timeout=600)
            failing = sorted({l.split(" (")[0] for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))})
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
