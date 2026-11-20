"""How R5 Mod Assistant runs: from a checkout, or from the packaged macOS app.

The packaged app (packaging/macos/) carries this repository's tracked tree unchanged
as data and runs these same source files from it, so every ROOT computed from
__file__ (config/, src/, docs/) and the builder's source manifest stay the same as in
a checkout. Only three things differ when frozen, and they are decided here:

- the builder runs as the app itself in build mode (`<app> --run-build ARGS`) instead
  of `python tools/build.py ARGS`, because sys.executable is the app's own binary;
- new build folders go under the user's home, because the app bundle is read-only;
- `--self-test` creates the window hidden, shows every step and exits (for CI).

Widget-free; this module never imports Tkinter.
"""
from __future__ import annotations

from pathlib import Path
import sys

APP_NAME = "R5 Mod Assistant"
BUNDLE_ID = "io.github.whiteavocad0.r5-mod-assistant"
VERSION = "0.1.0"  # the one version of the packaged app; release tags are app-v<VERSION>

BUILD_FLAG = "--run-build"
SELF_TEST_FLAG = "--self-test"
FROZEN_BUILDS_DIR = Path("R5-builds")  # under the home folder, next to ~/R5-rom-dumps and ~/R5-card-backups


def is_frozen() -> bool:
    """True inside the packaged app (PyInstaller sets sys.frozen)."""
    return bool(getattr(sys, "frozen", False))


def builder_command(build_py: Path, *, frozen: bool | None = None, executable: str | None = None) -> list[str]:
    """The command that starts the builder, before its own arguments.

    A checkout runs the documented CLI (`python tools/build.py`); the app runs itself in build mode."""
    frozen = is_frozen() if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    return [executable, BUILD_FLAG] if frozen else [executable, str(build_py)]


def builds_root(root: Path, *, frozen: bool | None = None, home: Path | None = None) -> Path:
    """Where new build folders go: the ignored build/ folder of a checkout, or ~/R5-builds for the app."""
    frozen = is_frozen() if frozen is None else frozen
    if frozen:
        return (Path.home() if home is None else Path(home)) / FROZEN_BUILDS_DIR
    return Path(root) / "build"


def main(argv: list[str]) -> int:
    """The packaged app's entry point. Unknown arguments start the window as usual."""
    if argv[:1] == [BUILD_FLAG]:
        import build

        return build.main(argv[1:])
    from gui import app

    if argv[:1] == [SELF_TEST_FLAG]:
        return app.self_test()
    return app.main()
