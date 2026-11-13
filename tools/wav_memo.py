#!/usr/bin/env python3
"""Read-only RIFF/WAVE inventory for camera-created memo experiments.

Container/chunk reference:
https://learn.microsoft.com/en-us/windows/win32/xaudio2/resource-interchange-file-format--riff-
This does not decode arbitrary codecs or establish camera compatibility.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from input_files import InputFileError, regular_stream


class WavError(Exception):
    def __init__(self, code, message, chunks=None):
        super().__init__(message)
        self.code = code
        self.chunks = chunks or []


def audio_summary(chunks):
    formats = [c for c in chunks if c["id_text"] == "fmt "]
    data = [c for c in chunks if c["id_text"] == "data"]
    result = {"format": None, "format_status": "missing_format", "data_chunk_count": len(data),
              "data_bytes": sum(c["size"] for c in data), "sample_frames": None, "duration_seconds": None}
    if len(formats) > 1:
        result["format_status"] = "ambiguous_format"
        return result
    if not formats:
        return result
    raw = bytes.fromhex(formats[0]["prefix_hex"])
    if len(raw) < 16:
        result["format_status"] = "truncated_format"
        return result
    fields = struct.unpack_from("<HHIIHH", raw)
    fmt = dict(zip(("format_tag", "channels", "sample_rate", "byte_rate", "block_align", "bits_per_sample"), fields))
    result["format"] = fmt
    if fmt["format_tag"] != 1:
        result["format_status"] = "encoding_not_validated"
        return result
    channels, rate, bits, align = fmt["channels"], fmt["sample_rate"], fmt["bits_per_sample"], fmt["block_align"]
    valid = (channels > 0 and rate > 0 and bits > 0 and bits % 8 == 0
             and align == channels * (bits // 8) and fmt["byte_rate"] == rate * align)
    if not valid or any(c["size"] % align for c in data):
        result["format_status"] = "invalid_pcm_geometry"
        return result
    result["format_status"] = "pcm_consistent"
    if data:
        result["sample_frames"] = result["data_bytes"] // align
        result["duration_seconds"] = result["sample_frames"] / rate
    return result


def inspect_wav(path, max_file_bytes, max_chunks):
    chunks = []
    if max_file_bytes < 1 or max_chunks < 1:
        raise WavError("invalid_limits", "Resource allowances must be positive.")
    source = path.resolve(strict=True)
    digest = hashlib.sha256()
    with regular_stream(source) as (stream, before):
        if before.st_size > max_file_bytes:
            raise WavError("resource_limit", f"File has {before.st_size} bytes; allowance is {max_file_bytes}. Raise --max-file-bytes explicitly if appropriate.")

        def consume(count, prefix_size=0):
            payload_hash = hashlib.sha256()
            prefix = bytearray()
            left = count
            while left:
                block = stream.read(min(left, 65536))
                if not block:
                    raise WavError("source_truncated", "Source ended during inspection.", chunks)
                digest.update(block); payload_hash.update(block)
                if len(prefix) < prefix_size:
                    prefix.extend(block[:prefix_size - len(prefix)])
                left -= len(block)
            return bytes(prefix), payload_hash.hexdigest()

        head = stream.read(12); digest.update(head)
        if len(head) < 12:
            raise WavError("truncated_riff", "Need a complete RIFF/WAVE header.")
        if head[:4] != b"RIFF" or head[8:] != b"WAVE":
            raise WavError("unsupported_container", "Expected little-endian RIFF/WAVE; other containers remain unsupported.")
        declared = struct.unpack_from("<I", head, 4)[0]
        end = declared + 8
        if declared < 4 or end > before.st_size:
            raise WavError("truncated_riff", "Declared RIFF range is invalid or exceeds the file.")
        while stream.tell() < end:
            start = stream.tell()
            if len(chunks) >= max_chunks:
                raise WavError("resource_limit", "Chunk allowance reached; raise --max-chunks explicitly if appropriate.", chunks)
            if end - start < 8:
                raise WavError("truncated_chunk_header", "Bytes remain without a complete chunk header.", chunks)
            header = stream.read(8); digest.update(header)
            if len(header) != 8:
                raise WavError("source_truncated", "Source ended during a chunk header read.", chunks)
            name, size = struct.unpack("<4sI", header)
            pad = size & 1
            if size + pad > end - stream.tell():
                raise WavError("chunk_out_of_bounds", f"Chunk at {start} exceeds its RIFF container.", chunks)
            prefix, payload_hash = consume(size, 40 if name == b"fmt " else 0)
            if pad:
                consume(1)
            try:
                label = name.decode("ascii")
            except UnicodeDecodeError:
                label = None
            entry = {"id_text": label, "id_hex": name.hex(), "offset": start,
                     "data_offset": start + 8, "size": size, "padding_bytes": pad,
                     "sha256": payload_hash}
            if name == b"fmt ":
                entry["prefix_hex"] = prefix.hex()
                entry["prefix_bytes_retained"] = len(prefix)
            chunks.append(entry)
        trailing = before.st_size - end
        consume(trailing)
        after = os.fstat(stream.fileno())
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise WavError("source_changed", "Source identity changed during inspection.", chunks)
    return {"ok": True, "source": {"caller_input_path": str(path), "resolved_path": str(source),
                                    "bytes": before.st_size, "sha256": digest.hexdigest()},
            "container": {"type": "RIFF/WAVE", "declared_size_field": declared,
                          "container_bytes": end, "trailing_bytes": trailing},
            "chunks": chunks, "audio": audio_summary(chunks),
            "coverage": "Complete declared RIFF chunk inventory and file hash; codec payloads are not decoded.",
            "writes_applied": False, "camera_validation": "Not performed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-file-bytes", type=int, default=64 * 1024 * 1024,
                        help="Host resource allowance, not a camera-format limit (default 64 MiB)")
    parser.add_argument("--max-chunks", type=int, default=4096,
                        help="Metadata-entry allowance, not a RIFF-format limit (default 4096)")
    args = parser.parse_args()
    try:
        result = inspect_wav(args.path, args.max_file_bytes, args.max_chunks)
    except WavError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)},
                          "partial_chunks": error.chunks}, indent=2)); return 2
    except (OSError, InputFileError) as error:
        print(json.dumps({"ok": False, "error": {"code": "source_io_failure", "message": str(error)}}, indent=2)); return 2
    print(json.dumps(result, indent=2)); return 0


if __name__ == "__main__":
    sys.exit(main())
