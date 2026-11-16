"""Portable host C compilation and behavior tests for XPan native policies and silent worker."""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
XPAN_SRC = ROOT / "src" / "experimental" / "xpan"
XPAN_RES = XPAN_SRC / "resident"
XPAN_TOOLS = XPAN_SRC / "tools"
R5_221_SRC = ROOT / "src" / "r5_221"
CFG = ROOT / "config"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(XPAN_TOOLS))

from build import observer_source_header
import frame_row_profile
from host_cc import find_host_cc


class XPanNativeCHostTests(unittest.TestCase):
    def test_playback_mask_fit_projection_and_view_profiles_in_c(self):
        cc = find_host_cc()
        test_c = r'''
#include "playback_mask_native.h"
#include "playback_first_view.h"
#include <stdio.h>
#include <string.h>

#define CK(x) do { if (!(x)) { fprintf(stderr, "fail at line %d\n", __LINE__); return 1; } } while (0)

int main(void) {
    CK(PM_FIT_SCOPE_BYTES == 184u);
    CK(sizeof(struct playback_mask_view_owner) == 40u);
    CK(PFV_R_COUNT == 67u);

    struct playback_mask_fit_facts f = {0};
    f.entry_lr = 0xe07110fdu;
    f.original_sp = 0x02001000u;
    f.output = f.original_sp + 8u;
    f.object = f.saved_object = 0x11ce10u;
    f.object_known = f.cache_known = 1u;
    f.object_facts = (struct playback_box_facts){{7u, 8192u, 3024u, 0u, 1220u}, 1620u, 1080u};
    f.cache_facts = f.object_facts;

    /* Admitted (device, orientation, r7=0) matrix */
    const unsigned devs[4] = {0u, 0u, 1u, 1u};
    const unsigned oris[4] = {0u, 2u, 0u, 2u};
    for (unsigned role = 0u; role < 3u; role++) {
        f.selector = f.saved_selector = role;
        for (unsigned i = 0u; i < 4u; i++) {
            f.view.device = devs[i];
            f.view.orientation = oris[i];
            f.rotation = 0u;
            CK(playback_mask_fit_admit(&f));
        }
    }

    /* Declined profiles: unknown device, non-0/2 orientation, non-zero rotation, category 13, non-black clear */
    f.selector = f.saved_selector = 0u;
    f.view.device = 2u; f.view.orientation = 0u; CK(!playback_mask_fit_admit(&f));
    f.view.device = 0u; f.view.orientation = 1u; CK(!playback_mask_fit_admit(&f));
    f.view.orientation = 3u; CK(!playback_mask_fit_admit(&f));
    f.view.orientation = 0x5au; CK(!playback_mask_fit_admit(&f));
    f.view.orientation = 2u; f.rotation = 2u; CK(!playback_mask_fit_admit(&f));
    f.rotation = 0u; f.view.category = 13u; f.category = 13u; CK(!playback_mask_fit_admit(&f));
    f.view.category = 0u; f.category = 0u; f.view.color[0] = 1u; CK(!playback_mask_fit_admit(&f));

    /* Exact 512:189 projection across selectors 0/1/2 */
    const uint32_t want_full[4] = {0u, 1220u, 8192u, 3024u};
    const uint32_t want_fhd[4] = {0u, 241u, 1620u, 598u};
    const uint32_t want_thumb[4] = {0u, 30u, 160u, 59u};
    uint32_t out[4] = {0};

    struct playback_mask_projection_facts p0 = {0u, 8192u, 5464u, 0u, {0,0,0,0}, 512u, 189u};
    CK(playback_mask_project(&p0, out) && memcmp(out, want_full, 16) == 0);

    struct playback_mask_projection_facts p2 = {2u, 1620u, 1080u, 0u, {0,0,0,0}, 512u, 189u};
    CK(playback_mask_project(&p2, out) && memcmp(out, want_fhd, 16) == 0);

    struct playback_mask_projection_facts p1 = {1u, 160u, 120u, 1u, {0u, 6u, 160u, 108u}, 512u, 189u};
    CK(playback_mask_project(&p1, out) && memcmp(out, want_thumb, 16) == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "test.c").write_text(test_c)
            exe = t / "test_mask"
            srcs = [
                "playback_mask_native.c", "playback_native.c", "playback_box.c",
                "playback_tags.c", "playback_reject.c", "playback_first_view.c",
                "frame_memory.c", "ifd_memory.c",
            ]
            proc = subprocess.run(
                [
                    cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-DPLAYBACK_MASK_HOST", "-DPLAYBACK_MASK_POLICY_ONLY",
                    "-DPLAYBACK_NATIVE_HOST", "-DPLAYBACK_NATIVE_POLICY_ONLY",
                    "-I", str(XPAN_SRC), str(t / "test.c"),
                    *[str(XPAN_SRC / s) for s in srcs],
                    "-o", str(exe),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_ifd_memory_and_playback_box_in_c(self):
        cc = find_host_cc()
        with tempfile.TemporaryDirectory() as tmp:
            so = Path(tmp) / "ifd_box.so"
            proc = subprocess.run(
                [
                    cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    "-I", str(XPAN_SRC),
                    str(XPAN_SRC / "ifd_memory.c"),
                    str(XPAN_SRC / "frame_memory.c"),
                    str(XPAN_SRC / "playback_box.c"),
                    "-o", str(so),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lib = ctypes.CDLL(str(so))
            lib.frame_memory_data_span.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
            lib.frame_memory_data_span.restype = ctypes.c_uint32
            lib.ifd_memory_span.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
            lib.ifd_memory_span.restype = ctypes.c_uint32
            lib.ifd_memory_mapping_supported.argtypes = [
                ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32
            ]
            lib.ifd_memory_mapping_supported.restype = ctypes.c_uint32
            lib.playback_box_kind.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
            lib.playback_box_kind.restype = ctypes.c_uint32

            self.assertEqual(lib.frame_memory_data_span(0x44F88214, 44), 0)
            self.assertEqual(lib.ifd_memory_span(0x44F88214, 44), 1)
            self.assertEqual(lib.ifd_memory_span(0x45003000, 44), 0)
            desc16 = (ctypes.c_uint32 * 16)(*([0x44054402] * 16))
            self.assertEqual(lib.ifd_memory_mapping_supported(0x44F88214, 44, desc16, 16), 1)
            desc16[0] ^= 4
            self.assertEqual(lib.ifd_memory_mapping_supported(0x44F88214, 44, desc16, 16), 0)

            raw_facts = (ctypes.c_uint32 * 7)(7, 8192, 3024, 0, 1220, 8192, 5464)
            jpg_facts = (ctypes.c_uint32 * 7)(7, 8192, 3024, 0, 0, 8192, 3024)
            bad_facts = (ctypes.c_uint32 * 7)(7, 8192, 4608, 0, 428, 8192, 5464)
            self.assertEqual(lib.playback_box_kind(raw_facts), 1)
            self.assertEqual(lib.playback_box_kind(jpg_facts), 2)
            self.assertEqual(lib.playback_box_kind(bad_facts), 0)

    def test_frame_native_and_row_projection_with_synthetic_header_in_c(self):
        cc = find_host_cc()
        test_c = r'''
#include "frame_native.h"
#include "frame-row/frame_row.h"
#include "frame-row/frame_row_constants.h"
#include <stdio.h>
#include <string.h>

#define CK(x) do { if (!(x)) { fprintf(stderr, "fail at line %d\n", __LINE__); return 1; } } while (0)

int main(void) {
    uint32_t out[12];
    memcpy(out, xpan_frame_row_cases[0].original, sizeof out);
    CK(xpan_frame_row_project(7u, out) == 1u);
    CK(memcmp(out, xpan_frame_row_cases[0].projected, sizeof out) == 0);
    CK(xpan_frame_row_project(7u, out) == 2u);
    CK(xpan_frame_row_project(1u, out) == 0u);

    struct frame_native_facts f = {0};
    f.role = FN_ROLE_ROW;
    f.entry_lr = FN_ROW_LR;
    f.outer_lr = FN_ROW_OUTER_LR;
    f.native_sp = 0x01001000u;
    f.output_x = f.native_sp + FN_ROW_OUTPUT;
    f.context = 0x001231B8u;
    f.display = 1u;
    f.display_mask = 1u;
    f.descriptor = FN_DESCRIPTORS;
    f.descriptor_index = 0u;
    f.descriptor_context = f.context;
    f.rich = 1u;
    f.tag_pointer = f.context;
    f.tag_frame = 0x01001000u;
    f.native_frame = 0x01001000u;
    f.tag_raw = 7u;
    f.tag_revision = 1u;
    f.tag_ready = 1u;
    f.tag_same = 1u;
    memcpy(f.row, xpan_frame_row_cases[0].original, sizeof f.row);
    CK(frame_native_admit(&f) == 1u);

    /* EVF display=0x10 */
    f.display = 0x10u;
    f.display_mask = 0x10u;
    memcpy(f.row, xpan_frame_row_cases[2].original, sizeof f.row);
    CK(frame_native_admit(&f) == 3u);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "frame-row").mkdir()
            (t / "frame-row" / "frame_row_constants.h").write_text(
                frame_row_profile.render_synthetic_constants_header()
            )
            (t / "test.c").write_text(test_c)
            exe = t / "test_frame"
            proc = subprocess.run(
                [
                    cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-DFRAME_NATIVE_HOST", "-DFRAME_NATIVE_POLICY_ONLY",
                    "-I", str(t), "-I", str(t / "frame-row"), "-I", str(XPAN_SRC),
                    str(t / "test.c"),
                    str(XPAN_SRC / "frame_native.c"),
                    str(XPAN_SRC / "frame_memory.c"),
                    str(XPAN_SRC / "frame-row" / "frame_row.c"),
                    "-o", str(exe),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_silent_crop_only_worker_disables_audio_in_c(self):
        cc = find_host_cc()
        stub_c = r'''
#include "observer.h"
#include "sound_boot.h"
#include "sound_worker.h"
#include "boot_mmu.h"
#include <stdio.h>
#include <string.h>

static int stub_create(sound_worker_entry_fn entry, void *arg, void *stack_addr,
                       unsigned stack_bytes, int prio, unsigned options, const char *name) {
    (void)entry; (void)arg; (void)stack_addr; (void)stack_bytes; (void)prio; (void)options; (void)name;
    return 11;
}
static int stub_start(int raw_id) { (void)raw_id; return 0; }
static int stub_delete(int raw_id) { (void)raw_id; return 0; }
static unsigned stub_read_counter(unsigned *c, unsigned *v) { *c = 0x1234u; *v = 55u; return 1u; }
static void stub_sleep_ms(unsigned ms) { (void)ms; }
static void stub_read_ctx(unsigned *cpsr, unsigned *s100c, int *s102c) { *cpsr = 0x10u; *s100c = 0u; *s102c = 0; }
static void stub_reg(const unsigned *p, unsigned n, observer_callback cb, void *arg, void (*tok)(unsigned)) {
    (void)p; (void)n; (void)cb; (void)arg; (void)tok;
}
static void stub_ack(unsigned t, unsigned p) { (void)t; (void)p; }
static void stub_dbg(unsigned c, unsigned l, const char *f, ...) { (void)c; (void)l; (void)f; }
void boot_mmu_request_report(void) {}
void boot_mmu_tick(void) {}

#ifdef NON_NULL_PLAY_SHUTTER
static unsigned stub_dummy_play(void) { return 0u; }
const struct sound_worker_api sound_worker_api = {
    stub_create, stub_start, stub_delete, stub_read_counter, stub_dummy_play, stub_sleep_ms
};
#else
const struct sound_worker_api sound_worker_api = {
    stub_create, stub_start, stub_delete, stub_read_counter, 0, stub_sleep_ms
};
#endif
const struct sound_boot_api sound_boot_api = { stub_read_ctx, sound_worker_start };
const struct observer_api observer_api = { stub_reg, stub_ack, stub_dbg, stub_read_counter, sound_boot_report };
unsigned observer_read_counter(unsigned *c, unsigned *v) { return stub_read_counter(c, v); }

int main(void) {
#ifdef NON_NULL_PLAY_SHUTTER
    /* Non-null play_shutter must be rejected in R5_CROP_ONLY mode */
    return sound_boot_start_worker() == -1 ? 0 : 1;
#else
    if (sound_boot_start_worker() != 0) return 1;
    sound_worker_set_enabled(1u);
    if (sound_worker_is_enabled() != 0u) return 2;
    if (sound_worker_poll_once() != SOUND_WORKER_POLL_DISABLED) return 3;
    return 0;
#endif
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cfg = json.loads((CFG / "firmware.json").read_text())["observer"]
            (t / "observer-source.h").write_text(observer_source_header(cfg, "0123456789abcdef"))
            (t / "stub.c").write_text(stub_c)
            exe = t / "test_silent_worker"
            proc = subprocess.run(
                [
                    cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-DR5_CROP_ONLY=1",
                    "-I", str(t), "-I", str(XPAN_RES), "-I", str(R5_221_SRC),
                    str(R5_221_SRC / "sound_boot.c"),
                    str(XPAN_RES / "sound_worker.c"),
                    str(XPAN_RES / "observer.c"),
                    str(XPAN_RES / "boot_mmu.c"),
                    str(t / "stub.c"),
                    "-o", str(exe),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, f"silent worker failed rc={run.returncode}")

            exe_bad = t / "test_silent_worker_bad"
            proc_bad = subprocess.run(
                [
                    cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-DR5_CROP_ONLY=1", "-DNON_NULL_PLAY_SHUTTER=1",
                    "-I", str(t), "-I", str(XPAN_RES), "-I", str(R5_221_SRC),
                    str(R5_221_SRC / "sound_boot.c"),
                    str(XPAN_RES / "sound_worker.c"),
                    str(XPAN_RES / "observer.c"),
                    str(XPAN_RES / "boot_mmu.c"),
                    str(t / "stub.c"),
                    "-o", str(exe_bad),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_bad.returncode, 0, proc_bad.stderr)
            run_bad = subprocess.run([str(exe_bad)], capture_output=True, text=True)
            self.assertEqual(run_bad.returncode, 0, f"non-null play_shutter was not rejected rc={run_bad.returncode}")


if __name__ == "__main__":
    unittest.main()
