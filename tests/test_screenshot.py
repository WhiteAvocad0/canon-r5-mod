"""Host tests for the opt-in xpan-crop one-press screenshot (--screenshot rate): the
build-side owner (src/experimental/xpan/tools/screenshot.py), its cue facts and
generated bindings, the linked-image check, the strict contracts, the feature
manifest, and mock-toolchain builds. The native C is tested in
test_screenshot_capture.py and test_screenshot_y22.py.

No ROM, camera, card or ARM toolchain. A real build is separately opt-in: set
R5_SCREENSHOT_ROM (your own 64 MiB R5 2.2.1 dump) and R5_SCREENSHOT_TOOLCHAIN (the
arm-none-eabi bin directory); R5_SCREENSHOT_OUTPUT optionally names an existing
folder to build in (default: a temporary one, removed afterwards). The ROM is only
read.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screenshot_host as H  # noqa: E402

import build as build_mod  # noqa: E402
import feature_contract  # noqa: E402
import features  # noqa: E402
import record_indicator as ri  # noqa: E402
import screenshot as sc  # noqa: E402
import tone_ref  # noqa: E402
from contracts import MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, validate_xpan_target_contract_consistency  # noqa: E402
from host_cc import find_host_cc  # noqa: E402

ROOT = H.ROOT
AUDIO = MANIFEST["audio"]
CLICK_PCM_SHA256 = "0e70f9e9e3563b8e83ae62564eb180d30a31cfbeaac2fd57fd547c00800e8777"
BIP_LOW_PCM_SHA256 = "7b515cfefa8cbe8653315ac63ade05c9d7351e5cc19f65e9912550928ea32043"
DESTINATIONS = {d for *_, d in sc.HOOKS}
SCREENSHOT_PAGES = {0xE0108000, 0xE0109000, 0xE016C000, 0xE0236000, 0xE0260000, 0xE0263000, 0xE04A7000, 0xE04A8000,
                    0xE0548000, 0xE0549000, 0xE07E1000, 0xE07E2000, 0xE07E3000, 0xE07E4000, 0xE07EA000, 0xE07F0000,
                    0xE08AB000, 0xE091B000, 0xE099D000, 0xE0A08000, 0xE0CCB000, 0xE0D5B000}


def strip_sections(src: str) -> str:
    """Generated firmware C without its ELF section placement (host objects may be Mach-O)."""
    return re.sub(r'__attribute__\(\(section\("[^"]+"\)\)\)', "", src).replace('section(".resident_data"), ', "")


def syntax_check(test: unittest.TestCase, name: str, text: str, *defines: str) -> None:
    with tempfile.TemporaryDirectory(prefix="r5-screenshot-") as tmp:
        f = Path(tmp) / name
        f.write_text(text)
        proc = subprocess.run([find_host_cc(), "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", *defines,
                               "-I", str(H.XPAN_SRC), "-I", str(H.R5_221_SRC), str(f)], capture_output=True, text=True)
        test.assertEqual(proc.returncode, 0, proc.stderr)


def screenshot_hooks() -> list[dict]:
    """The screenshot hook extension as build hook records."""
    return [{"va": hex(h["va"]), "length": h["length"], "destination": h["destination_symbol"], "original_hex": h["original_bytes"]}
            for h in sc.hook_inventory()["hooks"]]


class CueFacts(unittest.TestCase):
    def test_click_and_low_bip(self):
        f = sc.cue_facts()
        self.assertEqual([c["name"] for c in f["cues"]], list(sc.CUES))
        click, bip = f["cues"]
        self.assertEqual((click["pcm_sha256"], bip["pcm_sha256"]), (CLICK_PCM_SHA256, BIP_LOW_PCM_SHA256))
        self.assertEqual(f["capacity_samples"], 3504)
        self.assertTrue(f["segments_hex"].startswith("8001000002040500"))
        self.assertEqual((f["segment_total"], click["segment_count"], bip["segment_first"]), (4, 3, 3))
        self.assertEqual((click["offset_samples"], bip["offset_samples"]), (0, click["padded_samples"]), "one contiguous buffer")
        for c in f["cues"]:
            self.assertEqual((c["bytes"], c["bytes"] % 32, c["padded_samples"] % 16), (2 * c["padded_samples"], 0, 0))
            self.assertLessEqual(c["peak_q15"], 32767, "never saturates")
        self.assertEqual((click["duration_ms"], bip["duration_ms"]), (33, 40))

    def test_screenshot_cues_are_never_record_cues(self):
        for name in sc.CUES:
            self.assertNotIn(name, tone_ref.VARIANTS)
            self.assertNotIn(name, tone_ref.STOP_VARIANTS)
            self.assertNotIn(name, ri.CHOICES + ri.STOP_CHOICES)
        self.assertEqual(set(tone_ref.SCREENSHOT_CUES), set(sc.CUES))

    def test_facts_and_choices(self):
        self.assertEqual((sc.CHOICES, sc.admit_choice(None), sc.admit_choice("off")), (("off", "rate"), "off", "off"))
        with self.assertRaisesRegex(ValueError, "unknown --screenshot 'on'"):
            sc.admit_choice("on")
        self.assertFalse(sc.enabled("off"))
        with self.assertRaisesRegex(ValueError, "--screenshot rate"):
            sc.facts("off")
        t = sc.facts("rate")
        self.assertEqual({k: v for k, v in t.items() if k != "cues"},
                         {"trigger": "rate", "trigger_code": 50, "se_id": ri.SE_ID, "y22_path": H.Y22_PATH,
                          "y22_bytes": H.Y22_BYTES, "band_request": 1_179_648})
        self.assertEqual(t["cues"], sc.cue_facts())
        self.assertEqual(sc.TRIGGER_CODE, H.defines("combo_observe.h", "COMBO_")["TRIGGER_CODE"])
        inject = H.defines("hw_jpeg_inject.h", "INJECT_")
        self.assertEqual((sc.BAND_REQUEST, sc.STRIP_BYTES), (inject["BAND_REQUEST"], inject["SHARED_STRIP_BYTES"]))


class Bindings(unittest.TestCase):
    def test_capture_cue_bindings(self):
        t = sc.facts("rate")
        f = t["cues"]
        src = sc.capture_cue_bindings(t)
        rodata = ' __attribute__((section(".resident_rodata")))'
        self.assertIn('_Static_assert(CAPTURE_CUES == 2, "cue count");', src)
        self.assertIn('_Static_assert(REC_TONE_SE_ID == 2u, "SE id");', src)
        self.assertIn(f"const struct tone_segment capture_cue_segments[4]{rodata} = {{\n", src)
        self.assertIn(f"const struct capture_cue capture_cues[CAPTURE_CUES]{rodata} = {{\n"
                      "    {0u, 3u, 0u, 1584u, 3168u},\n    {3u, 1u, 1584u, 1920u, 3840u},\n};\n", src)
        self.assertIn(f"const uint32_t capture_cue_capacity{rodata} = 3504u;", src)
        self.assertIn("int16_t capture_cue_pcm[3504] __attribute__((aligned(32)));", src, "BSS, filled once by the worker")
        desc = struct.unpack("<32I", b"".join(ri.pending_descriptor(c) for c in f["cues"]))
        rows = "".join("    {" + ", ".join(hex(v) + "u" for v in desc[16 * i:16 * i + 16]) + "},\n" for i in range(2))
        self.assertIn('uint32_t capture_cue_descriptor[CAPTURE_CUES][16] __attribute__((section(".resident_data"), '
                      'aligned(32))) = {\n' + rows + "};\n", src)
        self.assertEqual((desc[0], desc[1], desc[2], desc[16 + 2]), (1, 0, 3168, 3840), "pending: no PCM pointer until published")
        self.assertEqual(src.count("{"), src.count("}"))
        syntax_check(self, "capture-cue-bindings.c", strip_sections(src))

    def test_tone_api_bindings_without_the_record_indicator(self):
        src = sc.tone_api_bindings(AUDIO)
        self.assertIn(f"(rec_tone_query_fn){hex(AUDIO['state_query'])}u", src)
        self.assertIn(f"(rec_tone_beep_fn){hex(AUDIO['beep_start'])}u", src)
        self.assertIn("const struct rec_tone_config rec_tone_config", src)
        self.assertIn("_Static_assert(REC_TONE_SE_ID == 2u && REC_STATE_WORD == 0x16dd4u", src)
        for absent in ("rec_tone_cues", "rec_tone_pcm", "rec_tone_segments", "rec_tone_descriptor", "rec_tone_stop"):
            self.assertNotIn(absent, src)
        cfg = ri.config_words({"rec_state_word": ri.REC_STATE_WORD}, AUDIO)
        self.assertIn("    " + ", ".join(hex(v) + "u" for v in cfg) + ",\n};\n", src)
        self.assertEqual(cfg, ri.config_words(ri.facts("ding"), AUDIO), "the record indicator's config words")
        with self.assertRaisesRegex(ValueError, "Thumb"):
            sc.tone_api_bindings(dict(AUDIO, beep_start=AUDIO["beep_start"] & ~1))
        # Not host-compiled: it carries the native read / ARM cache-clean bindings for a 32-bit target.
        self.assertIn('_Static_assert(sizeof(uintptr_t) == 4, "native target pointers are 32-bit");', src)

    def test_hook_inventory_matches_the_owner_and_the_native_resume_addresses(self):
        inv = sc.hook_inventory()
        self.assertNotIn("rom_sha256", inv)
        got = [(h["va"], h["length"], h["original_bytes"], h["destination_symbol"]) for h in inv["hooks"]]
        self.assertEqual(got, [(va, n, o, d) for va, n, o, d in sc.HOOKS])
        for h in inv["hooks"]:
            self.assertEqual(h["resume_thumb"], (h["va"] + h["length"]) | 1)
        combo, inject = H.defines("combo_observe.h", "COMBO_"), H.defines("hw_jpeg_inject.h", "INJECT_")
        self.assertEqual([h["resume_thumb"] for h in inv["hooks"]],
                         [combo["HOOK_RESUME"], inject["H3_RESUME"], inject["H8_RESUME"]])
        self.assertEqual([h["va"] for h in inv["hooks"]], [combo["HOOK_VA"], inject["H3_VA"], inject["H8_VA"]])
        for asm, names in (("combo_observe_hook.S", ("combo_gui_dispatch_entry",)),
                           ("hw_jpeg_inject_hook.S", ("inject_encode_entry", "inject_develop_end_entry"))):
            text = (H.XPAN_SRC / asm).read_text()
            for name in names:
                self.assertIn(".global " + name, text)
        extra = {e["address"] for e in XPAN_TARGET_CONTRACT["extra_code_fingerprints"]}
        self.assertEqual({int(p, 16) for p in inv["source_guard_pages"]} - SCREENSHOT_PAGES, {0xE07F7000},
                         "one guard page is already a base XPan fingerprint page")
        self.assertIn(0xE07F7000, extra)


def synthetic_linked(t: dict, hooks: list[dict]):
    """A finalized-image stand-in laid out from the build's own encoders, for verify_linked."""
    base, load = 0x1A0000, 0x40
    symbols = {"_resident_start": base, "_resident_load_offset": load}
    data = bytearray(0x10000)
    at = base + 0x100

    def place(name, blob=b"", align=32):
        nonlocal at
        at = (at + align - 1) & ~(align - 1)
        symbols[name] = at
        data[load + at - base:load + at - base + len(blob)] = blob
        at += max(len(blob), 4)
    f = t["cues"]
    place("capture_cue_segments", bytes.fromhex(f["segments_hex"]))
    place("capture_cue_descriptor", b"".join(ri.pending_descriptor(c) for c in f["cues"]))
    bss_sizes = {**sc.BSS_OBJECTS, "inject_shared_strip": sc.STRIP_BYTES, "combo_worker_tick": 4,
                 "capture_cue_pcm": 2 * f["capacity_samples"]}
    for name in (*sc.SYMBOLS, *sc.SHARED_SYMBOLS):
        if name not in symbols and name not in bss_sizes:
            place(name)
    symbols["_resident_data_end"] = at + 64
    bss = symbols["_bss_start"] = (at + 4096) & ~4095
    for name, size in bss_sizes.items():
        symbols[name] = bss
        bss = (bss + size + 31) & ~31
    symbols["_bss_objects_end"] = bss
    return bytes(data), symbols, {"inject_shared_strip": sc.STRIP_BYTES}, hooks


class LinkedCheck(unittest.TestCase):
    """screenshot.verify_linked against a synthetic image built from the same encoders (no toolchain)."""

    def setUp(self):
        self.t = sc.facts("rate")
        self.data, self.symbols, self.sizes, self.hooks = synthetic_linked(self.t, [{"va": "0xe0100000", "length": 4,
                                                                                    "original_hex": "00bf00bf",
                                                                                    "destination": "frame"},
                                                                                   *screenshot_hooks()])

    def check(self, data=None, symbols=None, sizes=None, hooks=None, remaining=900_000, indicator=False):
        hooks = [{**h, "original_hex": h["original_hex"]} for h in (hooks if hooks is not None else self.hooks)]
        return sc.verify_linked(self.data if data is None else data, self.symbols if symbols is None else symbols,
                                self.sizes if sizes is None else sizes, self.t, hooks, indicator=indicator,
                                minimum_user_left=MANIFEST["minimum_user_left"], remaining_user_bytes=remaining)

    def test_accepts_the_encoders_layout(self):
        rec = self.check(indicator=True)
        self.assertEqual([h["destination"] for h in rec["hooks"]], [d for *_, d in sc.HOOKS])
        self.assertEqual([h["resume"] for h in rec["hooks"]], ["0xe04a8031", "0xe0ccb28d", "0xe0549263"])
        self.assertEqual((rec["cue_pcm_address"], rec["cue_descriptor_address"], rec["ring_address"]),
                         (self.symbols["capture_cue_pcm"], self.symbols["capture_cue_descriptor"], self.symbols["combo_hook_ring"]))
        self.assertEqual((rec["hardware_verified"], rec["indicator_shares_tone_engine"], rec["trigger"]), (False, True, "rate"))
        self.assertEqual(rec["cues"], self.t["cues"])

    def test_fails_closed(self):
        s = self.symbols
        for name in ("one_press_step", "rec_tone_preflight", "tone_gen_fill", "inject_pic_row"):
            with self.subTest(missing=name), self.assertRaisesRegex(ValueError, "symbols missing from opt-in build: " + name):
                self.check(symbols={k: v for k, v in s.items() if k != name})
        for name in sc.BSS_OBJECTS:
            with self.subTest(misaligned=name), self.assertRaisesRegex(ValueError, name + " outside aligned owned BSS"):
                self.check(symbols={**s, name: s[name] + 4})
        with self.assertRaisesRegex(ValueError, "one_press_block outside aligned owned BSS"):
            self.check(symbols={**s, "one_press_block": s["_bss_objects_end"]})
        with self.assertRaisesRegex(ValueError, "shared strip"):
            self.check(sizes={"inject_shared_strip": sc.STRIP_BYTES - 32})
        with self.assertRaisesRegex(ValueError, "shared strip"):
            self.check(symbols={**s, "inject_shared_strip": s["inject_shared_strip"] + 16})
        with self.assertRaisesRegex(ValueError, "combo_worker_tick outside owned BSS"):
            self.check(symbols={**s, "combo_worker_tick": s["_bss_start"] - 4})
        for name in ("capture_api", "inject_band_api", "rec_tone_config", "inject_develop_end_entry"):
            with self.subTest(resident=name), self.assertRaisesRegex(ValueError, name + " outside resident initialized section"):
                self.check(symbols={**s, name: s["_bss_start"] + 8})
        with self.assertRaisesRegex(ValueError, "cue PCM outside aligned owned BSS"):
            self.check(symbols={**s, "capture_cue_pcm": s["capture_cue_pcm"] + 2})
        loc = lambda name: s["_resident_load_offset"] + s[name] - s["_resident_start"]  # noqa: E731
        for name, message in (("capture_cue_descriptor", "descriptors differ"), ("capture_cue_segments", "segments differ")):
            bad = bytearray(self.data)
            bad[loc(name) + 8] ^= 1
            with self.subTest(changed=name), self.assertRaisesRegex(ValueError, message):
                self.check(data=bytes(bad))
        wrong = [dict(h, original_hex="00" * h["length"]) if h["destination"] == "inject_encode_entry" else h for h in self.hooks]
        for hooks in (wrong, self.hooks[:-1], [h for h in self.hooks if h["destination"] not in DESTINATIONS]):
            with self.assertRaisesRegex(ValueError, "hook records differ"):
                self.check(hooks=hooks)
        with self.assertRaisesRegex(ValueError, "heap floor"):
            self.check(remaining=MANIFEST["minimum_user_left"] - 1)


class ContractsAndCatalog(unittest.TestCase):
    def test_screenshot_pages_are_admitted_only_by_their_own_fingerprints(self):
        contract, hooks, _ = feature_contract.load_hooks(optional_extensions=(sc.HOOK_EXTENSION,))
        guards = [int(v, 16) for v in contract["source_guard_pages"]]
        validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, hooks=hooks,
                                                  screenshot=True, guard_pages=guards)
        with self.assertRaisesRegex(ValueError, "0xe04a8028|0xe0ccb284|0xe0549258"):
            validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, hooks=hooks)
        with self.assertRaisesRegex(ValueError, "0xe04a8028|0xe0ccb284|0xe0549258"):
            validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, hooks=hooks,
                                                      record_indicator=True)
        base_hooks = [h for h in hooks if h["destination"] not in DESTINATIONS]
        with self.assertRaisesRegex(ValueError, "guard page"):
            validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, hooks=base_hooks,
                                                      guard_pages=guards)
        with self.assertRaisesRegex(ValueError, "screenshot must be bool"):
            validate_xpan_target_contract_consistency(MANIFEST, TARGET_CONTRACT, XPAN_TARGET_CONTRACT, screenshot=1)
        fps = {e["address"] for e in XPAN_TARGET_CONTRACT["screenshot_code_fingerprints"]}
        self.assertEqual(fps, SCREENSHOT_PAGES)
        self.assertTrue(all(e["bytes"] == 4096 for e in XPAN_TARGET_CONTRACT["screenshot_code_fingerprints"]))
        self.assertFalse(fps & {e["address"] for e in XPAN_TARGET_CONTRACT["extra_code_fingerprints"]})
        self.assertFalse(fps & {e["address"] for e in XPAN_TARGET_CONTRACT["record_indicator_code_fingerprints"]})
        self.assertTrue({int(h["va"], 16) & ~4095 for h in hooks if h["destination"] in DESTINATIONS} <= fps)

    def test_capacity_and_hook_inventories(self):
        limits = {k: H.defines(h, "")[k] for h, k in (("code_patch.h", "CODE_PATCH_MAX"), ("code_patch.h", "CODE_PATCH_MAX_SCREENSHOT"),
                                                     ("multi_plan.h", "MULTI_MAX_PAGES"), ("multi_plan.h", "MULTI_MAX_PAGES_SCREENSHOT"),
                                                     ("multi_plan.h", "MULTI_MAX_TABLES"),
                                                     ("multi_plan.h", "MULTI_MAX_TABLES_SCREENSHOT"))}
        self.assertEqual((limits["CODE_PATCH_MAX_SCREENSHOT"], limits["MULTI_MAX_PAGES_SCREENSHOT"],
                          limits["MULTI_MAX_TABLES_SCREENSHOT"]), (38, 24, 11))
        for optional in ((), (ri.HOOK_EXTENSION,), (sc.HOOK_EXTENSION,), (ri.HOOK_EXTENSION, sc.HOOK_EXTENSION)):
            with self.subTest(optional=optional):
                _, hooks, _ = feature_contract.load_hooks(optional_extensions=optional)
                pages = {int(h["va"], 16) & ~4095 for h in hooks}
                suffix = "_SCREENSHOT" if sc.HOOK_EXTENSION in optional else ""
                self.assertLessEqual(len(hooks), limits["CODE_PATCH_MAX" + suffix])
                self.assertLessEqual(len(pages), limits["MULTI_MAX_PAGES" + suffix])
                self.assertLessEqual(len({(p >> 20) & 15 for p in pages}), limits["MULTI_MAX_TABLES" + suffix])
                self.assertEqual(len([h for h in hooks if h["destination"] in DESTINATIONS]), 3 if suffix else 0)
                self.assertEqual(len(hooks), 34 + len(optional) + (2 if suffix else 0))

    def test_feature_manifest_option(self):
        build_mod.check_feature_catalog()
        option = features.load().options["screenshot"]
        self.assertEqual((option.flag, option.values, option.default, option.profiles), ("--screenshot", sc.CHOICES, "off", ("xpan-crop",)))
        raw = json.loads(features.FEATURES_PATH.read_text())
        index = next(i for i, o in enumerate(raw["options"]) if o["id"] == "screenshot")
        drifted = copy.deepcopy(raw)
        drifted["options"][index]["choices"].append({"value": "set", "label": "SET", "status": "experimental"})
        with self.assertRaisesRegex(ValueError, "screenshot.CHOICES"):
            build_mod.check_feature_catalog(features.FeatureManifest(drifted))
        drifted = copy.deepcopy(raw)
        drifted["options"][index]["default"] = "rate"
        with self.assertRaisesRegex(ValueError, "screenshot.CHOICES"):
            build_mod.check_feature_catalog(features.FeatureManifest(drifted))

    def test_cli_and_api_refuse_a_misplaced_or_unknown_screenshot_before_output(self):
        wavs = ["--startup-wav", "a.wav", "--shutter-wav", "b.wav"]
        with tempfile.TemporaryDirectory(prefix="r5-screenshot-") as tmp:
            for argv in (["--profile", "xpan-crop", "--screenshot", "on"], ["--screenshot", "rate", *wavs],
                         ["--screenshot", "off", *wavs]):
                out = Path(tmp) / "out"
                proc = subprocess.run([sys.executable, str(ROOT / "tools" / "build.py"), "--rom", "r.bin", "--toolchain", "t",
                                       "--output", str(out), *argv], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2, argv)
                self.assertIn("--screenshot", proc.stderr, argv)
                self.assertFalse(out.exists(), argv)
        with self.assertRaisesRegex(ValueError, "--screenshot requires --profile xpan-crop"):
            build_mod.build_image(rom_path=Path("r.bin"), startup_wav=Path("a"), shutter_wav=Path("b"),
                                  toolchain_dir=Path("t"), output_dir=Path("o"), screenshot="rate")


def synthetic_screenshot_rom():
    """The public synthetic XPan ROM (with the record indicator's pages) plus the screenshot
    hooks, and screenshot fingerprints recomputed over those synthetic bytes."""
    from test_record_indicator import _synthetic_indicator_rom
    manifest, contract, xpan_contract, hooks, indicator_hooks, rom = _synthetic_indicator_rom()
    xpan_contract = copy.deepcopy(xpan_contract)
    base = manifest["rom_base"]
    for h in sc.hook_inventory()["hooks"]:
        rom[h["va"] - base:h["va"] - base + h["length"]] = bytes.fromhex(h["original_bytes"])
    for region in xpan_contract["screenshot_code_fingerprints"]:
        off = region["address"] - base
        region["sha256"] = hashlib.sha256(rom[off:off + region["bytes"]]).hexdigest()
    return manifest, contract, xpan_contract, hooks, indicator_hooks, rom


def write_mock_toolchain(bin_dir: Path, manifest: dict) -> None:
    """test_xpan_build_cli's mock toolchain, with each tool a copy of the driver (no symlinks)."""
    from test_xpan_build_cli import write_xpan_mock_toolchain

    def copy_instead(self, target):
        if self.is_symlink() or self.exists():
            raise FileExistsError(self)
        shutil.copy2(self.parent / target, self)
    with mock.patch.object(Path, "symlink_to", copy_instead):
        write_xpan_mock_toolchain(bin_dir, manifest)
    assert not any(p.is_symlink() for p in bin_dir.iterdir())


class MockBuild(unittest.TestCase):
    """build_image with a mock arm-none-eabi toolchain (no symbols of the opt-in code are linked)."""

    @classmethod
    def setUpClass(cls):
        from test_record_indicator import _synthetic_indicator_rom
        cls.inputs = synthetic_screenshot_rom()
        cls.indicator_rom = _synthetic_indicator_rom()[-1]

    def build(self, screenshot, indicator=None, hooks="auto", rom=None):
        manifest, contract, xpan_contract, base_hooks, indicator_hooks, synthetic = self.inputs
        if screenshot is None or screenshot == "off":
            # The recorded default digest is over test_record_indicator's synthetic ROM
            # (frame_row_constants.h and feature-contract.json carry the ROM digest).
            synthetic = self.indicator_rom
        tmp = tempfile.TemporaryDirectory(prefix="r5-screenshot-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "rom.bin").write_bytes(bytes(synthetic if rom is None else rom))
        (root / "tc").mkdir()
        write_mock_toolchain(root / "tc", manifest)
        if hooks == "auto":
            hooks = [*(indicator_hooks if indicator else base_hooks), *(screenshot_hooks() if screenshot == "rate" else [])]
        out = root / "out"
        self.tmp = tmp.name

        def call():
            with mock.patch.dict(os.environ, {}, clear=True):
                return build_mod.build_image(rom_path=root / "rom.bin", toolchain_dir=root / "tc", output_dir=out,
                                             profile="xpan-crop", manifest=manifest, target_contract=contract,
                                             xpan_contract=xpan_contract, xpan_hooks=hooks, record_indicator=indicator,
                                             screenshot=screenshot)
        return call, out

    def compiled(self, out):
        """Every compiled source (C and assembly) of build.log, with its argv."""
        cmds = [json.loads(line) for line in (out / "build.log").read_text().splitlines() if line.startswith("[")]
        return [(Path(next(a for a in c if a.endswith((".c", ".S")))).name, c) for c in cmds
                if Path(c[0]).name == "arm-none-eabi-gcc" and "-c" in c]

    def test_default_and_explicit_off_are_the_default_build(self):
        from test_record_indicator import DEFAULT_OFF_MOCK_DIGEST, DEFAULT_OFF_MOCK_IMAGE_SHA256, default_off_digest
        for screenshot in (None, "off"):
            with self.subTest(screenshot=screenshot):
                call, out = self.build(screenshot)
                record = call()
                self.assertIsNone(record["screenshot"])
                self.assertNotIn("screenshot", record["configuration"])
                self.assertNotIn("screenshot", json.loads((out / "source-manifest.json").read_text())["configuration"])
                self.assertEqual(record["format"]["sha256"], DEFAULT_OFF_MOCK_IMAGE_SHA256)
                digest, files, argv = default_off_digest(out, self.tmp)
                self.assertEqual(digest, DEFAULT_OFF_MOCK_DIGEST, json.dumps({"files": files, "argv": argv}, indent=1))
                log = (out / "build.log").read_text()
                for absent in ("R5_SCREENSHOT", "combo_capture", "hw_jpeg_inject", "one_press", "capture-cue-bindings"):
                    self.assertNotIn(absent, log)
                self.assertFalse((out / "capture-cue-bindings.c").exists())
                feature = json.loads((out / "feature-contract.json").read_text())
                self.assertEqual((len(feature["hooks"]), len(feature["pages"]), len(feature["source_guard_pages"])), (34, 20, 137))

    def test_default_build_refuses_a_retained_screenshot_symbol(self):
        call, _ = self.build(None)
        # The mock linker emits sound_worker_entry; pretend it were a screenshot symbol.
        with mock.patch.object(sc, "SYMBOLS", (*sc.SYMBOLS, "sound_worker_entry")):
            with self.assertRaisesRegex(ValueError, "screenshot retained without opt-in: sound_worker_entry"):
                call()

    def test_rate_admits_compiles_and_fails_closed_at_the_linked_check(self):
        call, out = self.build("rate")
        # The mock linker emits none of the screenshot symbols: the linked-image owner must refuse.
        with self.assertRaisesRegex(ValueError, "screenshot symbols missing from opt-in build"):
            call()
        compiled = self.compiled(out)
        names = [n for n, _ in compiled]
        for name in (*(m + ".c" for m in sc.C_MODULES), *(s for s, _ in sc.ASM_MODULES), "capture-cue-bindings.c",
                     "rec-tone-bindings.c"):
            self.assertEqual(names.count(name), 1, name)
        self.assertNotIn("rec_state.c", names)
        self.assertNotIn("rec_state_hook.S", names)
        self.assertEqual({n for n, argv in compiled if sc.DEFINE not in argv}, {"footer.S"}, "every other object")
        self.assertFalse(any(ri.DEFINE in argv for _, argv in compiled), "no record indicator")
        manifest = self.inputs[0]
        self.assertEqual((out / "rec-tone-bindings.c").read_text(), sc.tone_api_bindings(manifest["audio"]))
        self.assertEqual((out / "capture-cue-bindings.c").read_text(), sc.capture_cue_bindings(sc.facts("rate")))
        feature = json.loads((out / "feature-contract.json").read_text())
        self.assertEqual(len(feature["hooks"]), 37)
        self.assertEqual({h["destination"] for h in feature["hooks"]} & DESTINATIONS, DESTINATIONS)
        self.assertEqual(feature["capacity"], {"pages": 24, "tables": 11, "patches": 38})
        self.assertEqual(len(feature["pages"]), 23)
        source = json.loads((out / "source-manifest.json").read_text())
        self.assertEqual(source["configuration"]["screenshot"], sc.facts("rate"))
        self.assertIsNone(source["configuration"]["record_indicator"])
        for path in ("src/experimental/xpan/one_press.c", "src/experimental/xpan/hw_jpeg_inject_hook.S",
                     "src/experimental/xpan/tools/screenshot.py", "src/experimental/xpan/tools/screenshot-hooks.json"):
            self.assertIn(path, source["inputs"])

    def test_rate_with_the_record_indicator_compiles_the_shared_modules_once(self):
        call, out = self.build("rate", indicator="ding")
        with self.assertRaisesRegex(ValueError, "record indicator symbols missing"):
            call()
        compiled = self.compiled(out)
        names = [n for n, _ in compiled]
        for name in ("rec_tone.c", "tone_gen.c", "rec-tone-bindings.c", "capture-cue-bindings.c", "combo_capture.c",
                     "rec_state.c"):
            self.assertEqual(names.count(name), 1, name)
        self.assertEqual({n for n, argv in compiled if sc.DEFINE not in argv or ri.DEFINE not in argv}, {"footer.S"})
        bindings = (out / "rec-tone-bindings.c").read_text()
        self.assertEqual(bindings, ri.bindings_source(ri.facts("ding"), self.inputs[0]["audio"]), "the indicator's own bindings")
        feature = json.loads((out / "feature-contract.json").read_text())
        self.assertEqual((len(feature["hooks"]), len(feature["pages"]), feature["capacity"]["pages"]), (38, 24, 24))

    def test_hook_inventory_must_agree_with_the_choice(self):
        manifest, _, _, base_hooks, _, _ = self.inputs
        for screenshot, hooks in (("rate", base_hooks), ("off", [*base_hooks, *screenshot_hooks()]),
                                  (None, [*base_hooks, *screenshot_hooks()])):
            with self.subTest(screenshot=screenshot):
                call, out = self.build(screenshot, hooks=hooks)
                with self.assertRaisesRegex(ValueError, "screenshot hook inventory disagrees"):
                    call()
                self.assertFalse(out.exists(), "refused before any output")

    def test_rate_rejects_a_rom_whose_screenshot_page_differs(self):
        manifest = self.inputs[0]
        rom = bytearray(self.inputs[-1])
        rom[0xE0D5B000 + 0x800 - manifest["rom_base"]] ^= 0xFF
        call, out = self.build("rate", rom=rom)
        with self.assertRaisesRegex(ValueError, "screenshot_page_e0d5b000"):
            call()
        self.assertFalse(out.exists())
        call, _ = self.build(None, rom=rom)
        self.assertIsNone(call()["screenshot"], "the default build never reads that page")


@unittest.skipUnless(os.environ.get("R5_SCREENSHOT_ROM") and os.environ.get("R5_SCREENSHOT_TOOLCHAIN"),
                     "opt-in real-ROM build: set R5_SCREENSHOT_ROM and R5_SCREENSHOT_TOOLCHAIN")
class RealBuild(unittest.TestCase):
    def test_rate_build_record(self):
        rom = Path(os.environ["R5_SCREENSHOT_ROM"])
        toolchain = Path(os.environ["R5_SCREENSHOT_TOOLCHAIN"])
        parent = os.environ.get("R5_SCREENSHOT_OUTPUT")
        if parent and (Path(parent).is_symlink() or not Path(parent).is_dir()):
            self.fail("R5_SCREENSHOT_OUTPUT must be an existing folder, not a link")
        work = Path(tempfile.mkdtemp(prefix="screenshot-rate-", dir=parent))
        if not parent:
            self.addCleanup(shutil.rmtree, work)
        rom_sha = hashlib.sha256(rom.read_bytes()).hexdigest()
        out = work / "out"
        record = build_mod.build_image(rom_path=rom, toolchain_dir=toolchain, output_dir=out, profile="xpan-crop",
                                       screenshot="rate")
        self.assertEqual(hashlib.sha256(rom.read_bytes()).hexdigest(), rom_sha, "the ROM is only read")
        on_disk = json.loads((out / "build.json").read_text())
        shot = on_disk["screenshot"]
        self.assertEqual(shot, json.loads(json.dumps(record["screenshot"])))
        self.assertIsNone(on_disk["record_indicator"])
        self.assertEqual(on_disk["configuration"]["screenshot"], sc.facts("rate"))
        self.assertEqual((shot["trigger"], shot["trigger_code"], shot["se_id"], shot["hardware_verified"],
                          shot["indicator_shares_tone_engine"]), ("rate", 50, 2, False, False))
        self.assertEqual(shot["cues"], sc.cue_facts())
        self.assertEqual([(h["va"], h["length"], h["original_hex"], h["destination"]) for h in shot["hooks"]],
                         [(hex(va), n, o, d) for va, n, o, d in sc.HOOKS])
        self.assertEqual((shot["cue_pcm_address"] % 32, shot["cue_descriptor_address"] % 32), (0, 0))
        self.assertGreaterEqual(shot["remaining_user_bytes"], MANIFEST["minimum_user_left"])
        self.assertEqual(shot["remaining_user_bytes"], on_disk["reservation"]["remaining_user_bytes"])
        # The linked-image check again, from the files on disk.
        symbols = json.loads((out / "symbols.json").read_text())
        nm = next(toolchain.glob("*-nm"))
        sizes = {row.split()[3]: int(row.split()[1], 16)
                 for row in subprocess.run([str(nm), "-S", "--defined-only", str(out / "handoff.elf")], capture_output=True,
                                           text=True, check=True).stdout.splitlines() if len(row.split()) == 4}
        feature = json.loads((out / "feature-contract.json").read_text())
        again = sc.verify_linked((out / "autoexec.bin").read_bytes(), symbols, sizes, sc.facts("rate"), feature["hooks"],
                                 indicator=False, minimum_user_left=MANIFEST["minimum_user_left"],
                                 remaining_user_bytes=on_disk["reservation"]["remaining_user_bytes"])
        self.assertEqual(json.loads(json.dumps(again)), shot)
        self.assertEqual((len(feature["hooks"]), len(feature["pages"]), feature["capacity"]),
                         (37, 23, {"pages": 24, "tables": 11, "patches": 38}))
        for name in ("rec_tone_state", "rec_tone_step", "rec_tone_report", "rec_tone_cues", "rec_state_entry"):
            self.assertNotIn(name, symbols, "a screenshot-only build links the preflight alone")
        log = (out / "build.log").read_text()
        self.assertIn(sc.DEFINE, log)
        self.assertNotIn(ri.DEFINE, log)


if __name__ == "__main__":
    unittest.main()
