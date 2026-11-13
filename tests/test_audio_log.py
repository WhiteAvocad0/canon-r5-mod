"""Public tests for Canon EOS R5 fixed-slot startup audio log schema and parser."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audio_log import AudioLogError, FIELDS, FLAGS, OUTCOMES, SCHEMA, build_template, parse_audio_log, render_audio_log


def make_fields(outcome: int, *, flags: int = 0) -> dict[str, int]:
    base = {k: 0 for k in FIELDS}
    base["OUTCOME"] = outcome
    base["OBS_FLAGS"] = flags
    return base


class AudioLogTests(unittest.TestCase):
    def test_template_and_all_outcomes_roundtrip(self):
        rom_sha = "a" * 64
        src_sha = "b" * 64
        tmpl, offsets = build_template(rom_sha, src_sha)
        self.assertTrue(tmpl.startswith((SCHEMA["format"] + "\n").encode("ascii")))
        self.assertEqual(list(offsets.keys()), list(FIELDS))

        all_flags = 0
        for bit in FLAGS.values():
            all_flags |= bit

        for code, name in OUTCOMES.items():
            fields = make_fields(code)
            if code == SCHEMA["outcomes"]["settled_observed"]:
                fields.update(
                    OBS_FLAGS=all_flags,
                    READY_POLLS=1,
                    OBSERVE_POLLS=2,
                    CONTEXT_BEFORE=0x12345678,
                    CONTEXT_AFTER=0x12345678,
                    STATE_BEFORE=1,
                    STATE_AFTER=1,
                    PHASE_BEFORE=3,
                    PHASE_AFTER=3,
                    SUBMIT_RETURN=0,
                    TEST_OWNER_BEFORE=0,
                    ACTIVE_POLL=1,
                    ACTIVE_PCM_PTR=0x01100020,
                    ACTIVE_PCM_BYTES=4096,
                    PHASE_SEEN_MASK=10,
                )
            raw = render_audio_log(fields, rom_sha256=rom_sha, source_sha256=src_sha)
            self.assertEqual(len(raw), len(tmpl))
            parsed = parse_audio_log(raw, expected_rom_sha256=rom_sha, expected_source_sha256=src_sha)
            self.assertEqual(parsed["outcome_name"], name)
            self.assertEqual(parsed["outcome"], code)

    def test_rejects_corrupt_mismatched_or_inconsistent_logs(self):
        rom_sha = "c" * 64
        src_sha = "d" * 64
        all_flags = 0
        for bit in FLAGS.values():
            all_flags |= bit
        settled_fields = make_fields(SCHEMA["outcomes"]["settled_observed"], flags=all_flags)
        settled_fields.update(
            READY_POLLS=1,
            OBSERVE_POLLS=2,
            CONTEXT_BEFORE=0x12345678,
            CONTEXT_AFTER=0x12345678,
            STATE_BEFORE=1,
            STATE_AFTER=1,
            PHASE_BEFORE=3,
            PHASE_AFTER=3,
            SUBMIT_RETURN=0,
            TEST_OWNER_BEFORE=0,
            ACTIVE_POLL=1,
            ACTIVE_PCM_PTR=0x01100020,
            ACTIVE_PCM_BYTES=4096,
            PHASE_SEEN_MASK=10,
        )
        raw = render_audio_log(settled_fields, rom_sha256=rom_sha, source_sha256=src_sha)

        with self.assertRaises(AudioLogError):
            parse_audio_log(raw[:-1], expected_rom_sha256=rom_sha, expected_source_sha256=src_sha)
        with self.assertRaises(AudioLogError):
            parse_audio_log(b"BAD!" + raw[4:], expected_rom_sha256=rom_sha, expected_source_sha256=src_sha)
        with self.assertRaises(AudioLogError):
            parse_audio_log(raw, expected_rom_sha256="e" * 64, expected_source_sha256=src_sha)
        with self.assertRaises(AudioLogError):
            parse_audio_log(raw, expected_rom_sha256=rom_sha, expected_source_sha256="f" * 64)

        bad_out = render_audio_log(make_fields(99), rom_sha256=rom_sha, source_sha256=src_sha)
        with self.assertRaises(AudioLogError):
            parse_audio_log(bad_out, expected_rom_sha256=rom_sha, expected_source_sha256=src_sha)

        unsettled = render_audio_log(
            make_fields(SCHEMA["outcomes"]["settled_observed"], flags=0),
            rom_sha256=rom_sha,
            source_sha256=src_sha,
        )
        with self.assertRaises(AudioLogError):
            parse_audio_log(unsettled, expected_rom_sha256=rom_sha, expected_source_sha256=src_sha)


if __name__ == "__main__":
    unittest.main()
