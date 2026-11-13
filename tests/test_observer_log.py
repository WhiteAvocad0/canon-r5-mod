"""Public synthetic tests for R5O1/R5O2 capture-observer and worker-supplement decoder."""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from observer_log import V2_MAX_DUMPS, WORKER_KINDS, decode_observer_log

SOURCE = "0123456789abcdef"


def make_record(fmt: str, args: tuple[int, ...] = (), counter: int = 1, timer: int = 0x1000, cls: int = 0x84, lvl: int = 3) -> bytes:
    buf = bytearray(84)
    struct.pack_into("<II", buf, 0, counter, timer)
    buf[8] = 0xFF
    buf[9] = cls & 0xFF
    buf[10] = lvl & 0xFF
    fmt_bytes = fmt.encode("ascii") + b"\x00"
    assert len(fmt_bytes) + len(args) * 4 <= 73
    buf[11 : 11 + len(fmt_bytes)] = fmt_bytes
    for idx, val in enumerate(args):
        struct.pack_into("<I", buf, 80 - 4 * idx, val & 0xFFFFFFFF)
    return bytes(buf)


def make_group_v1(
    *,
    source: str = SOURCE,
    dump: int = 1,
    total_events: int = 2,
    overflow: int = 0,
    counter: int = 10,
) -> list[bytes]:
    return [
        make_record("R5O1 D=%u C=%u O=%u", (dump, total_events, overflow), counter),
        make_record(f"R5O1 S={source} T=%08X", (0x41632100,), counter + 1),
        make_record("R5O1 N=%u P=%08X L=%u V=%08X F=%u", (1, 0x12000003, 4, 100, 1), counter + 2),
        make_record("R5O1 N=%u P=%08X L=%u V=%08X F=%u", (2, 0x80000023, 4, 0, 1), counter + 3),
        make_record("R5O1 E=%u", (dump,), counter + 4),
    ]


def make_group_v2_with_worker(
    worker_specs: list[tuple[str, tuple[int, ...]]],
    *,
    source: str = SOURCE,
    dump: int = 1,
    before_samples: bool = False,
) -> list[bytes]:
    recs = [
        make_record("R5O2 D=%u C=%u O=%u", (dump, 2, 0), 10),
        make_record(f"R5O2 S={source} T=%08X", (0x41632100,), 11),
        make_record("R5O2 N=%u P=%08X L=%u V=%08X F=%u", (1, 0x12000003, 4, 100, 1), 12),
        make_record("R5O2 N=%u P=%08X L=%u V=%08X F=%u", (2, 0x80000023, 4, 0, 1), 13),
    ]
    if before_samples:
        for idx, (fmt, args) in enumerate(worker_specs):
            recs.append(make_record(fmt, args, 14 + idx))
        for idx in range(1, dump + 1):
            recs.append(make_record("R5O2 G=%u N=%u F=%u P=%08X V=%08X", (idx, idx, 1, 0x0139FECC, 500 + idx - 1), 30 + idx))
    else:
        for idx in range(1, dump + 1):
            recs.append(make_record("R5O2 G=%u N=%u F=%u P=%08X V=%08X", (idx, idx, 1, 0x0139FECC, 500 + idx - 1), 13 + idx))
        for idx, (fmt, args) in enumerate(worker_specs):
            recs.append(make_record(fmt, args, 30 + idx))
    recs.append(make_record("R5O2 E=%u", (dump,), 40))
    return recs


def default_worker_rows(
    *,
    boot: tuple[int, ...] = (1, 0, 1, 9, 3),
    task: tuple[int, ...] = (7, 7, 0, 1, 7),
    playback: tuple[int, ...] = (1, 1, 1, 0, 0),
    baseline: tuple[int, ...] = (1, 0x0139FECC, 113072, 9, 0),
    counters: tuple[int, ...] = (1, 0, 0, 0, 0),
) -> list[tuple[str, tuple[int, ...]]]:
    return [
        ("R5O2 I=%u R=%d A=%u O=%u C=%u", boot),
        ("R5O2 T=%d C=%d S=%d O=%u E=%u", task),
        ("R5O2 W=%u P=%u S=%u D=%u F=%u", playback),
        ("R5O2 B=%u C=%08X V=%u L=%u R=%u", baseline),
        ("R5O2 Q=%u C=%u D=%u X=%u R=%u", counters),
    ]


class ObserverLogTests(unittest.TestCase):
    def decode(self, records: list[bytes], source: str = SOURCE) -> dict:
        return decode_observer_log(b"".join(records), expected_source=source)

    def test_v1_valid_and_malformed_groups(self):
        res = self.decode(make_group_v1())
        self.assertEqual(res["rejected_candidates"], [])
        self.assertEqual(len(res["groups"]), 1)
        g = res["groups"][0]
        self.assertEqual(g["version"], "R5O1")
        self.assertTrue(g["retrieval_complete"])
        self.assertTrue(g["stored_history_complete"])
        self.assertEqual(len(g["events"]), 2)
        self.assertIsNone(g.get("worker_supplement"))

        # Mismatched source hash marks group as incomplete with source_mismatch
        res_bad_src = self.decode(make_group_v1(source="fedcba9876543210"))
        self.assertFalse(res_bad_src["groups"][0]["retrieval_complete"])
        self.assertIn("source_mismatch", res_bad_src["groups"][0]["problems"])

        # Overflow O=1 retains complete retrieval of stored events while marking stored history incomplete
        ovf_res = self.decode(make_group_v1(overflow=1))
        self.assertTrue(ovf_res["groups"][0]["retrieval_complete"])
        self.assertFalse(ovf_res["groups"][0]["stored_history_complete"])

        # Missing stored events mark retrieval incomplete
        trunc_res = self.decode(make_group_v1(total_events=5))
        self.assertFalse(trunc_res["groups"][0]["retrieval_complete"])
        self.assertIn("missing_duplicate_or_unordered_events", trunc_res["groups"][0]["problems"])

    def test_v2_counter_delta_and_worker_supplement_edges(self):
        self.assertEqual(V2_MAX_DUMPS, 8)
        self.assertEqual(
            list(WORKER_KINDS),
            ["worker_boot", "worker_task", "worker_playback", "worker_baseline", "worker_counters"],
        )

        specs = default_worker_rows(
            boot=(1, 0xFFFFFFFF, 1, 9, 3),
            task=(0x8001002A, 0x8001002A, 0xFFFFFFFE, 1, 6),
            counters=(0xFFFFFFFF, 0xFFFFFFFE, 3, 2, 1),
        )
        res = self.decode(make_group_v2_with_worker(specs, dump=2))
        self.assertEqual(res["rejected_candidates"], [])
        g = res["groups"][0]
        self.assertTrue(g["retrieval_complete"])
        self.assertTrue(g["stored_history_complete"])
        ws = g["worker_supplement"]
        self.assertIsNotNone(ws)
        self.assertEqual(ws["worker_boot"]["boot_rc_i32"], -1)
        self.assertEqual(ws["worker_task"]["raw_task_id_i32"], -2147418070)
        self.assertEqual(ws["worker_task"]["start_returned_i32"], -2)
        self.assertIs(ws["worker_task"]["worker_entered"], False)
        self.assertEqual(ws["worker_counters"]["shots_observed"], 0xFFFFFFFF)
        self.assertEqual(ws["worker_counters"]["coalesced_drops"], 0xFFFFFFFE)

        # Unstarted E=0 / mid-preemption E=3 cases
        mid = self.decode(make_group_v2_with_worker(default_worker_rows(task=(7, 7, 0xFFFFFFFF, 1, 3))))
        mws = mid["groups"][0]["worker_supplement"]
        self.assertTrue(mid["groups"][0]["retrieval_complete"])
        self.assertIsNone(mws["worker_task"]["start_returned"])
        self.assertIs(mws["worker_task"]["worker_entered"], True)

        unstarted = self.decode(
            make_group_v2_with_worker(
                default_worker_rows(
                    boot=(0, 0xFFFFFFFF, 0, 0, 1),
                    task=(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0, 0),
                )
            )
        )["groups"][0]
        self.assertTrue(unstarted["retrieval_complete"])
        self.assertIs(unstarted["worker_supplement"]["worker_boot"]["context_checked"], True)
        self.assertIs(unstarted["worker_supplement"]["worker_boot"]["context_ready"], False)
        self.assertIsNone(unstarted["worker_supplement"]["worker_task"]["create_returned"])

    def test_v2_worker_supplement_rejects_bad_rows_and_v1(self):
        base = default_worker_rows()
        for drop in range(5):
            g = self.decode(make_group_v2_with_worker([r for i, r in enumerate(base) if i != drop]))["groups"][0]
            self.assertFalse(g["retrieval_complete"])
            self.assertIn("missing_duplicate_or_unordered_worker_supplement", g["problems"])

        g_dup = self.decode(make_group_v2_with_worker([*base, base[0]]))["groups"][0]
        self.assertIn("missing_duplicate_or_unordered_worker_supplement", g_dup["problems"])

        g_swap = self.decode(make_group_v2_with_worker([base[1], base[0], *base[2:]]))["groups"][0]
        self.assertIn("missing_duplicate_or_unordered_worker_supplement", g_swap["problems"])

        g_early = self.decode(make_group_v2_with_worker(base, before_samples=True))["groups"][0]
        self.assertIn("worker_supplement_before_prior_rows", g_early["problems"])

        g_ctx_bad = self.decode(make_group_v2_with_worker(default_worker_rows(boot=(1, 0, 1, 9, 2))))["groups"][0]
        self.assertIn("inconsistent_worker_context", g_ctx_bad["problems"])

        g_base_bad = self.decode(make_group_v2_with_worker(default_worker_rows(baseline=(1, 0, 10, 9, 0))))["groups"][0]
        self.assertIn("inconsistent_worker_baseline", g_base_bad["problems"])

        v1_bad = make_group_v1()
        v1_bad.insert(4, make_record("R5O1 I=%u R=%d A=%u O=%u C=%u", (1, 0, 1, 9, 1), 100))
        res_v1 = self.decode(v1_bad)
        self.assertTrue(any(r["reason"] == "unknown_observer_format" for r in res_v1["rejected_candidates"]))


if __name__ == "__main__":
    unittest.main()
