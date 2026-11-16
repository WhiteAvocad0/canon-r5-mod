"""Portable host C11 compiler discovery without macOS-specific pinning."""
from pathlib import Path
import shutil


def find_host_cc() -> str:
    """Return an available host C11 compiler (cc/gcc/clang) or raise FileNotFoundError."""
    for candidate in ("cc", "gcc", "clang"):
        found = shutil.which(candidate)
        if found:
            return found
    if Path("/usr/bin/cc").is_file():
        return "/usr/bin/cc"
    raise FileNotFoundError("A host C11 compiler (cc, gcc, or clang) is required")
