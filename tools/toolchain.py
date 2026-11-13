"""Portable toolchain admission and controlled build environment rules."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "toolchain_policy.json"

# Ambient make/GCC/Python/Git overrides that could silently alter build inputs.
# Values are never persisted.
OVERRIDE_VARIABLES = frozenset({
    "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES", "MAKEOVERRIDES",
    "CFLAGS", "CPPFLAGS", "CXXFLAGS", "LDFLAGS", "ASFLAGS", "ARFLAGS",
    "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
    "LIBRARY_PATH", "COMPILER_PATH", "GCC_EXEC_PREFIX",
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "PYTHONPATH", "PYTHONHOME",
    "FATAL_ASSERTS", "CONFIG_LVAPP_HACK_RELOC", "ML_OBJS", "ML_SRC_EXTRA_OBJS",
    "ML_ENTRY_OBJ", "ML_INIT_OBJ", "ML_BITRATE_OBJ", "ML_AF_PATTERNS_OBJ",
    "ML_BOOTFLAGS_OBJ", "ML_AUDIO_OBJ", "ML_ZEBRA_OBJ",
})


def controlled_environment() -> dict[str, str]:
    """Reject ambient build overrides and return a clean subprocess environment."""
    present = sorted(OVERRIDE_VARIABLES.intersection(os.environ))
    if present:
        raise ValueError(
            "Fixed build profile refuses ambient overrides: "
            + ", ".join(present)
            + ". Unset those variables for this command or define a separate explicit profile."
        )
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    return environment


def _resolve_tool(folder: Path, name: str) -> Path:
    candidate = folder / name
    if candidate.is_file():
        return candidate
    if sys.platform == "win32" or os.name == "nt":
        exe = folder / f"{name}.exe"
        if exe.is_file():
            return exe
    raise ValueError(f"Required toolchain executable missing in {folder}: {name}")


def _run_tool_query(exe: Path, args: list[str], env: dict[str, str]) -> str:
    proc = subprocess.run([str(exe), *args], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise ValueError(f"Toolchain query failed ({exe.name} {' '.join(args)}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def admit_toolchain(caller_path: Path, *, policy_path: Path = POLICY_PATH) -> tuple[Path, dict]:
    """Validate explicit toolchain directory portably without pinning host-OS executable hashes."""
    caller_path = Path(caller_path)
    if not caller_path.exists():
        raise ValueError(f"Toolchain directory does not exist: {caller_path}")
    folder = caller_path.resolve(strict=True)
    if not folder.is_dir():
        raise ValueError(f"Toolchain path is not a directory: {folder}")

    policy = json.loads(policy_path.read_text())
    env = controlled_environment()

    resolved_tools: dict[str, Path] = {}
    for name in policy["required_executables"]:
        exe = _resolve_tool(folder, name)
        if not os.access(exe, os.X_OK):
            raise ValueError(f"Toolchain executable is not executable: {exe}")
        resolved_tools[name] = exe

    gcc = resolved_tools["arm-none-eabi-gcc"]
    target = _run_tool_query(gcc, ["-dumpmachine"], env)
    if target != policy["required_target"]:
        raise ValueError(
            f"Unsupported toolchain target {target!r}; expected {policy['required_target']!r}"
        )

    gcc_version = _run_tool_query(gcc, ["-dumpfullversion"], env)
    if gcc_version != policy["required_gcc_version"]:
        raise ValueError(
            f"Unsupported GCC version {gcc_version!r}; reference version is {policy['required_gcc_version']!r}"
        )

    executables_info = {}
    for name, exe in resolved_tools.items():
        ver_out = _run_tool_query(exe, ["--version"], env)
        first_line = ver_out.splitlines()[0] if ver_out else ""
        executables_info[name] = {
            "path": str(exe),
            "sha256": _sha256_file(exe),
            "version": first_line,
        }

    admission = {
        "caller_input_path": str(caller_path),
        "resolved_path": str(folder),
        "target": target,
        "gcc_version": gcc_version,
        "policy": policy,
        "executables": executables_info,
    }
    return folder, admission
