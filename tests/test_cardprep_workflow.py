"""Prepare, write, verify, cancel and restore against image files through the real helper code."""
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
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
from cardprep import devices, helper, layout, workflow
from cardprep.layout import CardPrepError


class ImageOps:
    """Stands in for devices.MacOps. The partition and the disk are image files, the raw
    reads and writes go through helper.main exactly as the root helper runs them, and the
    mount state is tracked like macOS would: refresh reports an unmounted card without its
    filesystem and name, and refuses a card that is gone or no longer the chosen one."""

    def __init__(self, chosen: devices.Candidate, disk: Path, part: Path, *, verify_ok=True):
        self.chosen, self.disk, self.part, self.verify_ok = chosen, disk, part, verify_ok
        self.calls: list[str] = []
        self.mounted = True
        self.mount_fails = self.unmount_fails = self.gone = False
        self.mount_raises: Exception | None = None
        self.verify_raises: Exception | None = None
        self.before_write = None
        self.cancel = None

    def _now(self) -> devices.Candidate:
        if self.mounted:
            return self.chosen
        return replace(self.chosen, mount_point="", volume_name="", filesystem=None)

    def refresh(self, cand):
        self.calls.append("refresh")
        if self.gone:
            raise devices.DeviceError("disk4 is gone. Was the card removed?")
        now = self._now()
        if not devices.same_card(cand, now):
            raise devices.DeviceError("The card changed since you chose it.")
        return now

    def unmount(self, cand):
        self.calls.append("unmount")
        if self.unmount_fails:
            raise devices.DeviceError("diskutil unmount disk4s1 did not finish within 120 s.")
        self.mounted = False

    def mount(self, cand):
        self.calls.append("mount")
        if self.mount_raises:
            raise self.mount_raises
        if self.mount_fails:
            return False, "Volume on disk4s1 failed to mount"
        self.mounted = True
        return True, "mounted"

    def verify(self, cand):
        self.calls.append("verify")
        assert not self.mounted, "the volume check runs while unmounted"
        if self.verify_raises:
            raise self.verify_raises
        return self.verify_ok, "File system check exit code is %d." % (0 if self.verify_ok else 8)

    def raw_read(self, cand, length):
        self.calls.append("read")
        assert not self.mounted, "raw reads happen while unmounted"
        if self.cancel == "read":
            raise devices.Cancelled("The password prompt was cancelled.")
        out = helper.main(["read", "--disk", str(self.disk), "--partition", str(self.part), "--length", str(length)],
                          allow_image_files=True)
        assert out["ok"], out
        return bytes.fromhex(out["mbr_hex"]), bytes.fromhex(out["area_hex"])

    def raw_write(self, cand, ranges, expect_sha, after, *, expect_mbr_sha256, expect_area_sha256):
        self.calls.append("write")
        assert not self.mounted, "raw writes happen while unmounted"
        if self.cancel == "write":
            raise devices.Cancelled("The password prompt was cancelled.")
        if self.before_write:
            self.before_write()
        path = self.part.parent / "after-bytes.bin"
        path.write_bytes(after)
        args = ["write", "--disk", str(self.disk), "--partition", str(self.part)]
        for offset, length in ranges:
            args += ["--range", f"{offset}:{length}"]
        args += ["--expect-sha", expect_sha, "--expect-mbr-sha", expect_mbr_sha256,
                 "--expect-area-sha", expect_area_sha256, "--after", str(path), "--after-sha", layout.sha256(after)]
        return helper.main(args, allow_image_files=True)


def candidate(geometry, filesystem, name="EOS_DIGITAL"):
    return devices.Candidate("darwin", "disk4", "disk4s1", "Built In SDXC Reader", "Secure Digital",
                             geometry["disk_bytes"], geometry["start_lba"] * 512, geometry["sectors"] * 512,
                             filesystem, name, f"/Volumes/{name}", ci.SD_TREE)


def flaky_pwrite(fail_at: int):
    real, calls = os.pwrite, []

    def pwrite(fd, data, offset):
        calls.append(offset)
        if len(calls) == fail_at:
            raise OSError(5, "Input/output error")
        return real(fd, data, offset)
    return pwrite


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardprep-flow-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.backups = self.tmp / "backups"

    def card(self, kind="exFAT"):
        mbr, area, _ = ci.exfat_card() if kind == "exFAT" else ci.fat32_card()
        disk, part = self.tmp / f"{kind}-disk.img", self.tmp / f"{kind}-part.img"
        disk.write_bytes(mbr + bytes(8192))
        part.write_bytes(area + bytes(1 << 16))
        cand = candidate(ci.LAB_EXFAT if kind == "exFAT" else ci.LAB_FAT32, kind)
        return cand, ImageOps(cand, disk, part)

    def records(self, backup):
        return sorted(p.name for p in backup.folder.glob("result-*.json"))

    def test_fat32_prepare_writes_one_sector_then_is_already_prepared(self):
        cand, ops = self.card("FAT32")
        original = ops.part.read_bytes()
        session = workflow.start(cand, ops, self.backups)
        self.assertTrue(session.needs_write)
        self.assertFalse(ops.mounted, "the card stays unmounted from the read to the write (F8)")
        self.assertEqual(session.backup.area, original[:12288])
        report = workflow.write(session, ops)
        self.assertTrue(report.ok, report.lines)
        self.assertEqual(report.written_sectors, [0])
        now = ops.part.read_bytes()
        self.assertEqual(now[512:], original[512:], "only sector 0 changes")
        self.assertEqual(now[71:82] + now[92:100], b"EOS_DEVELOPBOOTDISK")
        self.assertEqual(ops.calls, ["refresh", "unmount", "read", "refresh", "write", "verify", "mount"])
        self.assertTrue(ops.mounted)
        self.assertIn(f"Backup: {session.backup.folder}", report.lines)
        self.assertEqual(json.loads(report.record.read_text())["written_sectors"], [0])
        # Again: nothing to write; the card is mounted again and no write prompt is needed.
        ops.calls.clear()
        again = workflow.start(cand, ops, self.backups)
        self.assertFalse(again.needs_write)
        self.assertTrue(again.report.ok and again.report.nothing_written)
        self.assertIn("Already prepared", again.report.title)
        self.assertIn(f"Backup: {again.backup.folder}", again.report.lines)
        self.assertEqual(ops.calls, ["refresh", "unmount", "read", "mount"])
        self.assertNotEqual(again.backup.folder, session.backup.folder)

    def test_exfat_prepare_then_undo_from_the_latest_backup(self):
        cand, ops = self.card("exFAT")
        original = ops.part.read_bytes()
        session = workflow.start(cand, ops, self.backups)
        self.assertEqual(workflow.write(session, ops).written_sectors, [12, 23, 0, 11])
        self.assertEqual(ops.part.read_bytes()[:12288], session.plan.after_area)
        workflow.start(cand, ops, self.backups)  # an "already prepared" read adds a newer, marked backup
        # The camera mounts the card and writes files: VolumeFlags and PercentInUse change.
        with open(ops.part, "r+b") as f:
            f.seek(106)
            f.write(b"\x02")
            f.seek(112)
            f.write(b"\x05")
        latest = workflow.latest_backup(cand, self.backups)
        self.assertEqual(latest.folder, session.backup.folder, "Undo uses the backup from before marking")
        undo = workflow.start(cand, ops, self.backups, restore_from=latest)
        self.assertEqual(undo.kind, "restore")
        self.assertIs(undo.source, latest)
        report = workflow.write(undo, ops)
        self.assertTrue(report.ok, report.lines)
        expected = bytearray(original)
        expected[106], expected[112] = 0x02, 0x05
        self.assertEqual(ops.part.read_bytes(), bytes(expected))

    def test_cancel_writes_nothing_and_mounts_again(self):
        cand, ops = self.card("exFAT")
        before = ops.part.read_bytes()
        session = workflow.start(cand, ops, self.backups)
        report = workflow.cancel(session, ops)
        self.assertTrue(report.nothing_written)
        self.assertEqual(ops.part.read_bytes(), before)
        self.assertTrue(ops.mounted)
        self.assertIn(f"Backup: {session.backup.folder}", report.lines)
        self.assertEqual(len(self.records(session.backup)), 1)

    def test_card_changed_or_swapped_before_the_write_writes_nothing(self):
        for change in ("percent", "mbr"):
            cand, ops = self.card("exFAT")
            session = workflow.start(cand, ops, self.backups)

            def drift(change=change, ops=ops):
                path, offset, value = (ops.part, 112, b"\x09") if change == "percent" else (ops.disk, 458, b"\x01")
                with open(path, "r+b") as f:
                    f.seek(offset)
                    f.write(value)
            ops.before_write = drift
            snapshot_part = ops.part.read_bytes()
            report = workflow.write(session, ops)
            self.assertFalse(report.ok)
            self.assertTrue(report.nothing_written, report.lines)
            self.assertIn("Nothing was written", report.title)
            if change == "mbr":
                self.assertEqual(ops.part.read_bytes(), snapshot_part)
            self.assertTrue(ops.mounted)
            self.assertIn(f"Backup: {session.backup.folder}", report.lines)

    def test_cancelled_password_prompts(self):
        cand, ops = self.card("FAT32")
        ops.cancel = "read"
        with self.assertRaises(devices.Cancelled) as ctx:
            workflow.start(cand, ops, self.backups)
        self.assertIn("Nothing was written", str(ctx.exception))
        self.assertTrue(ops.mounted)
        ops.cancel = "write"
        session = workflow.start(cand, ops, self.backups)
        before = ops.part.read_bytes()
        report = workflow.write(session, ops)
        self.assertTrue(report.nothing_written)
        self.assertIn("cancelled", report.title)
        self.assertEqual(ops.part.read_bytes(), before)
        self.assertTrue(ops.mounted)

    def test_volume_check_failure_or_timeout_keeps_the_report(self):
        for problem in ("fails", "times out"):
            cand, ops = self.card("FAT32")
            if problem == "fails":
                ops.verify_ok = False
            else:
                ops.verify_raises = subprocess.TimeoutExpired(["diskutil", "verifyVolume"], 1800)
            session = workflow.start(cand, ops, self.backups)
            report = workflow.write(session, ops)
            text = "\n".join(report.lines)
            self.assertFalse(report.ok)
            self.assertEqual(report.written_sectors, [0])
            self.assertIn("Read-back check passed", text)
            self.assertIn("Volume check FAILED", text)
            self.assertIn("backup boot sector", text)
            self.assertIn(str(session.backup.folder), text)
            self.assertTrue(ops.mounted)
            self.assertEqual(len(self.records(session.backup)), 1)

    def test_mount_timeout_after_a_write_keeps_the_report_and_record(self):
        """F2: a hanging remount after a good write must not lose the result."""
        cand, ops = self.card("exFAT")
        session = workflow.start(cand, ops, self.backups)
        ops.mount_raises = subprocess.TimeoutExpired(["diskutil", "mount"], 120)
        report = workflow.write(session, ops)
        self.assertEqual(report.written_sectors, [12, 23, 0, 11])
        self.assertIn("Read-back check passed", "\n".join(report.lines))
        self.assertIn("could not be mounted again", "\n".join(report.lines))
        record = json.loads(report.record.read_text())
        self.assertEqual(record["written_sectors"], [12, 23, 0, 11])

    def test_unmount_failure_reads_nothing_and_still_tries_to_mount(self):
        cand, ops = self.card("FAT32")
        ops.unmount_fails = True
        with self.assertRaises(workflow.PrepFailure) as ctx:
            workflow.start(cand, ops, self.backups)
        self.assertIn("Nothing was read or written", str(ctx.exception))
        self.assertEqual(ops.calls, ["refresh", "unmount", "mount"])

    def test_card_removed_before_the_write(self):
        cand, ops = self.card("FAT32")
        before = ops.part.read_bytes()
        session = workflow.start(cand, ops, self.backups)
        ops.gone = True
        report = workflow.write(session, ops)
        self.assertTrue(report.nothing_written)
        self.assertNotIn("write", ops.calls)
        self.assertEqual(ops.part.read_bytes(), before)
        self.assertIn(f"Backup: {session.backup.folder}", report.lines)

    def test_interrupted_write_on_an_unmountable_card_can_be_undone(self):
        """F1: the card is unmounted and macOS refuses to mount it; Undo still works."""
        cand, ops = self.card("exFAT")
        original = ops.part.read_bytes()
        session = workflow.start(cand, ops, self.backups)
        ops.mount_fails = True
        with mock.patch.object(helper.os, "pwrite", flaky_pwrite(2)):
            report = workflow.write(session, ops)
        self.assertFalse(report.ok)
        self.assertEqual(report.written_sectors, [12, 23], "sector 23 was started, so it is possibly written")
        self.assertIn("Undo", "\n".join(report.lines))
        self.assertFalse(ops.mounted)
        # A normal read refuses the half-written card (stale backup checksum)...
        with self.assertRaises(CardPrepError) as ctx:
            workflow.start(cand, ops, self.backups)
        self.assertEqual(ctx.exception.code, "exfat_checksum")
        # ...but Undo identifies it without a mount, checks it against the backup and repairs it.
        ops.calls.clear()
        undo = workflow.start(cand, ops, self.backups, restore_from=workflow.latest_backup(cand, self.backups))
        self.assertNotIn("unmount", ops.calls)
        self.assertEqual([w.sector for w in undo.plan.writes], [12])
        ops.mount_fails = False
        report = workflow.write(undo, ops)
        self.assertTrue(report.ok, report.lines)
        self.assertEqual(ops.part.read_bytes(), original)
        self.assertTrue(ops.mounted)

    def test_backup_folders_never_collide(self):
        """F9: two backups in the same instant get different folders; none is reused."""
        cand, ops = self.card("FAT32")
        when = datetime(2026, 11, 18, 12, 0, 0)
        first = workflow.start(cand, ops, self.backups, now=when)
        workflow.cancel(first, ops)
        second = workflow.start(cand, ops, self.backups, now=when)
        self.assertNotEqual(first.backup.folder, second.backup.folder)
        self.assertTrue(first.backup.folder.exists() and second.backup.folder.exists())
        self.assertTrue(second.backup.folder.name.endswith("-2"))

    def test_identity_and_backup_checks(self):
        cand, ops = self.card("FAT32")
        wrong = replace(cand, partition_offset=cand.partition_offset + 512)
        ops.chosen = wrong
        with self.assertRaises(CardPrepError) as ctx:
            workflow.start(wrong, ops, self.backups)
        self.assertEqual(ctx.exception.code, "geometry_mismatch")
        self.assertTrue(ops.mounted)
        ops.chosen = cand
        session = workflow.start(cand, ops, self.backups)
        workflow.cancel(session, ops)
        backup = session.backup
        (backup.folder / "boot-area.bin").chmod(0o644)
        with open(backup.folder / "boot-area.bin", "r+b") as f:
            f.write(b"X")
        with self.assertRaises(workflow.PrepFailure):
            workflow.load_backup(backup.folder)
        self.assertIsNone(workflow.latest_backup(cand, self.backups), "a damaged backup is skipped")
        with self.assertRaises(workflow.PrepFailure):
            workflow.load_backup(self.tmp)
        # A backup of another card is refused for restore, and is not offered by Undo.
        ecand, eops = self.card("exFAT")
        esession = workflow.start(ecand, eops, self.backups)
        workflow.cancel(esession, eops)
        self.assertIsNone(workflow.latest_backup(cand, self.backups))
        with self.assertRaises(CardPrepError):
            workflow.start(cand, ops, self.backups, restore_from=esession.backup)
        self.assertTrue(ops.mounted)
        # A session can be written at most once.
        session = workflow.start(cand, ops, self.backups)
        self.assertTrue(workflow.write(session, ops).ok)
        session.report = workflow.Report(True, "used")
        self.assertTrue(workflow.write(session, ops).nothing_written)


@unittest.skipUnless(sys.platform == "darwin" and Path("/sbin/newfs_msdos").exists() and Path("/sbin/fsck_msdos").exists(),
                     "needs macOS newfs_msdos and fsck_msdos")
class Fat32FilesystemCheck(unittest.TestCase):
    """A real FAT32 filesystem in an image file stays valid for fsck_msdos after marking."""

    def test_fsck_before_and_after(self):
        tmp = Path(tempfile.mkdtemp(prefix="cardprep-fsck-"))
        self.addCleanup(shutil.rmtree, tmp)
        part, disk = tmp / "part.img", tmp / "disk.img"
        sectors, start = 98304, 8192
        with open(part, "wb") as f:
            f.truncate(sectors * 512)
        subprocess.run(["/sbin/newfs_msdos", "-F", "32", "-c", "1", "-S", "512", "-s", str(sectors), "-o", str(start),
                        "-h", "16", "-u", "63", "-v", "EOS_DIGITAL", str(part)], check=True, capture_output=True)
        with open(part, "r+b") as f:  # camera-style: no x86 boot code in either boot sector
            for base in (0, 6 * 512):
                f.seek(base + 0x5A)
                f.write(bytes(0x1FE - 0x5A))
        disk.write_bytes(ci.mbr(start, sectors, 0x0C))
        proc = subprocess.run(["/sbin/fsck_msdos", "-n", str(part)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        cand = candidate({"disk_bytes": (start + sectors) * 512, "start_lba": start, "sectors": sectors}, "FAT32")
        ops = ImageOps(cand, disk, part)
        report = workflow.write(workflow.start(cand, ops, tmp / "backups"), ops)
        self.assertTrue(report.ok, report.lines)
        proc = subprocess.run(["/sbin/fsck_msdos", "-n", str(part)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = part.read_bytes()
        self.assertEqual(data[71:82] + data[92:100], b"EOS_DEVELOPBOOTDISK")


if __name__ == "__main__":
    unittest.main()
