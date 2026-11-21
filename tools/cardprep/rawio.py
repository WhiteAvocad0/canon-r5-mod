"""Raw access to a card's whole-disk device through macOS ``authopen`` (nothing runs as root).

``/usr/libexec/authopen -stdoutpipe -o FLAGS /dev/rdiskN`` asks macOS for the right
``sys.openfile.readonly./dev/rdiskN`` (or ``.readwrite.``) in the system's own
password window, opens only that device, and sends the open file descriptor back to
this process over a UNIX socket (SCM_RIGHTS). The descriptor is checked to be that
device, used for the exact reads and writes below, and always closed again. This is
the method Raspberry Pi Imager uses.

All offsets are absolute on the whole disk: the MBR at 0 and the partition's boot
area at the partition start. Writes keep the safety rules the root helper used to
enforce: only single 512-byte sectors 0, 11, 12 and 23 of the partition, at most four,
in the order given; and only if the MBR, the whole 24-sector boot area and the target
sectors still hash to the values from the read the plan was made from. Each write is
followed by a flush (F_FULLFSYNC where available). Everything written is read back,
and a sector whose write was started but not confirmed is reported as possibly written.
"""
from __future__ import annotations

import errno
import hashlib
import os
import socket
import stat
import subprocess
from typing import Callable, Sequence

AUTHOPEN = "/usr/libexec/authopen"
SECTOR = 512
AREA_BYTES = 24 * SECTOR
MAX_RANGES = 4
WRITABLE_SECTORS = (0, 11, 12, 23)  # FAT32 sector 0; exFAT 0 and 11 (main), 12 and 23 (backup)
AUTH_CANCELED = "-60006"  # errAuthorizationCanceled


class DeviceError(Exception):
    """A device query or raw access failed, or the card changed."""


class Cancelled(DeviceError):
    """The person cancelled the password window."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_cancelled(returncode: int | None, stderr: str) -> bool:
    """authopen's report of a cancelled password window (its exact text is not documented)."""
    text = stderr.lower()
    return AUTH_CANCELED in text or "cancel" in text


def open_device(device: str, flags: int, *, command: Sequence[str] = (AUTHOPEN,), timeout: float = 1800) -> int:
    """Have authopen open ``device`` with open(2) ``flags`` and return the descriptor it sends.

    Raises Cancelled if the password window was cancelled, otherwise DeviceError with
    authopen's own message. The caller owns (and must close) the returned descriptor."""
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            proc = subprocess.Popen([*command, "-stdoutpipe", "-o", str(flags), device], stdin=subprocess.DEVNULL,
                                    stdout=child.fileno(), stderr=subprocess.PIPE, close_fds=True)
        except OSError as exc:
            raise DeviceError(f"authopen could not be started: {exc}") from exc
        finally:
            child.close()
        fds: list[int] = []
        problem = None
        try:
            parent.settimeout(timeout)
            _msg, fds, _flags, _addr = socket.recv_fds(parent, 1024, 4)
        except socket.timeout:
            problem = f"authopen did not answer within {int(timeout)} s."
            proc.kill()
        except OSError as exc:
            problem = f"No descriptor was received from authopen ({exc})."
        try:
            _out, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()
        message = (err or b"").decode(errors="replace").strip()
        if proc.returncode == 0 and len(fds) == 1 and problem is None:
            return fds[0]
        for fd in fds:
            os.close(fd)
        if looks_cancelled(proc.returncode, message):
            raise Cancelled("The password window was cancelled.")
        if problem:
            raise DeviceError(problem)
        if proc.returncode == 0:
            raise DeviceError(f"authopen sent {len(fds)} descriptors for {device}, expected exactly one.")
        raise DeviceError(f"authopen could not open {device} (exit status {proc.returncode}): "
                          f"{message or 'no message'}")
    finally:
        parent.close()


def check_char_device(fd: int, device: str) -> None:
    """Production binding: the descriptor must be the character device ``device`` itself."""
    st, expected = os.fstat(fd), os.stat(device)
    if not stat.S_ISCHR(st.st_mode) or not stat.S_ISCHR(expected.st_mode):
        raise DeviceError(f"The descriptor received for {device} is not a raw character device.")
    if st.st_rdev != expected.st_rdev:
        raise DeviceError(f"The descriptor received is not {device}; refusing it.")


def _read(fd: int, offset: int, length: int) -> bytes:
    chunks, done = [], 0
    while done < length:
        piece = os.pread(fd, length - done, offset + done)
        if not piece:
            raise OSError(errno.EIO, f"short read at byte {offset + done}")
        chunks.append(piece)
        done += len(piece)
    return b"".join(chunks)


def _write(fd: int, offset: int, data: bytes) -> None:
    done = 0
    while done < len(data):
        count = os.pwrite(fd, data[done:], offset + done)
        if count <= 0:
            raise OSError(errno.EIO, f"write returned {count} at byte {offset + done}")
        done += count


def _flush(fd: int) -> None:
    """F_FULLFSYNC (macOS) where the device supports it, otherwise fsync."""
    import fcntl
    full = getattr(fcntl, "F_FULLFSYNC", None)
    if full is not None:
        try:
            fcntl.fcntl(fd, full)
            return
        except OSError as exc:
            if exc.errno not in (errno.ENOTTY, errno.ENOTSUP, errno.EINVAL, errno.ENODEV):
                raise
    os.fsync(fd)


def check_start(mbr: bytes, sector0: bytes, start: int) -> None:
    """The partition start must be the one this card's own bytes give: the single MBR
    entry's LBA x 512, and the boot sector's field (exFAT PartitionOffset at byte 64,
    FAT32 HiddenSectors at byte 28). Checked on the descriptor itself before any I/O
    at ``start``, so rawio never reads or writes a partition the card doesn't have."""
    if len(mbr) != SECTOR or mbr[510:512] != b"\x55\xaa":
        raise DeviceError("The card has no MBR partition table.")
    entries = [mbr[446 + 16 * i:462 + 16 * i] for i in range(4)]
    used = [e for e in entries if e != bytes(16)]
    if len(used) != 1:
        raise DeviceError(f"Expected exactly one partition on the card, found {len(used)}.")
    lba = int.from_bytes(used[0][8:12], "little")
    if lba < 1 or lba * SECTOR != start:
        raise DeviceError(f"The MBR partition starts at byte {lba * SECTOR:,}, not at the expected {start:,}.")
    if sector0 is None:
        return
    if sector0[3:11] == b"EXFAT   ":
        field = int.from_bytes(sector0[64:72], "little")
    elif sector0[82:90] == b"FAT32   ":
        field = int.from_bytes(sector0[28:32], "little")
    else:
        raise DeviceError("The partition's boot sector is not FAT32 or exFAT; its position cannot be confirmed.")
    if field != lba:
        raise DeviceError(f"The boot sector says the partition starts at sector {field:,}, the MBR says {lba:,}.")


def validate_ranges(ranges: Sequence[tuple[int, int]], after: bytes) -> str | None:
    """Why a write request is outside the whitelist, or None."""
    if not 1 <= len(ranges) <= MAX_RANGES:
        return f"Between 1 and {MAX_RANGES} ranges are allowed."
    seen = set()
    for offset, length in ranges:
        if not (isinstance(offset, int) and isinstance(length, int)) or length != SECTOR or offset % SECTOR \
                or offset // SECTOR not in WRITABLE_SECTORS:
            return f"Range {offset}:{length} is not one of the boot sectors {WRITABLE_SECTORS}."
        if offset in seen:
            return f"Range {offset}:{length} is given twice."
        seen.add(offset)
    if len(after) != SECTOR * len(ranges):
        return "The after-bytes do not match the ranges."
    return None


class RawAccess:
    """Opens a whole-disk device through authopen for one step and does that step's I/O."""

    def __init__(self, *, command: Sequence[str] = (AUTHOPEN,),
                 check: Callable[[int, str], None] = check_char_device, timeout: float = 1800):
        self.command, self.check, self.timeout = tuple(command), check, timeout

    def _open(self, device: str, flags: int, confirm: Callable[[], object] | None) -> int:
        """Open through authopen, bind the descriptor to ``device``, then call ``confirm``
        (the caller re-identifies the card: the password window may have been open a long time).
        Writes confirm before opening instead; see ``write``."""
        fd = open_device(device, flags, command=self.command, timeout=self.timeout)
        try:
            self.check(fd, device)
            import fcntl
            if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != flags & os.O_ACCMODE:
                raise DeviceError("The descriptor received does not have the requested access mode.")
            if confirm is not None:
                confirm()
        except BaseException:
            os.close(fd)
            raise
        return fd

    def read(self, device: str, start: int, length: int = AREA_BYTES, *,
             confirm: Callable[[], object] | None = None) -> tuple[bytes, bytes]:
        """The MBR and the first ``length`` bytes of the partition at byte ``start`` (read-only)."""
        if start <= 0 or start % SECTOR or not SECTOR <= length <= AREA_BYTES or length % SECTOR:
            raise DeviceError("The read request is outside the boot area.")
        fd = self._open(device, os.O_RDONLY, confirm)
        try:
            mbr = _read(fd, 0, SECTOR)
            check_start(mbr, None, start)
            area = _read(fd, start, length)
            check_start(mbr, area[:SECTOR], start)
            return mbr, area
        except OSError as exc:
            raise DeviceError(f"Reading the card failed: {exc}") from exc
        finally:
            os.close(fd)

    def write(self, device: str, start: int, ranges: Sequence[tuple[int, int]], after: bytes, *,
              expect_sha256: str, expect_mbr_sha256: str, expect_area_sha256: str,
              confirm: Callable[[], object] | None = None) -> dict:
        """Write the planned partition sectors; the result says exactly what happened.

        Refusals and failures are results, not exceptions; only a cancelled password
        window raises Cancelled."""
        problem = validate_ranges(ranges, after) if start > 0 and start % SECTOR == 0 else "Bad partition start."
        if problem:
            return {"ok": False, "code": "range_not_allowed", "error": problem, "written": [], "possibly_written": []}
        # Re-identify before opening: while the whole disk is open for writing, macOS reports
        # no partition table, so diskutil cannot confirm the card then. After the open, the
        # MBR and boot-area hashes in _write_steps bind the descriptor to the planned card.
        try:
            if confirm is not None:
                confirm()
            fd = self._open(device, os.O_RDWR, None)
        except Cancelled:
            raise
        except (DeviceError, OSError) as exc:
            return {"ok": False, "code": "open_failed", "error": str(exc), "written": [], "possibly_written": []}
        written: list[int] = []
        started: list[int] = []
        try:
            return _write_steps(fd, start, list(ranges), after, expect_sha256, expect_mbr_sha256,
                                expect_area_sha256, written, started)
        except OSError as exc:
            return {"ok": False, "code": "os_error", "error": str(exc), "written": written,
                    "possibly_written": list(started)}
        finally:
            os.close(fd)


def _write_steps(fd, start, ranges, after, expect_sha, expect_mbr_sha, expect_area_sha, written, started) -> dict:
    pieces = [after[i * SECTOR:(i + 1) * SECTOR] for i in range(len(ranges))]
    mbr_before = _read(fd, 0, SECTOR)
    try:
        check_start(mbr_before, None, start)
        area_before = _read(fd, start, AREA_BYTES)
        check_start(mbr_before, area_before[:SECTOR], start)
    except DeviceError as exc:
        return {"ok": False, "code": "start_mismatch", "error": f"{exc} Nothing was written.", "written": [],
                "possibly_written": []}
    current = b"".join(area_before[o:o + n] for o, n in ranges)
    for value, expected, what in ((mbr_before, expect_mbr_sha, "MBR"), (area_before, expect_area_sha, "boot area"),
                                  (current, expect_sha, "target sectors")):
        if _sha(value) != expected:
            return {"ok": False, "code": "before_mismatch", "mismatch": what, "written": [], "possibly_written": [],
                    "error": f"The card's {what} differs from the read the plan was made from; nothing was written."}
    if current != after:
        for (offset, _), data in zip(ranges, pieces):
            started.append(offset)
            try:
                _write(fd, start + offset, data)
                _flush(fd)
            except OSError as exc:
                return {"ok": False, "code": "write_failed", "written": written, "possibly_written": list(started),
                        "error": f"Writing partition sector {offset // SECTOR} failed ({len(written)} of "
                                 f"{len(ranges)} confirmed): {exc}"}
            written.append(offset)
    try:
        readback = b"".join(_read(fd, start + o, n) for o, n in ranges)
        area = _read(fd, start, AREA_BYTES)
        mbr_after = _read(fd, 0, SECTOR)
    except OSError as exc:
        return {"ok": False, "code": "readback_failed", "written": written, "possibly_written": list(started),
                "error": f"Reading back after the write failed: {exc}"}
    ok = readback == after and mbr_after == mbr_before
    return {"ok": ok, "code": None if ok else "readback_mismatch", "written": written,
            "possibly_written": list(started), "before_sha256": _sha(current), "readback_sha256": _sha(readback),
            "mbr_unchanged": mbr_after == mbr_before, "mbr_hex": mbr_after.hex(), "area_hex": area.hex()}
