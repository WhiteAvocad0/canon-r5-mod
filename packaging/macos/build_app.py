"""Build "R5 Mod Assistant.app" and its .dmg on macOS.

    python3 packaging/macos/build_app.py [--output DIR] [--self-test] [--no-dmg]

Needs a Python 3.11+ with Tkinter (the python.org or actions/setup-python builds) and
PyInstaller (see packaging/macos/requirements.txt). It reads HEAD of this repository,
refuses uncommitted changes to tracked files, and writes only below --output (default
build/macos-app/, which git ignores):

    stage/r5-mod/                     the tracked tree, from `git archive HEAD`
    dist/R5 Mod Assistant.app         the app; it carries stage/r5-mod byte for byte
    R5-Mod-Assistant-<version>-macos-<arch>.dmg

The app is not signed with a Developer ID and not notarized (see docs/gui.md).
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = HERE / "r5-mod-assistant.spec"
DMG_README = """\
R5 Mod Assistant {version} for macOS ({arch})

Drag "R5 Mod Assistant" onto Applications, then open it from there.

This app is not signed with an Apple Developer ID, so the first time macOS says it
can't check the app for malicious software. To open it anyway:
  - Control-click (or right-click) the app in Applications, choose Open, then Open; or
  - try to open it once, then go to System Settings > Privacy & Security and click
    "Open Anyway" next to the message about R5 Mod Assistant.

You still need the GNU Arm toolchain (xPack arm-none-eabi-gcc 15.2.1-1.1); the app
explains where to get it. Read docs/gui.md and docs/getting-started.md first:
https://github.com/WhiteAvocad0/canon-r5-mod
"""


def run(argv, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, **kwargs)


def git(*args: str) -> str:
    return run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout


def app_facts(tree: Path) -> dict[str, str]:
    """APP_NAME, BUNDLE_ID and VERSION from the staged tools/gui/bundle.py (their one source)."""
    sys.path.insert(0, str(tree / "tools"))
    try:
        from gui import bundle
    finally:
        sys.path.pop(0)
    if Path(bundle.__file__).resolve() != (tree / "tools" / "gui" / "bundle.py").resolve():
        raise SystemExit(f"loaded {bundle.__file__} instead of the staged bundle.py")
    return {"name": bundle.APP_NAME, "bundle_id": bundle.BUNDLE_ID, "version": bundle.VERSION}


def stage_tree(dest: Path) -> str:
    """Extract HEAD's tracked files into dest; return the commit."""
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit("Tracked files differ from HEAD. Commit or stash them first: "
                         "the app carries HEAD's tree, so a build must not mix in other changes.")
    commit = git("rev-parse", "HEAD").strip()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with subprocess.Popen(["git", "-C", str(ROOT), "archive", "--format=tar", commit],
                          stdout=subprocess.PIPE) as proc:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            tar.extractall(dest, filter="data")
    if proc.returncode:
        raise SystemExit(f"git archive failed with status {proc.returncode}")
    return commit


def file_hashes(base: Path) -> dict[str, str]:
    """Relative path -> SHA-256 for every file below base (symlinks are reported as such)."""
    out = {}
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base).as_posix()
        if path.is_symlink():
            out[rel] = "symlink:" + os.readlink(path)
        elif path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def bundled_tree(app: Path) -> Path:
    """The tree inside the app, where the launcher finds it (sys._MEIPASS/r5-mod)."""
    for candidate in (app / "Contents" / "Frameworks" / "r5-mod", app / "Contents" / "Resources" / "r5-mod"):
        if candidate.exists():
            return candidate.resolve()
    raise SystemExit(f"no r5-mod tree inside {app}")


def verify_tree(app: Path, stage: Path) -> int:
    expected, actual = file_hashes(stage), file_hashes(bundled_tree(app))
    if expected != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
        raise SystemExit(f"the app's tree differs from HEAD: missing {missing[:5]}, extra {extra[:5]}, "
                         f"changed {changed[:5]}")
    return len(expected)


def make_dmg(app: Path, dmg: Path, facts: dict[str, str], arch: str) -> None:
    root = dmg.with_suffix(".contents")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    run(["ditto", str(app), str(root / app.name)])  # keeps the bundle's symlinks and signature
    (root / "Applications").symlink_to("/Applications")
    (root / "Read me first.txt").write_text(DMG_README.format(version=facts["version"], arch=arch))
    argv = ["hdiutil", "create", "-volname", facts["name"], "-srcfolder", str(root), "-fs", "HFS+",
            "-format", "UDZO", "-ov", str(dmg)]
    for attempt in range(3):  # hdiutil occasionally reports "Resource busy" on CI machines
        if subprocess.run(argv).returncode == 0:
            break
        time.sleep(5 * (attempt + 1))
    else:
        raise SystemExit("hdiutil create failed three times")
    shutil.rmtree(root)
    run(["hdiutil", "verify", str(dmg)], capture_output=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--output", type=Path, default=ROOT / "build" / "macos-app")
    ap.add_argument("--self-test", action="store_true", help="run the built app's --self-test")
    ap.add_argument("--no-dmg", action="store_true", help="build the .app only")
    ap.add_argument("--print-version", action="store_true", help="print the app version and exit")
    args = ap.parse_args(argv)

    if args.print_version:
        print(app_facts(ROOT)["version"])
        return 0
    if sys.platform != "darwin":
        raise SystemExit("the macOS app can only be built on macOS")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SystemExit(f"{sys.executable} has no Tkinter; use a python.org or actions/setup-python build")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is missing: pip install -r packaging/macos/requirements.txt")

    out = args.output.resolve()
    stage = out / "stage" / "r5-mod"
    commit = stage_tree(stage)
    facts = app_facts(stage)
    arch = platform.machine()
    env = {**os.environ, "R5_APP_TREE": str(stage), "R5_APP_NAME": facts["name"],
           "R5_APP_BUNDLE_ID": facts["bundle_id"], "R5_APP_VERSION": facts["version"], "R5_APP_COMMIT": commit}
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", str(out / "dist"), "--workpath", str(out / "work"), str(SPEC)], env=env)
    app = out / "dist" / f"{facts['name']}.app"
    files = verify_tree(app, stage)
    print(f"{app}: {facts['version']} ({arch}) from {commit[:12]}, carries {files} tracked files unchanged")
    if args.self_test:
        run([str(app / "Contents" / "MacOS" / facts["name"]), "--self-test"])
    if not args.no_dmg:
        dmg = out / f"{facts['name'].replace(' ', '-')}-{facts['version']}-macos-{arch}.dmg"
        make_dmg(app, dmg, facts, arch)
        print(dmg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
