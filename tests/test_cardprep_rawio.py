"""Raw access through authopen fd passing, against image files and a fake authopen.

The fake (cardprep_fake_authopen.py) sends a real descriptor of a temporary image
file over a real UNIX socketpair, so the SCM_RIGHTS receive path runs for real.
"""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cardprep_images as ci
from cardprep import devices, layout, rawio

START = ci.LAB_EXFAT["start_lba"] * 512


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(ci.FD_PASSING, "needs UNIX sockets with SCM_RIGHTS")
class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardprep-rawio-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.mbr, self.area, self.disk_bytes = ci.exfat_card()
        self.disk = self.tmp / "disk.img"
        ci.write_disk_image(self.disk, self.mbr, self.area, ci.LAB_EXFAT["start_lba"])
        self.raw = rawio.RawAccess(command=ci.fake_authopen(), check=ci.same_file)

    def file_area(self) -> bytes:
        with open(self.disk, "rb") as f:
            f.seek(START)
            return f.read(12288)

    def poke(self, offset: int, data: bytes) -> None:
        with open(self.disk, "r+b") as f:
            f.seek(offset)
            f.write(data)

    def write(self, ranges, after, *, expect=None, mbr=None, area=None, raw=None):
        current = self.file_area()
        return (raw or self.raw).write(
            str(self.disk), START, ranges, after,
            expect_sha256=expect or sha(b"".join(current[o:o + n] for o, n in ranges)),
            expect_mbr_sha256=mbr or sha(self.disk.read_bytes()[:512]),
            expect_area_sha256=area or sha(current))

    def plan(self):
        return layout.plan_markers(self.mbr, self.area, disk_bytes=self.disk_bytes)


class OpenDevice(Images):
    def test_descriptor_arrives_over_scm_rights(self):
        fd = rawio.open_device(str(self.disk), os.O_RDONLY, command=ci.fake_authopen())
        try:
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            self.assertEqual(os.pread(fd, 2, 510), b"\x55\xaa")
            ci.same_file(fd, str(self.disk))
        finally:
            os.close(fd)

    def test_cancel_error_and_missing_descriptor(self):
        with self.assertRaises(devices.Cancelled):
            rawio.open_device(str(self.disk), os.O_RDONLY, command=ci.fake_authopen("cancel"))
        with self.assertRaises(devices.DeviceError) as ctx:
            rawio.open_device(str(self.disk), os.O_RDONLY, command=ci.fake_authopen("error"))
        self.assertNotIsInstance(ctx.exception, devices.Cancelled)
        self.assertIn("Operation not permitted", str(ctx.exception))
        for mode in ("nofd", "two"):
            with self.assertRaises(devices.DeviceError):
                rawio.open_device(str(self.disk), os.O_RDONLY, command=ci.fake_authopen(mode))
        with self.assertRaises(devices.DeviceError):
            rawio.open_device(str(self.disk), os.O_RDONLY, command=("/nonexistent/authopen",))

    def test_cancel_text(self):
        self.assertTrue(rawio.looks_cancelled(1, "AuthorizationCopyRights failed: -60006"))
        self.assertTrue(rawio.looks_cancelled(1, "The user canceled the operation."))
        self.assertFalse(rawio.looks_cancelled(1, "Operation not permitted"))

    def test_production_check_refuses_anything_but_the_character_device(self):
        fd = os.open(self.disk, os.O_RDONLY)
        try:
            with self.assertRaises(devices.DeviceError):
                rawio.check_char_device(fd, str(self.disk))  # a regular file, not a character device
            if os.path.exists("/dev/null") and stat.S_ISCHR(os.stat("/dev/null").st_mode):
                null = os.open("/dev/null", os.O_RDONLY)
                try:
                    rawio.check_char_device(null, "/dev/null")  # the same character device passes
                    with self.assertRaises(devices.DeviceError):
                        rawio.check_char_device(null, "/dev/zero")  # another character device does not
                finally:
                    os.close(null)
        finally:
            os.close(fd)

    def test_wrong_file_or_access_mode_is_refused(self):
        decoy = self.tmp / "decoy.img"
        decoy.write_bytes(bytes(4096))
        raw = rawio.RawAccess(command=ci.fake_authopen(f"other:{decoy}"), check=ci.same_file)
        with self.assertRaises(devices.DeviceError):
            raw.read(str(self.disk), START)
        raw = rawio.RawAccess(command=ci.fake_authopen("rdonly"), check=ci.same_file)
        out = self.write([(0, 512)], self.area[:512], raw=raw)
        self.assertEqual(out["code"], "open_failed")
        self.assertIn("access mode", out["error"])
        self.assertEqual(self.file_area(), self.area)

    def test_descriptor_is_always_closed(self):
        before = len(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None
        for _ in range(5):
            self.raw.read(str(self.disk), START)
            self.write([(0, 512)], self.area[:512])
        if before is not None:
            self.assertEqual(len(os.listdir("/dev/fd")), before)


class Read(Images):
    def test_reads_mbr_and_area_at_absolute_offsets(self):
        mbr, area = self.raw.read(str(self.disk), START)
        self.assertEqual((mbr, area), (self.mbr, self.area))

    def test_read_request_limits(self):
        for start, length in ((0, 12288), (START + 1, 12288), (START, 12800), (START, 100)):
            with self.assertRaises(devices.DeviceError):
                self.raw.read(str(self.disk), start, length)


class Write(Images):
    def test_writes_only_the_planned_sectors_and_reads_back(self):
        plan = self.plan()
        before = self.disk.read_bytes()
        out = self.write([(w.offset, 512) for w in plan.writes], plan.after_bytes)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["written"], [w.offset for w in plan.writes])
        after = self.disk.read_bytes()
        self.assertEqual(after[START:START + 12288], plan.after_area)
        changed = [i for i in range(len(before)) if before[i] != after[i]]
        self.assertTrue(all(START <= i < START + 12288 for i in changed))
        self.assertEqual(bytes.fromhex(out["area_hex"]), plan.after_area)
        self.assertTrue(out["mbr_unchanged"])
        again = self.write([(w.offset, 512) for w in plan.writes], plan.after_bytes)
        self.assertTrue(again["ok"])
        self.assertEqual(again["written"], [])

    def test_refuses_before_hash_mismatch_without_writing(self):
        before = self.disk.read_bytes()
        for kwargs, what in (({"expect": "0" * 64}, "target sectors"), ({"mbr": "0" * 64}, "MBR"),
                             ({"area": "0" * 64}, "boot area")):
            out = self.write([(0, 512)], b"\x01" * 512, **kwargs)
            self.assertEqual((out["code"], out["mismatch"]), ("before_mismatch", what))
            self.assertEqual((out["written"], out["possibly_written"]), ([], []))
            self.assertEqual(self.disk.read_bytes(), before)

    def test_swapped_card_with_identical_target_sector_is_refused(self):
        planned_mbr = sha(self.disk.read_bytes()[:512])
        self.poke(446 + 12, b"\x01")  # another card: a different partition size in the MBR
        before = self.disk.read_bytes()
        out = self.write([(0, 512)], b"\x01" * 512, mbr=planned_mbr)
        self.assertEqual((out["code"], out["mismatch"]), ("before_mismatch", "MBR"))
        self.assertEqual(self.disk.read_bytes(), before)

    def test_refuses_ranges_outside_the_whitelist_before_opening(self):
        before = self.disk.read_bytes()
        bad = [[(1, 512)], [(0, 100)], [(0, 0)], [(512, 512)], [(3072, 512)], [(0, 1024)], [(5632, 1024)],
               [(12288, 512)], [(0, 512), (0, 512)], [(0, 512), (5632, 512), (6144, 512), (11776, 512), (512, 512)],
               [], [(-512, 512)]]
        raw = rawio.RawAccess(command=("/nonexistent/authopen",), check=ci.same_file)  # never started
        for ranges in bad:
            out = self.write(ranges, b"\x00" * 512 * max(1, len(ranges)), raw=raw)
            self.assertEqual(out["code"], "range_not_allowed", ranges)
            self.assertEqual(out["possibly_written"], [])
        self.assertEqual(self.write([(0, 512)], b"\x00" * 511, raw=raw)["code"], "range_not_allowed")
        self.assertEqual(self.disk.read_bytes(), before)

    def test_write_failure_reports_written_and_possibly_written(self):
        plan = self.plan()
        real, calls = os.pwrite, []

        def flaky(fd, data, offset):
            calls.append(offset)
            if len(calls) == 2:
                raise OSError(5, "Input/output error")
            return real(fd, data, offset)
        with mock.patch.object(rawio.os, "pwrite", flaky):
            out = self.write([(w.offset, 512) for w in plan.writes], plan.after_bytes)
        self.assertEqual(out["code"], "write_failed")
        self.assertEqual(out["written"], [plan.writes[0].offset])
        self.assertEqual(out["possibly_written"], [plan.writes[0].offset, plan.writes[1].offset])

    def test_flush_failure_reports_possibly_written(self):
        """F6: the sector was written even though the flush failed."""
        plan = self.plan()
        with mock.patch.object(rawio, "_flush", side_effect=OSError(5, "Input/output error")):
            out = self.write([(w.offset, 512) for w in plan.writes], plan.after_bytes)
        self.assertEqual((out["code"], out["written"], out["possibly_written"]),
                         ("write_failed", [], [plan.writes[0].offset]))
        self.assertEqual(self.file_area()[6144:6656], plan.writes[0].after)

    def test_open_failure_and_cancel(self):
        """N3: a failure before any write reports empty written lists; a cancel raises."""
        before = self.disk.read_bytes()
        out = self.write([(0, 512)], b"\x01" * 512, raw=rawio.RawAccess(command=ci.fake_authopen("error"),
                                                                        check=ci.same_file))
        self.assertEqual((out["code"], out["written"], out["possibly_written"]), ("open_failed", [], []))
        with self.assertRaises(devices.Cancelled):
            self.write([(0, 512)], b"\x01" * 512, raw=rawio.RawAccess(command=ci.fake_authopen("cancel"),
                                                                      check=ci.same_file))
        self.assertEqual(self.disk.read_bytes(), before)

    def test_flush_prefers_full_fsync(self):
        import fcntl
        calls = []
        with mock.patch.object(fcntl, "F_FULLFSYNC", 51, create=True), \
                mock.patch.object(fcntl, "fcntl", side_effect=lambda fd, op: calls.append(op)), \
                mock.patch.object(rawio.os, "fsync", side_effect=AssertionError("fsync used")):
            rawio._flush(3)
        self.assertEqual(calls, [51])
        with mock.patch.object(fcntl, "F_FULLFSYNC", 51, create=True), \
                mock.patch.object(fcntl, "fcntl", side_effect=OSError(25, "Inappropriate ioctl")), \
                mock.patch.object(rawio.os, "fsync") as fsync:
            rawio._flush(3)
        fsync.assert_called_once_with(3)


class MacOpsRawPath(Images):
    """MacOps opens the WHOLE disk node of the chosen card and uses diskutil's partition start."""

    def test_raw_read_and_write_go_to_the_whole_disk(self):
        seen = []

        class Recorder(rawio.RawAccess):
            def read(self, device, start, length=12288):
                seen.append(("read", device, start, length))
                return b"m" * 512, b"a" * length

            def write(self, device, start, ranges, after, **kw):
                seen.append(("write", device, start, list(ranges)))
                return {"ok": True}
        cand = devices.Candidate("darwin", "disk6", "disk6s1", "Built In SDXC Reader", "Secure Digital",
                                 ci.LAB_EXFAT["disk_bytes"], START, ci.LAB_EXFAT["sectors"] * 512, "exFAT",
                                 "EOS_DIGITAL", "/Volumes/EOS_DIGITAL")
        ops = devices.MacOps(raw=Recorder())
        ops.raw_read(cand, 12288)
        ops.raw_write(cand, [(0, 512)], "a" * 64, b"x" * 512, expect_mbr_sha256="b" * 64, expect_area_sha256="c" * 64)
        self.assertEqual(seen, [("read", "/dev/rdisk6", START, 12288), ("write", "/dev/rdisk6", START, [(0, 512)])])
        with self.assertRaises(devices.DeviceError):
            devices.MacOps(raw=Recorder()).raw_read(devices.Candidate(**{**cand.__dict__, "whole_id": "disk6s1"}),
                                                    12288)

    def test_default_command_is_authopen(self):
        self.assertEqual(rawio.RawAccess().command, ("/usr/libexec/authopen",))
        self.assertIs(rawio.RawAccess().check, rawio.check_char_device)


class DiskutilFailures(unittest.TestCase):
    """F2: a hanging or missing diskutil never raises out of mount/verify."""

    def test_timeouts(self):
        import subprocess
        cand = devices.Candidate("darwin", "disk4", "disk4s1", "", "", 1, 2, 3, "FAT32", "N", "/Volumes/N")

        def hang(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        ops = devices.MacOps(run=hang)
        mounted, out = ops.mount(cand)
        self.assertFalse(mounted)
        self.assertIn("did not finish", out)
        self.assertFalse(ops.verify(cand)[0])
        with self.assertRaises(devices.DeviceError):
            ops.unmount(cand)
        with self.assertRaises(devices.DeviceError):
            ops.refresh(cand)

    def test_unmounts_rather_than_ejects(self):
        import subprocess
        cand = devices.Candidate("darwin", "disk4", "disk4s1", "", "", 1, 2, 3, "FAT32", "N", "/Volumes/N")
        seen = []

        def fake_run(argv, **kwargs):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "ok", "")
        ops = devices.MacOps(run=fake_run)
        ops.unmount(cand)
        ops.mount(cand)
        ops.verify(cand)
        self.assertEqual(seen, [[devices.DISKUTIL, "unmount", "disk4s1"], [devices.DISKUTIL, "mount", "disk4s1"],
                                [devices.DISKUTIL, "verifyVolume", "disk4s1"]])


if __name__ == "__main__":
    unittest.main()
