"""How R5 Mod Assistant runs: from a checkout, or from the packaged macOS or Windows app.

The packaged app (packaging/) carries this repository's tracked tree unchanged
as data and runs these same source files from it, so every ROOT computed from
__file__ (config/, src/, docs/) and the builder's source manifest stay the same as in
a checkout. Only these things differ when frozen, and they are decided here:

- the builder runs as the app itself in build mode (`<app> --run-build ARGS`) instead
  of `python tools/build.py ARGS`, because sys.executable is the app's own binary;
- new build folders go under the user's home, because the app bundle is read-only;
- `--self-test` creates the window hidden, shows every step and exits (for CI);
- on Windows, console programs the app starts (GCC, PowerShell, the builder) get no
  console window, because the windowed app has no console for them to share.

Widget-free; this module never imports Tkinter.
"""
from __future__ import annotations

from pathlib import Path
import sys

APP_NAME = "R5 Mod Assistant"
BUNDLE_ID = "io.github.whiteavocad0.r5-mod-assistant"
VERSION = "0.1.1"  # the one version of the packaged app; release tags are app-v<VERSION>

BUILD_FLAG = "--run-build"
SELF_TEST_FLAG = "--self-test"
FROZEN_BUILDS_DIR = Path("R5-builds")  # under the home folder (%USERPROFILE% on Windows), next to ~/R5-card-backups


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


def hide_console_windows(popen_class=None, flag: int | None = None) -> None:
    """Give every process started from here CREATE_NO_WINDOW (Windows app only).

    Without a console of its own the windowed app would make Windows open, and flash, a
    new console window for each GCC call. Doing it here, once, leaves the builder's files
    unchanged; they are inputs of the source manifest that every image embeds. Pipes to
    the child (the runner's stdout/stderr capture) are not affected."""
    import subprocess

    cls = subprocess.Popen if popen_class is None else popen_class
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if flag is None else flag
    original = cls.__init__
    if getattr(original, "hides_console_window", False):
        return

    def __init__(self, *args, **kwargs):
        if len(args) < 14:  # creationflags is not given positionally
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | flag
        original(self, *args, **kwargs)

    __init__.hides_console_window = True
    cls.__init__ = __init__


def main(argv: list[str], *, platform: str | None = None) -> int:
    """The packaged app's entry point. Unknown arguments start the window as usual."""
    if is_frozen() and (sys.platform if platform is None else platform) == "win32":
        hide_console_windows()
    if argv[:1] == [BUILD_FLAG]:
        import build

        sys.argv = [build.__file__, *argv[1:]]  # usage and errors name build.py, as from the CLI
        return build.main(argv[1:])
    from gui import app

    if argv[:1] == [SELF_TEST_FLAG]:
        return app.self_test()
    return app.main()
