"""Screenshot conversion (tools/screenshots.py) with synthetic R5SS files only (no camera data)."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import screenshots as S

LCD, EVF = (1024, 682), (1600, 1200)


def header(w=1024, h=682, version=3, size=None, magic=b"R5SS", fourcc=b"UYVA", pitch=None, length=None,
           meta=(7 << 16) | (1 << 8)):
    size = (512 if version == 3 else 32) if size is None else size
    pitch = 3 * w if pitch is None else pitch
    length = pitch * h if length is None else length
    used = magic + struct.pack("<HHIIII4sI", version, size, w, h, pitch, length, fourcc, meta)
    return used + bytes(max(size - 32, 0))


def shot(w=1024, h=682, version=3, pairs=None, fill=(128, 16, 128, 16, 0, 0)):
    """A screenshot whose first pairs are ``pairs`` (U, Y0, V, Y1, A0, A1) and the rest ``fill``."""
    body = bytearray(bytes(fill) * (w * h // 2))
    for i, pair in enumerate(pairs or ()):
        body[6 * i:6 * i + 6] = bytes(pair)
    return header(w, h, version) + bytes(body)


def decode_png(data):
    """(width, height, rows of RGB bytes); checks the signature, every CRC and the filter bytes."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr, tags = 8, b"", None, []
    while pos < len(data):
        n, = struct.unpack(">I", data[pos:pos + 4])
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        crc, = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF
        tags.append(tag)
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    assert tags == [b"IHDR", b"IDAT", b"IEND"], tags
    w, h, depth, colour, *_ = ihdr
    assert (depth, colour) == (8, 2)
    raw = zlib.decompress(idat)
    stride = 3 * w + 1
    assert len(raw) == stride * h and all(raw[y * stride] == 0 for y in range(h))
    return w, h, [raw[y * stride + 1:(y + 1) * stride] for y in range(h)]


def tree(folder):
    """Every path under folder with its bytes and modification time, to prove nothing changed."""
    out = {}
    for path in sorted(Path(folder).rglob("*")):
        st = os.lstat(path)
        out[str(path.relative_to(folder))] = (st.st_mtime_ns, path.read_bytes() if path.is_file() else None)
    return out


class HeaderTests(unittest.TestCase):
    def test_v3_lcd_and_evf(self):
        h = S.parse_header(header(), 512 + 3 * 1024 * 682)
        self.assertEqual(h, S.Header(3, 512, 1024, 682, 3072, 3 * 1024 * 682, "LCD", 0, 1, 7))
        evf = S.parse_header(header(*EVF), 512 + 3 * 1600 * 1200)
        self.assertEqual((evf.surface, evf.width, evf.height, evf.pitch), ("EVF", 1600, 1200, 4800))
        self.assertEqual(evf.describe(), "1600x1200 EVF, R5SS v3")

    def test_older_32_byte_headers(self):
        v1 = S.parse_header(header(version=1, meta=0), 32 + 3 * 1024 * 682)
        self.assertEqual((v1.version, v1.header_bytes, v1.output, v1.selected_buffer, v1.number_low16),
                         (1, 32, None, None, None))
        v2 = S.parse_header(header(version=2), 32 + 3 * 1024 * 682)
        self.assertEqual((v2.header_bytes, v2.selected_buffer, v2.number_low16), (32, 1, 7))

    def test_malformed_and_unknown_headers_are_refused(self):
        full = 3 * 1024 * 682
        padded = bytearray(header())
        padded[300] = 1
        cases = {
            "empty": (b"", 0),
            "short": (header()[:31], 31),
            "magic": (header(magic=b"R5SX"), 512 + full),
            "version 0": (header(version=0, size=32), 32 + full),
            "version 4": (header(version=4, size=512), 512 + full),
            "version 9": (header(version=9, size=32), 32 + full),
            "v3 with a 32-byte header": (header(version=3, size=32), 32 + full),
            "v1 with a 512-byte header": (header(version=1, size=512), 512 + full),
            "v2 with a 512-byte header": (header(version=2, size=512), 512 + full),
            "layout": (header(fourcc=b"YUYV"), 512 + full),
            "padding": (bytes(padded), 512 + full),
            "header cut short": (header()[:100], 512 + full),
            "tiny size": (header(4, 2), 512 + 24),
            "odd width": (header(1023, 682), 512 + 3 * 1023 * 682),
            "portrait LCD": (header(682, 1024), 512 + full),
            "one row more": (header(1024, 683), 512 + 3 * 1024 * 683),
            "pitch": (header(pitch=4096), 512 + 4096 * 682),
            "length": (header(length=full - 6), 512 + full - 6),
            "file one byte short": (header(), 512 + full - 1),
            "file one byte long": (header(), 512 + full + 1),
            "zero size": (header(0, 0), 512),
        }
        for name, (data, size) in cases.items():
            with self.subTest(name), self.assertRaises(S.ScreenshotError):
                S.parse_header(data, size)

    def test_a_refusal_says_why(self):
        with self.assertRaisesRegex(S.ScreenshotError, "unknown R5SS version 4"):
            S.parse_header(header(version=4, size=512), 512 + 3 * 1024 * 682)
        with self.assertRaisesRegex(S.ScreenshotError, "unknown display size 4x2"):
            S.parse_header(header(4, 2), 512 + 24)


class PixelTests(unittest.TestCase):
    def test_known_ycc_values(self):
        # (Y, Cb, Cr) -> RGB at full range (the default) and at limited range, BT.601.
        cases = [
            ((128, 128, 128), (128, 128, 128), (130, 130, 130)),
            ((0, 128, 128), (0, 0, 0), (0, 0, 0)),
            ((255, 128, 128), (255, 255, 255), (255, 255, 255)),
            ((16, 128, 128), (16, 16, 16), (0, 0, 0)),
            ((235, 128, 128), (235, 235, 235), (255, 255, 255)),
            ((76, 85, 255), (254, 0, 0), (255, 0, 0)),
            ((149, 43, 21), (0, 255, 0), (0, 255, 0)),
            ((29, 255, 107), (0, 0, 254), (0, 0, 255)),
            ((128, 128, 228), (255, 57, 128), (255, 49, 130)),
        ]
        for ycc, full, limited in cases:
            with self.subTest(ycc=ycc):
                self.assertEqual(S.ycc_to_rgb(*ycc), full)
                self.assertEqual(S.ycc_to_rgb(*ycc, full_range=False), limited)

    def test_round_trip_of_a_synthetic_lcd_screenshot(self):
        pairs = [
            (128, 0, 128, 255, 255, 255),   # black, white
            (85, 76, 255, 76, 255, 0),      # red, red (alpha differs and is not used)
            (43, 149, 21, 149, 128, 128),   # green, green
            (255, 29, 107, 29, 0, 0),       # blue, blue
            (128, 128, 228, 128, 9, 9),     # strong Cr on mid grey
        ]
        png, h = S.convert_bytes(shot(pairs=pairs))
        self.assertEqual((h.width, h.height, h.surface), (1024, 682, "LCD"))
        w, height, rows = decode_png(png)
        self.assertEqual((w, height), LCD)
        expected = bytes([0, 0, 0, 255, 255, 255, 254, 0, 0, 254, 0, 0, 0, 255, 0, 0, 255, 0,
                          0, 0, 254, 0, 0, 254, 255, 57, 128, 255, 57, 128])
        self.assertEqual(rows[0][:30], expected)
        self.assertEqual(rows[0][30:], bytes([16]) * (3 * 1024 - 30))  # the fill: Y 16 at full range
        self.assertTrue(all(row == bytes([16]) * 3072 for row in rows[1:]))

    def test_alpha_does_not_change_the_picture_and_rows_follow_the_pitch(self):
        a = shot(*EVF, version=2, pairs=[(90, 200, 150, 60, 0, 0)])
        b = shot(*EVF, version=2, pairs=[(90, 200, 150, 60, 255, 17)])
        self.assertEqual(S.convert_bytes(a)[0], S.convert_bytes(b)[0])
        w, h, rows = decode_png(S.convert_bytes(a)[0])
        self.assertEqual((w, h), EVF)
        self.assertEqual(rows[0][:6], bytes(S.ycc_to_rgb(200, 90, 150) + S.ycc_to_rgb(60, 90, 150)))
        body = bytearray(3 * 1600 * 1200)
        body[4800:4806] = bytes((128, 255, 128, 255, 0, 0))  # first pair of the second row
        rows = decode_png(S.convert_bytes(header(*EVF, version=1) + bytes(body))[0])[2]
        self.assertEqual(rows[1][:6], b"\xff" * 6)
        self.assertEqual(rows[0][:6], bytes(S.ycc_to_rgb(0, 0, 0) * 2))

    def test_limited_range_and_matrix_options(self):
        data = shot(pairs=[(128, 235, 128, 16, 0, 0)])
        rows = decode_png(S.convert_bytes(data, full_range=False)[0])[2]
        self.assertEqual(rows[0][:6], bytes([255, 255, 255, 0, 0, 0]))
        rows = decode_png(S.convert_bytes(shot(pairs=[(85, 76, 255, 76, 0, 0)]), matrix="709")[0])[2]
        self.assertEqual(rows[0][:3], bytes(S.ycc_to_rgb(76, 85, 255, matrix="709")))
        with self.assertRaises(S.ScreenshotError):
            S.body_to_rows(bytes(12), 4, 2, matrix="2020")
        with self.assertRaises(S.ScreenshotError):
            S.body_to_rows(bytes(11), 4, 1)


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.card = self.base / "EOS_DIGITAL"
        self.shots = self.card / "DCIM" / "R5SHOTS"
        self.shots.mkdir(parents=True)
        (self.card / "DCIM" / "100CANON").mkdir()
        (self.shots / "SS_00001.YCA").write_bytes(shot(pairs=[(128, 255, 128, 255, 0, 0)]))
        (self.shots / "SS_00002.YCA").write_bytes(shot(*EVF, pairs=[(128, 0, 128, 0, 0, 0)]))
        (self.shots / "SS_00003.YCA").write_bytes(b"R5SSbroken")
        (self.shots / "._SS_00001.YCA").write_bytes(b"\x00\x05\x16\x07")  # macOS metadata, not a shot
        (self.shots / "NOTES.TXT").write_text("not a shot")
        self.out = self.base / "Pictures" / "R5 screenshots" / "2026-11-19"

    def convert(self, out=None, **kw):
        return S.convert_folder(self.shots, out or self.out, picked=self.card, home=self.base, **kw)

    def test_finds_r5shots_from_what_the_person_picks(self):
        for picked in (self.card, self.card / "DCIM", self.shots):
            self.assertEqual(S.find_shots_folder(picked), self.shots)
        copied = self.base / "copy"
        (copied / "r5shots").mkdir(parents=True)
        self.assertEqual(S.find_shots_folder(copied), copied / "r5shots")
        lower = self.base / "card2" / "dcim" / "r5shots"
        lower.mkdir(parents=True)
        self.assertEqual(S.find_shots_folder(self.base / "card2"), lower)
        self.assertIsNone(S.find_shots_folder(self.base / "Pictures"))
        self.assertIsNone(S.find_shots_folder(self.shots / "SS_00001.YCA"))
        (lower / "ss_00009.yca").write_bytes(b"")
        self.assertEqual([p.name for p in S.list_shots(lower)], ["ss_00009.yca"])
        self.assertEqual([p.name for p in S.list_shots(self.shots)], ["SS_00001.YCA", "SS_00002.YCA", "SS_00003.YCA"])

    def test_converts_counts_errors_and_never_writes_to_the_source(self):
        before = tree(self.card)
        seen = []
        result = self.convert(on_progress=lambda i, n, name: seen.append((i, n, name)))
        self.assertEqual(tree(self.card), before, "the card folder is only read")
        self.assertEqual([c.png.name for c in result.converted], ["SS_00001.png", "SS_00002.png"])
        self.assertEqual([c.header.surface for c in result.converted], ["LCD", "EVF"])
        self.assertEqual([name for name, _ in result.errors], ["SS_00003.YCA"])
        self.assertIn("not an R5SS screenshot", result.errors[0][1])
        self.assertFalse(result.ok)
        self.assertEqual(result.summary(), "2 converted, 0 already converted, 1 not converted")
        self.assertEqual(seen, [(0, 3, "SS_00001.YCA"), (1, 3, "SS_00002.YCA"), (2, 3, "SS_00003.YCA"), (3, 3, None)])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [S.MANIFEST_NAME, "SS_00001.png", "SS_00002.png"])
        self.assertEqual(decode_png((self.out / "SS_00001.png").read_bytes())[2][0][:6], b"\xff" * 6)
        self.assertEqual(decode_png((self.out / "SS_00002.png").read_bytes())[:2], EVF)

    def test_a_second_run_skips_what_is_already_converted(self):
        self.convert()
        pngs = tree(self.out)
        again = self.convert()
        self.assertEqual((len(again.converted), len(again.skipped), len(again.errors)), (0, 2, 1))
        self.assertEqual(again.summary(), "0 converted, 2 already converted, 1 not converted")
        manifest = tree(self.out).pop(S.MANIFEST_NAME)
        self.assertEqual({k: v for k, v in tree(self.out).items() if k != S.MANIFEST_NAME},
                         {k: v for k, v in pngs.items() if k != S.MANIFEST_NAME}, "nothing rewritten")
        self.assertEqual(manifest, pngs[S.MANIFEST_NAME])
        (self.shots / "SS_00003.YCA").unlink()
        self.assertTrue(self.convert().ok)

    def test_same_name_and_size_but_new_contents_is_converted_without_overwriting(self):
        self.convert()
        old = (self.out / "SS_00001.png").read_bytes()
        new = shot(pairs=[(128, 0, 128, 0, 0, 0)])  # same size, a different picture (card reformatted)
        (self.shots / "SS_00001.YCA").write_bytes(new)
        result = self.convert()
        tag = hashlib.sha256(new).hexdigest()[:8]
        self.assertEqual([c.png.name for c in result.converted], [f"SS_00001-{tag}.png"])
        self.assertEqual((self.out / "SS_00001.png").read_bytes(), old)
        self.assertEqual(len(self.convert().converted), 0)

    def test_existing_pngs_without_a_record(self):
        expected = S.convert_bytes((self.shots / "SS_00001.YCA").read_bytes())[0]
        self.out.mkdir(parents=True)
        (self.out / "SS_00001.png").write_bytes(expected)       # e.g. converted by the lab tool
        (self.out / "SS_00002.png").write_bytes(b"someone's own file")
        result = self.convert()
        self.assertEqual([c.png.name for c in result.skipped], ["SS_00001.png"])
        self.assertEqual(len(result.converted), 1)
        self.assertTrue(result.converted[0].png.name.startswith("SS_00002-"))
        self.assertEqual((self.out / "SS_00002.png").read_bytes(), b"someone's own file")

    def test_a_damaged_record_is_ignored(self):
        self.out.mkdir(parents=True)
        (self.out / S.MANIFEST_NAME).write_text("{not json")
        self.assertEqual(len(self.convert().converted), 2)
        (self.out / S.MANIFEST_NAME).write_text('{"version": 99, "files": {}}')
        self.assertEqual(len(self.convert().skipped), 2)  # the PNGs themselves are identical

    def test_output_inside_the_source_or_on_the_card_is_refused(self):
        before = tree(self.card)
        for out in (self.shots, self.shots / "png", self.card, self.card / "PNG", self.card / "DCIM"):
            with self.subTest(out=out), self.assertRaisesRegex(S.ScreenshotError, "inside"):
                self.convert(out)
        (self.base / "file").write_text("x")
        with self.assertRaisesRegex(S.ScreenshotError, "not a folder"):
            self.convert(self.base / "file")
        self.assertEqual(tree(self.card), before)
        # The card is its own drive (device 2); the home folder is on this computer's drive (1).
        with mock.patch.object(S, "_device", lambda p: 2 if "EOS_DIGITAL" in str(p) else 1):
            with self.assertRaisesRegex(S.ScreenshotError, "same drive"):
                S.check_output(self.base / "EOS_DIGITAL_OTHER" / "png", self.shots, home=self.base)
            S.check_output(self.out, self.shots, home=self.base)
        with mock.patch.object(S, "_device", lambda p: 1):  # copied to this computer: the same drive is fine
            S.check_output(self.base / "png", self.shots, home=self.base)

    def test_links_devices_and_oversized_files_are_not_read(self):
        (self.shots / "SS_00004.YCA").symlink_to(self.shots / "SS_00001.YCA")
        big = self.shots / "SS_00005.YCA"
        with open(big, "wb") as f:
            f.truncate(S.MAX_FILE_BYTES + 1)
        if os.path.exists("/dev/null"):
            (self.shots / "SS_00006.YCA").symlink_to("/dev/null")
        errors = dict(self.convert().errors)
        self.assertIn("not a regular file", errors["SS_00004.YCA"])
        self.assertIn("larger than any screenshot", errors["SS_00005.YCA"])
        if "SS_00006.YCA" in errors:
            self.assertIn("not a regular file", errors["SS_00006.YCA"])


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args, home):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run([sys.executable, str(ROOT / "tools" / "screenshots.py"), *map(str, args)],
                              capture_output=True, text=True, env=env, timeout=120)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            shots = d / "copy" / "R5SHOTS"
            shots.mkdir(parents=True)
            (shots / "SS_00001.YCA").write_bytes(shot())
            out = self.run_cli(shots, d / "png", home=d)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("SS_00001.YCA: 1024x682 LCD, R5SS v3 ->", out.stdout)
            self.assertIn("1 converted, 0 already converted", out.stdout)
            again = self.run_cli(d / "copy", d / "png", home=d)  # the parent of a copied R5SHOTS works too
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already converted (SS_00001.png)", again.stdout)
            (shots / "SS_00002.YCA").write_bytes(header(version=7, size=32))
            bad = self.run_cli(shots, d / "png", home=d)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("SS_00002.YCA: not converted (unknown R5SS version 7", bad.stderr)
            refused = self.run_cli(shots, shots / "png", home=d)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("Refused", refused.stderr)
            self.assertFalse((shots / "png").exists())
            missing = self.run_cli(d / "png", d / "x", home=d)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("No R5SHOTS folder", missing.stderr)


class StandaloneTests(unittest.TestCase):
    def test_standard_library_only(self):
        import ast
        tree_ = ast.parse((ROOT / "tools" / "screenshots.py").read_text())
        imported = set()
        for node in ast.walk(tree_):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
