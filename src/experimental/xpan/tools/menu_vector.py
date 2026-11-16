"""Build-time XPan menu vector resource generator from an admitted ROM and clean-room font paths."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

TOOLS_DIR = Path(__file__).resolve().parent
FONT_PATH = TOOLS_DIR / "xpan_font.json"

RESOURCE_VA = 0xE38F614C
ROM_BASE = 0xE0000000
RESOURCE_OFFSET = RESOURCE_VA - ROM_BASE
RESOURCE_BYTES = 968
RESOURCE_SHA = "4b68993fdc4260574570299af42a5ea588c3787b8e6740e1a4ba80f4eff18cf9"
XPAN_LINEAR_BYTES = 680
XPAN_LINEAR_SHA = "4708131ff6e243482f7a34efeb49ddba268b86eb698d297c17780b2fd0c15869"

# Khronos OpenVG 1.1 path markers.
COMMANDS = {
    0: ("Z", 0), 2: ("M", 2), 4: ("L", 2), 6: ("H", 1),
    8: ("V", 1), 10: ("Q", 4), 12: ("C", 6), 14: ("T", 2), 16: ("S", 4),
}


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _chunk(data: bytes, offset: int, end: int) -> tuple[int, int]:
    if offset < 0 or offset + 4 > end:
        raise ValueError("truncated chunk header")
    tag, size = struct.unpack_from("<HH", data, offset)
    if size < 4 or size % 4 or offset + size > end:
        raise ValueError("chunk length outside owner")
    return tag, size


def decode(data: bytes) -> dict:
    if len(data) < 72 or _u16(data, 0) != 0x9999 or _u16(data, 2) != 12 or _u32(data, 4) != len(data) or _u32(data, 8) != 6:
        raise ValueError("unsupported outer vector header")
    if _chunk(data, 12, len(data)) != (0x1112, 60) or _u32(data, 16) != len(data):
        raise ValueError("unsupported primary header")
    facts = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bounds_units": list(struct.unpack_from("<4h", data, 36)),
        "paths": [],
        "chunks": [],
        "unknown_primary_fields_hex": data[24:36].hex(),
    }
    offset = 12
    frame = None
    while offset < len(data):
        tag, size = _chunk(data, offset, len(data))
        item = {"offset": offset, "tag": tag, "bytes": size}
        if tag == 0xCC10:
            if facts["paths"]:
                raise ValueError("multiple path groups not supported")
            n = _u16(data, offset + 4)
            table = offset + 6
            if table + 2 * n > offset + size:
                raise ValueError("path offset table exceeds group")
            for i in range(n):
                start = table + 2 * _u16(data, table + 2 * i)
                tag0, len0 = _chunk(data, start, offset + size)
                if tag0 != 0xAAA0 or len0 < 6:
                    raise ValueError("missing segment record")
                count = _u16(data, start + 4)
                if 6 + count > len0:
                    raise ValueError("segments exceed record")
                segments = list(data[start + 6 : start + 6 + count])
                next_offset = start + len0
                tag1, len1 = _chunk(data, next_offset, offset + size)
                if tag1 != 0xAAA1 or len1 < 6:
                    raise ValueError("missing coordinate record")
                coords = _u16(data, next_offset + 4)
                if 6 + 2 * coords > len1:
                    raise ValueError("coordinates exceed record")
                values = list(struct.unpack_from("<" + "h" * coords, data, next_offset + 6))
                expected = 0
                for marker in segments:
                    if marker & ~1 not in COMMANDS:
                        raise ValueError("unsupported OpenVG marker")
                    expected += COMMANDS[marker & ~1][1]
                if expected != coords:
                    raise ValueError("segment arity differs from coordinate count")
                segment_copy_end = start + 6 + ((count + 31) & ~15)
                coordinate_copy_end = next_offset + 6 + ((2 * coords + 31) & ~15)
                if max(segment_copy_end, coordinate_copy_end) > len(data):
                    raise ValueError("native padded source read exceeds complete payload")
                facts["paths"].append(
                    {
                        "offset": start,
                        "segments": segments,
                        "coordinates": values,
                        "native_segment_copy_end": segment_copy_end,
                        "native_coordinate_copy_end": coordinate_copy_end,
                    }
                )
        if tag == 0x2220:
            frame = offset
        facts["chunks"].append(item)
        offset += size
        if tag == 0xFFFF:
            if offset != len(data):
                raise ValueError("trailing bytes after final chunk")
            break
    if offset != len(data) or not facts["chunks"] or facts["chunks"][-1]["tag"] != 0xFFFF:
        raise ValueError("missing end marker")
    if frame != 12 + _u32(data, 20):
        raise ValueError("primary frame offset mismatch")
    if not facts["paths"]:
        raise ValueError("no path group")
    if _u16(data, 52) != len(facts["paths"]):
        raise ValueError("primary path count differs from path group")
    for item in facts["chunks"]:
        if item["tag"] == 0xAAAC:
            if item["bytes"] == 12 and _u16(data, item["offset"] + 4) == 0:
                if not 1 <= _u16(data, item["offset"] + 8) <= len(facts["paths"]):
                    raise ValueError("timeline path reference outside group")
            else:
                item["opaque_timeline_action_hex"] = data[item["offset"] : item["offset"] + item["bytes"]].hex()
    return facts


def control_bounds(paths: list[dict]) -> tuple[int, int, int, int]:
    points = []
    for path in paths:
        x = y = sx = sy = 0
        index = 0
        previous = None
        last_quad = None
        last_cubic = None
        for marker in path["segments"]:
            op = marker & ~1
            n = COMMANDS[op][1]
            args = path["coordinates"][index : index + n]
            index += n
            relative = marker & 1
            pair = lambda i: (args[i] + (x if relative else 0), args[i + 1] + (y if relative else 0))
            if op == 0:
                x, y = sx, sy
            elif op == 2:
                x, y = pair(0)
                sx, sy = x, y
                points.append((x, y))
            elif op == 4:
                x, y = pair(0)
                points.append((x, y))
            elif op == 6:
                x = args[0] + (x if relative else 0)
                points.append((x, y))
            elif op == 8:
                y = args[0] + (y if relative else 0)
                points.append((x, y))
            elif op == 10:
                ctrl = pair(0)
                end = pair(2)
                points.extend([ctrl, end])
                last_quad = ctrl
                x, y = end
            elif op == 12:
                c1 = pair(0)
                c2 = pair(2)
                end = pair(4)
                points.extend([c1, c2, end])
                last_cubic = c2
                x, y = end
            elif op == 14:
                ctrl = (2 * x - last_quad[0], 2 * y - last_quad[1]) if previous in (10, 14) else (x, y)
                end = pair(0)
                points.extend([ctrl, end])
                last_quad = ctrl
                x, y = end
            elif op == 16:
                c1 = (2 * x - last_cubic[0], 2 * y - last_cubic[1]) if previous in (12, 16) else (x, y)
                c2 = pair(0)
                end = pair(2)
                points.extend([c1, c2, end])
                last_cubic = c2
                x, y = end
            previous = op
    if not points:
        raise ValueError("path geometry has no points")
    return min(x for x, _ in points), max(x for x, _ in points), min(y for _, y in points), max(y for _, y in points)


def pack_chunk(tag: int, payload: bytes) -> bytes:
    size = (len(payload) + 7) & ~3
    if size > 65535:
        raise ValueError("chunk exceeds native u16 length")
    return struct.pack("<HH", tag, size) + payload + bytes(size - 4 - len(payload))


def pack_paths(paths: list[dict]) -> bytes:
    n = len(paths)
    cursor = (6 + 2 * n + 3) & ~3
    records = []
    offsets = []
    for p in paths:
        seg = p["segments"]
        coords = p["coordinates"]
        if len(seg) > 65535 or len(coords) > 65535:
            raise ValueError("path count exceeds native u16")
        record = pack_chunk(0xAAA0, struct.pack("<H", len(seg)) + bytes(seg)) + pack_chunk(
            0xAAA1, struct.pack("<H", len(coords)) + struct.pack("<" + "h" * len(coords), *coords)
        )
        offsets.append((cursor - 6) // 2)
        records.append(record)
        cursor += len(record)
    if cursor > 65535:
        raise ValueError("path group exceeds native u16")
    header = struct.pack("<HHH", 0xCC10, cursor, n) + struct.pack("<" + "H" * n, *offsets)
    return header + bytes((len(header) + 3) // 4 * 4 - len(header)) + b"".join(records)


def linearize_paths(paths: list[dict]) -> list[dict]:
    arity = {0: 0, 2: 2, 4: 2, 10: 4, 12: 6}
    result = []
    for path in paths:
        segments = []
        coordinates = []
        offset = 0
        for marker in path["segments"]:
            if marker not in arity:
                raise ValueError("linearization requires absolute CoreText M/L/Q/C/close commands")
            count = arity[marker]
            values = path["coordinates"][offset : offset + count]
            offset += count
            if len(values) != count:
                raise ValueError("truncated CoreText coordinates")
            segments.append(4 if marker in (10, 12) else marker)
            coordinates.extend(values[-2:] if marker in (10, 12) else values)
        if offset != len(path["coordinates"]):
            raise ValueError("trailing CoreText coordinates")
        result.append({"segments": segments, "coordinates": coordinates})
    return result


def make(original: bytes, font: dict, *, linearize: bool = False) -> tuple[bytes, dict, dict]:
    old = decode(original)
    glyphs = font["paths"]
    if len(glyphs) != 4 or len(old["paths"]) != 4:
        raise ValueError("expected four glyphs and four path slots")
    old_l, old_r, old_t, old_b = control_bounds(old["paths"])
    new_l, new_r, new_t, new_b = control_bounds(glyphs)
    sx = (old_r - old_l) / (new_r - new_l)
    sy = (old_b - old_t) / (new_b - new_t)
    mapped = []
    for glyph in glyphs:
        coordinates = [
            round(old_l + (x - new_l) * sx) if i % 2 == 0 else round(old_t + (x - new_t) * sy)
            for i, x in enumerate(glyph["coordinates"])
        ]
        mapped.append({"segments": glyph["segments"], "coordinates": coordinates})
    if linearize:
        mapped = linearize_paths(mapped)

    xs = mapped[0]["coordinates"][::2]
    ys = mapped[0]["coordinates"][1::2]
    l, r, t, b = min(xs), max(xs), min(ys), max(ys)
    stroke = max(1, round((r - l) * 0.23))
    arm = lambda points: {"segments": [2, 4, 4, 4, 0], "coordinates": [v for xy in points for v in xy]}
    x1 = arm([(l, t), (l + stroke, t), (r, b), (r - stroke, b)])
    x2 = arm([(r - stroke, t), (r, t), (l + stroke, b), (l, b)])
    merge = lambda a, b_p: {"segments": a["segments"] + b_p["segments"], "coordinates": a["coordinates"] + b_p["coordinates"]}
    paths = [x1, merge(mapped[1], mapped[3]), x2, mapped[2]]

    budget = []
    for prior, new in zip(old["paths"], paths):
        limits = (len(prior["segments"]), len(prior["coordinates"]))
        used = (len(new["segments"]), len(new["coordinates"]))
        if used[0] > limits[0] or used[1] > limits[1]:
            raise ValueError(f"new path grows beyond original slot counts: {used}>{limits}")
        budget.append({"old": limits, "new": used})

    group = next(c for c in old["chunks"] if c["tag"] == 0xCC10)
    start = group["offset"]
    end = start + group["bytes"]
    newgroup = pack_paths(paths)
    candidate = bytearray(original[:start] + newgroup + original[end:])
    delta = len(candidate) - len(original)
    struct.pack_into("<I", candidate, 4, len(candidate))
    struct.pack_into("<I", candidate, 16, len(candidate))
    struct.pack_into("<I", candidate, 20, struct.unpack_from("<I", original, 20)[0] + delta)
    facts = decode(bytes(candidate))
    if candidate[24:36] != original[24:36]:
        raise ValueError("menu vector primary header fields modified unexpectedly")
    if facts["bounds_units"] != old["bounds_units"]:
        raise ValueError("menu vector bounds mismatch")

    proof = {
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "source_bytes": len(original),
        "candidate_bytes": len(candidate),
        "font": font["font"],
        "path_budgets": budget,
        "native_hints_unchanged": True,
        "camera_eligible": False,
        "scope": "Host vector candidate generated from verified ROM resource and clean-room XPan glyph paths.",
    }
    if linearize:
        proof["variant"] = "linear"
    return bytes(candidate), facts, proof


def build_from_rom(
    rom_bytes: bytes,
    *,
    expected_resource_sha256: str = RESOURCE_SHA,
    resource_va: int = RESOURCE_VA,
    resource_bytes: int = RESOURCE_BYTES,
    linearize: bool = True,
) -> tuple[bytes, dict, dict]:
    """Extract verified stock menu resource from admitted ROM and build XPan vector candidate."""
    offset = resource_va - ROM_BASE
    original = rom_bytes[offset : offset + resource_bytes]
    actual_sha = hashlib.sha256(original).hexdigest()
    if len(original) != resource_bytes or actual_sha != expected_resource_sha256:
        raise ValueError("stock menu vector resource SHA-256 mismatch")
    font = json.loads(FONT_PATH.read_text())
    return make(original, font, linearize=linearize)
