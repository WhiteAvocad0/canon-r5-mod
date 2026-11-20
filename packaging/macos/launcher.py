"""Entry point of the packaged macOS app (PyInstaller runs this file frozen).

It runs R5 Mod Assistant from the repository tree the app carries unchanged as data
(Contents/Resources/r5-mod), not from frozen copies of its modules, so the paths the
modules derive from __file__ and the builder's source manifest match a checkout.
r5-mod-assistant.spec leaves the tree's own modules out of the frozen archive; only the
Python standard library they need is frozen. See tools/gui/bundle.py for the modes.
"""
from pathlib import Path
import sys

sys.dont_write_bytecode = True  # the app bundle is read-only and sealed by its signature
if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS, "r5-mod").resolve()
else:  # python3 packaging/macos/launcher.py from a checkout behaves the same way
    ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from gui import bundle  # noqa: E402  (imported from ROOT/tools)

raise SystemExit(bundle.main(sys.argv[1:]))
