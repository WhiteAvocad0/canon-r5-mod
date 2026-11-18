"""Single source of named R5.221 hook windows and generated native bindings."""
from pathlib import Path
import hashlib
import json
import re
import struct

TOOLS_DIR = Path(__file__).resolve().parent
XPAN_SRC = TOOLS_DIR.parent
REPO_ROOT = XPAN_SRC.parents[2]


def native_limit(header: str, name: str) -> int:
    matches = re.findall(r"^#define " + re.escape(name) + r" ([0-9]+)u$", (XPAN_SRC / header).read_text(), re.M)
    if len(matches) != 1 or int(matches[0]) < 1:
        raise ValueError("native capacity declaration: " + name)
    return int(matches[0])


def checksum(data: bytes) -> int:
    value = 0
    for (word,) in struct.iter_unpack("<I", data):
        value = ((value >> 1) | ((value << 31) & 0xFFFFFFFF)) ^ word
    return value


def _repo_key(path: Path) -> str:
    resolved = path.resolve()
    return resolved.relative_to(REPO_ROOT.resolve()).as_posix() if resolved.is_relative_to(REPO_ROOT.resolve()) else str(resolved)


def load_hooks(
    contract: dict | None = None, *, optional_extensions: tuple[str, ...] = ()
) -> tuple[dict, list[dict], list[dict]]:
    """Load base hooks + named hook extensions without any whole-ROM digest lock.

    optional_extensions are opt-in hook extensions (for example the record
    indicator); their source_guard_pages join the returned contract's witnesses."""
    if contract is None:
        contract = json.loads((TOOLS_DIR / "feature-contract.json").read_text())
    if contract.get("schema") != "canon-r5-lab.crop-feature-input.v2":
        raise ValueError("feature input schema; named extension composition requires v2")
    if "rom_sha256" in contract or "dump_sha256" in contract:
        raise ValueError("public feature contract must not pin a single-device rom_sha256")
    if "framing_hooks" in contract:
        raise ValueError("superseded framing_hooks; use hook_extensions")

    hooks = list(contract["hooks"])
    extension_facts = []
    extensions = contract.get("hook_extensions", [])
    if not isinstance(extensions, list) or any(not isinstance(n, str) or not n for n in extensions):
        raise ValueError("named hook extension inventory")
    if not isinstance(optional_extensions, tuple) or any(
        not isinstance(n, str) or not n or n in extensions for n in optional_extensions
    ) or len(set(optional_extensions)) != len(optional_extensions):
        raise ValueError("optional hook extension inventory")
    extensions = [*extensions, *optional_extensions]
    guard_pages = list(contract["source_guard_pages"])

    for name in extensions:
        selected = TOOLS_DIR / name
        raw = selected.read_bytes()
        extension = json.loads(raw)
        if type(extension.get("schema")) is not int or extension["schema"] != 1:
            raise ValueError("hook extension schema: " + str(selected))
        if "rom_sha256" in extension or "dump_sha256" in extension:
            raise ValueError("public hook extension must not pin rom_sha256: " + str(selected))
        if not isinstance(extension.get("hooks"), list):
            raise ValueError("hook extension inventory: " + str(selected))
        for h in extension["hooks"]:
            if type(h.get("va")) is not int or type(h.get("length")) is not int:
                raise ValueError("hook extension address/length: " + str(selected))
            hooks.append(
                {
                    "va": hex(h["va"]),
                    "length": h["length"],
                    "destination": h["destination_symbol"],
                    "original_hex": h["original_bytes"],
                }
            )
        extra_guards = extension.get("source_guard_pages", [])
        if not isinstance(extra_guards, list) or any(
            not isinstance(v, str) or not re.fullmatch(r"0xe[0-3][0-9a-f]{2}[0-9a-f]000", v) for v in extra_guards
        ):
            raise ValueError("hook extension source_guard_pages: " + str(selected))
        guard_pages.extend(v for v in extra_guards if v not in guard_pages)
        extension_facts.append({"selected_path": _repo_key(selected), "sha256": hashlib.sha256(raw).hexdigest()})
    if guard_pages != list(contract["source_guard_pages"]):
        contract = {**contract, "source_guard_pages": guard_pages}
    if optional_extensions:
        contract = {**contract, "optional_hook_extensions": list(optional_extensions)}
    return contract, hooks, extension_facts


def generate(
    out: Path,
    rom: bytes,
    *,
    actual_rom_sha256: str | None = None,
    hooks_override: list[dict] | None = None,
    rom_base: int = 0xE0000000,
    optional_extensions: tuple[str, ...] = (),
) -> dict:
    """Validate hook windows on pre-admitted ROM bytes and generate feature headers."""
    contract, loaded_hooks, extension_facts = load_hooks(optional_extensions=optional_extensions)
    hooks = list(hooks_override) if hooks_override is not None else loaded_hooks
    if actual_rom_sha256 is None:
        actual_rom_sha256 = hashlib.sha256(rom).hexdigest()

    pages = sorted({int(h["va"], 16) & ~4095 for h in hooks})
    witnesses = sorted({int(v, 16) for v in contract["source_guard_pages"]})
    if any(v & 4095 or not rom_base <= v < rom_base + 0x04000000 for v in witnesses):
        raise ValueError("source witness page")

    capacity = {
        "pages": native_limit("multi_plan.h", "MULTI_MAX_PAGES"),
        "tables": native_limit("multi_plan.h", "MULTI_MAX_TABLES"),
        "patches": native_limit("code_patch.h", "CODE_PATCH_MAX"),
    }
    if not pages or pages[0] != 0xE0100000 or len(pages) > capacity["pages"]:
        raise ValueError("feature page layout")
    if len(hooks) > capacity["patches"]:
        raise ValueError("feature hook capacity")
    if len({(page >> 20) & 15 for page in pages}) > capacity["tables"]:
        raise ValueError("feature table capacity")

    for hook in hooks:
        address = int(hook["va"], 16)
        before = bytes.fromhex(hook["original_hex"])
        if not re.fullmatch(r"[a-z][a-z0-9_]*", hook["destination"]):
            raise ValueError("unnamed feature destination")
        if hook["length"] != len(before) or not 8 <= len(before) <= 16 or len(before) % 2:
            raise ValueError("feature window size")
        if (address & 2) and len(before) < 10:
            raise ValueError("unaligned far literal")
        if rom[address - rom_base : address - rom_base + len(before)] != before:
            raise ValueError("source hook bytes differ")

    previous_end = 0
    for hook in sorted(hooks, key=lambda h: int(h["va"], 16)):
        address = int(hook["va"], 16)
        if address < previous_end:
            raise ValueError("overlapping feature hook owners")
        previous_end = address + hook["length"]

    (out / "feature-config.h").write_text(
        f"#define FEATURE_PAGE_COUNT {len(pages)}u\n"
        f"#define FEATURE_PATCH_COUNT {len(hooks)}u\n"
        f"#define FEATURE_WITNESS_COUNT {len(witnesses)}u\n"
    )
    lines = ["const struct code_patch feature_patches[FEATURE_PATCH_COUNT] DATA = {"]
    for h in hooks:
        original = bytes.fromhex(h["original_hex"])
        lines.append(
            "{"
            + h["va"]
            + "u,(uint32_t)(uintptr_t)"
            + h["destination"]
            + ","
            + str(h["length"])
            + "u,{"
            + ",".join(hex(x) for x in original)
            + "}},"
        )
    lines += ["};", "const struct multi_target feature_targets[FEATURE_PAGE_COUNT] DATA = {"]
    for i, va in enumerate(pages):
        symbol = "boot_shadow_page" if i == 0 else f"feature_extra_pages[{i - 1}]"
        lines.append("{" + hex(va) + "u,(uint32_t)(uintptr_t)" + symbol + "},")
    lines += ["};", "const uint32_t feature_source_checksums[FEATURE_PAGE_COUNT] DATA = {"]
    lines.append(",".join(hex(checksum(rom[va - rom_base : va - rom_base + 4096])) + "u" for va in pages))
    lines += ["};", "const struct feature_witness feature_witnesses[FEATURE_WITNESS_COUNT] DATA = {"]
    for va in witnesses:
        lines.append("{" + hex(va) + "u," + hex(checksum(rom[va - rom_base : va - rom_base + 4096])) + "u},")
    lines += ["};"]
    (out / "feature-targets.h").write_text("\n".join(lines) + "\n")

    record = {
        **contract,
        "rom_sha256": actual_rom_sha256,
        "hooks": hooks,
        "pages": pages,
        "source_page_sha256": {
            hex(va): hashlib.sha256(rom[va - rom_base : va - rom_base + 4096]).hexdigest()
            for va in sorted(set(pages) | set(witnesses))
        },
        "capacity": capacity,
        "hook_extension_sources": extension_facts,
    }
    (out / "feature-contract.json").write_text(json.dumps(record, indent=2) + "\n")
    return record
