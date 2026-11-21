"""Entry point of the packaged macOS and Windows app (PyInstaller runs this file frozen).

It runs R5 Mod Assistant from the repository tree the app carries unchanged as data
(sys._MEIPASS/r5-mod: Contents/Resources/r5-mod on macOS, _internal/r5-mod on Windows),
not from frozen copies of its modules, so the paths the modules derive from __file__ and
the builder's source manifest match a checkout. r5-mod-assistant.spec leaves the tree's
own modules out of the frozen archive; only the Python standard library they need is
frozen. See tools/gui/bundle.py for the modes.
"""
from pathlib import Path
import sys

sys.dont_write_bytecode = True  # the app's own files stay unchanged (read-only and signed on macOS)
if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS, "r5-mod").resolve()
else:  # python3 packaging/launcher.py from a checkout behaves the same way
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from gui import bundle  # noqa: E402  (imported from ROOT/tools)

raise SystemExit(bundle.main(sys.argv[1:]))
