"""Feature manifest (config/features.json): loading, rules, CLI/API equivalence and the read-only ROM check."""
import argparse
import contextlib
import copy
import io
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src" / "experimental" / "xpan" / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import build as build_mod
import features
import record_indicator as ri
from test_contracts import make_synthetic_contract_and_rom
from test_xpan_contracts import make_synthetic_xpan_contract_and_rom

RAW = json.loads(features.FEATURES_PATH.read_text())


def legacy_parser():
    """Frozen copy of the pre-manifest build.py argument parser (commit 81df0e0)."""
    ap = argparse.ArgumentParser(
        description=(
            "Offline builder for Canon EOS R5 (firmware 2.2.1) shutter-feedback sound mod (default) "
            "or silent experimental XPan crop-only preview (--profile xpan-crop)."
        )
    )
    ap.add_argument(
        "--profile",
        choices=("shutter-feedback", "xpan-crop"),
        default="shutter-feedback",
        help="Build profile: shutter-feedback (default, requires two WAVs) or xpan-crop (no WAVs; silent unless --record-indicator)",
    )
    ap.add_argument("--rom", type=Path, required=True, help="Path to user's 64 MiB Canon EOS R5 2.2.1 ROM dump")
    ap.add_argument("--startup-wav", type=Path, required=False, help="Path to 48 kHz mono 16-bit PCM WAVE startup clip")
    ap.add_argument("--shutter-wav", type=Path, required=False, help="Path to 48 kHz mono 16-bit PCM WAVE shutter clip")
    ap.add_argument("--toolchain", type=Path, required=True, help="Directory containing arm-none-eabi GCC 15.2.1 binaries")
    ap.add_argument("--output", type=Path, required=True, help="New output directory for autoexec.bin and build artifacts")
    ap.add_argument(
        "--record-indicator",
        choices=("off", "ding", "chirp2", "2k150loud"),
        default=None,
        help=(
            "xpan-crop only: opt-in movie record indicator cue played on the Movie REC button press "
            "(default off; 'ding' is the cue tested on a private lab build)"
        ),
    )
    return ap


def legacy_cli(argv):
    """Frozen copy of the pre-manifest build.py main() argument handling (commit 81df0e0).

    Returns ("exit", code, stderr) or ("build", kwargs)."""
    ap = legacy_parser()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            args = ap.parse_args(argv)
            if args.profile == "xpan-crop":
                if args.startup_wav is not None or args.shutter_wav is not None:
                    ap.error("--profile xpan-crop is silent and forbids --startup-wav / --shutter-wav")
            else:
                if args.record_indicator is not None:
                    ap.error("--record-indicator requires --profile xpan-crop")
                missing = [
                    flag
                    for flag, val in (("--startup-wav", args.startup_wav), ("--shutter-wav", args.shutter_wav))
                    if val is None
                ]
                if missing:
                    ap.error(f"the following arguments are required for --profile shutter-feedback: {', '.join(missing)}")
    except SystemExit as exc:
        return ("exit", exc.code, stderr.getvalue())
    return ("build", dict(
        rom_path=args.rom, startup_wav=args.startup_wav, shutter_wav=args.shutter_wav,
        toolchain_dir=args.toolchain, output_dir=args.output, profile=args.profile,
        record_indicator=args.record_indicator,
    ))


def manifest_cli(argv):
    """The current build.main() with build_image replaced by a recorder."""
    calls = []

    def fake_build_image(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop after validation")

    stderr = io.StringIO()
    with mock.patch.object(build_mod, "build_image", fake_build_image):
        try:
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
                build_mod.main(argv)
        except SystemExit as exc:
            return ("exit", exc.code, stderr.getvalue())
        except RuntimeError:
            pass
    return ("build", calls[0])


class PassedValidation(Exception):
    pass


def legacy_api(profile, startup_wav, shutter_wav, record_indicator):
    """Frozen copy of the pre-manifest build_image() selection checks (commit 81df0e0)."""
    if profile != "xpan-crop" and record_indicator not in (None, "off"):
        raise ValueError("--record-indicator requires --profile xpan-crop")
    if profile == "xpan-crop":
        if startup_wav is not None or shutter_wav is not None:
            raise ValueError("--profile xpan-crop forbids --startup-wav and --shutter-wav")
        return ("xpan", record_indicator)
    if profile != "shutter-feedback":
        raise ValueError(f"Unsupported build profile: {profile!r}")
    if startup_wav is None or shutter_wav is None:
        raise ValueError("shutter-feedback profile requires --startup-wav and --shutter-wav")
    return ("shutter-feedback", None)


def manifest_api(profile, startup_wav, shutter_wav, record_indicator):
    import xpan_build

    def fake_xpan(**kwargs):
        return ("xpan", kwargs["record_indicator"])

    def stop():
        raise PassedValidation

    with mock.patch.object(xpan_build, "build_xpan_image", fake_xpan), \
            mock.patch.object(build_mod, "controlled_environment", stop):
        try:
            return build_mod.build_image(
                rom_path=Path("r.bin"), startup_wav=startup_wav, shutter_wav=shutter_wav,
                toolchain_dir=Path("t"), output_dir=Path("o"), profile=profile, record_indicator=record_indicator,
            )
        except PassedValidation:
            return ("shutter-feedback", None)


def outcome(fn, *args):
    try:
        return ("ok", fn(*args))
    except ValueError as exc:
        return ("error", str(exc))


class ManifestLoadingTests(unittest.TestCase):
    def test_committed_manifest_loads_and_matches_its_owners(self):
        m = features.load()
        self.assertEqual(m.profile_ids, ("shutter-feedback", "xpan-crop"))
        self.assertEqual(m.default_profile, "shutter-feedback")
        indicator = m.options["record_indicator"]
        self.assertEqual(indicator.values, ri.CHOICES)
        self.assertEqual(indicator.default, ri.OFF)
        self.assertEqual(indicator.profiles, ("xpan-crop",))
        self.assertEqual(indicator.choice("ding").status, "private-lab-verified")
        for value in ("chirp2", "2k150loud"):
            self.assertEqual(indicator.choice(value).status, "experimental")
        self.assertIsNone(indicator.choice("off").status)
        # Evidence wording follows docs/usage.md's evidence boundary.
        self.assertIn("private A/B trial", indicator.choice("chirp2").status_detail)
        self.assertIn("start request", indicator.choice("2k150loud").status_detail)
        for value in ("chirp2", "2k150loud"):
            self.assertIn("public single-cue configuration", indicator.choice(value).status_detail)
        self.assertIn("extra diagnostics", m.status_levels["private-lab-verified"].text)
        self.assertIn("has not been tested", m.profile_exclusivity)
        self.assertEqual(m.profiles["xpan-crop"].inputs_with("forbidden"), ("startup_wav", "shutter_wav"))
        self.assertEqual(m.profiles["shutter-feedback"].inputs_with("required"), ("startup_wav", "shutter_wav"))
        for profile in m.profiles.values():
            self.assertIn(profile.status, m.status_levels)
        build_mod.check_feature_catalog(m)

    def test_malformed_manifests_are_rejected(self):
        def broken(edit):
            raw = copy.deepcopy(RAW)
            edit(raw)
            return raw

        cases = {
            "schema": lambda r: r.update(schema="v0"),
            "unknown status": lambda r: r["profiles"][0].update(status="works"),
            "choice status": lambda r: r["options"][0]["choices"][1].update(status="works"),
            "option profile": lambda r: r["options"][0].update(profiles=["xpan"]),
            "default": lambda r: r["options"][0].update(default="loud"),
            "duplicate choice": lambda r: r["options"][0]["choices"].append(dict(r["options"][0]["choices"][1])),
            "missing policy": lambda r: r["profiles"][1]["inputs"].pop("shutter_wav"),
            "bad policy": lambda r: r["profiles"][1]["inputs"].update(shutter_wav="optional"),
            "duplicate profile": lambda r: r["profiles"].append(dict(r["profiles"][0])),
            "default profile": lambda r: r.update(default_profile="xpan"),
            "flag clash": lambda r: r["options"][0].update(flag="--startup-wav"),
            "empty label": lambda r: r["profiles"][0].update(label=""),
        }
        for name, edit in cases.items():
            with self.subTest(name), self.assertRaises(features.FeatureManifestError):
                features.FeatureManifest(broken(edit))
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "features.json"
            bad.write_text("{not json")
            with self.assertRaises(features.FeatureManifestError):
                features.load(bad)

    def test_builder_refuses_a_manifest_that_drifts_from_the_cue_owner(self):
        raw = copy.deepcopy(RAW)
        raw["options"][0]["choices"].append({"value": "beep", "label": "Beep", "status": "experimental"})
        with self.assertRaisesRegex(ValueError, "record_indicator.CHOICES"):
            build_mod.check_feature_catalog(features.FeatureManifest(raw))
        raw = copy.deepcopy(RAW)
        raw["profiles"].append({**raw["profiles"][1], "id": "new-profile"})
        with self.assertRaisesRegex(ValueError, "profiles"):
            build_mod.check_feature_catalog(features.FeatureManifest(raw))


class ConflictRuleTests(unittest.TestCase):
    m = features.load()

    def kinds(self, profile, startup=False, shutter=False, indicator=None):
        return [
            (v.kind, v.subjects)
            for v in self.m.selection_violations(
                profile, inputs={"startup_wav": startup, "shutter_wav": shutter}, options={"record_indicator": indicator}
            )
        ]

    def test_record_indicator_requires_xpan_crop(self):
        self.assertEqual(self.kinds("xpan-crop", indicator="ding"), [])
        self.assertEqual(self.kinds("shutter-feedback", True, True, "ding"),
                         [(features.OPTION_REQUIRES_PROFILE, ("record_indicator",))])
        self.assertEqual(self.m.option_availability("xpan-crop", "record_indicator"), (True, None))
        enabled, reason = self.m.option_availability("shutter-feedback", "record_indicator")
        self.assertFalse(enabled)
        self.assertIn("xpan-crop", reason)

    def test_wavs_required_for_shutter_feedback_and_forbidden_for_xpan(self):
        self.assertEqual(self.kinds("shutter-feedback", True, True), [])
        self.assertEqual(self.kinds("shutter-feedback", True, False), [(features.MISSING_INPUT, ("shutter_wav",))])
        self.assertEqual(self.kinds("xpan-crop", False, True), [(features.FORBIDDEN_INPUT, ("shutter_wav",))])
        self.assertEqual(self.kinds("xpan-crop"), [])

    def test_violation_order_and_unknown_profile(self):
        self.assertEqual(
            self.kinds("shutter-feedback", indicator="off"),
            [(features.OPTION_REQUIRES_PROFILE, ("record_indicator",)), (features.MISSING_INPUT, ("startup_wav", "shutter_wav"))],
        )
        self.assertEqual(self.kinds("bogus", True, indicator="ding"),
                         [(features.OPTION_REQUIRES_PROFILE, ("record_indicator",)), (features.UNKNOWN_PROFILE, ())])
        with self.assertRaises(features.FeatureManifestError):
            self.m.selection_violations("xpan-crop", inputs={"intro_wav": True}, options={})
        for v in self.m.selection_violations("bogus", inputs={}, options={"record_indicator": "ding"}):
            self.assertTrue(self.m.describe_violation(v))


class ValidationEquivalenceTests(unittest.TestCase):
    """The manifest-driven builder accepts and refuses exactly what the pre-manifest builder did."""

    def test_cli_matrix_matches_the_legacy_cli(self):
        profiles = [None, "shutter-feedback", "xpan-crop", "bogus"]
        wavs = [None, "a.wav"]
        indicators = [None, "off", "ding", "chirp2", "2k150loud", "loud"]
        extras = [[], ["--force"], ["--skip-target-check"]]
        drops = [None, "--rom", "--toolchain", "--output"]
        seen = {"exit": 0, "build": 0}
        for profile, startup, shutter, indicator, extra, drop in itertools.product(
            profiles, wavs, wavs, indicators, extras, drops
        ):
            argv = []
            for flag, value in (("--rom", "r.bin"), ("--toolchain", "tc"), ("--output", "out")):
                if flag != drop:
                    argv += [flag, value]
            for flag, value in (("--profile", profile), ("--startup-wav", startup), ("--shutter-wav", shutter),
                                ("--record-indicator", indicator)):
                if value is not None:
                    argv += [flag, value]
            argv += extra
            with self.subTest(argv=argv):
                expected = legacy_cli(argv)
                self.assertEqual(manifest_cli(argv), expected)
                seen[expected[0]] += 1
        # Accepted: shutter-feedback with both WAVs (implicit or explicit profile), and
        # xpan-crop without WAVs for each of the five indicator spellings; all else refused.
        self.assertEqual(seen["build"], 7)
        self.assertEqual(seen["exit"], 4 * 2 * 2 * 6 * 3 * 4 - 7)

    def test_help_text_is_unchanged(self):
        self.assertEqual(build_mod.build_parser().format_help(), legacy_parser().format_help())

    def test_api_matrix_matches_the_legacy_build_image(self):
        for profile, startup, shutter, indicator in itertools.product(
            ["shutter-feedback", "xpan-crop", "bogus", None],
            [None, Path("a.wav")],
            [None, Path("b.wav")],
            [None, "off", "ding", "loud"],
        ):
            with self.subTest(profile=profile, startup=startup, shutter=shutter, indicator=indicator):
                self.assertEqual(
                    outcome(manifest_api, profile, startup, shutter, indicator),
                    outcome(legacy_api, profile, startup, shutter, indicator),
                )


class CheckRomTests(unittest.TestCase):
    def test_shutter_feedback_check_admits_without_writing_and_refuses_like_the_build(self):
        manifest, contract, rom = make_synthetic_contract_and_rom()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom_path = root / "gang100.bin"
            rom_path.write_bytes(bytes(rom))
            before = sorted(root.iterdir())
            summary = build_mod.check_rom(rom_path, manifest=manifest, target_contract=contract)
            self.assertTrue(summary["rom_admitted"])
            self.assertEqual(summary["bytes"], manifest["dump_bytes"])
            self.assertEqual(summary["profile"], "shutter-feedback")
            self.assertIsNone(summary["record_indicator"])
            self.assertEqual(sorted(root.iterdir()), before)

            short = root / "short.bin"
            short.write_bytes(bytes(rom[:-4]))
            with self.assertRaisesRegex(ValueError, "exactly"):
                build_mod.check_rom(short, manifest=manifest, target_contract=contract)
            bad = bytearray(rom)
            off = manifest["main_start"] - manifest["rom_base"]
            bad[off] ^= 0xFF
            rom_path.write_bytes(bytes(bad))
            with self.assertRaisesRegex(ValueError, "signature mismatch"):
                build_mod.check_rom(rom_path, manifest=manifest, target_contract=contract)
            with self.assertRaisesRegex(ValueError, "requires --profile xpan-crop"):
                build_mod.check_rom(rom_path, record_indicator="ding", manifest=manifest, target_contract=contract)
            with self.assertRaisesRegex(ValueError, "Unsupported build profile"):
                build_mod.check_rom(rom_path, profile="xpan", manifest=manifest, target_contract=contract)

    def test_xpan_check_runs_the_xpan_admission(self):
        manifest, contract, xpan_contract, hooks, rom = make_synthetic_xpan_contract_and_rom()
        with tempfile.TemporaryDirectory() as tmp:
            rom_path = Path(tmp) / "gang100.bin"
            rom_path.write_bytes(bytes(rom))
            kwargs = dict(profile="xpan-crop", manifest=manifest, target_contract=contract,
                          xpan_contract=xpan_contract, xpan_hooks=hooks)
            summary = build_mod.check_rom(rom_path, **kwargs)
            self.assertEqual(summary["record_indicator"], "off")
            self.assertGreater(summary["verified_xpan_regions"], 0)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["gang100.bin"])
            # The indicator needs its own hook and fingerprinted pages, as in a build.
            with self.assertRaisesRegex(ValueError, "record indicator"):
                build_mod.check_rom(rom_path, record_indicator="ding", **kwargs)
            with self.assertRaisesRegex(ValueError, "unknown --record-indicator"):
                build_mod.check_rom(rom_path, record_indicator="loud", **kwargs)
            hook = hooks[0]
            va = int(hook["va"], 16) if isinstance(hook["va"], str) else hook["va"]
            bad = bytearray(rom)
            bad[va - manifest["rom_base"]] ^= 0xFF
            rom_path.write_bytes(bytes(bad))
            with self.assertRaises(ValueError):
                build_mod.check_rom(rom_path, **kwargs)


if __name__ == "__main__":
    unittest.main()
