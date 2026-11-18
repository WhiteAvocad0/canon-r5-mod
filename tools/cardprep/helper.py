"""Privileged raw-sector helper for the build assistant's card preparation.

The assistant runs this file as root through the operating system's own password
prompt (macOS: ``osascript ... with administrator privileges``), after copying it
to a root-owned temporary folder and checking its SHA-256. It never sees or
handles a password. It is self-contained (standard library only) and accepts only:

  version
  read  --disk /dev/rdiskN --partition /dev/rdiskNsM --length BYTES
  write --disk /dev/rdiskN --partition /dev/rdiskNsM --range OFFSET:LENGTH [--range ...]
        --expect-sha HEX --after FILE --after-sha HEX

``read`` returns the 512-byte MBR of the disk and the first BYTES of the
partition. ``write`` writes only the listed ranges of the partition, and only if
the bytes currently there hash to --expect-sha; it then reads back the whole boot
area and the MBR. The MBR is only ever read. Ranges are whole 512-byte sectors in
the first 24 sectors of the partition, at most four, non-overlapping, and written
in the order given, each followed by fsync. It prints exactly one JSON line and
exits 0 for every handled outcome (refusals included); a crash exits non-zero.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys

PROTOCOL = 1
SECTOR = 512
AREA_BYTES = 24 * SECTOR
MAX_RANGES = 4
DISK_RE = re.compile(r"/dev/rdisk([0-9]{1,3})")
PART_RE = re.compile(r"/dev/rdisk([0-9]{1,3})s([0-9]{1,2})")
RANGE_RE = re.compile(r"([0-9]{1,6}):([0-9]{1,6})")
SHA_RE = re.compile(r"[0-9a-f]{64}")
OPTIONS = {
    "version": {},
    "read": {"--disk": "one", "--partition": "one", "--length": "one"},
    "write": {"--disk": "one", "--partition": "one", "--range": "many", "--expect-sha": "one",
              "--after": "one", "--after-sha": "one"},
}


class Refused(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.extra = code, extra


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_args(argv: list[str]) -> tuple[str, dict]:
    if not argv or argv[0] not in OPTIONS:
        raise Refused("usage", "The first argument must be version, read or write.")
    op, spec, values = argv[0], OPTIONS[argv[0]], {}
    rest = argv[1:]
    if len(rest) % 2:
        raise Refused("usage", "Every option needs exactly one value.")
    for name, value in zip(rest[::2], rest[1::2]):
        if name not in spec:
            raise Refused("usage", f"Option {name!r} is not allowed for {op}.")
        if spec[name] == "one":
            if name in values:
                raise Refused("usage", f"Option {name} given twice.")
            values[name] = value
        else:
            values.setdefault(name, []).append(value)
    missing = [n for n in spec if n not in values]
    if missing:
        raise Refused("usage", "Missing option(s): " + ", ".join(missing))
    return op, values


def _device(path: str, pattern: re.Pattern, allow_image_files: bool) -> str:
    """A raw disk node matching the whitelist (or, in tests only, a regular image file)."""
    try:
        st = os.stat(path)
    except OSError as exc:
        raise Refused("device_missing", f"Cannot open {path}: {exc.strerror}.")
    if allow_image_files and stat.S_ISREG(st.st_mode) and not os.path.islink(path):
        return path
    if not pattern.fullmatch(path):
        raise Refused("device_not_allowed", f"{path} is not an allowed raw disk node.")
    if not stat.S_ISCHR(st.st_mode):
        raise Refused("device_not_allowed", f"{path} is not a raw character device.")
    return path


def _devices(values: dict, allow_image_files: bool) -> tuple[str, str]:
    disk = _device(values["--disk"], DISK_RE, allow_image_files)
    part = _device(values["--partition"], PART_RE, allow_image_files)
    d, p = DISK_RE.fullmatch(disk), PART_RE.fullmatch(part)
    if (d is None) != (p is None) or (d and d.group(1) != p.group(1)):
        raise Refused("device_not_allowed", "The partition does not belong to the given disk.")
    return disk, part


def _ranges(texts: list[str]) -> list[tuple[int, int]]:
    if not 1 <= len(texts) <= MAX_RANGES:
        raise Refused("range_not_allowed", f"Between 1 and {MAX_RANGES} ranges are allowed.")
    ranges = []
    for text in texts:
        m = RANGE_RE.fullmatch(text)
        if not m:
            raise Refused("range_not_allowed", f"Range {text!r} is not OFFSET:LENGTH in decimal bytes.")
        offset, length = int(m.group(1)), int(m.group(2))
        if offset % SECTOR or length % SECTOR or length == 0 or offset + length > AREA_BYTES:
            raise Refused("range_not_allowed",
                          f"Range {text} must be whole 512-byte sectors inside the first {AREA_BYTES} bytes.")
        for o, n in ranges:
            if offset < o + n and o < offset + length:
                raise Refused("range_not_allowed", f"Range {text} overlaps another range.")
        ranges.append((offset, length))
    return ranges


def _pread(fd: int, offset: int, length: int) -> bytes:
    chunks, done = [], 0
    while done < length:
        piece = os.pread(fd, length - done, offset + done)
        if not piece:
            raise Refused("short_read", f"Short read at byte {offset + done}.")
        chunks.append(piece)
        done += len(piece)
    return b"".join(chunks)


def _pwrite(fd: int, offset: int, data: bytes) -> None:
    done = 0
    while done < len(data):
        count = os.pwrite(fd, data[done:], offset + done)
        if count <= 0:
            raise OSError(f"write returned {count} at byte {offset + done}")
        done += count


def _read_mbr(disk: str) -> bytes:
    fd = os.open(disk, os.O_RDONLY)
    try:
        return _pread(fd, 0, SECTOR)
    finally:
        os.close(fd)


def do_read(values: dict, allow_image_files: bool) -> dict:
    disk, part = _devices(values, allow_image_files)
    text = values["--length"]
    if not text.isdigit() or int(text) % SECTOR or not SECTOR <= int(text) <= AREA_BYTES:
        raise Refused("range_not_allowed", f"--length must be whole sectors between {SECTOR} and {AREA_BYTES}.")
    mbr = _read_mbr(disk)
    fd = os.open(part, os.O_RDONLY)
    try:
        area = _pread(fd, 0, int(text))
    finally:
        os.close(fd)
    return {"ok": True, "mbr_hex": mbr.hex(), "area_hex": area.hex()}


def _after_file(path: str, size: int, expected_sha: str) -> bytes:
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise Refused("after_file", f"Cannot read the after-bytes file: {exc.strerror}.")
    if not stat.S_ISREG(st.st_mode) or st.st_size != size:
        raise Refused("after_file", f"The after-bytes file must be a regular file of exactly {size} bytes.")
    with open(path, "rb") as f:
        data = f.read(size + 1)
    if len(data) != size or _sha(data) != expected_sha:
        raise Refused("after_file", "The after-bytes file does not match --after-sha.")
    return data


def do_write(values: dict, allow_image_files: bool) -> dict:
    disk, part = _devices(values, allow_image_files)
    ranges = _ranges(values["--range"])
    expect, after_sha = values["--expect-sha"], values["--after-sha"]
    if not SHA_RE.fullmatch(expect) or not SHA_RE.fullmatch(after_sha):
        raise Refused("usage", "SHA-256 values must be 64 lowercase hex digits.")
    after = _after_file(values["--after"], sum(n for _, n in ranges), after_sha)
    pieces, at = [], 0
    for _, n in ranges:
        pieces.append(after[at:at + n])
        at += n
    mbr_before = _read_mbr(disk)
    written: list[int] = []
    fd = os.open(part, os.O_RDWR)
    try:
        current = b"".join(_pread(fd, o, n) for o, n in ranges)
        if _sha(current) != expect:
            raise Refused("before_mismatch",
                          "The card's current bytes differ from what was planned; nothing was written.",
                          current_sha256=_sha(current), written=[])
        if current != after:
            try:
                for (offset, _), data in zip(ranges, pieces):
                    _pwrite(fd, offset, data)
                    os.fsync(fd)
                    written.append(offset)
            except OSError as exc:
                raise Refused("write_failed", f"Writing failed after {len(written)} of {len(ranges)} range(s): {exc}",
                              written=written)
        try:
            readback = b"".join(_pread(fd, o, n) for o, n in ranges)
            area = _pread(fd, 0, AREA_BYTES)
        except (OSError, Refused) as exc:
            raise Refused("readback_failed", f"Reading back after the write failed: {exc}", written=written)
    finally:
        os.close(fd)
    try:
        mbr_after = _read_mbr(disk)
    except (OSError, Refused) as exc:
        raise Refused("readback_failed", f"Reading back the MBR failed: {exc}", written=written)
    return {"ok": readback == after and mbr_after == mbr_before, "code": None if readback == after else "readback_mismatch",
            "written": written, "before_sha256": _sha(current), "readback_sha256": _sha(readback),
            "mbr_unchanged": mbr_after == mbr_before, "mbr_hex": mbr_after.hex(), "area_hex": area.hex()}


def main(argv: list[str], *, allow_image_files: bool = False) -> dict:
    """Run one operation and return its JSON-able result. Image files are for tests only."""
    try:
        op, values = parse_args(argv)
        if op == "version":
            result = {"ok": True}
        elif op == "read":
            result = do_read(values, allow_image_files)
        else:
            result = do_write(values, allow_image_files)
    except Refused as exc:
        op = argv[0] if argv else None
        result = {"ok": False, "code": exc.code, "error": str(exc), **exc.extra}
    except OSError as exc:
        op = argv[0] if argv else None
        result = {"ok": False, "code": "os_error", "error": str(exc)}
    return {"helper": "r5-cardprep", "protocol": PROTOCOL, "op": op, **result}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), sort_keys=True))
    sys.exit(0)
