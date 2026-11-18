"""SD-card detection and refusal rules with mocked diskutil / lsblk / PowerShell output."""
from pathlib import Path
import plistlib
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cardprep_images as ci
from cardprep import devices

ROOT_INFO = {"DeviceIdentifier": "disk3s1s1", "ParentWholeDisk": "disk3", "APFSContainerReference": "disk3",
             "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}], "MountPoint": "/"}
BOOT = devices.boot_wholes_macos(ROOT_INFO)


def internal_ssd():
    whole = ci.mac_whole("disk0", 1_000_555_581_440, BusProtocol="Apple Fabric", Content="GUID_partition_scheme",
                         Removable=False, RemovableMedia=False, Ejectable=False, MediaName="APPLE SSD",
                         DeviceTreePath="IODeviceTree:/arm-io/ans")
    part = ci.mac_part("disk0", 2, fs="apfs", content="Apple_APFS", offset=20480, size=994_610_155_520,
                       BusProtocol="Apple Fabric", mount="", name="")
    return whole, [part]


def disk_image():
    whole = ci.mac_whole("disk6", 8_000_000_000, BusProtocol="Disk Image", VirtualOrPhysical="Virtual",
                         MediaName="Apple UDIF read-write Media")
    return whole, [ci.mac_part("disk6", offset=4194304, size=7_995_805_696, BusProtocol="Disk Image")]


def usb_reader():
    whole = ci.mac_whole("disk5", BusProtocol="USB", MediaName="Generic STORAGE DEVICE", Internal=False)
    return whole, [ci.mac_part("disk5", BusProtocol="USB")]


class MacClassify(unittest.TestCase):
    def test_lab_cards_are_accepted(self):
        fat = devices.classify_macos(ci.mac_whole(), [ci.mac_part()], BOOT)
        self.assertTrue(fat.accepted, fat.refusals)
        self.assertEqual((fat.filesystem, fat.volume_name, fat.model), ("FAT32", "EOS_DIGITAL", "Built In SDXC Reader"))
        self.assertEqual((fat.raw_disk, fat.raw_partition), ("/dev/rdisk4", "/dev/rdisk4s1"))
        self.assertEqual(fat.partition_offset, ci.LAB_FAT32["start_lba"] * 512)
        ex = devices.classify_macos(ci.mac_whole(size=ci.LAB_EXFAT["disk_bytes"]), [ci.mac_exfat_part()], BOOT)
        self.assertTrue(ex.accepted, ex.refusals)
        self.assertEqual(ex.filesystem, "exFAT")
        self.assertIn("Built In SDXC Reader", "\n".join(ex.details()))

    def refusal(self, whole, parts, text, boot=BOOT):
        cand = devices.classify_macos(whole, parts, boot)
        self.assertFalse(cand.accepted)
        self.assertTrue(any(text in r for r in cand.refusals), cand.refusals)
        return cand

    def test_internal_disk(self):
        cand = self.refusal(*internal_ssd(), "running from")
        self.assertTrue(any("not an SD card slot" in r for r in cand.refusals))

    def test_disk_image(self):
        self.refusal(*disk_image(), "disk image")
        self.refusal(ci.mac_whole(VirtualOrPhysical="Unknown"), [ci.mac_part()], "physical disk")

    def test_usb_reader(self):
        self.refusal(*usb_reader(), "USB card readers")

    def test_system_disk_even_if_it_looks_like_sd(self):
        self.refusal(ci.mac_whole(), [ci.mac_part()], "running from", boot={"disk4"})

    def test_other_refusals(self):
        self.refusal(ci.mac_whole(Content="GUID_partition_scheme"), [ci.mac_part()], "Not an MBR")
        self.refusal(ci.mac_whole(), [ci.mac_part(), ci.mac_part(index=2)], "2 partitions")
        self.refusal(ci.mac_whole(), [], "0 partitions")
        self.refusal(ci.mac_whole(size=1_000_000_000), [ci.mac_part(size=990_000_000)], "outside the SD card range")
        self.refusal(ci.mac_whole(size=4 * 1024**4), [ci.mac_part()], "outside the SD card range")
        self.refusal(ci.mac_whole(WritableMedia=False), [ci.mac_part()], "write-protected")
        self.refusal(ci.mac_whole(), [ci.mac_part(mount="")], "not mounted")
        self.refusal(ci.mac_whole(), [ci.mac_part(mount="/")], "running system")
        self.refusal(ci.mac_whole(), [ci.mac_part(mount="/private/var/x")], "system location")
        self.refusal(ci.mac_whole(), [ci.mac_part(fs="hfs", content="Apple_HFS")], "not FAT32 or exFAT")
        self.refusal(ci.mac_whole(), [ci.mac_part(fs="msdos", content="DOS_FAT_16")], "not FAT32 or exFAT")
        self.refusal(ci.mac_whole(DeviceBlockSize=4096), [ci.mac_part()], "not 512")
        self.refusal(ci.mac_whole(Removable=False), [ci.mac_part()], "removable")
        self.refusal(ci.mac_whole(SystemImage=True), [ci.mac_part()], "system media")
        self.refusal(ci.mac_whole(), [ci.mac_part(ParentWholeDisk="disk9")], "does not belong")
        self.refusal(ci.mac_whole(), [ci.mac_part(name="")], "no name")

    def test_boot_disk_detection(self):
        self.assertEqual(BOOT, {"disk0", "disk3"})
        with self.assertRaises(devices.DeviceError):
            devices.boot_wholes_macos({})

    def test_enumerate(self):
        tables = {
            "/": ROOT_INFO,
            "disk0": internal_ssd()[0], "disk0s2": internal_ssd()[1][0],
            "disk4": ci.mac_whole(), "disk4s1": ci.mac_part(),
            "disk5": usb_reader()[0], "disk5s1": usb_reader()[1][0],
            "disk6": disk_image()[0], "disk6s1": disk_image()[1][0],
        }
        listing = {"AllDisksAndPartitions": [
            {"DeviceIdentifier": "disk0", "Partitions": [{"DeviceIdentifier": "disk0s2"}]},
            {"DeviceIdentifier": "disk4", "Partitions": [{"DeviceIdentifier": "disk4s1"}]},
            {"DeviceIdentifier": "disk5", "Partitions": [{"DeviceIdentifier": "disk5s1"}]},
            {"DeviceIdentifier": "disk6", "Partitions": [{"DeviceIdentifier": "disk6s1"}]},
        ]}
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[1] == "list":
                return listing
            return tables[argv[-1]]
        found = devices.enumerate_macos(run)
        self.assertEqual([c.whole_id for c in found if c.accepted], ["disk4"])
        self.assertEqual(len(found), 4)
        self.assertTrue(all(a[0] == devices.DISKUTIL and a[1] in ("info", "list") for a in calls))

    def test_refresh_detects_a_swapped_card(self):
        chosen = devices.classify_macos(ci.mac_whole(), [ci.mac_part()], BOOT)
        state = {"disk4s1": ci.mac_part()}

        def run(argv, **kwargs):
            args = argv[1:]
            if args[:2] == ["list", "-plist"]:
                data = {"AllDisksAndPartitions": [{"DeviceIdentifier": "disk4",
                                                   "Partitions": [{"DeviceIdentifier": "disk4s1"}]}]}
            elif args[-1] == "/":
                data = ROOT_INFO
            elif args[-1] == "disk4":
                data = ci.mac_whole()
            else:
                data = state[args[-1]]
            return subprocess.CompletedProcess(argv, 0, plistlib.dumps(data), b"")
        ops = devices.MacOps(run=run)
        self.assertEqual(ops.refresh(chosen), chosen)
        state["disk4s1"] = ci.mac_part(name="OTHER")
        with self.assertRaises(devices.DeviceError):
            ops.refresh(chosen)


class LinuxClassify(unittest.TestCase):
    def sd(self, **override):
        dev = {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 15931539456, "rm": True,
               "hotplug": False, "ro": False, "tran": None, "model": None, "log-sec": 512, "pttype": "dos",
               "mountpoints": [None], "children": [{"name": "mmcblk0p1", "type": "part", "fstype": "exfat",
                                                    "label": "EOS_DIGITAL", "mountpoints": ["/media/me/EOS_DIGITAL"]}]}
        dev.update(override)
        return dev

    def test_sd_card_detected(self):
        cand = devices.classify_linux(self.sd())
        self.assertTrue(cand.accepted, cand.refusals)
        self.assertEqual((cand.filesystem, cand.volume_name), ("exFAT", "EOS_DIGITAL"))

    def test_refusals(self):
        self.assertFalse(devices.classify_linux(self.sd(name="sda", tran="usb")).accepted)
        self.assertFalse(devices.classify_linux(self.sd(rm=False, hotplug=False)).accepted)
        self.assertFalse(devices.classify_linux(self.sd(pttype="gpt")).accepted)
        self.assertFalse(devices.classify_linux(self.sd(children=[{"name": "p1", "fstype": "ext4",
                                                                   "mountpoints": ["/"]}])).accepted)

    def test_enumerate(self):
        found = devices.enumerate_linux(lambda argv: {"blockdevices": [self.sd(), self.sd(name="nvme0n1", rm=False)]})
        self.assertEqual([c.accepted for c in found], [True, False])


class WindowsClassify(unittest.TestCase):
    DISK = {"Number": 2, "FriendlyName": "SD Card", "BusType": "SD", "Size": 15931539456, "IsSystem": False,
            "IsBoot": False, "IsReadOnly": False, "PartitionStyle": "MBR", "LogicalSectorSize": 512}
    PART = {"DiskNumber": 2, "PartitionNumber": 1, "Offset": 4194304, "Size": 15927345152, "DriveLetter": "E"}
    VOL = {"DriveLetter": "E", "FileSystem": "FAT32", "FileSystemLabel": "EOS_DIGITAL"}

    def test_sd_card_detected(self):
        cand = devices.classify_windows(self.DISK, [self.PART], [self.VOL], "C:")
        self.assertTrue(cand.accepted, cand.refusals)
        self.assertEqual(cand.mount_point, "E:\\")

    def test_refusals(self):
        self.assertFalse(devices.classify_windows({**self.DISK, "BusType": "USB"}, [self.PART], [self.VOL], "C:").accepted)
        self.assertFalse(devices.classify_windows({**self.DISK, "IsBoot": True}, [self.PART], [self.VOL], "C:").accepted)
        self.assertFalse(devices.classify_windows(self.DISK, [self.PART], [self.VOL], "E:").accepted)
        self.assertFalse(devices.classify_windows({**self.DISK, "PartitionStyle": "GPT"}, [self.PART], [self.VOL],
                                                  "C:").accepted)

    def test_enumerate_handles_single_objects(self):
        answers = iter([self.DISK, self.PART, self.VOL])
        found = devices.enumerate_windows(lambda command: next(answers))
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].accepted, found[0].refusals)


class Support(unittest.TestCase):
    def test_only_macos_prepares(self):
        self.assertTrue(devices.prepare_supported("darwin"))
        self.assertFalse(devices.prepare_supported("linux"))
        self.assertFalse(devices.prepare_supported("win32"))
        self.assertIn("untested", devices.UNTESTED_NOTE["linux"])
        self.assertIn("untested", devices.UNTESTED_NOTE["win32"])


if __name__ == "__main__":
    unittest.main()
