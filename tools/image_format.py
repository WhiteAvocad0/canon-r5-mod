#!/usr/bin/env python3
"""ML's footer/XOR convention, adapted from GPL build_tools/xor_chk.c.

This checks our image format. It is not Canon authentication or evidence that
Canon requires this footer/checksum before loading a file.
"""
import argparse
import functools
import hashlib
import json
from pathlib import Path
import struct

FOOTER_MAGIC = 0xE12FFF13
PLACEHOLDER = 0xCCCCCCCC


class FormatError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def inspect_image(data, *, unfinished=False):
    if len(data) < 32 or len(data) % 32:
        raise FormatError("invalid_alignment", "Image size must be a nonzero multiple of 32 bytes.")
    marker, last = struct.unpack_from("<II", data, len(data)-8)
    if marker != FOOTER_MAGIC or (unfinished and last != PLACEHOLDER):
        raise FormatError("invalid_footer", "Missing ML footer marker or checksum placeholder.")
    checksum = functools.reduce(int.__xor__, (x[0] for x in struct.iter_unpack("<I", data)), 0)
    if not unfinished and checksum:
        raise FormatError("invalid_checksum", "Whole-image little-endian word XOR is nonzero.")
    return {"bytes": len(data), "footer_offset": len(data)-8, "footer_magic": hex(marker),
            "xor_u32_le": hex(checksum), "sha256": hashlib.sha256(data).hexdigest()}


def finalize_image(data):
    facts = inspect_image(data, unfinished=True)
    old = struct.unpack_from("<I", data, len(data)-4)[0]
    result = data[:-4] + struct.pack("<I", old ^ int(facts["xor_u32_le"], 16))
    inspect_image(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    final = sub.add_parser("finalize")
    final.add_argument("source", type=Path)
    final.add_argument("output", type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("source", type=Path)
    args = parser.parse_args()
    try:
        data = args.source.read_bytes()
        if args.operation == "finalize":
            if args.output.exists():
                raise FormatError("output_exists", "Output already exists; refusing overwrite.")
            data = finalize_image(data)
            with args.output.open("xb") as handle:
                handle.write(data)
        print(json.dumps({"ok": True, **inspect_image(data)}))
        return 0
    except (FormatError, OSError) as error:
        print(json.dumps({"ok": False, "error": getattr(error, "code", "io_error"), "message": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
