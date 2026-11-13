"""Host C compilation tests for src/r5_221/{sound_boot.c, sound_worker.c, observer.c}."""
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "r5_221"
CFG = ROOT / "config"
sys.path.insert(0, str(ROOT / "tools"))

from build import observer_source_header
from observer_log import decode_observer_log


def find_host_cc() -> str:
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang") or "/usr/bin/cc"
    if not Path(cc).exists() and not shutil.which(cc):
        raise AssertionError("host C11 compiler (cc/gcc/clang) required to run native C logic tests")
    return cc


STUB_C = r'''
#include "observer.h"
#include "sound_boot.h"
#include "sound_worker.h"
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static unsigned stub_cpsr = 0x10u;
static unsigned stub_100c = 0u;
static int stub_102c = 0;

static int stub_create_rc = 7;
static int stub_start_rc = 0;
static int stub_delete_rc = 0;
static unsigned create_calls = 0, start_calls = 0, delete_calls = 0;
static unsigned play_calls = 0;
static int preempt_in_start = 0;
static int disable_during_preempt = 0;
static int capture_mid_preempt_report = 0;
static unsigned preempt_counter_val = 113072u;
static jmp_buf worker_jmp;

static void stub_read_context(unsigned *cpsr_out, unsigned *s100c, int *s102c) {
    *cpsr_out = stub_cpsr;
    *s100c = stub_100c;
    *s102c = stub_102c;
}

static int stub_create(sound_worker_entry_fn entry, void *arg, void *stack_addr,
                       unsigned stack_bytes, int prio, unsigned options, const char *name) {
    (void)entry; (void)arg; (void)stack_addr; (void)stack_bytes; (void)prio; (void)options; (void)name;
    create_calls++;
    return stub_create_rc;
}

static int stub_start(int raw_id) {
    (void)raw_id;
    start_calls++;
    if (preempt_in_start && stub_start_rc == 0) {
        if (setjmp(worker_jmp) == 0) {
            sound_worker_entry(0);
        }
    }
    return stub_start_rc;
}

static int stub_delete(int raw_id) {
    (void)raw_id;
    delete_calls++;
    return stub_delete_rc;
}

static unsigned stub_read_counter(unsigned *ctx_out, unsigned *val_out) {
    *ctx_out = 0x0139FECCu;
    *val_out = preempt_counter_val;
    return 1u;
}

static unsigned stub_play_shutter(void) {
    play_calls++;
    return SOUND_WORKER_AO_SETTLED_OBSERVED;
}

static unsigned sleep_calls = 0;
static observer_callback obs_cb = 0;
static void *obs_arg = 0;
static void (*obs_tok)(unsigned) = 0;
static char messages[256][128];
static char msg_fmts[256][73];
static unsigned msg_args[256][5];
static unsigned msg_arg_counts[256];
static unsigned msg_cls[256], msg_lvl[256], msg_count = 0;

static void stub_sleep_ms(unsigned ms) {
    (void)ms;
    sleep_calls++;
    if (preempt_in_start && sleep_calls == 1u) {
        preempt_counter_val++;
    } else if (preempt_in_start && sleep_calls >= 2u) {
        if (disable_during_preempt) {
            sound_worker_set_enabled(0u);
        }
        if (capture_mid_preempt_report && obs_cb) {
            msg_count = 0;
            unsigned beep = 0u;
            obs_cb(0x80000023u, obs_arg, &beep, 4u);
        }
        longjmp(worker_jmp, 1);
    }
}

const struct sound_worker_api sound_worker_api = {
    stub_create, stub_start, stub_delete, stub_read_counter, stub_play_shutter, stub_sleep_ms,
};
const struct sound_boot_api sound_boot_api = {
    stub_read_context, sound_worker_start,
};

static void stub_reg(const unsigned *p, unsigned n, observer_callback cb, void *arg, void (*tok)(unsigned)) {
    (void)p; (void)n;
    obs_cb = cb; obs_arg = arg; obs_tok = tok;
}
static void stub_ack(unsigned tok, unsigned prop) { (void)tok; (void)prop; }
static unsigned count_fmt_specs(const char *fmt) {
    unsigned n = 0;
    for (const char *p = fmt; *p; p++) {
        if (*p == '%' && p[1] != '%') n++;
    }
    return n > 5u ? 5u : n;
}
static void stub_debug(unsigned cls, unsigned level, const char *fmt, ...) {
    if (msg_count >= 256u) return;
    msg_cls[msg_count] = cls;
    msg_lvl[msg_count] = level;
    snprintf(msg_fmts[msg_count], 73, "%s", fmt);
    unsigned nargs = count_fmt_specs(fmt);
    msg_arg_counts[msg_count] = nargs;
    va_list ap, ap_raw;
    va_start(ap, fmt);
    va_copy(ap_raw, ap);
    for (unsigned i = 0; i < 5u; i++) {
        msg_args[msg_count][i] = i < nargs ? va_arg(ap_raw, unsigned) : 0u;
    }
    va_end(ap_raw);
    vsnprintf(messages[msg_count], 128, fmt, ap);
    va_end(ap);
    msg_count++;
}
unsigned observer_read_counter(unsigned *ctx_out, unsigned *val_out) {
    return stub_read_counter(ctx_out, val_out);
}
const struct observer_api observer_api = {
    stub_reg, stub_ack, stub_debug, observer_read_counter, sound_boot_report,
};

void harness_reset(void) {
    sound_boot_reset();
    memset((void *)&sound_worker_state, 0, sizeof(sound_worker_state));
    memset((void *)&observer_state, 0, sizeof(observer_state));
    stub_cpsr = 0x10u; stub_100c = 0u; stub_102c = 0;
    stub_create_rc = 7; stub_start_rc = 0; stub_delete_rc = 0;
    create_calls = start_calls = delete_calls = play_calls = sleep_calls = 0;
    preempt_in_start = disable_during_preempt = capture_mid_preempt_report = 0;
    preempt_counter_val = 113072u;
    msg_count = 0;
    memset(messages, 0, sizeof(messages));
}

void set_ctx(unsigned cpsr, unsigned s100c, int s102c) {
    stub_cpsr = cpsr; stub_100c = s100c; stub_102c = s102c;
}
void set_lifecycle_rc(int create_rc, int start_rc, int delete_rc) {
    stub_create_rc = create_rc; stub_start_rc = start_rc; stub_delete_rc = delete_rc;
}
void set_preempt(int preempt, int disable_after) {
    preempt_in_start = preempt; disable_during_preempt = disable_after;
}
void set_mid_preempt_report(int enable) {
    capture_mid_preempt_report = enable;
    if (enable) {
        observer_register();
        if (obs_tok) obs_tok(0x41632100u);
    }
}
void copy_packed_records(unsigned char *dst) {
    for (unsigned idx = 0; idx < msg_count; idx++) {
        unsigned char *rec = dst + idx * 84u;
        memset(rec, 0, 84u);
        unsigned ctr = idx + 1u;
        unsigned tmr = 0x2000u + idx;
        memcpy(rec + 0, &ctr, 4);
        memcpy(rec + 4, &tmr, 4);
        rec[8] = 0xFFu;
        rec[9] = (unsigned char)msg_cls[idx];
        rec[10] = (unsigned char)msg_lvl[idx];
        size_t flen = strlen(msg_fmts[idx]);
        if (flen >= 72u) flen = 71u;
        memcpy(rec + 11, msg_fmts[idx], flen + 1u);
        for (unsigned a = 0; a < msg_arg_counts[idx]; a++) {
            memcpy(rec + 80u - 4u * a, &msg_args[idx][a], 4);
        }
    }
}
void emit_report_only(void) {
    msg_count = 0;
    sound_boot_report(stub_debug, 0x84u, 3u);
}
void trigger_observer_dump(unsigned extra_events) {
    observer_register();
    if (obs_tok) obs_tok(0x41632100u);
    unsigned v = 113072u;
    for (unsigned i = 0; i < extra_events; i++) {
        v += 1u;
        if (obs_cb) obs_cb(0x12000003u, obs_arg, &v, 4u);
    }
    msg_count = 0;
    unsigned beep = 0u;
    if (obs_cb) obs_cb(0x80000023u, obs_arg, &beep, 4u);
}
unsigned get_msg_count(void) { return msg_count; }
const char *get_msg(unsigned i) { return i < msg_count ? messages[i] : ""; }
unsigned get_task_calls(unsigned which) {
    if (which == 0) return create_calls;
    if (which == 1) return start_calls;
    if (which == 2) return delete_calls;
    return play_calls;
}
'''


class SoundWorkerHostCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "stub.c").write_text(STUB_C)
        cfg = json.loads((CFG / "firmware.json").read_text())["observer"]
        (root / "observer-source.h").write_text(observer_source_header(cfg, "0123456789abcdef"))
        cc = find_host_cc()
        lib_path = root / "bootglue.so"
        cmd = [
            cc, "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
            "-I", str(SRC), "-I", str(root),
            str(SRC / "sound_boot.c"),
            str(SRC / "sound_worker.c"),
            str(SRC / "observer.c"),
            str(root / "stub.c"),
            "-o", str(lib_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        cls.lib = ctypes.CDLL(str(lib_path))
        cls.lib.set_ctx.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
        cls.lib.set_lifecycle_rc.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        cls.lib.set_preempt.argtypes = [ctypes.c_int, ctypes.c_int]
        cls.lib.set_mid_preempt_report.argtypes = [ctypes.c_int]
        cls.lib.copy_packed_records.argtypes = [ctypes.c_void_p]
        cls.lib.sound_boot_record_boot_return.argtypes = [ctypes.c_int]
        cls.lib.sound_boot_record_startup_outcome.argtypes = [ctypes.c_uint]
        cls.lib.sound_boot_start_worker.restype = ctypes.c_int
        cls.lib.sound_boot_context_is_ready.restype = ctypes.c_uint
        cls.lib.trigger_observer_dump.argtypes = [ctypes.c_uint]
        cls.lib.get_msg_count.restype = ctypes.c_uint
        cls.lib.get_msg.argtypes = [ctypes.c_uint]
        cls.lib.get_msg.restype = ctypes.c_char_p
        cls.lib.get_task_calls.argtypes = [ctypes.c_uint]
        cls.lib.get_task_calls.restype = ctypes.c_uint
        cls.lib.sound_worker_poll_once.restype = ctypes.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.lib.harness_reset()

    def _msgs(self) -> list[str]:
        return [self.lib.get_msg(i).decode() for i in range(self.lib.get_msg_count())]

    def test_context_gate_unready_blocks_worker_creation(self):
        self.lib.emit_report_only()
        self.assertEqual(self._msgs()[0], "R5O2 I=0 R=-1 A=0 O=0 C=0")

        for cpsr, s100c, s102c in [
            (0x90, 0, 0),
            (0x10, 1, 0),
            (0x10, 0xFFFFFFFF, 0),
            (0x10, 0, 1),
        ]:
            with self.subTest(cpsr=hex(cpsr), s100c=hex(s100c), s102c=s102c):
                self.lib.harness_reset()
                self.lib.set_ctx(cpsr, s100c, s102c)
                self.assertEqual(self.lib.sound_boot_context_is_ready(), 0)
                self.assertEqual(self.lib.sound_boot_start_worker(), -1)
                self.assertEqual(self.lib.get_task_calls(0), 0)
                self.lib.emit_report_only()
                msgs = self._msgs()
                self.assertEqual(msgs[0], "R5O2 I=0 R=-1 A=0 O=0 C=1")
                self.assertEqual(msgs[1], "R5O2 T=-1 C=-1 S=-1 O=0 E=0")

    def test_mid_preemption_single_shot_and_decoder_roundtrip(self):
        self.lib.set_ctx(0x10, 0, 0)
        self.lib.sound_boot_record_boot_return(0)
        self.lib.sound_boot_record_startup_outcome(9)
        self.lib.set_preempt(1, 0)
        self.lib.set_mid_preempt_report(1)
        self.assertEqual(self.lib.sound_boot_start_worker(), 0)
        self.assertEqual(self.lib.get_task_calls(0), 1)
        self.assertEqual(self.lib.get_task_calls(1), 1)
        self.assertEqual(self.lib.get_task_calls(2), 0)
        self.assertEqual(self.lib.get_task_calls(3), 1)

        n = self.lib.get_msg_count()
        buf = ctypes.create_string_buffer(n * 84)
        self.lib.copy_packed_records(buf)
        decoded = decode_observer_log(buf.raw, expected_source="0123456789abcdef")
        self.assertEqual(decoded["rejected_candidates"], [])
        self.assertEqual(len(decoded["groups"]), 1)
        g = decoded["groups"][0]
        self.assertTrue(g["retrieval_complete"], g["problems"])
        ws = g["worker_supplement"]
        self.assertEqual(ws["worker_boot"]["startup_outcome"], 9)
        self.assertIs(ws["worker_task"]["worker_entered"], True)
        self.assertIs(ws["worker_task"]["start_return_observed"], False)
        self.assertEqual(ws["worker_playback"]["playback_attempts"], 1)
        self.assertEqual(ws["worker_playback"]["playback_successes"], 1)
        self.assertEqual(ws["worker_counters"]["shots_observed"], 1)

    def test_failed_start_deletes_created_task_and_reports_signed_id(self):
        # delete_rc=0 releases ownership and resets raw_task_id while preserving create_returned_raw
        self.lib.set_ctx(0x10, 0, 0)
        self.lib.set_lifecycle_rc(-2147418070, -2, 0)
        self.assertEqual(self.lib.sound_boot_start_worker(), -1)
        self.assertEqual(self.lib.get_task_calls(0), 1)
        self.assertEqual(self.lib.get_task_calls(1), 1)
        self.assertEqual(self.lib.get_task_calls(2), 1)

        self.lib.trigger_observer_dump(1)
        n = self.lib.get_msg_count()
        buf = ctypes.create_string_buffer(n * 84)
        self.lib.copy_packed_records(buf)
        g = decode_observer_log(buf.raw, expected_source="0123456789abcdef")["groups"][0]
        self.assertTrue(g["retrieval_complete"])
        wt = g["worker_supplement"]["worker_task"]
        self.assertEqual(wt["raw_task_id_raw"], 0xFFFFFFFF)
        self.assertEqual(wt["create_returned_raw"], 0x8001002A)
        self.assertEqual(wt["create_returned"], -2147418070)
        self.assertEqual(wt["start_returned"], -2)
        self.assertEqual(wt["task_owned"], 0)
        self.assertIs(wt["delete_return_observed"], True)

        # Failed delete retains opaque raw_task_id and task_owned=1
        self.lib.harness_reset()
        self.lib.set_ctx(0x10, 0, 0)
        self.lib.set_lifecycle_rc(-2147418070, -2, -3)
        self.assertEqual(self.lib.sound_boot_start_worker(), -1)
        self.lib.trigger_observer_dump(1)
        n = self.lib.get_msg_count()
        buf = ctypes.create_string_buffer(n * 84)
        self.lib.copy_packed_records(buf)
        g2 = decode_observer_log(buf.raw, expected_source="0123456789abcdef")["groups"][0]
        wt2 = g2["worker_supplement"]["worker_task"]
        self.assertEqual(wt2["raw_task_id_raw"], 0x8001002A)
        self.assertEqual(wt2["task_owned"], 1)


if __name__ == "__main__":
    unittest.main()
