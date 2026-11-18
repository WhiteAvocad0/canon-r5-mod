"""Host tests for the XPan automatic post-shot review fix (default in xpan-crop).

The actual xpan_authority.c, xpan_review_gate.c and review_crop.c are compiled
with the host C compiler against fake Canon memory. No ROM, camera or ARM
toolchain is needed.
"""
import ctypes
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
XPAN_SRC = ROOT / "src" / "experimental" / "xpan"
XPAN_TOOLS = XPAN_SRC / "tools"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(XPAN_TOOLS))

import feature_contract  # noqa: E402
from contracts import MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, validate_xpan_target_contract_consistency  # noqa: E402
from host_cc import find_host_cc  # noqa: E402

U = ctypes.c_uint32
READ = ctypes.CFUNCTYPE(U, U)
WRITE = ctypes.CFUNCTYPE(None, U, U)

SHIM = r'''
#include <stdint.h>
typedef uint32_t (*rd_fn)(uint32_t);
typedef void (*wr_fn)(uint32_t, uint32_t);
static rd_fn rd; static wr_fn wr;
void host_set(rd_fn r, wr_fn w) { rd = r; wr = w; }
uint32_t xpan_authority_host_read(uint32_t a) { return rd(a); }
uint32_t xpan_review_gate_host_read(uint32_t a) { return rd(a); }
void xpan_review_gate_host_write(uint32_t a, uint32_t v) { wr(a, v); }
uint32_t review_crop_host_read(uint32_t a) { return rd(a); }
void review_crop_host_write(uint32_t a, uint32_t v) { wr(a, v); }
'''

SIGNATURE, JOBID, ASPECT, FLAGS = 0xE2083738, 0x3C0, 0x14C0, 0x19F0
SR_FLAG, ASPECT_SLOT, ASPECT_OFFSET = 0x17268, 0x171DC, 0x18C0
CALLER_LR, UNDISP_LR, CROP_LR, MARKER = 0xE0A957CB, 0xE05FDF6D, 0xE063BECF, 0x53524331
FULL = [0, 0, 0, 0, 1024, 682]
XPAN = [0, 152, 0, 152, 1024, 378]
GATE = dict(NONE=0, CLAIMED=1, NOT_XPAN=2, NO_NEW_EVENT=3, MULTIPLE=4, UNSTABLE=5, UNPUBLISHED=6, INVALID=7,
            NO_CLAIM=8, REVOKED=9, STALE=10)
OUTLINE = dict(HIDDEN=0, FRAME=1, CALLER=2, DISPLAY=3, GUI_ASPECT=4, DECISION=5, GATE=6)
CROP = dict(APPLIED=0, COMPLETION=1, CALLER=2, SLOT=3, FRAME=4, DESTINATION=5, SOURCE_SLOT=6, ACTIVE=7, KIND=8,
            ID=9, SOURCE_DIMS=10, TUPLE=11, GATE=12)


class Latest(ctypes.Structure):
    _fields_ = [(n, U) for n in ("ordinal", "reason", "jobid", "aspect", "flags")]


class GateCounts(ctypes.Structure):
    _fields_ = [(n, U) for n in ("reviews", "outline_calls", "outline_decisions", "outline_hidden", "crop_decisions",
                                 "crop_applied", "closes")] + [
        ("open", U * 11), ("crop", U * 11), ("outline", U * 7), ("last_gate", U)]


class Call(ctypes.Structure):
    _fields_ = [(n, U) for n in ("caller", "caller_sp", "destination", "source", "controller", "slot", "completed",
                                 "original_result")]


def build_lib():
    tmp = tempfile.mkdtemp(prefix="r5-review-fix-")
    shim = Path(tmp) / "shim.c"
    shim.write_text(SHIM)
    lib = Path(tmp) / "review.so"
    subprocess.run([find_host_cc(), "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    "-DXPAN_AUTHORITY_HOST", "-DXPAN_REVIEW_GATE_HOST", "-DREVIEW_CROP_HOST", "-I", str(XPAN_SRC),
                    str(shim), *(str(XPAN_SRC / n) for n in ("xpan_authority.c", "xpan_review_gate.c", "review_crop.c",
                                                            "frame_memory.c")), "-o", str(lib)], check=True)
    return lib


class Camera:
    """Fresh library per test (its .bss is the state under test) plus fake memory."""
    SP, CROP_SP, CONTROLLER, HANDLE, OBJECT, GUI = 0x00200000, 0x00210000, 0x00300000, 0x00400000, 0x00500000, 0x00600000

    def __init__(self, path):
        self.lib = ctypes.CDLL(str(path), mode=getattr(ctypes, "RTLD_LOCAL", 0))
        self.mem = {}
        self._r, self._w = READ(self.read), WRITE(self.write)
        self.lib.host_set(self._r, self._w)
        self.lib.xpan_review_authority_observe.argtypes = [ctypes.POINTER(U)]
        self.lib.xpan_review_outline_decide.argtypes = [U, U]
        self.lib.xpan_review_gate_crop.argtypes = [U]
        self.lib.xpan_authority_latest.argtypes = [ctypes.POINTER(Latest)]
        self.lib.review_crop_apply.argtypes = [ctypes.POINTER(Call), ctypes.POINTER(U)]
        self.lib.review_crop_apply.restype = U
        self.writes = []
        self.set_gui_aspect(7)
        self.mem[SR_FLAG] = 0

    def read(self, a):
        return self.mem.get(a, 0)

    def write(self, a, v):
        self.writes.append((a, v))
        self.mem[a] = v

    def set_gui_aspect(self, value):
        self.mem[ASPECT_SLOT] = self.GUI
        at = self.GUI + ASPECT_OFFSET
        self.mem[at & ~3] = value << (8 * (at & 3))

    def shot(self, aspect=7, flags=0x101, jobid=0x41, signature=SIGNATURE):
        self.mem[self.HANDLE] = signature
        self.mem[self.HANDLE + 4] = self.OBJECT
        self.mem[self.OBJECT + JOBID] = jobid
        self.mem[self.OBJECT + ASPECT] = aspect
        self.mem[self.OBJECT + FLAGS] = flags
        saved = (U * 6)(0x1234, self.HANDLE, 0, 0, 0, 0xE0E35C41)
        self.lib.xpan_review_authority_observe(saved)

    def outline(self, caller=CALLER_LR, bd=UNDISP_LR, display=0, force=1, h=1, v=0, sr=1, sp=None):
        sp = self.SP if sp is None else sp
        self.mem[SR_FLAG] = sr
        self.mem.update({sp + 84: caller, sp + 108: bd, sp + 68: display, sp + 28: h, sp + 32: v})
        self.lib.xpan_review_outline_decide(sp, force)
        return self.mem[sp + 28], self.mem[sp + 32]

    def descriptor(self, tuple6=FULL, width=1024, height=682, active=1, kind=0, ident=(7, 0)):
        d = self.CROP_SP + 16
        words = [active, kind, ident[0], ident[1], 0, width, height, *tuple6]
        for i, w in enumerate(words):
            self.mem[d + 4 * i] = w
        return d

    def crop(self, **over):
        d = self.descriptor(**{k: over.pop(k) for k in list(over) if k in ("tuple6", "width", "height", "active", "kind", "ident")})
        c = dict(caller=CROP_LR, caller_sp=self.CROP_SP, destination=d, source=self.CONTROLLER + 0x10A0,
                 controller=self.CONTROLLER, slot=0, completed=MARKER, original_result=1)
        c.update(over)
        reason = U()
        rc = self.lib.review_crop_apply(ctypes.byref(Call(**c)), ctypes.byref(reason))
        tuple6 = [self.mem.get(c["destination"] + 28 + 4 * i, 0) for i in range(6)]
        return rc, reason.value, tuple6

    def counts(self):
        out = GateCounts()
        self.lib.xpan_review_gate_counts(ctypes.byref(out))
        return out

    def latest(self):
        out = Latest()
        ok = self.lib.xpan_authority_latest(ctypes.byref(out))
        return ok, (out.ordinal, out.reason, out.jobid, out.aspect, out.flags)

    def end_review(self):
        self.outline(sr=0)


class ReviewFixTests(unittest.TestCase):
    _n = 0

    @classmethod
    def setUpClass(cls):
        cls.base = build_lib()

    def setUp(self):
        # A distinct copy per test: each load has fresh .bss state.
        ReviewFixTests._n += 1
        path = self.base.with_name(f"review{ReviewFixTests._n}.so")
        path.write_bytes(self.base.read_bytes())
        self.cam = Camera(path)

    def test_authority_validates_the_job_handle(self):
        cam = self.cam
        self.assertEqual(cam.latest()[0], 0)
        cam.shot(jobid=0x41, aspect=7, flags=0x101)
        self.assertEqual(cam.latest(), (1, (1, 1, 0x41, 7, 0x101)))
        cam.shot(signature=0x12345678)
        self.assertEqual(cam.latest(), (1, (2, 3, 0, 0, 0)))
        cam.mem[cam.HANDLE] = SIGNATURE
        cam.mem[cam.HANDLE + 4] = 0x40000000  # outside the admitted data span
        cam.lib.xpan_review_authority_observe((U * 6)(0, cam.HANDLE, 0, 0, 0, 0))
        self.assertEqual(cam.latest()[1][:2], (3, 4))
        cam.lib.xpan_review_authority_observe((U * 6)(0, 0x2, 0, 0, 0, 0))
        self.assertEqual(cam.latest()[1][:2], (4, 2))

    def test_xpan_shot_gets_immediate_bars_and_hidden_outline(self):
        cam = self.cam
        cam.shot()
        self.assertEqual(cam.outline(), (0, 0), "review-start outline hidden")
        rc, why, tuple6 = cam.crop()
        self.assertEqual((rc, why, tuple6), (1, CROP["APPLIED"], XPAN))
        # Exactly the three words that differ were written.
        d = cam.CROP_SP + 16
        self.assertEqual(sorted(a for a, _ in cam.writes if a >= d), [d + 32, d + 40, d + 48])
        self.assertEqual(cam.outline(bd=0xE0000001, force=0), (0, 0), "later redraws of the same review stay hidden")
        c = cam.counts()
        self.assertEqual((c.reviews, c.open[GATE["CLAIMED"]], c.crop_applied, c.outline_hidden), (1, 1, 1, 2))

    def test_full_frame_and_raw_only_shots_keep_the_stock_review(self):
        for aspect, flags in ((0, 0x101), (7, 0x0), (2, 0x101)):
            self.setUp()
            cam = self.cam
            cam.shot(aspect=aspect, flags=flags)
            self.assertEqual(cam.outline(), (1, 0), (aspect, flags))
            rc, why, tuple6 = cam.crop()
            self.assertEqual((why, tuple6), (CROP["GATE"], FULL), (aspect, flags))
            self.assertEqual(cam.counts().open[GATE["NOT_XPAN"]], 1)

    def test_burst_gives_multiple_and_a_stock_review(self):
        cam = self.cam
        cam.shot()
        cam.shot(jobid=0x42)
        self.assertEqual(cam.outline(), (1, 0))
        self.assertEqual(cam.crop()[1:], (CROP["GATE"], FULL))
        c = cam.counts()
        self.assertEqual((c.open[GATE["MULTIPLE"]], c.last_gate), (1, GATE["MULTIPLE"]))

    def test_claim_is_single_use_per_review_and_closed_outside_review(self):
        cam = self.cam
        cam.shot()
        cam.outline()
        self.assertEqual(cam.crop()[1], CROP["APPLIED"])
        cam.end_review()
        self.assertEqual(cam.counts().closes, 1)
        # A later review without a new shot (for example a redisplay) is stock.
        self.assertEqual(cam.outline(), (1, 0))
        self.assertEqual(cam.crop()[1:], (CROP["GATE"], FULL))
        self.assertEqual(cam.counts().open[GATE["NO_NEW_EVENT"]], 1)
        cam.end_review()
        cam.shot()
        self.assertEqual(cam.outline(), (0, 0), "the next XPan shot claims again")

    def test_newer_event_during_review_revokes_the_claim(self):
        cam = self.cam
        cam.shot()
        self.assertEqual(cam.outline(), (0, 0))
        cam.shot(jobid=0x42)
        self.assertEqual(cam.crop()[1:], (CROP["GATE"], FULL))
        self.assertEqual(cam.counts().crop[GATE["STALE"]], 1)
        self.assertEqual(cam.outline(bd=0, force=0), (1, 0))
        self.assertEqual(cam.counts().last_gate, GATE["REVOKED"])

    def test_outline_guards_decline_without_writing(self):
        cases = [(dict(caller=0xE0A95001), "CALLER"), (dict(display=1), "DISPLAY"), (dict(h=0, v=1), "DECISION"),
                 (dict(sp=0x40000000), "FRAME")]
        for over, reason in cases:
            self.setUp()
            cam = self.cam
            cam.shot()
            cam.writes.clear()
            cam.outline(**over)
            self.assertEqual(cam.writes, [], reason)
            self.assertEqual(cam.counts().outline[OUTLINE[reason]], 1, reason)
        self.setUp()
        self.cam.shot()
        self.cam.set_gui_aspect(5)
        self.assertEqual(self.cam.outline(), (1, 0))
        self.assertEqual(self.cam.counts().outline[OUTLINE["GUI_ASPECT"]], 1)

    def test_outline_outside_sr_is_passive(self):
        cam = self.cam
        cam.shot()
        cam.writes.clear()
        self.assertEqual(cam.outline(sr=0), (1, 0))
        self.assertEqual(cam.writes, [])
        self.assertEqual(cam.counts().outline_decisions, 0)

    def test_crop_requires_the_exact_full_source_descriptor_and_scope(self):
        cases = [
            (dict(tuple6=XPAN), "TUPLE"), (dict(tuple6=[0, 0, 0, 0, 1024, 680]), "TUPLE"),
            (dict(width=1620), "SOURCE_DIMS"), (dict(active=0), "ACTIVE"), (dict(kind=1), "KIND"),
            (dict(ident=(0, 0)), "ID"), (dict(caller=0xE063BED1), "CALLER"), (dict(slot=1), "SLOT"),
            (dict(completed=0), "COMPLETION"), (dict(caller_sp=Camera.CROP_SP + 4), "FRAME"),
            (dict(destination=Camera.CROP_SP + 20), "DESTINATION"), (dict(source=Camera.CONTROLLER), "SOURCE_SLOT"),
        ]
        for over, reason in cases:
            self.setUp()
            cam = self.cam
            cam.shot()
            cam.outline()
            cam.writes.clear()
            rc, why, _ = cam.crop(**over)
            self.assertEqual((rc, why), (1, CROP[reason]), over)
            self.assertEqual(cam.writes, [], over)
        self.assertEqual(self.cam.lib.review_crop_apply(None, None), 0)

    def test_profiles_and_trampolines_replay_the_exact_original_bytes(self):
        inv = json.loads((XPAN_TOOLS / "xpan-review-hooks.json").read_text())
        self.assertNotIn("rom_sha256", inv)
        thumb = {"push {r4,r5,r6,lr}": "70b5", "movs r4,r0": "0400", "mov r6,r1": "0e46", "mov r5,r2": "1546",
                 "ldr r2,[sp,#32]": "089a", "movs r1,#30": "1e21", "ldr.w r0,[r8,#20]": "d8f81400",
                 "mov r4,r8": "4446", "stmdb sp!,{r4-r10,lr}": "2de9f047", "mov r7,r2": "1746",
                 "movs r2,#152": "9822", "mov r8,r3": "9846"}
        files = {"xpan_review_authority_entry": ("xpan_authority_hook.S", "pop {r0-r3,r12,lr}"),
                 "xpan_review_outline_entry": ("xpan_review_outline_hook.S", "pop {r0-r3,r12,lr}"),
                 "review_crop_entry": ("review_crop_hook.S", "review_crop_original:")}
        for hook in inv["hooks"]:
            name, anchor = files[hook["destination_symbol"]]
            asm = (XPAN_SRC / name).read_text()
            self.assertEqual(hook["resume_thumb"], (hook["va"] + hook["length"]) | 1)
            replay = asm[asm.index(anchor) + len(anchor):asm.index(f"ldr pc,=0x{hook['resume_thumb']:08x}")]
            lines = [l.strip() for l in replay.splitlines() if l.strip() and not l.strip().startswith(("/*", "*"))]
            self.assertEqual("".join(thumb[l] for l in lines), hook["original_bytes"], name)
        self.assertEqual([h["va"] for h in inv["hooks"]], [0xE063FBEE, 0xE054C530, 0xE0A956FE])

    def test_default_inventory_capacity_and_fingerprints(self):
        contract, hooks, _ = feature_contract.load_hooks()
        pages = {int(h["va"], 16) & ~4095 for h in hooks}
        tables = {(p >> 20) & 15 for p in pages}
        self.assertEqual((len(hooks), len(pages), len(tables), len(contract["source_guard_pages"])), (34, 20, 10, 137))
        self.assertEqual(feature_contract.native_limit("code_patch.h", "CODE_PATCH_MAX"), 35)
        self.assertEqual(feature_contract.native_limit("multi_plan.h", "MULTI_MAX_PAGES"), 21)
        self.assertEqual(feature_contract.native_limit("multi_plan.h", "MULTI_MAX_TABLES"), 10)
        guards = [int(v, 16) for v in contract["source_guard_pages"]]
        validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, hooks=hooks,
                                                  guard_pages=guards)
        fps = {e["address"] for e in XPAN_TARGET_CONTRACT["extra_code_fingerprints"]}
        self.assertEqual(fps, pages | set(guards))
        for name in ("review_crop_entry", "xpan_review_authority_entry", "xpan_review_outline_entry"):
            self.assertIn(name, [h["destination"] for h in hooks])



@unittest.skipUnless(__import__("os").environ.get("R5_XPAN_BUILD"), "opt-in real-ROM build verification")
class RealBuildTests(unittest.TestCase):
    def test_linked_review_fix(self):
        out = Path(__import__("os").environ["R5_XPAN_BUILD"])
        feature = json.loads((out / "feature-contract.json").read_text())
        symbols = json.loads((out / "symbols.json").read_text())
        inv = json.loads((XPAN_TOOLS / "xpan-review-hooks.json").read_text())
        linked = {(int(h["va"], 16), h["original_hex"]): h["destination"] for h in feature["hooks"]}
        for hook in inv["hooks"]:
            self.assertEqual(linked[(hook["va"], hook["original_bytes"])], hook["destination_symbol"])
            self.assertIn(hook["destination_symbol"], symbols)
        self.assertTrue(json.loads((out / "build.json").read_text())["configuration"]["xpan_instant_review_fix"])


if __name__ == "__main__":
    unittest.main()
