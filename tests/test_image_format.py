"""Public tests for Canon EOS R5 autoexec image header/footer format."""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from image_format import FOOTER_MAGIC, PLACEHOLDER, FormatError, finalize_image, inspect_image


def make_unfinalized_image(total_size: int = 0x200) -> bytearray:
    data = bytearray(total_size)
    data[:8] = bytes.fromhex("00bf01e0") + bytes.fromhex("000000ea")
    data[8:32] = b"TESTHDR" + bytes(17)
    data[32:-8] = b"\x11\x22\x33\x44" * ((total_size - 40) // 4)
    struct.pack_into("<II", data, total_size - 8, FOOTER_MAGIC, PLACEHOLDER)
    return data


class ImageFormatTests(unittest.TestCase):
    def test_finalize_and_inspect_roundtrip(self):
        raw = make_unfinalized_image(0x400)
        unfinished_facts = inspect_image(bytes(raw), unfinished=True)
        self.assertEqual(unfinished_facts["footer_magic"], hex(FOOTER_MAGIC))
        self.assertEqual(unfinished_facts["bytes"], 0x400)

        with self.assertRaises(FormatError):
            inspect_image(bytes(raw), unfinished=False)

        final = finalize_image(bytes(raw))
        facts = inspect_image(final, unfinished=False)
        self.assertEqual(facts["xor_u32_le"], "0x0")
        self.assertEqual(facts["bytes"], 0x400)
        self.assertEqual(len(facts["sha256"]), 64)
        xor_acc = 0
        for (word,) in struct.iter_unpack("<I", final):
            xor_acc ^= word
        self.assertEqual(xor_acc, 0)

    def test_rejects_bad_size_magic_or_checksum(self):
        with self.assertRaises(FormatError):
            finalize_image(b"\x00" * 16)
        with self.assertRaises(FormatError):
            finalize_image(bytes(make_unfinalized_image(0x204)))

        bad_magic = make_unfinalized_image(0x200)
        bad_magic[-8:-4] = b"NOPE"
        with self.assertRaises(FormatError):
            finalize_image(bytes(bad_magic))

        bad_sum = bytearray(finalize_image(bytes(make_unfinalized_image(0x200))))
        bad_sum[40] ^= 0xFF
        with self.assertRaises(FormatError):
            inspect_image(bytes(bad_sum), unfinished=False)

        with self.assertRaises(FormatError):
            finalize_image(bytes(bad_sum))

    def test_footer_source_preserves_gpl_notice(self):
        footer = (ROOT / "src" / "r5_221" / "footer.S").read_text()
        self.assertIn("GPL-2.0-or-later", footer)
        self.assertIn("0xE12FFF13", footer)
        self.assertIn("0xCCCCCCCC", footer)


if __name__ == "__main__":
    unittest.main()
