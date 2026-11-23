"""Build the R5 Mod Assistant desktop app: a .dmg on macOS, a .zip on Windows.

    python packaging/build_app.py [--output DIR] [--self-test] [--no-package] [--toolchain-archive FILE]

Needs a Python 3.11+ with Tkinter (the python.org or actions/setup-python builds) and
PyInstaller (packaging/requirements.txt). It reads HEAD of this repository, refuses
uncommitted changes to tracked files, and writes only below --output (default
build/desktop-app/, which git ignores). It downloads the host xPack archive at build
time unless --toolchain-archive supplies it locally. Both use the same SHA-256 pin.
Outputs:

    stage/r5-mod/                     HEAD's tracked files, checked against git's blob ids
    dist/R5 Mod Assistant.app         macOS: the app; it carries stage/r5-mod byte for byte
    dist/R5 Mod Assistant/            Windows: the app folder with "R5 Mod Assistant.exe"
    R5-Mod-Assistant-<version>-macos-<arm64|x86_64>.dmg
    R5-Mod-Assistant-<version>-windows-x64.zip
    unzipped/                         Windows: the .zip unpacked again, where --self-test runs

The app is not signed with an Apple Developer ID or a Windows code-signing certificate
(see docs/gui.md).
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import time
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = HERE / "r5-mod-assistant.spec"
GUIDE_URL = "https://github.com/WhiteAvocad0/canon-r5-mod/blob/main/docs/gui.md"
WINDOWS_ARCH = {"AMD64": "x64", "ARM64": "arm64"}
TOOLCHAIN_VERSION = "15.2.1-1.1"
TOOLCHAIN_ROOT = f"xpack-arm-none-eabi-gcc-{TOOLCHAIN_VERSION}"
TOOLCHAIN_RELEASE = ("https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/"
                     f"v{TOOLCHAIN_VERSION}")
# Official release archive suffix and SHA-256, indexed by the desktop package target.
TOOLCHAIN_ARCHIVES = {
    ("macos", "arm64"): ("darwin-arm64.tar.gz", "574082d35e49a2bcbdc355836b2a3ae5e5bb3b9456c9f5e37177db2ab4aad870"),
    ("macos", "x86_64"): ("darwin-x64.tar.gz", "5be906a5194c3173e145a58048e5607ffff947773237802b93e55e23c415f1b6"),
    ("windows", "x64"): ("win32-x64.zip", "bae6a3d1667697ce750c3b13d6d26d80973ecedc2cc87bf04869e83447fd93ea"),
}
README_COMMON = """\
The app includes the GNU Arm toolchain (xPack arm-none-eabi-gcc {toolchain_version})
and builds offline. Supply your own ROM dump as shown in the guide. This is an
untested prototype. Every build it makes is hardware-untested. Read the guides first:
{url}

Built from commit {commit}. App licenses are in the Licenses folder. The toolchain
licenses and supplementary notices are in {toolchain_path}/distro-info, under
licenses and R5-notices respectively.
"""
README = {
    "macos": """\
R5 Mod Assistant {version} for macOS ({arch}), prototype

Drag "R5 Mod Assistant" onto Applications, then open it from there.

This app is not signed with an Apple Developer ID and not notarized, so the first time
macOS says it can't verify the app. To open it anyway:
  - macOS 15 or later: try to open it once and click Done, then go to System Settings >
    Privacy & Security and click "Open Anyway" next to the message about R5 Mod Assistant.
  - macOS 14 or earlier: Control-click (or right-click) the app, choose Open, then Open.

""",
    "windows": """\
R5 Mod Assistant {version} for Windows ({arch}), prototype

Extract the whole zip (right-click > Extract All...), for example into your Documents
folder, then open "R5 Mod Assistant.exe" in the "R5 Mod Assistant" folder. Keep the
_internal folder next to it; the app needs everything in that folder.

This app is not signed with a code-signing certificate, so Microsoft Defender SmartScreen
may say "Windows protected your PC". To open it anyway, click "More info", then "Run anyway".

Preparing a card's boot markers (step 7) and the Canon Basic card preparation are offered
on macOS only. On Windows, prepare the card on a Mac first, then use step 8 (copy
autoexec.bin) on that card here.

""",
}


@dataclass(frozen=True)
class Target:
    os: str        # "macos" or "windows"
    arch: str      # the label in the file name
    app: Path      # what the person gets: the .app or the app folder
    exe: Path      # the program inside it
    tree: Path     # the bundled tree (sys._MEIPASS/r5-mod)


def run(argv, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, **kwargs)


def git(*args: str) -> str:
    return run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout


def app_facts(tree: Path) -> dict[str, str]:
    """APP_NAME, BUNDLE_ID and VERSION from tree/tools/gui/bundle.py (their one source).

    Parsed, not imported, so no __pycache__ appears in the staged tree."""
    module = ast.parse((tree / "tools" / "gui" / "bundle.py").read_text(encoding="utf-8"))
    values = {target.id: node.value for node in module.body if isinstance(node, ast.Assign)
              for target in node.targets if isinstance(target, ast.Name)}
    return {key: ast.literal_eval(values[name])
            for key, name in (("name", "APP_NAME"), ("bundle_id", "BUNDLE_ID"), ("version", "VERSION"))}


def target_for(dist: Path, name: str, system: str = sys.platform, machine: str | None = None) -> Target:
    machine = platform.machine() if machine is None else machine
    if system == "darwin":
        app = dist / f"{name}.app"
        return Target("macos", machine, app, app / "Contents" / "MacOS" / name, app / "Contents" / "Resources" / "r5-mod")
    if system == "win32":
        app = dist / name
        return Target("windows", WINDOWS_ARCH.get(machine.upper(), machine.lower()), app, app / f"{name}.exe",
                      app / "_internal" / "r5-mod")
    raise SystemExit(f"the desktop app is built on macOS or Windows, not {system}")


def install_toolchain(target: Target, local_archive: Path | None) -> None:
    """Verify the host archive before extracting it. Install the complete vendor tree beside r5-mod."""
    artifact = TOOLCHAIN_ARCHIVES.get((target.os, target.arch))
    if artifact is None:
        raise SystemExit(f"No bundled xPack toolchain for {target.os} {target.arch}")
    suffix, expected = artifact
    filename = f"{TOOLCHAIN_ROOT}-{suffix}"
    dest = target.tree.resolve().parent / "arm-toolchain"
    with tempfile.TemporaryDirectory(dir=dest.parent) as scratch:
        tmp = Path(scratch)
        archive = local_archive
        if archive is None:
            archive = tmp / filename
            url = f"{TOOLCHAIN_RELEASE}/{filename}"
            print(f"Downloading {url}", flush=True)
            try:
                urllib.request.urlretrieve(url, archive)
            except OSError as exc:
                raise SystemExit(f"Could not download {url}: {exc}. Supply it with --toolchain-archive.") from exc
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != expected:
                raise SystemExit(f"{archive}: SHA-256 {digest}, expected {expected} for {filename}")
            stream.seek(0)
            if suffix.endswith(".zip"):
                with zipfile.ZipFile(stream) as zf:
                    zf.extractall(tmp)
            else:
                with tarfile.open(fileobj=stream, mode="r|gz") as tar:
                    tar.extractall(tmp, filter="data")
        vendor = tmp / TOOLCHAIN_ROOT
        notices = target.tree / "packaging" / "xpack-notices"
        included = vendor / "distro-info" / "R5-notices"
        shutil.copytree(notices / "common", included)
        shutil.copytree(notices / target.os, included, dirs_exist_ok=True)
        vendor.rename(dest)
    print(f"Installed {filename} (SHA-256 {digest})")


def git_blob_id(data: bytes, algorithm: str) -> str:
    return hashlib.new(algorithm, b"blob %d\0" % len(data) + data).hexdigest()


def stage_tree(dest: Path) -> str:
    """Extract HEAD's tracked files into dest, byte for byte as git stores them; return the commit.

    Line endings are not converted (core.autocrlf off, LF), so a Windows build carries the
    same bytes as a macOS one; every file is checked against its git blob id."""
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit("Tracked files differ from HEAD. Commit or stash them first: "
                         "the app carries HEAD's tree, so a build must not mix in other changes.")
    commit = git("rev-parse", "HEAD").strip()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with subprocess.Popen(["git", "-C", str(ROOT), "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                           "archive", "--format=tar", commit], stdout=subprocess.PIPE) as proc:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            tar.extractall(dest, filter="data")
    if proc.returncode:
        raise SystemExit(f"git archive failed with status {proc.returncode}")
    algorithm = git("rev-parse", "--show-object-format").strip() or "sha1"
    expected = {}
    for entry in git("ls-tree", "-r", "-z", commit).split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            mode, _kind, oid = meta.split()
            expected[path] = (mode, oid)
    staged = set(file_hashes(dest))
    if staged != set(expected):
        raise SystemExit(f"the staged tree differs from {commit[:12]}: {sorted(staged ^ set(expected))[:5]}")
    for path, (mode, oid) in expected.items():
        file = dest / path
        data = os.readlink(file).encode() if mode == "120000" else file.read_bytes()
        if git_blob_id(data, algorithm) != oid:
            raise SystemExit(f"the staged {path} differs from its git blob {oid[:12]}")
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


def verify_tree(tree: Path, stage: Path) -> int:
    if not tree.is_dir():
        raise SystemExit(f"no r5-mod tree at {tree}")
    expected, actual = file_hashes(stage), file_hashes(tree.resolve())
    if expected != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
        raise SystemExit(f"the app's tree differs from HEAD: missing {missing[:5]}, extra {extra[:5]}, "
                         f"changed {changed[:5]}")
    return len(expected)


def self_test(exe: Path) -> None:
    """Run the app's --self-test and require its report on stdout (so the windowed program's
    output reaches a pipe, as the builder's does when the window runs a build)."""
    proc = subprocess.run([str(exe), "--self-test"], capture_output=True, text=True, timeout=900)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode or "self-test passed" not in proc.stdout:
        raise SystemExit(f"{exe} --self-test failed (exit status {proc.returncode})")


def write_extras(folder: Path, target: Target, facts: dict[str, str], commit: str, stage: Path) -> None:
    """Read me first.txt and the Licenses folder, next to the app in the .dmg or .zip."""
    text = README[target.os] + README_COMMON
    (folder / "Read me first.txt").write_text(
        text.format(version=facts["version"], arch=target.arch, url=GUIDE_URL, commit=commit,
                    toolchain_version=TOOLCHAIN_VERSION,
                    toolchain_path=(target.tree.parent / "arm-toolchain").relative_to(target.app).as_posix()),
        encoding="utf-8", newline="\r\n" if target.os == "windows" else "\n")
    licenses = folder / "Licenses"
    licenses.mkdir()
    python_license = next((p for p in (Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt",
                                       Path(sys.base_prefix) / "LICENSE.txt") if p.is_file()), None)
    if python_license is None:
        raise SystemExit("no Python LICENSE.txt to ship with the bundled Python")
    shutil.copyfile(python_license, licenses / "Python LICENSE.txt")
    for terms in sorted(Path(sys.base_prefix).glob("tcl/*/license.terms")):  # Tcl/Tk, where the Python has them
        shutil.copyfile(terms, licenses / f"{terms.parent.name} license.terms")
    for file in importlib.metadata.distribution("pyinstaller").files or []:
        if file.name == "COPYING.txt":
            shutil.copyfile(file.locate(), licenses / "PyInstaller COPYING.txt")
            break
    shutil.copyfile(stage / "LICENSE", licenses / "R5 Mod Assistant LICENSE.txt")
    shutil.copyfile(stage / "THIRD_PARTY_NOTICES.md", licenses / "THIRD_PARTY_NOTICES.md")


def fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def make_dmg(target: Target, dmg: Path, facts: dict[str, str], commit: str, stage: Path) -> None:
    root = fresh_dir(dmg.with_suffix(".contents"))
    run(["ditto", str(target.app), str(root / target.app.name)])  # keeps the bundle's symlinks and signature
    (root / "Applications").symlink_to("/Applications")
    write_extras(root, target, facts, commit, stage)
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


def make_zip(target: Target, archive: Path, facts: dict[str, str], commit: str, stage: Path) -> None:
    extras = fresh_dir(archive.with_suffix(".extras"))
    write_extras(extras, target, facts, commit, stage)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for base, prefix in ((target.app, target.app.name), (extras, "")):
            for path in sorted(base.rglob("*")):
                if path.is_file() or path.is_dir():
                    zf.write(path, "/".join(p for p in (prefix, path.relative_to(base).as_posix()) if p))
    shutil.rmtree(extras)
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
    if bad:
        raise SystemExit(f"{archive}: {bad} does not read back")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--output", type=Path, default=ROOT / "build" / "desktop-app")
    ap.add_argument("--toolchain-archive", type=Path, metavar="FILE",
                    help="use a local copy of the pinned host xPack archive for an offline build")
    ap.add_argument("--self-test", action="store_true", help="run the built app's --self-test")
    ap.add_argument("--no-package", action="store_true", help="build the app only, no .dmg or .zip")
    ap.add_argument("--print-version", action="store_true", help="print the app version and exit")
    args = ap.parse_args(argv)

    if args.print_version:
        print(app_facts(ROOT)["version"])
        return 0
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SystemExit(f"{sys.executable} has no Tkinter; use a python.org or actions/setup-python build")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is missing: pip install -r packaging/requirements.txt")

    out = args.output.resolve()
    stage = out / "stage" / "r5-mod"
    commit = stage_tree(stage)
    facts = app_facts(stage)
    target = target_for(out / "dist", facts["name"])
    env = {**os.environ, "R5_APP_TREE": str(stage), "R5_APP_NAME": facts["name"],
           "R5_APP_BUNDLE_ID": facts["bundle_id"], "R5_APP_VERSION": facts["version"], "R5_APP_COMMIT": commit}
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", str(out / "dist"), "--workpath", str(out / "work"), str(SPEC)], env=env)
    install_toolchain(target, args.toolchain_archive)
    if target.os == "macos":
        # Reseal the outer bundle after adding resources, preserving all nested signatures.
        run(["codesign", "--force", "--sign", "-", str(target.app)])
    files = verify_tree(target.tree, stage)
    print(f"{target.app}: {facts['version']} ({target.os} {target.arch}) from {commit[:12]}, "
          f"carries {files} tracked files unchanged")
    package = f"{facts['name'].replace(' ', '-')}-{facts['version']}-{target.os}-{target.arch}"
    if target.os == "macos":
        if args.self_test:
            self_test(target.exe)
            verify_tree(target.tree, stage)  # running the app wrote nothing into it (no __pycache__)
        run(["codesign", "--verify", "--deep", "--strict", str(target.app)])  # the ad-hoc signature holds
        if not args.no_package:
            dmg = out / f"{package}.dmg"
            make_dmg(target, dmg, facts, commit, stage)
            print(dmg)
        return 0
    if args.no_package:
        if args.self_test:
            self_test(target.exe)
            verify_tree(target.tree, stage)
        return 0
    archive = out / f"{package}.zip"
    make_zip(target, archive, facts, commit, stage)
    unzipped = fresh_dir(out / "unzipped")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(unzipped)
    copy = target_for(unzipped, facts["name"])
    verify_tree(copy.tree, stage)
    if args.self_test:
        self_test(copy.exe)  # from the unzipped folder, as the person runs it
        verify_tree(copy.tree, stage)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
