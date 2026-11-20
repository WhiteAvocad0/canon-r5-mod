"""Convert on-camera screenshots (DCIM/R5SHOTS/SS_nnnnn.YCA) to PNG files.

The on-camera screenshot build is a private lab feature; it is not in the public firmware
build yet. Each file it saves is an ``R5SS`` header followed by the camera's display layer
exactly as the camera holds it: 4:2:2 YCbCr with alpha, 6 bytes per pair of pixels in the
order U, Y0, V, Y1, A0, A1, at 1024x682 (rear LCD) or 1600x1200 (viewfinder). The layer is
the on-screen display only; the live image underneath is not part of it.

Standard library only (zlib and struct, no Pillow). This module only reads the screenshot
folder: it never writes, renames or deletes anything there, and it refuses an output folder
inside it or on the card's drive. Existing PNG files are never overwritten.

Run:  python3 tools/screenshots.py <R5SHOTS folder, or the card's top folder> <output folder>
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
from operator import itemgetter
import os
from pathlib import Path
import stat
import struct
import sys
import zlib

MAGIC = b"R5SS"
FOURCC = b"UYVA"
HEADER_FIELDS = struct.Struct("<4sHHIIII4sI")  # the 32 bytes every version uses
HEADER_BYTES = {1: 32, 2: 32, 3: 512}  # header size per known version (v3 pads to one sector with zeros)
SURFACES = {(1024, 682): "LCD", (1600, 1200): "EVF"}  # the two display layers the camera saves
BYTES_PER_PAIR = 6
MAX_FILE_BYTES = max(HEADER_BYTES.values()) + max(3 * w * h for w, h in SURFACES)
SHOTS_FOLDER = "R5SHOTS"
SHOT_PATTERN = "SS_*.YCA"
MANIFEST_NAME = ".r5-screenshots.json"  # what was converted here, so a second run skips it
MANIFEST_VERSION = 1
MATRICES = {"601": (0.299, 0.114), "709": (0.2126, 0.0722)}


class ScreenshotError(ValueError):
    """A screenshot file, folder or output location that is refused, with the reason."""


# -- the R5SS header --------------------------------------------------------------------


@dataclass(frozen=True)
class Header:
    version: int
    header_bytes: int
    width: int
    height: int
    pitch: int
    data_bytes: int
    surface: str                       # "LCD" or "EVF"
    output: int | None = None          # v2+: the video mixer output (0)
    selected_buffer: int | None = None # v2+: which of the display buffers was saved
    number_low16: int | None = None    # v2+: the capture number, low 16 bits

    def describe(self) -> str:
        return f"{self.width}x{self.height} {self.surface}, R5SS v{self.version}"


def parse_header(data: bytes, file_size: int) -> Header:
    """Check the header of a screenshot of ``file_size`` bytes whose first bytes are ``data``.

    Strict: the magic, a known version with its header size, zero padding, the UYVA layout,
    one of the two known display sizes and a file size that adds up are all required."""
    if len(data) < HEADER_FIELDS.size or data[:4] != MAGIC:
        raise ScreenshotError("not an R5SS screenshot (the file does not start with 'R5SS')")
    _magic, version, size, w, h, pitch, length, fourcc, meta = HEADER_FIELDS.unpack_from(data)
    if version not in HEADER_BYTES:
        raise ScreenshotError(f"unknown R5SS version {version} (known: {', '.join(map(str, HEADER_BYTES))})")
    if size != HEADER_BYTES[version]:
        raise ScreenshotError(f"R5SS v{version} header size is {size}, expected {HEADER_BYTES[version]}")
    if fourcc != FOURCC:
        raise ScreenshotError(f"unknown pixel layout {fourcc!r} (expected {FOURCC.decode()})")
    if len(data) < size or file_size < size:
        raise ScreenshotError(f"the file is shorter than its {size}-byte header")
    if any(data[HEADER_FIELDS.size:size]):
        raise ScreenshotError("the header's padding is not zero")
    surface = SURFACES.get((w, h))
    if surface is None:
        raise ScreenshotError(f"unknown display size {w}x{h} (known: "
                              + ", ".join(f"{a}x{b} {s}" for (a, b), s in SURFACES.items()) + ")")
    if pitch != 3 * w or length != pitch * h:
        raise ScreenshotError(f"pitch {pitch} and length {length} do not match {w}x{h} ({3 * w}, {3 * w * h})")
    if file_size != size + length:
        raise ScreenshotError(f"the file has {file_size:,} bytes; its header says {size + length:,}")
    extra = {}
    if version >= 2:
        extra = {"output": meta & 0xFF, "selected_buffer": (meta >> 8) & 0xFF, "number_low16": meta >> 16}
    return Header(version, size, w, h, pitch, length, surface, **extra)


# -- pixels -------------------------------------------------------------------------------


def _clamp(v: float) -> int:
    return 0 if v < 0 else 255 if v > 255 else int(v + 0.5)


def ycc_to_rgb(y: int, cb: int, cr: int, *, matrix: str = "601", full_range: bool = True) -> tuple[int, int, int]:
    """One pixel. Full range treats Y, Cb and Cr as 0-255; limited range as 16-235/16-240."""
    kr, kb = MATRICES[matrix]
    kg = 1 - kr - kb
    if full_range:
        yy, u, v = float(y), cb - 128.0, cr - 128.0
    else:
        yy, u, v = (y - 16) * 255 / 219, (cb - 128) * 255 / 224, (cr - 128) * 255 / 224
    r = yy + 2 * (1 - kr) * v
    b = yy + 2 * (1 - kb) * u
    g = (yy - kr * r - kb * b) / kg
    return _clamp(r), _clamp(g), _clamp(b)


class _PairCache(dict):
    """(U, Y0, V, Y1) -> the two RGB pixels. Display layers repeat a few colours a lot."""

    LIMIT = 1 << 16

    def __init__(self, matrix: str, full_range: bool):
        super().__init__()
        self.matrix, self.full_range = matrix, full_range

    def __missing__(self, quad: bytes) -> bytes:
        u, y0, v, y1 = quad
        rgb = bytes(ycc_to_rgb(y0, u, v, matrix=self.matrix, full_range=self.full_range)
                    + ycc_to_rgb(y1, u, v, matrix=self.matrix, full_range=self.full_range))
        self[quad] = rgb
        return rgb


_PAIRS = struct.Struct("4s2x")  # U Y0 V Y1, then the two alpha bytes (not used for the picture)


def body_to_rows(body: bytes, width: int, height: int, *, matrix: str = "601", full_range: bool = True) -> list[bytes]:
    """RGB rows of a UYVA body. Alpha is dropped: the picture is the layer's colour as stored."""
    if matrix not in MATRICES:
        raise ScreenshotError(f"unknown matrix {matrix!r}")
    pitch = 3 * width
    if width <= 0 or height <= 0 or width % 2 or len(body) != pitch * height:
        raise ScreenshotError(f"{len(body):,} bytes is not a {width}x{height} UYVA layer")
    cache = _PairCache(matrix, full_range)
    first = itemgetter(0)
    rows = []
    for top in range(0, pitch * height, pitch):
        rows.append(b"".join(map(cache.__getitem__, map(first, _PAIRS.iter_unpack(body[top:top + pitch])))))
        if len(cache) > cache.LIMIT:
            cache.clear()
    return rows


def encode_png(width: int, height: int, rows: list[bytes]) -> bytes:
    """An 8-bit RGB PNG: no filter, one IDAT at zlib level 6."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def convert_bytes(data: bytes, *, matrix: str = "601", full_range: bool = True) -> tuple[bytes, Header]:
    """A whole SS_nnnnn.YCA file in memory -> (PNG bytes, its header)."""
    header = parse_header(data, len(data))
    rows = body_to_rows(data[header.header_bytes:], header.width, header.height, matrix=matrix,
                        full_range=full_range)
    return encode_png(header.width, header.height, rows), header


# -- folders ------------------------------------------------------------------------------


def _child(folder: Path, name: str) -> Path | None:
    """A sub-folder by name in any upper/lower-case spelling (cards are FAT or exFAT)."""
    try:
        matches = [p for p in folder.iterdir() if p.name.casefold() == name.casefold() and p.is_dir()]
    except OSError:
        return None
    return sorted(matches)[0] if matches else None


def find_shots_folder(folder: Path) -> Path | None:
    """The R5SHOTS folder for what the person picked: R5SHOTS itself, the card's top folder
    (DCIM/R5SHOTS), DCIM, or a folder that holds a copied R5SHOTS."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    if folder.name.casefold() == SHOTS_FOLDER.casefold():
        return folder
    if folder.name.casefold() == "dcim":
        return _child(folder, SHOTS_FOLDER)
    dcim = _child(folder, "DCIM")
    return (_child(dcim, SHOTS_FOLDER) if dcim else None) or _child(folder, SHOTS_FOLDER)


def list_shots(shots: Path) -> list[Path]:
    """SS_*.YCA files in the folder (any case), in name order; macOS '._' files are not shots."""
    return sorted((p for p in Path(shots).iterdir() if fnmatch.fnmatchcase(p.name.upper(), SHOT_PATTERN)),
                  key=lambda p: p.name.upper())


def _resolved(path: Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser())).resolve()


def _device(path: Path) -> int | None:
    """The filesystem of the path, or of its nearest existing parent."""
    for candidate in (path, *path.parents):
        try:
            return os.stat(candidate).st_dev
        except OSError:
            continue
    return None


def check_output(out: Path, shots: Path, picked: Path | None = None, *, home: Path | None = None) -> None:
    """Refuse an output folder that would write into the screenshots' folder or onto the card.

    The output may not be (inside) the R5SHOTS folder or the folder the person picked. When
    the screenshots are on a different drive than the home folder (a card), the output may
    not be on that drive either."""
    out_r = _resolved(out)
    for source in (shots, picked):
        if source is None:
            continue
        src = _resolved(source)
        if out_r == src or out_r.is_relative_to(src):
            raise ScreenshotError(f"{out} is inside {source}; choose a folder outside the screenshots' folder.")
    shots_dev = _device(_resolved(shots))
    home_dev = _device(_resolved(Path.home() if home is None else home))
    if shots_dev is not None and shots_dev != home_dev and _device(out_r) == shots_dev:
        raise ScreenshotError(f"{out} is on the same drive as the screenshots (the card); choose a folder on "
                              "this computer.")
    if out_r.exists() and not out_r.is_dir():
        raise ScreenshotError(f"{out} exists and is not a folder.")


def _read_shot(path: Path) -> bytes:
    """Read a regular file (not a link or device), refusing anything larger than a screenshot."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ScreenshotError("not a regular file")
    if st.st_size > MAX_FILE_BYTES:
        raise ScreenshotError(f"{st.st_size:,} bytes is larger than any screenshot ({MAX_FILE_BYTES:,})")
    with open(path, "rb") as f:
        data = f.read(MAX_FILE_BYTES + 1)
    if len(data) != st.st_size:
        raise ScreenshotError("the file changed size while it was read")
    return data


@dataclass
class Converted:
    source: Path
    png: Path
    header: Header | None  # None when it was skipped as already converted


@dataclass
class BatchResult:
    shots: Path
    out_dir: Path
    converted: list[Converted] = field(default_factory=list)
    skipped: list[Converted] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (file name, reason)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [f"{len(self.converted)} converted", f"{len(self.skipped)} already converted"]
        if self.errors:
            parts.append(f"{len(self.errors)} not converted")
        return ", ".join(parts)


def _load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION or not isinstance(data.get("files"), dict):
        return {}
    return data["files"]


def _save_manifest(path: Path, files: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"version": MANIFEST_VERSION, "files": files}, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _write_new(path: Path, data: bytes) -> None:
    """Create ``path``; never replace an existing file. A partial file of ours is removed."""
    with open(path, "xb") as f:
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            path.unlink(missing_ok=True)
            raise


def _holds(path: Path, data: bytes) -> bool:
    """Whether the regular file at path has exactly these bytes (its size is compared first)."""
    try:
        st = os.lstat(path)
        return stat.S_ISREG(st.st_mode) and st.st_size == len(data) and path.read_bytes() == data
    except OSError:
        return False


def _already(record, size: int, digest: str, out_dir: Path) -> Path | None:
    """The PNG a previous run made from the same file (same name, size and SHA-256), if it is still there."""
    if not isinstance(record, dict) or record.get("bytes") != size or record.get("sha256") != digest:
        return None
    png = out_dir / str(record.get("png", ""))
    try:
        return png if png.parent == out_dir and png.is_file() and png.stat().st_size == record.get("png_bytes") \
            else None
    except OSError:
        return None


def convert_folder(shots: Path, out_dir: Path, *, picked: Path | None = None, matrix: str = "601",
                   full_range: bool = True, home: Path | None = None, on_progress=None) -> BatchResult:
    """Convert every SS_*.YCA in ``shots`` into ``out_dir`` (created if needed).

    A file already converted into this folder (same name, size and contents) is skipped. A
    different PNG of the same name is kept, and the new one gets the file's short SHA-256 in
    its name. ``on_progress(index, total, name)`` is called before each file."""
    shots, out_dir = Path(shots), Path(out_dir)
    check_output(out_dir, shots, picked, home=home)
    files = list_shots(shots)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    result = BatchResult(shots, out_dir)
    for index, source in enumerate(files):
        if on_progress is not None:
            on_progress(index, len(files), source.name)
        try:
            data = _read_shot(source)
            digest = hashlib.sha256(data).hexdigest()
            done = _already(manifest.get(source.name), len(data), digest, out_dir)
            if done is not None:
                result.skipped.append(Converted(source, done, None))
                continue
            png, header = convert_bytes(data, matrix=matrix, full_range=full_range)
            target = out_dir / f"{source.stem}.png"
            if target.exists() and not _holds(target, png):
                target = out_dir / f"{source.stem}-{digest[:8]}.png"  # keep the other file; never overwrite
                if target.exists() and not _holds(target, png):
                    raise ScreenshotError(f"{target.name} already exists with other contents; not overwritten")
            if target.exists():
                result.skipped.append(Converted(source, target, header))  # the same PNG is already there
            else:
                _write_new(target, png)
                result.converted.append(Converted(source, target, header))
            manifest[source.name] = {"bytes": len(data), "sha256": digest, "png": target.name, "png_bytes": len(png),
                                     **{k: v for k, v in header.__dict__.items() if v is not None}}
            _save_manifest(manifest_path, manifest)
        except (ScreenshotError, OSError) as exc:
            result.errors.append((source.name, str(exc)))
    if on_progress is not None:
        on_progress(len(files), len(files), None)
    return result


# -- command line -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert on-camera screenshots (R5SHOTS/SS_*.YCA) to PNG. Reads the source folder only.")
    parser.add_argument("source", type=Path, help="the R5SHOTS folder, DCIM, or the card's top folder")
    parser.add_argument("out", type=Path, help="output folder (created if needed; never on the card)")
    parser.add_argument("--limited-range", action="store_true",
                        help="treat YCbCr as limited range (16-235); the default is full range, as the lab used")
    parser.add_argument("--matrix", choices=sorted(MATRICES), default="601", help="YCbCr matrix (default 601)")
    args = parser.parse_args(argv)
    shots = find_shots_folder(args.source)
    if shots is None:
        print(f"No {SHOTS_FOLDER} folder found in {args.source}.", file=sys.stderr)
        return 2
    try:
        result = convert_folder(shots, args.out, picked=args.source, matrix=args.matrix,
                                full_range=not args.limited_range)
    except (ScreenshotError, OSError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    for item in result.converted:
        print(f"{item.source.name}: {item.header.describe()} -> {item.png}")
    for item in result.skipped:
        print(f"{item.source.name}: already converted ({item.png.name})")
    for name, reason in result.errors:
        print(f"{name}: not converted ({reason})", file=sys.stderr)
    print(f"{result.summary()} in {result.out_dir}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
