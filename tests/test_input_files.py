"""Regular-file admission and bounded input reads at the owning I/O boundary."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


class InputFilesTests(unittest.TestCase):
    def test_fixed_size_input_rejected_before_payload_read(self):
        import build
        self.assertTrue(hasattr(build, "read_regular_file"), "bounded ROM input owner missing")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "too-large.rom"
            with p.open("wb") as f:
                f.truncate(1024 * 1024 * 1024)
            with mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("unbounded read")):
                with self.assertRaisesRegex(ValueError, "size"):
                    build.read_regular_file(p, exact_bytes=64 * 1024 * 1024)

    def test_regular_input_identity_and_streaming_hash(self):
        import build
        self.assertTrue(hasattr(build, "hash_regular_file"), "streaming provenance owner missing")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "metadata.json"
            data = b"x" * (2 * 1024 * 1024 + 7)
            p.write_bytes(data)
            with mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("unbounded read")):
                self.assertEqual(build.hash_regular_file(p), hashlib.sha256(data).hexdigest())
                self.assertEqual(build.read_regular_file(p, exact_bytes=len(data)), data)
                with self.assertRaisesRegex(ValueError, "size"):
                    build.hash_regular_file(p, exact_bytes=len(data) - 1)

    def test_device_and_directory_refused_without_opening(self):
        import build
        self.assertTrue(hasattr(build, "read_regular_file"), "regular input admission missing")
        with tempfile.TemporaryDirectory() as d:
            for p in [Path(d), Path("/dev/null")]:
                with self.subTest(path=p), mock.patch("os.open", side_effect=AssertionError("nonregular open")):
                    with self.assertRaisesRegex(ValueError, "regular file"):
                        build.read_regular_file(p, exact_bytes=1)


if __name__ == "__main__":
    unittest.main()
