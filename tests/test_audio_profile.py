"""Public synthetic tests for 48 kHz mono 16-bit PCM WAVE admission and alignment."""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import audio_profile
from audio_profile import (
    DESCRIPTOR_BYTES,
    MAX_WAV_BYTES,
    MAX_WAV_CHUNKS,
    STOCK_PCM_OUTSEL,
    admit_pcm,
    descriptor,
)


def build_wav_bytes(
    frames: bytes,
    *,
    channels: int = 1,
    rate: int = 48000,
    bits: int = 16,
    fmt_tag: int = 1,
    extra_chunks: list[tuple[bytes, bytes]] = (),
) -> bytes:
    block_align = channels * (bits // 8)
    fmt_body = struct.pack("<HHIIHH", fmt_tag, channels, rate, rate * block_align, block_align, bits)
    chunks = bytearray()
    chunks += b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
    for cid, payload in extra_chunks:
        chunks += cid + struct.pack("<I", len(payload)) + payload
        if len(payload) & 1:
            chunks += b"\x00"
    chunks += b"data" + struct.pack("<I", len(frames)) + frames
    if len(frames) & 1:
        chunks += b"\x00"
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + bytes(chunks)


class AudioProfileTests(unittest.TestCase):
    def test_no_private_default_wav_exists(self):
        self.assertFalse(hasattr(audio_profile, "DEFAULT_WAV"))

    def test_admit_synthetic_aligned_and_padded_wavs_with_cwd_portability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 16 samples * 2 bytes = 32 bytes (already 32-byte aligned)
            aligned_frames = struct.pack("<16h", *range(-8, 8))
            aligned_wav = root / "aligned.wav"
            aligned_bytes = build_wav_bytes(aligned_frames, extra_chunks=[(b"LIST", b"INFO")])
            aligned_wav.write_bytes(aligned_bytes)

            pcm_a, facts_a = admit_pcm(aligned_wav)
            self.assertEqual(pcm_a, aligned_frames)
            self.assertEqual(facts_a["padding_bytes"], 0)
            self.assertEqual(facts_a["source_pcm_bytes"], 32)
            self.assertEqual(facts_a["embedded_pcm_bytes"], 32)
            self.assertEqual(facts_a["source_pcm_sha256"], facts_a["embedded_pcm_sha256"])
            self.assertEqual(facts_a["out_sel"], STOCK_PCM_OUTSEL)
            self.assertEqual(facts_a["format"]["sample_rate"], 48000)
            self.assertEqual(facts_a["format"]["channels"], 1)
            self.assertEqual(facts_a["format"]["bits_per_sample"], 16)
            self.assertEqual(facts_a["wav_sha256"], hashlib.sha256(aligned_bytes).hexdigest())

            # 17 samples * 2 bytes = 34 bytes -> requires 30 zero padding bytes for 32-byte alignment
            unaligned_frames = struct.pack("<17h", *range(17))
            unaligned_wav = root / "nested" / "unaligned.wav"
            unaligned_wav.parent.mkdir()
            unaligned_bytes = build_wav_bytes(unaligned_frames)
            unaligned_wav.write_bytes(unaligned_bytes)

            prev_cwd = Path.cwd()
            try:
                os.chdir(unaligned_wav.parent)
                pcm_b, facts_b = admit_pcm(Path("unaligned.wav"))
            finally:
                os.chdir(prev_cwd)

            self.assertEqual(facts_b["caller_input_path"], "unaligned.wav")
            self.assertEqual(Path(facts_b["resolved_path"]), unaligned_wav.resolve())
            self.assertEqual(facts_b["source_pcm_bytes"], 34)
            self.assertEqual(facts_b["padding_bytes"], 30)
            self.assertEqual(facts_b["embedded_pcm_bytes"], 64)
            self.assertEqual(len(pcm_b), 64)
            self.assertEqual(pcm_b[:34], unaligned_frames)
            self.assertEqual(pcm_b[34:], bytes(30))
            self.assertNotEqual(facts_b["source_pcm_sha256"], facts_b["embedded_pcm_sha256"])
            # Input file is never mutated.
            self.assertEqual(unaligned_wav.read_bytes(), unaligned_bytes)

    def test_rejects_invalid_formats_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.wav"
            with self.assertRaisesRegex(ValueError, "PCM admission"):
                admit_pcm(missing)

            bad_cases = [
                ("rate_44100.wav", build_wav_bytes(b"\x01\x02" * 16, rate=44100)),
                ("stereo.wav", build_wav_bytes(b"\x01\x02" * 32, channels=2)),
                ("bits24.wav", build_wav_bytes(b"\x01\x02\x03" * 16, bits=24)),
                ("float_fmt.wav", build_wav_bytes(b"\x01\x02" * 16, fmt_tag=3)),
                ("empty.wav", build_wav_bytes(b"")),
                ("odd_pcm.wav", build_wav_bytes(b"\x01\x02\x03")),
                ("too_large.wav", build_wav_bytes(b"\x00" * (MAX_WAV_BYTES + 2))),
                (
                    "too_many_chunks.wav",
                    build_wav_bytes(
                        b"\x01\x02" * 16,
                        extra_chunks=[(b"JUNK", b"abcd") for _ in range(MAX_WAV_CHUNKS)],
                    ),
                ),
            ]
            for name, payload in bad_cases:
                with self.subTest(name=name):
                    p = root / name
                    p.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        admit_pcm(p)

    def test_descriptor_layout_and_alignment(self):
        desc = descriptor(0x01100020, 640)
        self.assertEqual(len(desc), DESCRIPTOR_BYTES)
        words = struct.unpack("<16I", desc)
        self.assertEqual(words[0], 1)
        self.assertEqual(words[1], 0x01100020)
        self.assertEqual(words[2], 640)
        self.assertEqual(words[5], 5)
        self.assertEqual(words[7], STOCK_PCM_OUTSEL)
        self.assertEqual(words[9:12], (48, 16, 1))

        with self.assertRaises(ValueError):
            descriptor(0x01100010, 640)
        with self.assertRaises(ValueError):
            descriptor(0x01100020, 642)


if __name__ == "__main__":
    unittest.main()
