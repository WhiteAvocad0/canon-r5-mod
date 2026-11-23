"""The xPack corresponding-source companion: pinned inputs, deterministic archive, wiring."""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import build_app
import xpack_source_package as pkg


def row(**values):
    base = dict.fromkeys(pkg.COLUMNS, "")
    base.update(values)
    return "\t".join(base[c] for c in pkg.COLUMNS)


class CompanionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.blobs = {"https://example.org/a.tar.gz": b"alpha", "https://example.org/b.tar.xz": b"beta"}
        lines = ["\t".join(pkg.COLUMNS),
                 row(component="a", file="a.tar.gz", url="https://example.org/a.tar.gz",
                     sha256=hashlib.sha256(b"alpha").hexdigest()),
                 row(component="b", file="b.tar.xz", url="https://example.org/b.tar.xz",
                     sha256=hashlib.sha256(b"beta").hexdigest()),
                 row(component="gcc", url=pkg.ARM_URL, shipped="in Arm's source package")]
        self.inventory = self.base / "inputs.tsv"
        self.inventory.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def urlopen(self, url, timeout=None):
        return io.BytesIO(self.blobs[url])

    def build(self, out):
        with mock.patch.object(pkg.urllib.request, "urlopen", side_effect=self.urlopen):
            return pkg.build(self.base / out, self.base / "cache", self.inventory)

    def test_archive_is_deterministic_and_lists_pinned_sources(self):
        first, second = self.build("one"), self.build("two")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.name, f"{build_app.TOOLCHAIN_COMPANION_NAME}")
        with tarfile.open(first) as tar:
            names = tar.getnames()
            self.assertEqual(names, [f"{pkg.NAME}/{n}" for n in
                                     ("README.txt", "MANIFEST.tsv", "SHA256SUMS", "sources/a.tar.gz", "sources/b.tar.xz")])
            self.assertTrue(all(m.mtime == 0 and m.uid == 0 for m in tar.getmembers()))
            self.assertEqual(tar.extractfile(f"{pkg.NAME}/sources/b.tar.xz").read(), b"beta")
            self.assertIn(pkg.ARM_SHA256, tar.extractfile(f"{pkg.NAME}/README.txt").read().decode())
        sums = (self.base / "one" / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(sums[0], f"{hashlib.sha256(first.read_bytes()).hexdigest()}  {first.name}")
        self.assertIn(f"{hashlib.sha256(b'alpha').hexdigest()}  sources/a.tar.gz", sums)

    def test_refuses_a_download_that_differs_from_its_pin(self):
        self.blobs["https://example.org/a.tar.gz"] = b"tampered"
        with self.assertRaisesRegex(SystemExit, "a.tar.gz: SHA-256 .* expected"):
            self.build("out")
        self.assertFalse((self.base / "cache" / "a.tar.gz").exists())

    def test_repository_inventory_is_complete_and_pinned(self):
        rows = pkg.read_inventory()
        components = {r["component"] for r in rows}
        for needed in ("xpack-recipe", "xbb-helper", "gcc", "newlib", "binutils", "gdb", "readline", "ncurses",
                       "zlib", "gmp", "mpfr", "mpc", "isl", "expat", "libiconv", "python", "mingw-w64",
                       "gcc-mingw-runtime", "llvm-runtimes"):
            self.assertIn(needed, components)
        for r in rows:
            self.assertTrue(r["license"] and r["hosts"] and r["shipped"], r["component"])
            if not r["file"]:
                self.assertEqual(r["url"], pkg.ARM_URL)
        self.assertEqual(pkg.ARM_URL, build_app.TOOLCHAIN_SOURCE_URL)
        self.assertEqual(pkg.ARM_SHA256, build_app.TOOLCHAIN_SOURCE_SHA256)
        self.assertEqual(f"{pkg.NAME}.tar", build_app.TOOLCHAIN_COMPANION_NAME)
        self.assertTrue(build_app.TOOLCHAIN_COMPANION_URL.endswith("/" + build_app.TOOLCHAIN_COMPANION_NAME))


if __name__ == "__main__":
    unittest.main()
