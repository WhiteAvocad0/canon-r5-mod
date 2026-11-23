"""Host tests for the JPEG-only XPan automatic review (issue #31, default in xpan-crop).

Three parts, all actual C compiled with the host compiler against fake Canon memory
(no ROM, camera or ARM toolchain):
  - review_crop.c + xpan_review_gate.c + xpan_authority.c: the 16:9 letterbox tuple
    of the JPEG-only review gets the XPan band and arms the JPEG review token;
  - playback_mask_native.c: the pure Quick Review admission policy;
  - playback_mask_native.c fit_begin/fit_end with the trampoline's saved register
    image: the caller's r8 of 1 is set to 0 exactly for the token-admitted JPEG_L
    Quick Review that Canon leaves stock, and nothing else is written.
"""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
XPAN_SRC = ROOT / "src" / "experimental" / "xpan"
XPAN_TOOLS = XPAN_SRC / "tools"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(XPAN_TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import feature_contract  # noqa: E402
import playback_mask_profile  # noqa: E402
from contracts import XPAN_TARGET_CONTRACT  # noqa: E402
from host_cc import find_host_cc  # noqa: E402
import test_xpan_review_fix as review  # noqa: E402

U = ctypes.c_uint32
LETTERBOX_STOCK = [0, 53, 0, 53, 1024, 576]
GATE = review.GATE
CROP = dict(review.CROP, LETTERBOX=13)


class LetterboxAndToken(unittest.TestCase):
    """The review hook side: Canon's stock 16:9 tuple of the JPEG-only review and the token."""

    @classmethod
    def setUpClass(cls):
        cls.base = review.build_lib()
        cls.n = 0

    def setUp(self):
        LetterboxAndToken.n += 1
        path = self.base.with_name(f"jpeg{LetterboxAndToken.n}.so")
        path.write_bytes(self.base.read_bytes())
        self.cam = review.Camera(path)
        self.cam.lib.review_crop_jpeg_review.restype = U
        self.cam.lib.xpan_review_gate_event_current.argtypes = [U]
        self.cam.lib.xpan_review_gate_event_current.restype = U
        self.cam.lib.xpan_review_gate_claim_ordinal.restype = U

    def token(self):
        return self.cam.lib.review_crop_jpeg_review()

    def jpeg_review(self, flags=0x1, jobid=0x41):
        """One JPEG-only XPan shot and its automatic review (SR start, then the crop)."""
        cam = self.cam
        cam.shot(flags=flags, jobid=jobid)
        cam.outline()
        return cam.crop(tuple6=LETTERBOX_STOCK)

    def test_letterbox_tuple_gets_the_band_and_arms_the_token(self):
        cam = self.cam
        self.assertEqual(self.token(), GATE["NO_CLAIM"], "no review yet")
        rc, why, tuple6 = self.jpeg_review()
        self.assertEqual((rc, why, tuple6), (1, CROP["LETTERBOX"], review.XPAN))
        d = cam.CROP_SP + 16
        self.assertEqual(sorted(a for a, _ in cam.writes if a >= d), [d + 32, d + 40, d + 48])
        # Quick Review runs after the SR flag closed the claim: the token outlives it.
        cam.end_review()
        self.assertEqual(cam.lib.xpan_review_gate_claim_ordinal(), 0)
        self.assertEqual(self.token(), GATE["NONE"])
        self.assertEqual(self.token(), GATE["NONE"], "QR redraws of the same shot stay admitted")
        # The next authority event (the next shot, even an invalid one) ends it.
        cam.shot(signature=0x12345678)
        self.assertEqual(self.token(), GATE["STALE"])

    def test_both_large_jpeg_bits_arm_and_the_check_only_reads(self):
        for flags in (0x1, 0x100):
            self.setUp()
            self.assertEqual(self.jpeg_review(flags=flags)[1], CROP["LETTERBOX"], hex(flags))
            self.cam.end_review()
            self.cam.writes.clear()
            before = self.cam.counts()
            self.assertEqual(self.token(), GATE["NONE"], hex(flags))
            after = self.cam.counts()
            self.assertEqual(self.cam.writes, [])
            self.assertEqual(bytes(before), bytes(after), "no counter moves")

    def test_raw_plus_jpeg_flags_never_arm(self):
        # A letterbox tuple with a RAW bit cannot be admitted as a JPEG-only review.
        self.assertEqual(self.jpeg_review(flags=0x100001)[1], CROP["LETTERBOX"])
        self.assertEqual(self.token(), GATE["NOT_XPAN"])

    def test_full_source_crop_clears_and_refusals_keep_the_token(self):
        cam = self.cam
        self.jpeg_review()
        cam.end_review()
        # A burst: the gate refuses, the old token stays and is now stale.
        cam.shot(jobid=0x42)
        cam.shot(jobid=0x43)
        cam.outline()
        self.assertEqual(cam.crop(tuple6=LETTERBOX_STOCK)[1], CROP["GATE"])
        self.assertEqual(self.token(), GATE["STALE"])
        cam.end_review()
        # A RAW + JPEG XPan review: the full-source crop clears the token.
        cam.shot(flags=0x100101, jobid=0x44)
        cam.outline()
        self.assertEqual(cam.crop()[1], CROP["APPLIED"])
        self.assertEqual(self.token(), GATE["NO_CLAIM"])

    def test_newer_event_during_the_review_refuses_the_crop_and_the_token(self):
        cam = self.cam
        cam.shot(flags=0x1)
        cam.outline()
        cam.shot(flags=0x1, jobid=0x42)
        self.assertEqual(cam.crop(tuple6=LETTERBOX_STOCK)[1:], (CROP["GATE"], LETTERBOX_STOCK))
        self.assertEqual(self.token(), GATE["NO_CLAIM"])

    def test_claim_ordinal_is_only_an_open_unrevoked_xpan_claim(self):
        cam = self.cam
        cam.shot(flags=0x1)
        cam.outline()
        self.assertEqual(cam.lib.xpan_review_gate_claim_ordinal(), 1)
        cam.shot(flags=0x1, jobid=0x42)
        cam.outline(bd=0, force=0)  # a redraw sees the newer event and revokes the claim
        self.assertEqual(cam.counts().last_gate, GATE["STALE"])
        self.assertEqual(cam.lib.xpan_review_gate_claim_ordinal(), 0, "a revoked claim never arms the token")
        self.setUp()
        self.cam.shot(aspect=0, flags=0x1)
        self.cam.outline()
        self.assertEqual(self.cam.lib.xpan_review_gate_claim_ordinal(), 0, "a full-frame claim is not XPan")

    def test_full_frame_jpeg_review_stays_stock(self):
        cam = self.cam
        cam.shot(aspect=0, flags=0x1)
        cam.outline()
        self.assertEqual(cam.crop(tuple6=LETTERBOX_STOCK)[1:], (CROP["GATE"], LETTERBOX_STOCK))
        self.assertEqual(self.token(), GATE["NO_CLAIM"])

    def test_descriptor_near_misses_are_not_cropped(self):
        for tuple6 in ([0, 53, 0, 53, 1024, 577], [0, 54, 0, 53, 1024, 576], [0, 53, 0, 52, 1024, 576],
                       [0, 152, 0, 152, 1024, 378]):
            self.setUp()
            self.cam.shot(flags=0x1)
            self.cam.outline()
            self.cam.writes.clear()
            self.assertEqual(self.cam.crop(tuple6=tuple6)[:2], (1, CROP["TUPLE"]), tuple6)
            self.assertEqual(self.cam.writes, [], tuple6)
            self.assertEqual(self.token(), GATE["NO_CLAIM"], tuple6)


POLICY_C = r'''
#include "playback_mask_native.h"
#include <stdio.h>
#define CK(x) do { if (!(x)) { fprintf(stderr, "jpeg review policy line %d\n", __LINE__); return 1; } } while (0)
static struct playback_mask_fit_facts base(void) {
    struct playback_mask_fit_facts f = {0};
    f.entry_lr = 0xe07110fdu; f.original_sp = 0x02001000u; f.output = f.original_sp + 8u;
    f.object = f.saved_object = 0x11ce10u; f.object_known = f.cache_known = 1u; f.category = f.view.category = 7u;
    f.object_facts = (struct playback_box_facts){{7u, 8192u, 3024u, 0u, 0u}, 8192u, 3024u};
    f.cache_facts = f.object_facts;
    return f;
}
int main(void) {
    struct playback_mask_fit_facts f = base();
    CK(playback_mask_fit_jpeg_review_admit(&f));
    CK(!playback_mask_fit_admit(&f)); /* the RAW_L admission is unchanged */
    const unsigned devs[4] = {0u, 0u, 1u, 1u}, oris[4] = {0u, 2u, 0u, 2u};
    for (unsigned i = 0; i < 4u; i++) {
        f = base(); f.view.device = devs[i]; f.view.orientation = oris[i];
        CK(playback_mask_fit_jpeg_review_admit(&f));
    }
    CK(!playback_mask_fit_jpeg_review_admit(0));
    /* Each near miss changes one field. */
    f = base(); f.category = f.view.category = 1u; CK(!playback_mask_fit_jpeg_review_admit(&f)); /* normal playback */
    f = base(); f.category = f.view.category = 0u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.category = f.view.category = 13u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.view.category = 1u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.category = 1u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    for (unsigned s = 1; s < 4u; s++) { f = base(); f.selector = f.saved_selector = s; CK(!playback_mask_fit_jpeg_review_admit(&f)); }
    f = base(); f.saved_selector = 1u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    const unsigned dims[6][2] = {{8192, 5464}, {8192, 3022}, {8190, 3024}, {4096, 1512}, {1620, 598}, {1620, 1080}};
    for (unsigned i = 0; i < 6u; i++) {
        f = base();
        f.object_facts.current_width = f.cache_facts.current_width = dims[i][0];
        f.object_facts.current_height = f.cache_facts.current_height = dims[i][1];
        CK(!playback_mask_fit_jpeg_review_admit(&f));
    }
    f = base(); f.object_facts.words[4] = f.cache_facts.words[4] = 1220u; CK(!playback_mask_fit_jpeg_review_admit(&f)); /* RAW_L */
    f.object_facts.current_height = f.cache_facts.current_height = 5464u;
    CK(!playback_mask_fit_jpeg_review_admit(&f)); CK(playback_mask_fit_admit(&f));
    f = base(); f.object_facts.words[2] = f.cache_facts.words[2] = 4608u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.object_facts.words[0] = f.cache_facts.words[0] = 8u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.cache_facts.words[4] = 1220u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.cache_facts.current_height = 5464u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.object_known = 0u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.cache_known = 0u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.entry_lr = 0u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.output += 4u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.original_sp += 4u; f.output += 4u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.object = f.saved_object = 0x11dab0u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.saved_object = 0u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.drawmode = 1u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.rotation = 2u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.view.device = 2u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.view.orientation = 1u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    f = base(); f.view.color[1] = 1u; CK(!playback_mask_fit_jpeg_review_admit(&f));
    return 0;
}
'''

# Fake Canon memory for one fit call: the caller frame (stock region at sp+8 with
# canaries at sp+4 and sp+24), our trampoline frame (scope, then the saved register
# image at scope+184 whose r8 word is scope+PM_CPU_R8_OFFSET), the play object and
# the play-info cache published through playback_native_tags.
FIT_C = r'''
#include "playback_mask_native.h"
#include "playback_first_view.h"
#include "xpan_review_gate.h"
#include "frame_memory.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *test;
#define CK(x) do { if (!(x)) { fprintf(stderr, "%s: line %d: %s\n", test, __LINE__, #x); return 1; } } while (0)
#define SP 0x02001000u
static struct { uint32_t a, v; } memory[512];
static unsigned used, output_writes, r8_writes, bad_writes;
static struct playback_mask_fit_scope scope;
static struct playback_mask_cpu cpu, guide_cpu;
static uint32_t scope_va, cpu_va, guide_va, eligibility[3] = {1u, 0u, 1u}, token = XPAN_GATE_R_NONE, token_calls;
static uint32_t get(uint32_t a) { for (unsigned i = 0; i < used; i++) if (memory[i].a == a) return memory[i].v; return 0u; }
static void put(uint32_t a, uint32_t v) {
    for (unsigned i = 0; i < used; i++) if (memory[i].a == a) { memory[i].v = v; return; }
    if (used == sizeof memory / sizeof *memory) abort();
    memory[used].a = a; memory[used++].v = v;
}
static void half(uint32_t a, uint32_t v) {
    unsigned shift = (a & 3u) * 8u;
    put(a & ~3u, (get(a & ~3u) & ~(0xffffu << shift)) | ((v & 0xffffu) << shift));
}
uint32_t playback_mask_host_read(uint32_t a, uint32_t *v) {
    for (unsigned i = 0; i < used; i++) if (memory[i].a == a) { *v = memory[i].v; return 1u; }
    return 0u;
}
void playback_mask_host_write(uint32_t a, uint32_t v) {
    if (a >= SP + 8u && a < SP + 24u) output_writes++;
    else if (a == scope_va + PM_CPU_R8_OFFSET) r8_writes++;
    else bad_writes++;
    put(a, v);
}
uint32_t playback_mask_host_address(const void *p) {
    return p == &scope ? scope_va : p == &cpu ? cpu_va : p == &guide_cpu ? guide_va : 0u;
}
uint32_t playback_mask_host_native_get(uint32_t target) {
    if (target == PM_NATIVE_ASPECT_VALID) return eligibility[0];
    if (target == PM_NATIVE_HEADER_SET) return eligibility[1];
    if (target == PM_NATIVE_MODEL_MATCH) return eligibility[2];
    abort();
}
uint32_t playback_native_host_read(uint32_t a, uint32_t *v) { return playback_mask_host_read(a, v); }
uint32_t playback_native_host_address(const void *p) { (void)p; return 0u; }
struct playback_native_main_scope *playback_native_host_scope(uint32_t a) { (void)a; return 0; }
uint32_t playback_native_host_memcpy(uint32_t a, uint32_t b, uint32_t c) { (void)a; (void)b; (void)c; abort(); }
uint32_t playback_native_host_cache(uint32_t a) { (void)a; abort(); }
int root_ifd_memory_admitted(uint32_t a, uint32_t b) { (void)a; (void)b; return 0; }
void playback_native_main_continue(void) {}
void playback_mask_fit_continue(void) {}
uint32_t review_crop_jpeg_review(void) { token_calls++; return token; }

static void publish_object(const struct playback_box_facts *o) {
    struct playback_tag_lease l = playback_tags_begin(&playback_native_tags, 0);
    half(PN_OBJECT0 + 0x96u, o->words[0] & 15u);
    half(PN_OBJECT0 + PN_WIDTH_OFFSET, o->current_width);
    half(PN_OBJECT0 + PN_HEIGHT_OFFSET, o->current_height);
    put(PN_OBJECT0 + PN_HEADER_OFFSET, o->words[0] >> 8);
    playback_tags_end(&l, PN_OBJECT0, o, 1);
}
static void publish_cache(const struct playback_box_facts *c) {
    struct playback_tag_lease l = playback_tags_begin(&playback_native_tags, 4);
    half(PN_CACHE_KEY + 0x64u, c->words[0] & 15u);
    put(PN_CACHE_KEY + PN_CACHE_WIDTH_OFFSET, c->current_width);
    put(PN_CACHE_KEY + PN_CACHE_HEIGHT_OFFSET, c->current_height);
    put(PN_CACHE_FLAGS + 0x18u, c->words[0] >> 8);
    playback_tags_end(&l, PN_CACHE_KEY, c, 1);
}
/* Selector containers: pixel tokens and sizes (main from the object, thumbnail and preview fixed). */
static void containers(void) {
    put(PN_OBJECT0 + 0x2cu, 0x44f8a000u); put(PN_OBJECT0 + 0x24u, 0x44f8b000u); put(PN_OBJECT0 + 0x28u, 0x44f8c000u);
    half(PN_OBJECT0 + 0x36u, 160u); half(PN_OBJECT0 + 0x38u, 120u); half(PN_OBJECT0 + 0x3au, 1620u); half(PN_OBJECT0 + 0x3cu, 1080u);
    half(PN_OBJECT0 + 0x426u, 0u); half(PN_OBJECT0 + 0x428u, 159u); half(PN_OBJECT0 + 0x42au, 6u); half(PN_OBJECT0 + 0x42cu, 113u);
}
static unsigned is(const char *name) { return !strcmp(test, name); }
static uint32_t guide(void) {
    uint32_t sp = 0x02010000u;
    guide_va = sp - 56u;
    memset(&guide_cpu, 0, sizeof guide_cpu);
    guide_cpu.r[4] = PM_GUIDE_RESOURCE; guide_cpu.r[5] = sp + 0x60u;
    put(sp + 0x5cu, PM_GUIDE_CALLER); put(sp + 0x38u, sp + 0x60u); put(sp + 0x2cu, 0x1234u);
    return playback_mask_guide_decide(&guide_cpu);
}

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    test = argv[1];
    /* The JPEG-only XPan Quick Review call unless the case says otherwise. */
    struct playback_box_facts object = {{7u, 8192u, 3024u, 0u, 0u}, 8192u, 3024u}, cache;
    uint32_t selector = 0u, category = 7u, r8 = 1u;
    unsigned shape = 1u; /* the exact JPEG review shape: the token is asked */
    if (is("raw_l")) { object.words[4] = 1220u; object.current_height = 5464u; shape = 0u; }
    if (is("raw_l_3024")) { object.words[4] = 1220u; shape = 0u; }
    if (is("category_1")) { category = 1u; shape = 0u; }
    if (is("category_0")) { category = 0u; shape = 0u; }
    if (is("full_frame")) { object.words[2] = 4608u; object.current_height = 5464u; shape = 0u; }
    if (is("height_5464")) { object.current_height = 5464u; shape = 0u; }
    if (is("height_3022")) { object.current_height = 3022u; shape = 0u; }
    if (is("small")) { object.current_width = 1620u; object.current_height = 598u; shape = 0u; }
    if (is("thumb")) { selector = 1u; shape = 0u; }
    if (is("preview")) { selector = 2u; shape = 0u; }
    if (is("token_no_claim")) token = XPAN_GATE_R_NO_CLAIM;
    if (is("token_stale")) token = XPAN_GATE_R_STALE;
    if (is("token_not_xpan")) token = XPAN_GATE_R_NOT_XPAN;
    if (is("r8_0")) r8 = 0u;
    if (is("r8_2")) r8 = 2u;
    /* Camera (returned rows): Canon's ASPECT_VALID getter refuses a JPEG_L that is already
     * the aspect crop, so the region is never stored and fit returns Canon's 0. */
    if (strcmp(test, "eligible")) eligibility[0] = object.words[4] ? 1u : 0u;
    cache = object;
    if (is("cache_raw")) { cache.words[4] = 1220u; shape = 0u; }
    if (is("cache_dims")) { cache.current_height = 5464u; shape = 0u; }
    containers();
    half(PN_OBJECT0 + 0x3eu, object.current_width); /* the selector-0 container is the object's size */
    half(PN_OBJECT0 + 0x40u, object.current_height);
    publish_object(&object);
    publish_cache(&cache);
    put(PM_DISPLAY_WORD, 0u); put(PM_ORIENTATION_WORD, 0u); put(PM_CATEGORY_WORD, category);
    for (unsigned i = 0; i < 3u; i++) put(PM_CLEAR_COLOR + 4u * i, 0u);

    /* Our frame below the caller's: scope, then the saved image (APSR, pad, r0..r12, lr). */
    scope_va = SP - PM_CPU_IMAGE - PM_FIT_SCOPE_BYTES;
    cpu_va = SP - 56u;
    cpu.lr = is("wrong_caller") ? 0u : PM_FIT_CALLER;
    cpu.r[0] = SP + 8u; cpu.r[1] = cpu.r[4] = PN_OBJECT0; cpu.r[2] = cpu.r[5] = selector; cpu.r[3] = category;
    cpu.r[8] = r8;
    if (is("wrong_caller")) shape = 0u;
    put(scope_va + PM_CPU_R8_OFFSET, r8);
    put(SP + 4u, 0x11223344u); put(SP + 24u, 0x55667788u);
    for (unsigned n = 0; n < 4u; n++) put(SP + 8u + 4u * n, 0u); /* E07110C0..C8 zero the region */
    if (selector == 1u) { put(SP + 8u, 0u); put(SP + 12u, 6u); put(SP + 16u, 160u); put(SP + 20u, 108u); }

    playback_mask_fit_begin(&scope, &cpu, scope_va - PM_FIT_FRAME, (uint32_t)(uintptr_t)playback_mask_fit_continue);
    CK(token_calls == shape); /* the token is asked for the exact shape only */
    const unsigned admitted = scope.admitted;
    uint32_t stock[4];
    for (unsigned n = 0; n < 4u; n++) stock[n] = get(SP + 8u + 4u * n);
    const uint32_t original = selector == 1u ? 1u : 0u; /* Canon's own fit result for this call */
    if (is("late_object")) playback_tags_invalidate(&playback_native_tags, 0);
    if (is("late_category")) put(PM_CATEGORY_WORD, 1u);
    if (is("late_container")) put(PN_OBJECT0 + 0x2cu, 0x44f8a004u);
    if (is("nonzero_stock")) { put(SP + 8u, 2u); put(SP + 12u, 4u); put(SP + 16u, 100u); put(SP + 20u, 100u); stock[0] = 2u; stock[1] = 4u; stock[2] = 100u; stock[3] = 100u; }
    const uint32_t chosen = playback_mask_fit_end(&scope, original);

    CK(!bad_writes && get(SP + 4u) == 0x11223344u && get(SP + 24u) == 0x55667788u);
    const uint32_t slot = get(scope_va + PM_CPU_R8_OFFSET);
    /* Steered: exactly the admitted JPEG_L Quick Review with r8 == 1, current snapshots, Canon's stock 0. */
    static const char *const steer[] = {"jpeg", "eligible"};
    unsigned want = 0u;
    for (unsigned i = 0; i < sizeof steer / sizeof *steer; i++) want |= is(steer[i]);
    CK(r8_writes == want && slot == (want ? 0u : r8));
    if (is("eligible")) {
        /* Canon's getters pass (not seen on camera for JPEG_L): the whole JPEG is the region and fit returns 1,
         * so the caller takes its region route and never reads r8. */
        const uint32_t whole[4] = {0u, 0u, 8192u, 3024u};
        CK(admitted && chosen == 1u && output_writes == 4u);
        for (unsigned n = 0; n < 4u; n++) CK(get(SP + 8u + 4u * n) == whole[n]);
        CK(!guide()); /* the RAW_L-only guide hook stays stock */
        return 0;
    }
    if (is("raw_l") || is("raw_l_3024")) {
        /* The unchanged RAW_L path: the band in the object's coordinates; r8 is left alone. */
        const uint32_t band[4] = {0u, is("raw_l") ? 1220u : 0u, 8192u, 3024u};
        CK(admitted && chosen == 1u && output_writes == 4u && guide());
        for (unsigned n = 0; n < 4u; n++) CK(get(SP + 8u + 4u * n) == band[n]);
        return 0;
    }
    /* Everything else: Canon's result and region unchanged, nothing stored in the caller frame. */
    CK(chosen == original && output_writes == 0u);
    for (unsigned n = 0; n < 4u; n++) CK(get(SP + 8u + 4u * n) == stock[n]);
    static const char *const admitted_cases[] = {"jpeg", "r8_0", "r8_2", "late_object", "late_category",
                                                 "late_container", "nonzero_stock"};
    unsigned want_admitted = 0u;
    for (unsigned i = 0; i < sizeof admitted_cases / sizeof *admitted_cases; i++) want_admitted |= is(admitted_cases[i]);
    CK(admitted == want_admitted);
    CK(!guide());
    return 0;
}
'''

FIT_CASES = ["jpeg", "eligible", "raw_l", "raw_l_3024", "r8_0", "r8_2", "category_1", "category_0", "full_frame",
             "height_5464", "height_3022", "small", "thumb", "preview", "token_no_claim", "token_stale",
             "token_not_xpan", "cache_raw", "cache_dims", "wrong_caller", "late_object", "late_category",
             "late_container", "nonzero_stock"]
FIT_SOURCES = ["playback_mask_native.c", "playback_native.c", "playback_reject.c", "playback_first_view.c",
               "playback_tags.c", "playback_box.c", "frame_memory.c", "ifd_memory.c"]


def compile_c(tmp: Path, body: str, sources: list[str], defines: list[str]) -> Path:
    (tmp / "case.c").write_text(body)
    exe = tmp / "case"
    proc = subprocess.run([find_host_cc(), "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror", *defines,
                           "-I", str(XPAN_SRC), "-I", str(XPAN_SRC / "resident"), str(tmp / "case.c"), *(str(XPAN_SRC / s) for s in sources),
                           "-o", str(exe)], capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError("host compile failed:\n" + proc.stdout + proc.stderr)
    return exe


class QuickReviewFit(unittest.TestCase):
    def test_policy_admits_only_the_quick_review_shape(self):
        with tempfile.TemporaryDirectory() as t:
            exe = compile_c(Path(t), POLICY_C, ["playback_mask_native.c", "playback_box.c", "frame_memory.c"],
                            ["-DPLAYBACK_MASK_POLICY_ONLY"])
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_saved_r8_is_steered_only_for_the_admitted_jpeg_review(self):
        with tempfile.TemporaryDirectory() as t:
            exe = compile_c(Path(t), FIT_C, FIT_SOURCES, ["-DPLAYBACK_MASK_HOST", "-DPLAYBACK_NATIVE_HOST"])
            for case in FIT_CASES:
                with self.subTest(case=case):
                    run = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_r8_slot_is_the_trampoline_save_layout(self):
        # save_cpu: stmdb sp!,{r0-r12,lr}; sub sp,#8 (APSR word and pad); sub sp,#PM_FIT_SCOPE_BYTES.
        asm = (XPAN_SRC / "playback_mask_hooks.S").read_text()
        self.assertIn(" stmdb sp!,{r0-r12,lr}\n mrs r12,APSR\n sub sp,#8\n str r12,[sp]\n", asm)
        self.assertIn(" save_cpu\n sub sp,#PM_FIT_SCOPE_BYTES\n", asm)
        self.assertIn(" ldr r12,[sp]\n msr APSR_nzcvqg,r12\n add sp,#8\n ldmia sp!,{r0-r12,lr}\n", asm)
        header = (XPAN_SRC / "playback_mask_native.h").read_text()
        self.assertIn("#define PM_CPU_R8_OFFSET (PM_FIT_SCOPE_BYTES + 8u + 4u * 8u)", header)
        self.assertIn("#define PM_FIT_SCOPE_BYTES 184", header)
        self.assertEqual(playback_mask_profile.CONSTANTS["CPU_IMAGE"], 8 + 14 * 4)


class RouteWordPins(unittest.TestCase):
    def test_route_words_are_in_the_profile_and_a_fingerprinted_guard_page(self):
        inventory = json.loads((XPAN_TOOLS / "playback-mask-hooks.json").read_text())
        self.assertEqual(inventory, playback_mask_profile.inventory())
        words = {w["role"]: (w["va"], w["bytes"]) for w in inventory["route_words"]}
        self.assertEqual(words, {"r8_load": (0xE071107C, "ddf83880"), "r8_compare": (0xE0711116, "b8f1010f"),
                                 "r8_restore": (0xE071115C, "bde8f083")})
        contract, _, _ = feature_contract.load_hooks()
        guards = {int(v, 16) for v in contract["source_guard_pages"]}
        fingerprinted = {e["address"] for e in XPAN_TARGET_CONTRACT["extra_code_fingerprints"]}
        for va, _ in words.values():
            self.assertIn(va & ~4095, guards)
            self.assertIn(va & ~4095, fingerprinted)
        # The route needs no new hook, page or witness.
        self.assertNotIn(0xE0711000, {int(h["va"], 16) & ~4095 for h in feature_contract.load_hooks()[1]})

    def test_admission_refuses_a_rom_whose_route_word_differs(self):
        import xpan_build
        from test_xpan_contracts import make_synthetic_xpan_contract_and_rom
        manifest, contract, xpan_contract, hooks, rom = make_synthetic_xpan_contract_and_rom()
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "rom.bin"
            path.write_bytes(bytes(rom))
            kwargs = dict(indicator=None, stop_indicator=None, manifest=manifest, target_contract=contract,
                          xpan_contract=xpan_contract, xpan_hooks=hooks)
            xpan_build.admit_xpan_rom(path, **kwargs)
            va, raw = playback_mask_profile.ROUTE_WORDS[1][1:]
            bad = bytearray(rom)
            bad[va - manifest["rom_base"]] ^= 1  # cmp.w r8,#1 -> another immediate
            for region in xpan_contract["extra_code_fingerprints"]:  # keep page admission passing
                if region["address"] == va & ~4095:
                    import hashlib
                    off = region["address"] - manifest["rom_base"]
                    region["sha256"] = hashlib.sha256(bad[off:off + region["bytes"]]).hexdigest()
            path.write_bytes(bytes(bad))
            with self.assertRaisesRegex(ValueError, "route word r8_compare at 0xe0711116 differs from its pin"):
                xpan_build.admit_xpan_rom(path, **kwargs)


@unittest.skipUnless(__import__("os").environ.get("R5_ROM"), "opt-in real-ROM check")
class RealRomRouteWords(unittest.TestCase):
    def test_route_words_match_the_rom(self):
        rom = Path(__import__("os").environ["R5_ROM"]).read_bytes()
        for _, va, raw in playback_mask_profile.ROUTE_WORDS:
            self.assertEqual(rom[va - 0xE0000000:va - 0xE0000000 + len(raw) // 2].hex(), raw)


if __name__ == "__main__":
    unittest.main()
