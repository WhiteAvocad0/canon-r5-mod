"""Source-window owner for the isolated rear-LCD/EVF framing bindings; no target execution."""
from pathlib import Path
import hashlib
import json
import zlib

HOOKS = (
    ("default", 0xE07C3DAA, "70b51e4615460446fff7e8ff", "frame_native_default_entry", 0xE07C3DB7),
    ("snapshot", 0xE07C37C2, "2de9f0410126d34c40f60877", "frame_native_snapshot_entry", 0xE07C37CF),
    ("copy_default", 0xE07C37DE, "55f8200047f046ec0020", "frame_native_copy_default", 0xE07C37E9),
    ("copy_previous", 0xE07C3800, "55f8200047f034ec", "frame_native_copy_previous", 0xE07C3809),
    ("publish", 0xE07C3B4C, "e061401c63f738e8", "frame_native_snapshot_publish", 0xE07C3B55),
    ("refresh", 0xE07C42B0, "70b5354c643c606b", "frame_native_refresh_entry", 0xE07C42B9),
    ("copy_refresh", 0xE07C42CE, "056846f0ceeee0690321", "frame_native_copy_refresh", 0xE07C42D9),
    ("finish_refresh", 0xE07C42DE, "0560002060639c20bde87040", "frame_native_refresh_publish", 0xE07C42EB),
    ("reset", 0xE07C3EC6, "2de9f05f0021244e0846", "frame_native_reset_entry", 0xE07C3ED1),
    ("row", 0xE07F671E, "30b5134687b00c460546", "frame_native_row_entry", 0xE07F6729),
    ("offset", 0xE0392184, "13460a4601461846", "frame_native_offset_entry", 0xE039218D),
)
CONSTANTS = {
    "DTS_CONTROL": 0xAF0C, "DTS_DEFAULT": 0x1231B8, "DTS_TABLE": 0x1240C0,
    "DTS_BYTES": 0xF08, "DTS_SLOTS": 20, "DESCRIPTORS": 0x124340, "DESCRIPTOR_STRIDE": 0x288,
    "DESCRIPTOR_COUNT": 6, "PACKET_TABLE": 0x150248, "PACKET_COUNT": 8,
    "ORCA_SLOT": 0x105C4, "PROFILE_FLAGS": 0xBB20,
    "CAPTURE_LR_A": 0xE012A471, "CAPTURE_LR_B": 0xE012AB2D,
    "MODE_BASE": 0x5A4, "MODE_STRIDE": 0xA4, "MODE_COUNT": 14, "MODE_INDEX": 0xE9C, "RAW_OFFSET": 0x168C,
    "ROW_LR": 0xE0131DD5, "ROW_OUTER_LR": 0xE01327AD, "ROW_OUTER_RETURN": 0x94, "ROW_OUTPUT": 0x3C,
    "OFFSET_LR": 0xE037C715, "OFFSET_X": 0xB8, "OFFSET_Y": 0xBC,
    "OFFSET_PACKET": 0xC4, "OFFSET_PREFIX": 0xC8, "OFFSET_RET0": 0xE4, "OFFSET_RET1": 0x12C,
    "OFFSET_RET2": 0x17C, "OFFSET_RET3": 0x1DC, "OFFSET_TOKEN": 0x1E0, "OFFSET_DESCRIPTOR": 0x1F0,
    "OFFSET_LR0": 0xE037C923, "OFFSET_LR1": 0xE037CD29, "OFFSET_LR2": 0xE02B4909, "OFFSET_LR3": 0xE01316C3,
    "OFFSET_EVF_LR0": 0xE037C97F, "OFFSET_EVF_LR1": 0xE037CD3D,
    "OFFSET_LCD_PREFIX": 0x30, "OFFSET_EVF_PREFIX": 0x50, "OFFSET_LCD_ROW": 0x138, "OFFSET_EVF_ROW": 0x168,
    "SNAPSHOT_FRAME": 24, "REFRESH_FRAME": 16, "NATIVE_MEMCPY": 0xE080B070,
    "NATIVE_DIVIDE": 0xE0726BC4, "NATIVE_COUNTER_BEGIN": 0xE07C3D86,
    "NATIVE_OFFSET_TAIL": 0xE02B4B99,
}


def inventory() -> dict:
    return {
        "schema": 1,
        "camera_eligible": False,
        "hooks": [
            {
                "role": n,
                "va": va,
                "length": len(bytes.fromhex(b)),
                "original_bytes": b,
                "original_sha256": hashlib.sha256(bytes.fromhex(b)).hexdigest(),
                "original_crc32": zlib.crc32(bytes.fromhex(b)),
                "destination_symbol": sym,
                "resume_thumb": resume,
            }
            for n, va, b, sym, resume in HOOKS
        ],
    }


def header() -> str:
    lines = [
        "/* Generated only by frame_native_profile.py. */",
        "#ifndef R5_FRAME_NATIVE_PROFILE_H",
        "#define R5_FRAME_NATIVE_PROFILE_H",
    ]
    for key, value in CONSTANTS.items():
        lines.append(f"#define FN_{key} 0x{value:08X}")
    for name, va, _b, _, resume in HOOKS:
        lines += [f"#define FN_{name.upper()}_VA 0x{va:08X}", f"#define FN_{name.upper()}_RESUME 0x{resume:08X}"]
    lines += ["#endif", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    (here / "frame-native-hooks.json").write_text(json.dumps(inventory(), indent=2) + "\n")
    (here.parent / "frame_native_profile.h").write_text(header())
