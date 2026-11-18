"""The privileged raw-sector helper and its osascript wrapper, against image files only."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cardprep_images as ci
from cardprep import devices, helper, layout

HELPER = ROOT / "tools" / "cardprep" / "helper.py"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardprep-helper-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        mbr, area, _ = ci.exfat_card()
        self.disk = self.tmp / "disk.img"
        self.part = self.tmp / "part.img"
        self.disk.write_bytes(mbr + bytes(4096))
        self.part.write_bytes(area + bytes(65536))
        self.area = area

    def run_helper(self, *argv):
        return helper.main(list(argv), allow_image_files=True)

    def write_args(self, ranges, expect, after: bytes, after_sha=None, mbr_sha=None, area_sha=None):
        """Write arguments; the MBR and whole-area hashes default to the image's current bytes."""
        path = self.tmp / "after.bin"
        path.write_bytes(after)
        args = ["write", "--disk", str(self.disk), "--partition", str(self.part)]
        for r in ranges:
            args += ["--range", r]
        return args + ["--expect-sha", expect,
                       "--expect-mbr-sha", mbr_sha or sha(self.disk.read_bytes()[:512]),
                       "--expect-area-sha", area_sha or sha(self.part.read_bytes()[:12288]),
                       "--after", str(path), "--after-sha", after_sha or sha(after)]


class Read(Images):
    def test_reads_mbr_and_area(self):
        out = self.run_helper("read", "--disk", str(self.disk), "--partition", str(self.part), "--length", "12288")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["helper"], "r5-cardprep")
        self.assertEqual(bytes.fromhex(out["area_hex"]), self.area)
        self.assertEqual(bytes.fromhex(out["mbr_hex"]), self.disk.read_bytes()[:512])

    def test_length_whitelist(self):
        for length in ("100", "12800", "0", "-512", "0x200"):
            out = self.run_helper("read", "--disk", str(self.disk), "--partition", str(self.part), "--length", length)
            self.assertFalse(out["ok"])
            self.assertIn(out["code"], ("range_not_allowed",))


class Write(Images):
    def test_writes_only_the_ranges_and_reads_back(self):
        plan = layout.plan_markers(self.disk.read_bytes()[:512], self.area, disk_bytes=ci.LAB_EXFAT["disk_bytes"])
        before_file = self.part.read_bytes()
        ranges = [f"{w.offset}:512" for w in plan.writes]
        out = self.run_helper(*self.write_args(ranges, sha(plan.expected_before), plan.after_bytes))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["written"], [w.offset for w in plan.writes])
        after_file = self.part.read_bytes()
        self.assertEqual(after_file[:12288], plan.after_area)
        self.assertEqual(after_file[12288:], before_file[12288:])
        self.assertEqual(bytes.fromhex(out["area_hex"]), plan.after_area)
        self.assertTrue(out["mbr_unchanged"])
        # Running it again is a no-op: the current bytes already equal the after bytes.
        out = self.run_helper(*self.write_args(ranges, sha(plan.after_bytes), plan.after_bytes))
        self.assertTrue(out["ok"])
        self.assertEqual(out["written"], [])

    def test_refuses_before_sha_mismatch_without_writing(self):
        before = self.part.read_bytes()
        current = sha(self.area[:512])
        for kwargs, what in (({"expect": "0" * 64}, "target sectors"), ({"mbr_sha": "0" * 64}, "MBR"),
                             ({"area_sha": "0" * 64}, "boot area")):
            args = dict(ranges=["0:512"], expect=current, after=b"\x01" * 512)
            args.update(kwargs)
            out = self.run_helper(*self.write_args(**args))
            self.assertFalse(out["ok"])
            self.assertEqual((out["code"], out["mismatch"]), ("before_mismatch", what))
            self.assertEqual((out["written"], out["possibly_written"]), ([], []))
            self.assertEqual(self.part.read_bytes(), before)

    def test_swapped_card_with_identical_target_sector_is_refused(self):
        """F4: same sector 0 but a different MBR (another card) must not be written."""
        current, mbr_planned = sha(self.area[:512]), sha(self.disk.read_bytes()[:512])
        other = bytearray(self.disk.read_bytes())
        other[446 + 12] ^= 1  # a different partition size
        self.disk.write_bytes(bytes(other))
        before = self.part.read_bytes()
        out = self.run_helper(*self.write_args(["0:512"], current, b"\x01" * 512, mbr_sha=mbr_planned))
        self.assertEqual((out["code"], out["mismatch"]), ("before_mismatch", "MBR"))
        self.assertEqual(self.part.read_bytes(), before)

    def test_refuses_ranges_outside_the_whitelist(self):
        before = self.part.read_bytes()
        current = sha(self.area[:512])
        bad = [["1:512"], ["0:100"], ["0:0"], ["12288:512"], ["11776:1024"], ["0:512", "256:512"],
               ["0:512", "5632:512", "6144:512", "11776:512", "0:512"], ["-512:512"], ["0x0:512"], ["0"],
               ["512:512"], ["3072:512"], ["0:1024"], ["5632:1024"]]
        for ranges in bad:
            size = 512 * len(ranges)
            out = self.run_helper(*self.write_args(ranges, current, b"\x00" * size))
            self.assertFalse(out["ok"], ranges)
            self.assertEqual(out["code"], "range_not_allowed", (ranges, out))
        self.assertEqual(self.part.read_bytes(), before)

    def test_refuses_bad_after_file(self):
        current = sha(self.area[:512])
        out = self.run_helper(*self.write_args(["0:512"], current, b"\x00" * 511))
        self.assertEqual(out["code"], "after_file")
        out = self.run_helper(*self.write_args(["0:512"], current, b"\x00" * 512, after_sha="1" * 64))
        self.assertEqual(out["code"], "after_file")
        link = self.tmp / "link.bin"
        link.symlink_to(self.part)
        args = self.write_args(["0:512"], current, b"\x00" * 512)
        args[args.index("--after") + 1] = str(link)
        self.assertEqual(self.run_helper(*args)["code"], "after_file")

    def test_argument_whitelist(self):
        good = self.write_args(["0:512"], sha(self.area[:512]), self.area[:512])
        for argv in (["format"], [], good + ["--force", "1"], good + ["--disk", str(self.disk)],
                     ["read", "--disk", str(self.disk), "--length", "512"], good[:-1],
                     ["read", "--disk", str(self.disk), "--partition", str(self.part), "--length", "512", "extra"]):
            out = self.run_helper(*argv)
            self.assertFalse(out["ok"], argv)
            self.assertEqual(out["code"], "usage", argv)

    def test_write_failure_reports_what_was_written(self):
        plan = layout.plan_markers(self.disk.read_bytes()[:512], self.area, disk_bytes=ci.LAB_EXFAT["disk_bytes"])
        real = os.pwrite
        calls = []

        def flaky(fd, data, offset):
            calls.append(offset)
            if len(calls) == 2:
                raise OSError(5, "Input/output error")
            return real(fd, data, offset)
        with mock.patch.object(helper.os, "pwrite", flaky):
            out = self.run_helper(*self.write_args([f"{w.offset}:512" for w in plan.writes],
                                                   sha(plan.expected_before), plan.after_bytes))
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "write_failed")
        self.assertEqual(out["written"], [plan.writes[0].offset])
        self.assertEqual(out["possibly_written"], [plan.writes[0].offset, plan.writes[1].offset])

    def test_open_failure_reports_nothing_written(self):
        """N3: an OSError before any write (EBUSY on open) carries empty written lists."""
        real_open = os.open

        def busy(path, flags, *args):
            if flags & os.O_RDWR:
                raise OSError(16, "Resource busy")
            return real_open(path, flags, *args)
        with mock.patch.object(helper.os, "open", busy):
            out = self.run_helper(*self.write_args(["0:512"], sha(self.area[:512]), b"\x01" * 512))
        self.assertEqual(out["code"], "os_error")
        self.assertEqual((out["written"], out["possibly_written"]), ([], []))

    def test_fsync_failure_reports_possibly_written(self):
        """F6: the sector was written even though fsync failed."""
        plan = layout.plan_markers(self.disk.read_bytes()[:512], self.area, disk_bytes=ci.LAB_EXFAT["disk_bytes"])

        def failing_fsync(fd):
            raise OSError(5, "Input/output error")
        with mock.patch.object(helper.os, "fsync", failing_fsync):
            out = self.run_helper(*self.write_args([f"{w.offset}:512" for w in plan.writes],
                                                   sha(plan.expected_before), plan.after_bytes))
        self.assertEqual(out["code"], "write_failed")
        self.assertEqual(out["written"], [])
        self.assertEqual(out["possibly_written"], [plan.writes[0].offset])
        self.assertEqual(self.part.read_bytes()[6144:6656], plan.writes[0].after)


class Devices(unittest.TestCase):
    def test_cli_refuses_image_files_and_unlisted_nodes(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(bytes(12288))
            f.flush()
            for disk, part in ((f.name, f.name), ("/dev/disk999", "/dev/disk999s1"),
                               ("/dev/rdisk998", "/dev/rdisk999s1"), ("/dev/rdisk999", "/dev/rdisk999s1")):
                proc = subprocess.run([sys.executable, "-I", "-S", str(HELPER), "read", "--disk", disk,
                                       "--partition", part, "--length", "512"], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                out = json.loads(proc.stdout)
                self.assertFalse(out["ok"])
                self.assertIn(out["code"], ("device_not_allowed", "device_missing"), out)

    def test_names_are_checked_before_any_lookup(self):
        """Nothing under /dev is looked at unless the names pass the whitelist."""
        with mock.patch.object(helper.os, "stat", side_effect=AssertionError("stat called")):
            for disk, part in (("/dev/disk4", "/dev/disk4s1"), ("/dev/rdisk4", "/dev/rdisk5s1"),
                               ("/tmp/x", "/tmp/y"), ("/dev/rdisk4", "/dev/rdisk4")):
                with self.assertRaises(helper.Refused) as ctx:
                    helper._devices({"--disk": disk, "--partition": part}, False)
                self.assertEqual(ctx.exception.code, "device_not_allowed")

    def test_helper_runs_on_python_3_9(self):
        """Root runs it with Apple's Command Line Tools Python (3.9)."""
        import ast
        ast.parse(HELPER.read_text(), feature_version=(3, 9))

    def test_partition_must_belong_to_disk(self):
        chr_stat = os.stat_result((0o20640, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        with mock.patch.object(helper.os, "stat", return_value=chr_stat):
            with self.assertRaises(helper.Refused) as ctx:
                helper._devices({"--disk": "/dev/rdisk4", "--partition": "/dev/rdisk5s1"}, False)
            self.assertEqual(ctx.exception.code, "device_not_allowed")
            self.assertEqual(helper._devices({"--disk": "/dev/rdisk4", "--partition": "/dev/rdisk4s1"}, False),
                             ("/dev/rdisk4", "/dev/rdisk4s1"))
            with self.assertRaises(helper.Refused):
                helper._devices({"--disk": "/dev/disk4", "--partition": "/dev/disk4s1"}, False)  # buffered nodes

    def test_helper_is_self_contained(self):
        text = HELPER.read_text()
        for line in text.splitlines():
            if line.startswith(("import ", "from ")):
                self.assertNotIn("cardprep", line)
                self.assertNotIn(" . ", line)


class Wrapper(unittest.TestCase):
    def test_shell_script_checks_the_helper_hash(self):
        if not Path("/usr/bin/shasum").exists() or not Path("/usr/bin/mktemp").exists():
            self.skipTest("needs /usr/bin/shasum and /usr/bin/mktemp")
        good = devices.privileged_script(sys.executable, HELPER, sha(HELPER.read_bytes()), ["version"])
        proc = subprocess.run(["/bin/sh", "-c", good], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["helper"], "r5-cardprep")
        bad = devices.privileged_script(sys.executable, HELPER, "0" * 64, ["version"])
        proc = subprocess.run(["/bin/sh", "-c", bad], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_root_command_has_a_cleared_environment(self):
        script = devices.privileged_script("/usr/bin/python3", HELPER, "f" * 64, ["version"])
        self.assertTrue(script.startswith(f"exec /usr/bin/env -i PATH={devices.ROOT_PATH} /bin/sh -c "))
        self.assertIn("-I -S", script)

    def test_arguments_are_quoted(self):
        script = devices.privileged_script("/py thon", Path("/a b/helper.py"), "f" * 64,
                                           ["write", "--after", "/tmp/x y'; rm -rf /"])
        inner = shlex.split(script)[-1]  # the text /bin/sh -c runs
        self.assertIn("'/py thon' -I -S", inner)
        self.assertEqual(shlex.split(inner.split(" -I -S ", 1)[1])[-1], "/tmp/x y'; rm -rf /")

    @unittest.skipUnless(sys.platform == "darwin" and Path(devices.OSASCRIPT).exists(), "needs macOS osascript")
    def test_applescript_string_round_trips(self):
        text = devices.privileged_script(sys.executable, HELPER, "a" * 64, ["read", "--after", 'q"uo\\te $x'])
        literal = devices._applescript_string(text)
        proc = subprocess.run([devices.OSASCRIPT, "-e", f"return {literal}"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.rstrip("\n"), text)

    def test_macops_builds_one_osascript_call_and_parses_it(self):
        cand = devices.Candidate("darwin", "disk4", "disk4s1", "Built In SDXC Reader", "Secure Digital", 1, 2, 3,
                                 "FAT32", "EOS_DIGITAL", "/Volumes/EOS_DIGITAL")
        seen = []
        mbr, area = b"\x01" * 512, b"\x02" * 12288

        def fake_run(argv, **kwargs):
            seen.append(argv)
            out = json.dumps({"helper": "r5-cardprep", "protocol": 2, "op": "read", "ok": True,
                              "mbr_hex": mbr.hex(), "area_hex": area.hex()})
            return subprocess.CompletedProcess(argv, 0, out + "\n", "")
        ops = devices.MacOps(run=fake_run, python="/usr/bin/python3")
        self.assertEqual(ops.raw_read(cand, 12288), (mbr, area))
        argv = seen[0]
        self.assertEqual(argv[:2], [devices.OSASCRIPT, "-e"])
        self.assertEqual(len(argv), 3)
        self.assertIn("with administrator privileges", argv[2])
        self.assertIn("/dev/rdisk4", argv[2])
        self.assertIn("/dev/rdisk4s1", argv[2])
        self.assertNotIn("password", argv[2].replace("administrator privileges", ""))

        def cancelled(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "execution error: User canceled. (-128)")
        with self.assertRaises(devices.Cancelled):
            devices.MacOps(run=cancelled, python="/usr/bin/python3").raw_read(cand, 12288)

        def foreign(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, '{"ok": true}', "")
        with self.assertRaises(devices.DeviceError):
            devices.MacOps(run=foreign, python="/usr/bin/python3").raw_read(cand, 12288)

    def test_macops_unmounts_rather_than_ejects(self):
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


class RootInterpreter(unittest.TestCase):
    def test_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNotNone(devices.ownership_problem(tmp))  # owned by the test user
        if os.stat("/").st_uid == 0 and not os.stat("/").st_mode & 0o022:
            self.assertIsNone(devices.ownership_problem("/"))

    def test_user_owned_interpreter_is_refused(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        real, problem = devices.interpreter_problem(sys.executable)
        if real is None:
            self.assertTrue(problem)
        else:  # a root-owned interpreter (for example a system Python on CI) is fine
            self.assertIsNone(devices.ownership_problem(real))

    def test_selection_prefers_command_line_tools_and_refuses_otherwise(self):
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "/Library/Developer/CommandLineTools\n", "")
        with mock.patch.object(devices, "interpreter_problem",
                               side_effect=lambda py, run: ("/clt/python3.9", None) if py == devices.CLT_PYTHON
                               else (None, "not root-owned")), \
                mock.patch.object(devices.os.path, "exists", return_value=True):
            self.assertEqual(devices.select_root_python(run, own="/home/me/python"), "/clt/python3.9")
        with mock.patch.object(devices, "interpreter_problem", return_value=(None, "not root-owned")), \
                mock.patch.object(devices.os.path, "exists", return_value=True):
            with self.assertRaises(devices.DeviceError) as ctx:
                devices.select_root_python(run, own="/home/me/python")
            self.assertIn("xcode-select --install", str(ctx.exception))

    def test_no_command_line_tools_means_no_probe_of_usr_bin_python3(self):
        probed = []

        def run(argv, **kwargs):
            probed.append(argv[0])
            return subprocess.CompletedProcess(argv, 2, "", "no developer tools")
        with mock.patch.object(devices, "interpreter_problem", side_effect=lambda py, run: (probed.append(py), (None, "x"))[1]):
            with self.assertRaises(devices.DeviceError):
                devices.select_root_python(run, own="/home/me/python")
        self.assertNotIn(devices.CLT_PYTHON, probed)


class DiskutilFailures(unittest.TestCase):
    """F2: a hanging or missing diskutil never raises out of mount/verify."""

    def test_timeouts(self):
        cand = devices.Candidate("darwin", "disk4", "disk4s1", "", "", 1, 2, 3, "FAT32", "N", "/Volumes/N")

        def hang(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        ops = devices.MacOps(run=hang, python="/usr/bin/python3")
        mounted, out = ops.mount(cand)
        self.assertFalse(mounted)
        self.assertIn("did not finish", out)
        self.assertFalse(ops.verify(cand)[0])
        with self.assertRaises(devices.DeviceError):
            ops.unmount(cand)
        with self.assertRaises(devices.DeviceError):
            ops.refresh(cand)


if __name__ == "__main__":
    unittest.main()
