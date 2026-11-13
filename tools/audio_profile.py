"""One-shot PCM admission and descriptor encoding; RIFF semantics remain owned by wav_memo."""
from pathlib import Path
import hashlib
import struct
import sys

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from wav_memo import WavError, inspect_wav
from input_files import InputFileError, read_regular_file

MAX_WAV_BYTES = 1048576
MAX_WAV_CHUNKS = 16
EXPECTED_PCM_FORMAT = {
    "format_tag": 1,
    "channels": 1,
    "sample_rate": 48000,
    "byte_rate": 96000,
    "block_align": 2,
    "bits_per_sample": 16,
}
# Stock final PCM producer (0xe08bc782/0xe08bc898) stores OutSel=0 at +0x1c.
STOCK_PCM_OUTSEL = 0
DESCRIPTOR_WORDS = 16
DESCRIPTOR_BYTES = DESCRIPTOR_WORDS * 4
DESCRIPTOR_KIND_WORD = 0
DESCRIPTOR_PTR_WORD = 1
DESCRIPTOR_BYTES_WORD = 2
DESCRIPTOR_PTR_OFFSET = DESCRIPTOR_PTR_WORD * 4
DESCRIPTOR_BYTES_OFFSET = DESCRIPTOR_BYTES_WORD * 4
MAX_READINESS_LIMIT = 500


def admit_pcm(path: Path) -> tuple[bytes, dict]:
    """Validate and admit a 48 kHz mono 16-bit LE PCM WAV, zero-padding tail to 32-byte DMA alignment."""
    path = Path(path)
    try:
        f = inspect_wav(path, MAX_WAV_BYTES, MAX_WAV_CHUNKS)
    except (WavError, OSError, InputFileError) as e:
        raise ValueError("PCM admission: " + str(e)) from e

    a = f["audio"]
    if (
        a["format_status"] != "pcm_consistent"
        or a["format"] != EXPECTED_PCM_FORMAT
        or a["data_chunk_count"] != 1
    ):
        raise ValueError("unsupported PCM geometry (requires 48 kHz mono 16-bit PCM WAVE)")

    c = next(x for x in f["chunks"] if x["id_text"] == "data")
    if (
        c["size"] <= 0
        or a["sample_frames"] is None
        or a["sample_frames"] <= 0
        or c["size"] % EXPECTED_PCM_FORMAT["block_align"]
    ):
        raise ValueError("empty or partial-frame PCM data")

    resolved = Path(f["source"]["resolved_path"])
    wav_sha = f["source"]["sha256"]
    if path.resolve(strict=True) != resolved:
        raise ValueError("WAV path resolution changed after parsing")
    data = read_regular_file(resolved, exact_bytes=f["source"]["bytes"])
    if len(data) != f["source"]["bytes"] or hashlib.sha256(data).hexdigest() != wav_sha:
        raise ValueError("WAV changed after parsing")

    data_offset = c["data_offset"]
    raw_size = c["size"]
    if data_offset < 0 or data_offset + raw_size > len(data):
        raise ValueError("PCM data bounds exceed WAV source")
    raw_pcm = data[data_offset : data_offset + raw_size]
    raw_pcm_sha = hashlib.sha256(raw_pcm).hexdigest()
    if len(raw_pcm) != raw_size or raw_pcm_sha != c["sha256"]:
        raise ValueError("PCM identity mismatch")

    padding_bytes = (-raw_size) % 32
    if padding_bytes % EXPECTED_PCM_FORMAT["block_align"]:
        raise ValueError("PCM padding is not frame-aligned")
    embedded_pcm = raw_pcm + (b"\x00" * padding_bytes)
    if not 0 < len(embedded_pcm) <= 0xFFFFFFFF or len(embedded_pcm) % 32:
        raise ValueError("embedded PCM exceeds descriptor bounds")
    embedded_pcm_sha = hashlib.sha256(embedded_pcm).hexdigest()
    decoded_frames = a["sample_frames"]
    dma_frames = len(embedded_pcm) // EXPECTED_PCM_FORMAT["block_align"]

    return embedded_pcm, {
        "wav_sha256": wav_sha,
        "wav_bytes": f["source"]["bytes"],
        "source_pcm_sha256": raw_pcm_sha,
        "source_pcm_bytes": raw_size,
        "decoded_frames": decoded_frames,
        "padding_bytes": padding_bytes,
        "embedded_pcm_sha256": embedded_pcm_sha,
        "embedded_pcm_bytes": len(embedded_pcm),
        "dma_frames": dma_frames,
        "pcm_sha256": embedded_pcm_sha,
        "pcm_bytes": len(embedded_pcm),
        "frames": decoded_frames,
        "data_offset": data_offset,
        "format": dict(EXPECTED_PCM_FORMAT),
        "out_sel": STOCK_PCM_OUTSEL,
        "caller_input_path": str(path),
        "resolved_path": str(resolved),
    }


def descriptor(pointer: int, byte_count: int) -> bytes:
    """Encode the canonical 64-byte Canon ABeep PCM descriptor."""
    if (
        type(pointer) is not int
        or type(byte_count) is not int
        or pointer % 32
        or not 0 < pointer <= 0xFFFFFFFF
        or byte_count % 32
        or not 0 < byte_count <= 0xFFFFFFFF
    ):
        raise ValueError("PCM descriptor alignment/length")
    return struct.pack(
        "<16I", 1, pointer, byte_count, 0, 0, 5, 0, STOCK_PCM_OUTSEL, 0, 48, 16, 1, 0, 0, 0, 0
    )
