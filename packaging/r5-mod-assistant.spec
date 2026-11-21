# PyInstaller spec for R5 Mod Assistant: onedir and windowed, unsigned (on macOS only
# PyInstaller's ad-hoc signature). macOS gets "R5 Mod Assistant.app", Windows the folder
# "R5 Mod Assistant" with "R5 Mod Assistant.exe". Run it through packaging/build_app.py,
# which stages the tracked tree with `git archive` and passes these settings in the environment.
import os
from pathlib import Path
import sys

TREE = Path(os.environ["R5_APP_TREE"]).resolve()  # the staged tracked tree (git archive HEAD)
APP_NAME = os.environ["R5_APP_NAME"]
BUNDLE_ID = os.environ["R5_APP_BUNDLE_ID"]
VERSION = os.environ["R5_APP_VERSION"]
COMMIT = os.environ["R5_APP_COMMIT"]
SEARCH = [TREE / "tools", TREE / "src" / "experimental" / "xpan" / "tools"]


def tree_modules():
    """Every module of the tree, so the analysis finds all standard-library modules they import."""
    names = []
    for base in SEARCH:
        for path in sorted(base.rglob("*.py")):
            parts = path.relative_to(base).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                names.append(".".join(parts))
    return names


def in_tree(path):
    try:
        return Path(path).resolve().is_relative_to(TREE)
    except (OSError, TypeError, ValueError):
        return False


a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(p) for p in SEARCH],
    hiddenimports=tree_modules(),
    noarchive=False,
)
# The tree's own modules run from the bundled source files (see launcher.py), never frozen.
a.pure = [entry for entry in a.pure if not in_tree(entry[1])]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, Tree(str(TREE), prefix="r5-mod"), name=APP_NAME, upx=False)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleVersion": VERSION,
            "LSApplicationCategoryType": "public.app-category.photography",
            "NSHighResolutionCapable": True,
            "NSDownloadsFolderUsageDescription": (
                "R5 Mod Assistant looks for the GNU Arm toolchain you unpacked in Downloads. "
                "You can decline and choose its folder yourself."),
            "NSRemovableVolumesUsageDescription": (
                "R5 Mod Assistant reads and writes only the SD card you choose."),
            "R5SourceCommit": COMMIT,
        },
    )
