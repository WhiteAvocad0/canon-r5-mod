"""Boot-marker planning on synthetic FAT32/exFAT images (pure bytes, no device)."""
from pathlib import Path
import random
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cardprep_images as ci
from cardprep import layout
from cardprep.layout import CardPrepError


def refused(test, code, fn, *args, **kwargs):
    with test.assertRaises(CardPrepError) as ctx:
        fn(*args, **kwargs)
    test.assertEqual(ctx.exception.code, code, str(ctx.exception))
    return ctx.exception


class ExfatChecksum(unittest.TestCase):
    def test_matches_independent_implementation_on_random_regions(self):
        rng = random.Random(1234)
        for _ in range(40):
            region = bytes(rng.getrandbits(8) for _ in range(12 * 512))
            self.assertEqual(layout.exfat_boot_checksum(region), ci.exfat_checksum(region))

    def test_step_rotates_before_adding(self):
        """The lab's counterexample to the legacy expression: step(0xFFFFFFFF, 1)."""
        self.assertEqual(layout.exfat_checksum_step(0xFFFFFFFF, 1), 0)
        self.assertEqual(((0xFFFFFFFF << 31) | (0xFFFFFFFF >> 1) + 1) & 0xFFFFFFFF, 0x80000000)
        self.assertEqual(layout.exfat_checksum_step(0x00000001, 0), 0x80000000)
        self.assertEqual(layout.exfat_checksum_step(0x80000000, 0xFF), 0x400000FF)

    def test_excluded_bytes_do_not_change_the_checksum(self):
        area = ci.exfat_area()
        base = layout.exfat_boot_checksum(area)
        for offset in (106, 107, 112):
            changed = bytearray(area)
            changed[offset] ^= 0xFF
            self.assertEqual(layout.exfat_boot_checksum(bytes(changed)), base)
        changed = bytearray(area)
        changed[105] ^= 1
        self.assertNotEqual(layout.exfat_boot_checksum(bytes(changed)), base)

    def test_camera_formatted_lab_card_checksum(self):
        """The camera wrote 0x216DAA31 on the lab card; our implementation reproduces it."""
        area = ci.exfat_area()
        self.assertEqual(layout.exfat_boot_checksum(area[:6144]), ci.LAB_EXFAT_ORACLE["checksum_before"])
        self.assertEqual(layout.exfat_boot_checksum(area[6144:]), ci.LAB_EXFAT_ORACLE["checksum_before"])


class LabOracles(unittest.TestCase):
    def test_exfat_plan_matches_the_lab_record(self):
        mbr, area, disk = ci.exfat_card()
        o = ci.LAB_EXFAT_ORACLE
        self.assertEqual(layout.sha256(area), o["before_sha256"])
        plan = layout.plan_markers(mbr, area, disk_bytes=disk)
        self.assertEqual(layout.sha256(plan.after_area), o["after_sha256"])
        self.assertEqual([(c.offset, c.end) for c in plan.changes], o["ranges"])
        self.assertEqual([w.sector for w in plan.writes], o["sectors_in_write_order"])
        for c in plan.changes:
            if c.end - c.offset == 19:
                self.assertEqual(c.before, b"\xf4" * 19)
                self.assertEqual(c.after.hex(), o["marker_after_hex"])
            else:
                self.assertEqual(c.before, struct.pack("<I", o["checksum_before"]) * 128)
                self.assertEqual(c.after, struct.pack("<I", o["checksum_after"]) * 128)
        # VolumeFlags differ between the regions (main 2, backup 0) and are preserved.
        self.assertEqual(plan.after_area[106:108], b"\x02\x00")
        self.assertEqual(plan.after_area[6144 + 106:6144 + 108], b"\x00\x00")
        self.assertEqual(sum(len(c.after) for c in plan.changes), 1062)

    def test_fat32_plan_matches_the_lab_record(self):
        mbr, _, disk = ci.fat32_card()
        area = ci.fat32_area(ci.fat32_sector(**ci.LAB_FAT32, script=True))
        plan = layout.plan_markers(mbr, area, disk_bytes=disk)
        o = ci.LAB_FAT32_ORACLE
        self.assertEqual(layout.sha256(plan.before_area[:512]), o["before_sha256"])
        self.assertEqual(layout.sha256(plan.after_area[:512]), o["after_sha256"])
        self.assertEqual([(c.offset, c.end) for c in plan.changes], o["changed"])
        self.assertEqual([w.sector for w in plan.writes], [0])
        self.assertEqual(plan.after_area[512:], area[512:], "only the primary boot sector changes")
        self.assertEqual(plan.after_area[71:82], b"EOS_DEVELOP")
        self.assertEqual(plan.after_area[92:100], b"BOOTDISK")
        self.assertEqual(plan.after_area[496:502], b"SCRIPT", "an existing SCRIPT marker is left alone")
        self.assertTrue(any("SCRIPT" in n for n in plan.notes))


class Planning(unittest.TestCase):
    def test_idempotent_when_markers_are_present(self):
        for card in (ci.exfat_card(), ci.fat32_card()):
            mbr, area, disk = card
            once = layout.plan_markers(mbr, area, disk_bytes=disk)
            twice = layout.plan_markers(mbr, once.after_area, disk_bytes=disk)
            self.assertTrue(once.writes_needed)
            self.assertFalse(twice.writes_needed)
            self.assertEqual(twice.after_area, once.after_area)
            self.assertIn("already present", "\n".join(layout.describe_plan(twice)))

    def test_exfat_partial_marking_writes_only_the_missing_region(self):
        mbr, area, disk = ci.exfat_card()
        marked = layout.plan_markers(mbr, area, disk_bytes=disk).after_area
        half = marked[:6144] + area[6144:]  # main marked, backup still blank
        plan = layout.plan_markers(mbr, half, disk_bytes=disk)
        self.assertEqual([w.sector for w in plan.writes], [12, 23])
        self.assertEqual(plan.after_area, marked)

    def test_plan_dict_shows_before_and_after(self):
        mbr, area, disk = ci.exfat_card()
        d = layout.plan_markers(mbr, area, disk_bytes=disk).to_dict()
        self.assertEqual(d["sectors_in_write_order"], [12, 23, 0, 11])
        self.assertEqual(d["changed_ranges"][0]["before_hex"], "f4" * 19)
        self.assertEqual(d["identity"]["volume_serial"], "1D731468")


class Refusals(unittest.TestCase):
    def setUp(self):
        self.fmbr, self.farea, self.fdisk = ci.fat32_card()
        self.embr, self.earea, self.edisk = ci.exfat_card()

    def test_unknown_and_unsupported_filesystems(self):
        refused(self, "unknown_filesystem", layout.plan_markers, self.fmbr, bytes(12288), disk_bytes=self.fdisk)
        ntfs = bytearray(self.farea)
        ntfs[3:11] = b"NTFS    "
        ntfs[82:90] = b"        "
        refused(self, "ntfs", layout.plan_markers, self.fmbr, bytes(ntfs), disk_bytes=self.fdisk)
        fat16 = bytearray(self.farea)
        fat16[82:90] = b"        "
        fat16[54:62] = b"FAT16   "
        refused(self, "fat16", layout.plan_markers, self.fmbr, bytes(fat16), disk_bytes=self.fdisk)

    def test_sector_sizes_other_than_512(self):
        sector = ci.fat32_sector(**ci.LAB_FAT32, bps=4096)
        refused(self, "sector_size", layout.plan_markers, self.fmbr, ci.fat32_area(sector), disk_bytes=self.fdisk)
        refused(self, "sector_size", layout.plan_markers, self.embr, ci.exfat_area(shift=12), disk_bytes=self.edisk)

    def test_mbr_problems(self):
        refused(self, "mbr_signature", layout.plan_markers, bytes(512), self.farea, disk_bytes=self.fdisk)
        gpt = ci.mbr(1, 0xFFFFFFFF, 0xEE)
        refused(self, "gpt", layout.plan_markers, gpt, self.farea, disk_bytes=self.fdisk)
        two = bytearray(self.fmbr)
        two[462:478] = two[446:462]
        refused(self, "mbr_partition_count", layout.plan_markers, bytes(two), self.farea, disk_bytes=self.fdisk)
        refused(self, "mbr_beyond_disk", layout.plan_markers, self.fmbr, self.farea, disk_bytes=self.fdisk // 2)
        refused(self, "no_partition_table", layout.plan_markers, self.farea[:512], self.farea, disk_bytes=self.fdisk)
        wrong_type = ci.mbr(ci.LAB_FAT32["start_lba"], ci.LAB_FAT32["sectors"], 0x07)
        refused(self, "geometry_mismatch", layout.plan_markers, wrong_type, self.farea, disk_bytes=self.fdisk)

    def test_geometry_mismatch_between_mbr_and_volume(self):
        moved = ci.mbr(ci.LAB_FAT32["start_lba"] - 1, ci.LAB_FAT32["sectors"], 0x0C)
        refused(self, "geometry_mismatch", layout.plan_markers, moved, self.farea, disk_bytes=self.fdisk)
        small = ci.mbr(ci.LAB_FAT32["start_lba"], ci.LAB_FAT32["sectors"] - 1, 0x0C)
        refused(self, "geometry_mismatch", layout.plan_markers, small, self.farea, disk_bytes=self.fdisk)
        moved = ci.mbr(ci.LAB_EXFAT["start_lba"] - 8, ci.LAB_EXFAT["sectors"], 0x07)
        refused(self, "geometry_mismatch", layout.plan_markers, moved, self.earea, disk_bytes=self.edisk)

    def test_corrupted_exfat(self):
        bad = bytearray(self.earea)
        bad[11 * 512] ^= 1
        refused(self, "exfat_checksum", layout.plan_markers, self.embr, bytes(bad), disk_bytes=self.edisk)
        bad = bytearray(self.earea)
        bad[3 * 512 + 511] = 0
        refused(self, "exfat_extended", layout.plan_markers, self.embr, bytes(bad), disk_bytes=self.edisk)
        other = ci.exfat_area()[:6144] + ci.exfat_area(serial=0x12345678)[6144:]
        refused(self, "exfat_backup", layout.plan_markers, self.embr, other, disk_bytes=self.edisk)
        refused(self, "exfat_geometry", layout.plan_markers, self.embr, ci.exfat_area(clusters=10**9),
                disk_bytes=self.edisk)

    def test_unexpected_bytes_where_markers_go(self):
        refused(self, "unexpected_marker_bytes", layout.plan_markers, self.embr, ci.exfat_area(bootcode=0x00),
                disk_bytes=self.edisk)
        not_camera = ci.fat32_area(ci.fat32_sector(**ci.LAB_FAT32, bootcode_zero=False))
        refused(self, "unexpected_marker_bytes", layout.plan_markers, self.fmbr, not_camera, disk_bytes=self.fdisk)
        label = ci.fat32_area(ci.fat32_sector(**{**ci.LAB_FAT32, "label": b"\x00\xff" * 5 + b"\x00"}))
        refused(self, "unexpected_marker_bytes", layout.plan_markers, self.fmbr, label, disk_bytes=self.fdisk)

    def test_fat32_backup_boot_sector_must_match(self):
        bad = bytearray(self.farea)
        bad[6 * 512 + 13] = 8  # different sectors-per-cluster in the copy
        refused(self, "fat32_backup", layout.plan_markers, self.fmbr, bytes(bad), disk_bytes=self.fdisk)

    def test_wrong_lengths(self):
        refused(self, "area_length", layout.plan_markers, self.fmbr, self.farea[:512], disk_bytes=self.fdisk)
        refused(self, "mbr_length", layout.plan_markers, self.fmbr[:100], self.farea, disk_bytes=self.fdisk)


class Restore(unittest.TestCase):
    def round_trip(self, card):
        mbr, area, disk = card
        marked = layout.plan_markers(mbr, area, disk_bytes=disk).after_area
        plan = layout.plan_restore(mbr, area, mbr, marked, disk_bytes=disk)
        self.assertEqual(plan.kind, "restore")
        self.assertEqual(plan.after_area, area)
        return plan

    def test_round_trip(self):
        self.assertEqual([w.sector for w in self.round_trip(ci.fat32_card()).writes], [0])
        self.assertEqual([w.sector for w in self.round_trip(ci.exfat_card()).writes], [12, 23, 0, 11])

    def test_exfat_dynamic_fields_are_kept(self):
        mbr, area, disk = ci.exfat_card()
        marked = bytearray(layout.plan_markers(mbr, area, disk_bytes=disk).after_area)
        marked[106], marked[112] = 0x02, 7  # mounted since, and a few files written
        plan = layout.plan_restore(mbr, area, mbr, bytes(marked), disk_bytes=disk)
        self.assertEqual(plan.after_area[106], 0x02)
        self.assertEqual(plan.after_area[112], 7)
        self.assertFalse(layout.inspect(mbr, plan.after_area, disk_bytes=disk).markers_present)

    def test_partial_write_can_be_restored(self):
        mbr, area, disk = ci.exfat_card()
        marked = layout.plan_markers(mbr, area, disk_bytes=disk).after_area
        partial = area[:6144] + marked[6144:6144 + 512] + area[6144 + 512:]  # only sector 12 written
        self.assertEqual(layout.plan_restore(mbr, area, mbr, partial, disk_bytes=disk).after_area, area)

    def test_nothing_to_restore(self):
        mbr, area, disk = ci.fat32_card()
        self.assertFalse(layout.plan_restore(mbr, area, mbr, area, disk_bytes=disk).writes_needed)

    def test_refuses_other_card_or_unknown_bytes(self):
        mbr, area, disk = ci.exfat_card()
        other_mbr, other_area, _ = ci.exfat_card(serial=0x0BADCAFE)
        refused(self, "restore_identity", layout.plan_restore, mbr, area, other_mbr if other_mbr != mbr else mbr,
                other_area, disk_bytes=disk)
        moved = ci.mbr(ci.LAB_EXFAT["start_lba"], ci.LAB_EXFAT["sectors"] - 8, 0x07)
        refused(self, "restore_identity", layout.plan_restore, mbr, area, moved, area, disk_bytes=disk)
        weird = bytearray(area)
        weird[122:130] = b"NOTMINE!"
        refused(self, "restore_unknown_bytes", layout.plan_restore, mbr, area, mbr, bytes(weird), disk_bytes=disk)
        fmbr, farea, fdisk = ci.fat32_card()
        refused(self, "restore_identity", layout.plan_restore, fmbr, farea, fmbr, area, disk_bytes=fdisk)


class VerifyWritten(unittest.TestCase):
    def test_detects_problems(self):
        mbr, area, disk = ci.exfat_card()
        plan = layout.plan_markers(mbr, area, disk_bytes=disk)
        self.assertEqual(layout.verify_written(plan, mbr, mbr, plan.after_area, disk_bytes=disk), [])
        self.assertTrue(layout.verify_written(plan, mbr, mbr, area, disk_bytes=disk))
        other_mbr = bytearray(mbr)
        other_mbr[0] = 1
        self.assertIn("MBR", " ".join(layout.verify_written(plan, mbr, bytes(other_mbr), plan.after_area,
                                                            disk_bytes=disk)))
        stray = bytearray(plan.after_area)
        stray[3000] ^= 1
        self.assertTrue(layout.verify_written(plan, mbr, mbr, bytes(stray), disk_bytes=disk))


if __name__ == "__main__":
    unittest.main()
