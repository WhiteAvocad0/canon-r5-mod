"""Privileged raw-sector helper for the build assistant's card preparation.

The assistant runs this file as root through the operating system's own password
prompt (macOS: ``osascript ... with administrator privileges``), after copying it
to a root-owned temporary folder and checking its SHA-256. It never sees or
handles a password. It is self-contained (standard library only) and accepts only:

  version
  read  --disk /dev/rdiskN --partition /dev/rdiskNsM --length BYTES
  write --disk /dev/rdiskN --partition /dev/rdiskNsM --range OFFSET:LENGTH [--range ...]
        --expect-sha HEX --expect-mbr-sha HEX --expect-area-sha HEX --after FILE --after-sha HEX

``read`` returns the 512-byte MBR of the disk and the first BYTES of the
partition. ``write`` first checks, on the open devices, that the MBR, the whole
24-sector boot area and the target sectors still hash to the values from the
read the plan was made from (so a swapped or changed card is refused), and only
then writes the listed ranges; it then reads back the whole boot area and the
MBR. The MBR is only ever read. Write ranges are single 512-byte sectors chosen
from partition sectors 0, 11, 12 and 23, at most four, non-overlapping, written
in the order given, each followed by fsync. A sector whose write was started but
not confirmed is reported in ``possibly_written``. It prints exactly one JSON
line and exits 0 for every handled outcome (refusals included); a crash exits
non-zero. Paths are checked against the whitelist before they are touched.

It must stay runnable by Python 3.9 (the root-owned Python of Apple's Command
Line Tools), using only the standard library.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys

PROTOCOL = 2
SECTOR = 512
AREA_BYTES = 24 * SECTOR
MAX_RANGES = 4
# The only sectors any plan writes: FAT32 sector 0; exFAT sectors 0 and 11 (main) and 12 and 23 (backup).
WRITABLE_OFFSETS = frozenset({0, 11 * SECTOR, 12 * SECTOR, 23 * SECTOR})
DISK_RE = re.compile(r"/dev/rdisk([0-9]{1,3})")
PART_RE = re.compile(r"/dev/rdisk([0-9]{1,3})s([0-9]{1,2})")
RANGE_RE = re.compile(r"([0-9]{1,6}):([0-9]{1,6})")
SHA_RE = re.compile(r"[0-9a-f]{64}")
OPTIONS = {
    "version": {},
    "read": {"--disk": "one", "--partition": "one", "--length": "one"},
    "write": {"--disk": "one", "--partition": "one", "--range": "many", "--expect-sha": "one",
              "--expect-mbr-sha": "one", "--expect-area-sha": "one", "--after": "one", "--after-sha": "one"},
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


def _stat(path: str) -> os.stat_result:
    try:
        return os.stat(path)
    except OSError as exc:
        raise Refused("device_missing", f"Cannot open {path}: {exc.strerror}.")


def _devices(values: dict, allow_image_files: bool) -> tuple[str, str]:
    """Raw disk nodes matching the whitelist (or, in tests only, regular image files).

    The names are checked before anything is looked up on disk."""
    disk, part = values["--disk"], values["--partition"]
    if allow_image_files and not os.path.islink(disk) and not os.path.islink(part) \
            and all(stat.S_ISREG(_stat(p).st_mode) for p in (disk, part)):
        return disk, part
    d, p = DISK_RE.fullmatch(disk), PART_RE.fullmatch(part)
    if d is None or p is None:
        raise Refused("device_not_allowed", f"{disk} / {part} are not allowed raw disk nodes.")
    if d.group(1) != p.group(1):
        raise Refused("device_not_allowed", "The partition does not belong to the given disk.")
    for path in (disk, part):
        if not stat.S_ISCHR(_stat(path).st_mode):
            raise Refused("device_not_allowed", f"{path} is not a raw character device.")
    return disk, part


def _ranges(texts: list[str], allowed: frozenset[int] | None = None) -> list[tuple[int, int]]:
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
        if allowed is not None and (length != SECTOR or offset not in allowed):
            raise Refused("range_not_allowed", f"Range {text} is not one of the boot sectors 0, 11, 12 or 23.")
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
    ranges = _ranges(values["--range"], WRITABLE_OFFSETS)
    shas = {k: values[k] for k in ("--expect-sha", "--expect-mbr-sha", "--expect-area-sha", "--after-sha")}
    if not all(SHA_RE.fullmatch(v) for v in shas.values()):
        raise Refused("usage", "SHA-256 values must be 64 lowercase hex digits.")
    after = _after_file(values["--after"], sum(n for _, n in ranges), shas["--after-sha"])
    pieces, at = [], 0
    for _, n in ranges:
        pieces.append(after[at:at + n])
        at += n
    written: list[int] = []
    started: list[int] = []
    mbr_fd = os.open(disk, os.O_RDONLY)
    try:
        fd = os.open(part, os.O_RDWR)
        try:
            mbr_before = _pread(mbr_fd, 0, SECTOR)
            area_before = _pread(fd, 0, AREA_BYTES)
            current = b"".join(area_before[o:o + n] for o, n in ranges)
            for name, data, what in (("--expect-mbr-sha", mbr_before, "MBR"),
                                     ("--expect-area-sha", area_before, "boot area"),
                                     ("--expect-sha", current, "target sectors")):
                if _sha(data) != shas[name]:
                    raise Refused("before_mismatch",
                                  f"The card's {what} differs from the read the plan was made from; nothing was written.",
                                  mismatch=what, written=[], possibly_written=[])
            if current != after:
                try:
                    for (offset, _), data in zip(ranges, pieces):
                        started.append(offset)
                        _pwrite(fd, offset, data)
                        os.fsync(fd)
                        written.append(offset)
                except OSError as exc:
                    raise Refused("write_failed",
                                  f"Writing failed at byte {started[-1]} ({len(written)} of {len(ranges)} sector(s) "
                                  f"confirmed): {exc}", written=written, possibly_written=started)
            try:
                readback = b"".join(_pread(fd, o, n) for o, n in ranges)
                area = _pread(fd, 0, AREA_BYTES)
                mbr_after = _pread(mbr_fd, 0, SECTOR)
            except (OSError, Refused) as exc:
                raise Refused("readback_failed", f"Reading back after the write failed: {exc}",
                              written=written, possibly_written=started)
        finally:
            os.close(fd)
    finally:
        os.close(mbr_fd)
    ok = readback == after and mbr_after == mbr_before
    return {"ok": ok, "code": None if ok else "readback_mismatch",
            "written": written, "possibly_written": started, "before_sha256": _sha(current),
            "readback_sha256": _sha(readback), "mbr_unchanged": mbr_after == mbr_before,
            "mbr_hex": mbr_after.hex(), "area_hex": area.hex()}


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
