"""Entry point for the standalone Modsmith app built by PyInstaller.

With no arguments it opens the desktop app; with arguments it behaves like
the ``modsmith`` command line (``Modsmith.exe check -c pack.toml``)."""
import os
import sys

# A windowed build has no console: give print() somewhere harmless to go.
for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w"))

from modsmith.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:] or ["gui"]))
