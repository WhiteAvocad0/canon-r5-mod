"""Owning target admission, layout and Thumb patch contracts. No target actions."""
from pathlib import Path
import hashlib
import json
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_PATH = ROOT / "config" / "firmware.json"
TARGET_CONTRACT_PATH = ROOT / "config" / "target_contract.json"

MANIFEST = json.loads(FIRMWARE_PATH.read_text())
TARGET_CONTRACT = json.loads(TARGET_CONTRACT_PATH.read_text())

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_target_contract_consistency(
    manifest: dict = MANIFEST,
    contract: dict = TARGET_CONTRACT,
) -> None:
    """Validate once that firmware.json and target_contract.json agree and have no private dump lock."""
    if "dump_sha256" in manifest or "dump_sha256" in contract:
        raise ValueError("public target profile must not enforce a single-device full dump_sha256")
    if contract.get("admission_mode") != "immutable_code_fingerprints":
        raise ValueError("unsupported admission_mode in target_contract.json")

    rom_layout = contract.get("rom_layout")
    if type(rom_layout) is not dict:
        raise ValueError("missing rom_layout in target_contract.json")
    for key in ("dump_bytes", "rom_base", "main_start", "signature_words", "signature", "startup_bytes"):
        if rom_layout.get(key) != manifest.get(key):
            raise ValueError(f"target_contract.rom_layout.{key} disagrees with firmware.json")

    vb = contract.get("validated_bindings")
    if type(vb) is not dict:
        raise ValueError("missing validated_bindings in target_contract.json")

    # Counter reader contract
    cr = vb.get("counter_reader", {})
    obs = manifest.get("observer", {})
    if (obs.get("counter_context_slot"), obs.get("counter_field_offset")) != (
        cr.get("context_slot"),
        cr.get("counter_offset"),
    ):
        raise ValueError("observer counter slot/offset disagrees with target_contract.json")

    # Shutter worker contract
    sw = manifest.get("shutter_worker")
    sw_c = vb.get("shutter_worker")
    if type(sw) is not dict or type(sw_c) is not dict:
        raise ValueError("missing shutter_worker manifest/contract section")
    for key in ("create_task", "start_task", "delete_task", "sleep_ms"):
        val = sw.get(key)
        if type(val) is not int or not 0 < val <= 0xFFFFFFFF or not (val & 1):
            raise ValueError(f"invalid Thumb address for shutter_worker.{key}")
        if val != sw_c.get(key):
            raise ValueError(f"shutter_worker.{key} disagrees with target_contract.json")
    if sw["sleep_ms"] != manifest["witness"]["bindings"]["sleep"]:
        raise ValueError("shutter_worker.sleep_ms disagrees with witness.bindings.sleep")
    if (sw.get("stack_bytes"), sw.get("priority"), sw.get("options"), sw.get("readiness_limit")) != (
        4096,
        19,
        0,
        1,
    ) or (
        sw.get("stack_bytes"),
        sw.get("priority"),
        sw.get("options"),
        sw.get("readiness_limit"),
    ) != (
        sw_c.get("stack_bytes"),
        sw_c.get("priority"),
        sw_c.get("options"),
        sw_c.get("readiness_limit"),
    ):
        raise ValueError("invalid shutter_worker task parameters")
    if sw.get("context_base") != manifest["witness"]["context_base"] or sw.get("context_base") != sw_c.get(
        "context_base"
    ):
        raise ValueError("shutter_worker.context_base disagrees with witness.context_base / target_contract.json")

    # Audio OutSel0 & dependency bindings
    ao = vb.get("audio_outsel", {})
    audio = manifest.get("audio", {})
    if ao.get("stock_pcm_outsel") != 0:
        raise ValueError("validated_bindings.audio_outsel.stock_pcm_outsel must be 0")
    for key in (
        "poll_limit",
        "sleep_ms",
        "singleton_slot",
        "audioctrl_slot",
        "audioctrl_handle_offset",
        "audio_ic_slot",
        "audio_ic_init_offset",
        "output_context_slot",
        "icu_cache_base",
        "icu_cache_kind_offset",
        "icu_cache_init_offset",
        "icu_kind_vaif",
        "icu_kind_ak",
        "ak_param_slot",
        "vaif_param_slot",
    ):
        if ao.get(key) != audio.get(key):
            raise ValueError(f"audio.{key} disagrees with target_contract.json")

    # Observer bindings
    obs_c = vb.get("observer", {})
    for key in ("protocol", "public_slot", "beep_property", "class", "level", "max_notifications_logged", "max_dumps"):
        if obs_c.get(key) != obs.get(key):
            raise ValueError(f"observer.{key} disagrees with target_contract.json")

    # Code fingerprints geometry and full API coverage
    fps = contract.get("code_fingerprints")
    if type(fps) is not list or not fps:
        raise ValueError("missing code_fingerprints in target_contract.json")
    rom_base = manifest["rom_base"]
    dump_bytes = manifest["dump_bytes"]
    seen_names = set()
    covered_ranges = []
    for entry in fps:
        if type(entry) is not dict:
            raise ValueError("invalid code_fingerprints entry")
        name = entry.get("name")
        addr = entry.get("address")
        length = entry.get("bytes")
        sha = entry.get("sha256")
        if not isinstance(name, str) or not name or name in seen_names:
            raise ValueError(f"invalid or duplicate fingerprint name: {name!r}")
        seen_names.add(name)
        if type(addr) is not int or type(length) is not int or length <= 0:
            raise ValueError(f"invalid address/length for fingerprint {name}")
        off = addr - rom_base
        if off < 0 or off + length > dump_bytes:
            raise ValueError(f"fingerprint {name} outside 64 MiB ROM range")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            raise ValueError(f"invalid sha256 for fingerprint {name}")
        covered_ranges.append((addr, addr + length))

    def _is_covered(addr_val: int) -> bool:
        code_addr = addr_val & ~1
        return any(start <= code_addr < end for start, end in covered_ranges)

    if not any(
        entry["address"] == manifest["main_start"]
        and entry["bytes"] >= 4 * manifest["signature_words"]
        for entry in fps
    ):
        raise ValueError("code_fingerprints must cover main signature window (E0100000..E0140000)")
    if not any(
        entry["address"] == manifest["main_start"] and entry["bytes"] >= manifest["startup_bytes"]
        for entry in fps
    ):
        raise ValueError("code_fingerprints must cover 4 KiB startup block")

    bound_apis = [
        ("camif_fixup", manifest["camif_fixup"]),
        ("camif_status", manifest["camif_status"]),
        ("init_factory", manifest["init_factory"]),
        ("canon_init", manifest["canon_init"]),
        ("cache_clean", manifest["cache_clean"]),
        ("cache_invalidate", manifest["cache_invalidate"]),
        ("bzero", manifest["bzero"]),
        *[("witness.bindings." + k, v) for k, v in manifest["witness"]["bindings"].items()],
        ("audio.state_query", manifest["audio"]["state_query"]),
        ("audio.beep_start", manifest["audio"]["beep_start"]),
        ("observer.register_slave", manifest["observer"]["register_slave"]),
        ("observer.ack", manifest["observer"]["ack"]),
        ("observer.debug", manifest["observer"]["debug"]),
        ("shutter_worker.create_task", manifest["shutter_worker"]["create_task"]),
        ("shutter_worker.start_task", manifest["shutter_worker"]["start_task"]),
        ("shutter_worker.delete_task", manifest["shutter_worker"]["delete_task"]),
        ("shutter_worker.sleep_ms", manifest["shutter_worker"]["sleep_ms"]),
        ("counter_reader.getter_thumb", cr["getter_thumb"]),
    ]
    for api_name, api_addr in bound_apis:
        if not _is_covered(api_addr):
            raise ValueError(f"bound API {api_name} ({hex(api_addr)}) is not covered by code_fingerprints")


def admit_rom_with_provenance(
    data: bytes,
    *,
    manifest: dict = MANIFEST,
    contract: dict = TARGET_CONTRACT,
) -> tuple[bytes, dict]:
    """Strictly admit an R5 2.2.1 ROM dump using immutable code fingerprints and word-sum guard."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("ROM data must be bytes")
    validate_target_contract_consistency(manifest, contract)

    if len(data) != manifest["dump_bytes"]:
        raise ValueError(
            f"wrong/truncated ROM length: expected {manifest['dump_bytes']} bytes, got {len(data)}"
        )

    actual_rom_sha256 = hashlib.sha256(data).hexdigest()
    rom_base = manifest["rom_base"]
    off = manifest["main_start"] - rom_base
    end = off + 4 * manifest["signature_words"]
    word_sum = sum(x[0] for x in struct.iter_unpack("<I", data[off:end])) & 0xFFFFFFFF
    if word_sum != manifest["signature"]:
        raise ValueError(
            f"firmware signature mismatch: expected 0x{manifest['signature']:08x}, got 0x{word_sum:08x}"
        )

    verified_regions = []
    for region in contract["code_fingerprints"]:
        r_name = region["name"]
        r_addr = region["address"]
        r_len = region["bytes"]
        r_off = r_addr - rom_base
        actual_region_sha = hashlib.sha256(data[r_off : r_off + r_len]).hexdigest()
        if actual_region_sha != region["sha256"]:
            raise ValueError(
                f"ROM code fingerprint mismatch at {r_name} (0x{r_addr:08x}+{r_len}): "
                f"expected {region['sha256']}, got {actual_region_sha}"
            )
        verified_regions.append(
            {
                "name": r_name,
                "address": hex(r_addr),
                "bytes": r_len,
                "sha256": actual_region_sha,
            }
        )

    startup = bytes(data[off : off + manifest["startup_bytes"]])
    for patch in manifest["patches"]:
        p_off = patch["offset"]
        actual_hex = startup[p_off : p_off + 4].hex()
        if actual_hex != patch["expected"]:
            raise ValueError(
                f"startup opcode mismatch at offset {p_off}: expected {patch['expected']}, got {actual_hex}"
            )

    provenance = {
        "target": contract["target"],
        "admission_mode": contract["admission_mode"],
        "bytes": len(data),
        "sha256": actual_rom_sha256,
        "word_sum": f"0x{word_sum:08x}",
        "verified_code_regions": len(verified_regions),
        "code_fingerprints": verified_regions,
    }
    return startup, provenance


def admit_rom(
    data: bytes,
    *,
    manifest: dict = MANIFEST,
    contract: dict = TARGET_CONTRACT,
) -> bytes:
    """Admit ROM bytes and return the 4 KiB startup block."""
    startup, _ = admit_rom_with_provenance(data, manifest=manifest, contract=contract)
    return startup


def thumb_branch(pc: int, dest: int, *, link: bool) -> bytes:
    if pc % 2 or not dest & 1:
        raise ValueError("requires halfword site and Thumb target")
    delta = (dest & ~1) - (pc + 4)
    if delta % 2 or not -(1 << 24) <= delta < (1 << 24):
        raise ValueError("Thumb branch out of range")
    s = (delta >> 24) & 1
    i1 = (delta >> 23) & 1
    i2 = (delta >> 22) & 1
    first = 0xF000 | (s << 10) | ((delta >> 12) & 0x3FF)
    second = (0xD000 if link else 0x9000) | ((1 ^ (i1 ^ s)) << 13) | ((1 ^ (i2 ^ s)) << 11) | ((delta >> 1) & 0x7FF)
    return struct.pack("<HH", first, second)


def reservation(end: int, *, manifest: dict = MANIFEST) -> dict:
    start = manifest["user_start"]
    base = manifest["resident_base"]
    limit = manifest["objects_start"]
    if end % 16 or end <= base or end >= limit:
        raise ValueError("misaligned/overlapping reservation")
    used = end - start
    remaining = manifest["user_length"] - used
    if remaining < manifest["minimum_user_left"]:
        raise ValueError("insufficient Canon user memory")
    if start + manifest["user_length"] != limit:
        raise ValueError("input pool geometry inconsistent")
    return {"start": base, "end": end, "bytes_reserved": used, "remaining_user_bytes": remaining}


def patch_startup(startup: bytes, base: int, symbols: dict, *, manifest: dict = MANIFEST) -> bytes:
    if len(startup) != manifest["startup_bytes"]:
        raise ValueError("startup size")
    out = bytearray(startup)
    for p in manifest["patches"]:
        off = p["offset"]
        if startup[off : off + 4].hex() != p["expected"]:
            raise ValueError("startup opcode mismatch")
        if p["symbol"] not in symbols:
            raise ValueError("missing patch target")
        out[off : off + 4] = thumb_branch(base + off, symbols[p["symbol"]] | 1, link=p["link"])
    return bytes(out)


def deployment() -> dict:
    return {
        "ready": False,
        "blockers": [
            "public rebuild requires on-camera verification",
            "camera bootflag and card preparation are separate user prerequisites",
        ],
        "experimental_limitations": [
            "stock CamIF submission and storage-lock helpers can block indefinitely",
            "host validation does not establish camera recovery or successful runtime behavior",
        ],
    }


def validate_write_buffer(address: int, length: int, resident_start: int, resident_end: int) -> dict:
    # The resident owner is low cached RAM; Canon's converter supplies its bit30
    # alias to FIO. Keep that representation distinct from allocation ownership.
    if any(type(x) is not int for x in [address, length, resident_start, resident_end]):
        raise ValueError("FIO buffer geometry must be integers")
    if not 0 < resident_start <= address < resident_end <= 0x40000000 or length <= 0 or address + length > resident_end:
        raise ValueError("FIO buffer outside owned cached resident range")
    return {
        "cached_address": address,
        "uncached_address": address | 0x40000000,
        "bytes": length,
        "clean_start": address & ~31,
        "clean_end": (address + length + 31) & ~31,
    }


def validate_layout(symbols: dict, *, manifest: dict = MANIFEST) -> dict:
    s = symbols
    start = s["_resident_start"]
    end = s["_resident_data_end"]
    bss = s["_bss_start"]
    top = s["_private_stack_top"]
    reserved = s["_reserved_end"]
    if start != manifest["resident_base"] or not start < end <= bss < top == reserved:
        raise ValueError("resident/BSS/stack order")
    if top - bss < manifest["private_stack_bytes"]:
        raise ValueError("private stack overlaps initialized data/BSS")
    if not start <= s["copied_startup"] or s["copied_startup"] + manifest["startup_bytes"] > end:
        raise ValueError("copied startup outside resident image")
    if s["_resident_load_offset"] < 32 or s["_resident_load_offset"] + (end - start) > s["_image_bytes"] - 8:
        raise ValueError("copy exceeds on-card initialized payload")
    return reservation(reserved, manifest=manifest)
