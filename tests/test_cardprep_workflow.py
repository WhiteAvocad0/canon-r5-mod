"""Read, back up, write, verify and restore against image files through the real helper code."""
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
    """Stands in for devices.MacOps: the partition and the disk are image files, and the
    raw reads and writes go through helper.main exactly as the root helper would run them."""

    def __init__(self, disk: Path, part: Path, *, verify_ok=True):
        self.disk, self.part, self.verify_ok = disk, part, verify_ok
        self.calls: list[str] = []
        self.mounted = True
        self.before_write = None
        self.cancel = None

    def refresh(self, cand):
        self.calls.append("refresh")
        return cand

    def unmount(self, cand):
        self.calls.append("unmount")
        self.mounted = False

    def mount(self, cand):
        self.calls.append("mount")
        self.mounted = True
        return True, "mounted"

    def verify(self, cand):
        self.calls.append("verify")
        assert not self.mounted
        return self.verify_ok, "File system check exit code is %d." % (0 if self.verify_ok else 8)

    def raw_read(self, cand, length):
        self.calls.append("read")
        assert not self.mounted, "raw reads happen while unmounted"
        if self.cancel == "read":
            raise devices.Cancelled("cancelled")
        out = helper.main(["read", "--disk", str(self.disk), "--partition", str(self.part), "--length", str(length)],
                          allow_image_files=True)
        assert out["ok"], out
        return bytes.fromhex(out["mbr_hex"]), bytes.fromhex(out["area_hex"])

    def raw_write(self, cand, ranges, expect_sha, after):
        self.calls.append("write")
        assert not self.mounted, "raw writes happen while unmounted"
        if self.cancel == "write":
            raise devices.Cancelled("cancelled")
        if self.before_write:
            self.before_write()
        path = self.part.parent / "after-bytes.bin"
        path.write_bytes(after)
        args = ["write", "--disk", str(self.disk), "--partition", str(self.part)]
        for offset, length in ranges:
            args += ["--range", f"{offset}:{length}"]
        args += ["--expect-sha", expect_sha, "--after", str(path), "--after-sha", layout.sha256(after)]
        return helper.main(args, allow_image_files=True)


def candidate(geometry, filesystem, name="EOS_DIGITAL"):
    return devices.Candidate("darwin", "disk4", "disk4s1", "Built In SDXC Reader", "Secure Digital",
                             geometry["disk_bytes"], geometry["start_lba"] * 512, geometry["sectors"] * 512,
                             filesystem, name, f"/Volumes/{name}", ci.SD_TREE)


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cardprep-flow-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.backups = self.tmp / "backups"

    def card(self, kind="exFAT", area=None):
        mbr, default_area, _ = ci.exfat_card() if kind == "exFAT" else ci.fat32_card()
        disk, part = self.tmp / f"{kind}-disk.img", self.tmp / f"{kind}-part.img"
        disk.write_bytes(mbr + bytes(8192))
        part.write_bytes((area or default_area) + bytes(1 << 16))
        geometry = ci.LAB_EXFAT if kind == "exFAT" else ci.LAB_FAT32
        return candidate(geometry, kind), ImageOps(disk, part)

    def prepare(self, cand, ops, when="20261118-120000"):
        snap = workflow.read_card(cand, ops)
        plan = layout.plan_markers(snap.mbr, snap.area, disk_bytes=cand.disk_bytes)
        backup = workflow.save_backup(snap, plan, self.backups, now=datetime.strptime(when, "%Y%m%d-%H%M%S"))
        return snap, plan, backup, workflow.apply_plan(snap, plan, backup, ops)

    def test_fat32_prepare_writes_one_sector_and_is_idempotent(self):
        cand, ops = self.card("FAT32")
        original = ops.part.read_bytes()
        snap, plan, backup, report = self.prepare(cand, ops)
        self.assertTrue(report.ok, report.lines)
        self.assertEqual(report.written_sectors, [0])
        now = ops.part.read_bytes()
        self.assertEqual(now[512:], original[512:], "only sector 0 changes")
        self.assertEqual(now[71:82] + now[92:100], b"EOS_DEVELOPBOOTDISK")
        self.assertEqual(ops.calls, ["refresh", "unmount", "read", "mount", "refresh", "unmount", "write", "verify",
                                     "mount"])
        self.assertTrue(ops.mounted)
        self.assertEqual(sorted(p.name for p in backup.folder.iterdir()),
                         sorted(["boot-area.bin", "manifest.json", "mbr.bin", report.record.name]))
        self.assertEqual(json.loads(report.record.read_text())["written_sectors"], [0])
        self.assertEqual(backup.area, original[:12288])
        # Second time: nothing to write, and no unmount for a write.
        ops.calls.clear()
        _, plan2, _, report2 = self.prepare(cand, ops, when="20261118-120001")
        self.assertFalse(plan2.writes_needed)
        self.assertTrue(report2.ok)
        self.assertEqual(ops.calls, ["refresh", "unmount", "read", "mount"])

    def test_exfat_prepare_and_restore_round_trip(self):
        cand, ops = self.card("exFAT")
        original = ops.part.read_bytes()
        snap, plan, backup, report = self.prepare(cand, ops)
        self.assertTrue(report.ok, report.lines)
        self.assertEqual(report.written_sectors, [12, 23, 0, 11])
        marked = ops.part.read_bytes()
        self.assertEqual(marked[:12288], plan.after_area)
        self.assertEqual(marked[12288:], original[12288:])
        # The camera mounts the card and writes files: VolumeFlags and PercentInUse change.
        with open(ops.part, "r+b") as f:
            f.seek(106)
            f.write(b"\x02")
            f.seek(112)
            f.write(b"\x05")
        snap2 = workflow.read_card(cand, ops)
        self.assertTrue(snap2.volume.markers_present)
        restore = workflow.restore_plan(snap2, workflow.load_backup(backup.folder))
        before_restore = workflow.save_backup(snap2, restore, self.backups, purpose="before-restore",
                                              now=datetime(2026, 11, 18, 13, 0, 0))
        report = workflow.apply_plan(snap2, restore, before_restore, ops)
        self.assertTrue(report.ok, report.lines)
        restored = ops.part.read_bytes()
        expected = bytearray(original)
        expected[106], expected[112] = 0x02, 0x05
        self.assertEqual(restored, bytes(expected))
        facts = layout.inspect(snap2.mbr, restored[:12288], disk_bytes=cand.disk_bytes)
        self.assertFalse(any(m.present for m in facts.markers))

    def test_card_changed_between_read_and_write_writes_nothing(self):
        cand, ops = self.card("exFAT")
        snap = workflow.read_card(cand, ops)
        plan = layout.plan_markers(snap.mbr, snap.area, disk_bytes=cand.disk_bytes)
        backup = workflow.save_backup(snap, plan, self.backups)

        def drift():
            with open(ops.part, "r+b") as f:
                f.seek(112)
                f.write(b"\x09")
        ops.before_write = drift
        report = workflow.apply_plan(snap, plan, backup, ops)
        changed = ops.part.read_bytes()
        self.assertFalse(report.ok)
        self.assertIn("Nothing was written", report.title)
        self.assertEqual(changed[:12288], snap.area[:112] + b"\x09" + snap.area[113:])
        self.assertTrue(ops.mounted)

    def test_cancelled_prompts_leave_the_card_mounted(self):
        cand, ops = self.card("FAT32")
        ops.cancel = "read"
        with self.assertRaises(devices.Cancelled):
            workflow.read_card(cand, ops)
        self.assertTrue(ops.mounted)
        ops.cancel = None
        snap = workflow.read_card(cand, ops)
        plan = layout.plan_markers(snap.mbr, snap.area, disk_bytes=cand.disk_bytes)
        backup = workflow.save_backup(snap, plan, self.backups)
        ops.cancel = "write"
        before = ops.part.read_bytes()
        report = workflow.apply_plan(snap, plan, backup, ops)
        self.assertFalse(report.ok)
        self.assertIn("cancelled", report.title)
        self.assertEqual(ops.part.read_bytes(), before)
        self.assertTrue(ops.mounted)

    def test_failed_volume_check_is_reported(self):
        cand, ops = self.card("FAT32")
        ops.verify_ok = False
        _, _, backup, report = self.prepare(cand, ops)
        self.assertFalse(report.ok)
        self.assertIn("Volume check FAILED", "\n".join(report.lines))
        self.assertIn(str(backup.folder), "\n".join(report.lines))

    def test_partial_write_is_reported_and_restorable(self):
        cand, ops = self.card("exFAT")
        original = ops.part.read_bytes()
        snap = workflow.read_card(cand, ops)
        plan = layout.plan_markers(snap.mbr, snap.area, disk_bytes=cand.disk_bytes)
        backup = workflow.save_backup(snap, plan, self.backups)
        real, calls = os.pwrite, []

        def flaky(fd, data, offset):
            calls.append(offset)
            if len(calls) == 3:
                raise OSError(5, "Input/output error")
            return real(fd, data, offset)
        with mock.patch.object(helper.os, "pwrite", flaky):
            report = workflow.apply_plan(snap, plan, backup, ops)
        self.assertFalse(report.ok)
        self.assertEqual(report.written_sectors, [12, 23])
        self.assertIn("Restore it from", "\n".join(report.lines))
        self.assertTrue(ops.mounted)
        current = ops.part.read_bytes()[:12288]
        restore = layout.plan_restore(snap.mbr, backup.area, snap.mbr, current, disk_bytes=cand.disk_bytes)
        self.assertEqual([w.sector for w in restore.writes], [12, 23])
        self.assertEqual(restore.after_area, original[:12288])

    def test_identity_and_backup_checks(self):
        cand, ops = self.card("FAT32")
        wrong = devices.Candidate(**{**cand.__dict__, "partition_offset": cand.partition_offset + 512})
        with self.assertRaises(CardPrepError) as ctx:
            workflow.read_card(wrong, ops)
        self.assertEqual(ctx.exception.code, "geometry_mismatch")
        self.assertTrue(ops.mounted)
        snap = workflow.read_card(cand, ops)
        plan = layout.plan_markers(snap.mbr, snap.area, disk_bytes=cand.disk_bytes)
        backup = workflow.save_backup(snap, plan, self.backups)
        (backup.folder / "boot-area.bin").chmod(0o644)
        with open(backup.folder / "boot-area.bin", "r+b") as f:
            f.write(b"X")
        with self.assertRaises(workflow.PrepFailure):
            workflow.load_backup(backup.folder)
        other = workflow.Backup(backup.folder, backup.mbr, bytes(12288), backup.manifest)
        with self.assertRaises(workflow.PrepFailure):
            workflow.apply_plan(snap, plan, other, ops)
        # A backup from another card is refused for restore.
        ecand, eops = self.card("exFAT")
        esnap = workflow.read_card(ecand, eops)
        eplan = layout.plan_markers(esnap.mbr, esnap.area, disk_bytes=ecand.disk_bytes)
        ebackup = workflow.save_backup(esnap, eplan, self.backups, purpose="other")
        with self.assertRaises(CardPrepError):
            workflow.restore_plan(snap, ebackup)
        with self.assertRaises(workflow.PrepFailure):
            workflow.load_backup(self.tmp)


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
        geometry = {"disk_bytes": (start + sectors) * 512, "start_lba": start, "sectors": sectors}
        ops = ImageOps(disk, part)
        cand = candidate(geometry, "FAT32")
        snap = workflow.read_card(cand, ops)
        plan = layout.plan_markers(snap.mbr, snap.area, disk_bytes=cand.disk_bytes)
        backup = workflow.save_backup(snap, plan, tmp / "backups")
        report = workflow.apply_plan(snap, plan, backup, ops)
        self.assertTrue(report.ok, report.lines)
        proc = subprocess.run(["/sbin/fsck_msdos", "-n", str(part)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = part.read_bytes()
        self.assertEqual(data[71:82] + data[92:100], b"EOS_DEVELOPBOOTDISK")


if __name__ == "__main__":
    unittest.main()
