"""Regular-file admission, bounded reads and streaming hashes for selected inputs."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat


class InputFileError(ValueError):
    pass


@contextmanager
def regular_stream(path):
    source = Path(path).resolve(strict=True)
    if not stat.S_ISREG(source.stat().st_mode):
        raise InputFileError("Input must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(source, flags)
    try:
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise InputFileError("Input must be a regular file")
        yield stream, before


def check_unchanged(stream, before):
    after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InputFileError("Input file changed while reading")


def _check_size(before, exact_bytes, max_bytes):
    if exact_bytes is not None and before.st_size != exact_bytes:
        raise InputFileError(f"Input size must be exactly {exact_bytes} bytes; got {before.st_size}")
    if max_bytes is not None and before.st_size > max_bytes:
        raise InputFileError(f"Input size exceeds the {max_bytes}-byte allowance")


def read_regular_file(path, *, exact_bytes=None, max_bytes=None):
    if exact_bytes is None and max_bytes is None:
        raise InputFileError("A read size bound is required")
    with regular_stream(path) as (stream, before):
        _check_size(before, exact_bytes, max_bytes)
        data = stream.read(before.st_size + 1)
        if len(data) != before.st_size:
            raise InputFileError("Input file size changed while reading")
        check_unchanged(stream, before)
        return data


def hash_regular_file(path, *, exact_bytes=None):
    with regular_stream(path) as (stream, before):
        _check_size(before, exact_bytes, None)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            block = stream.read(min(65536, remaining))
            if not block:
                raise InputFileError("Input file size changed while hashing")
            digest.update(block)
            remaining -= len(block)
        if stream.read(1):
            raise InputFileError("Input file size changed while hashing")
        check_unchanged(stream, before)
        return digest.hexdigest()
