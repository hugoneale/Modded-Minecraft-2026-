#!/usr/bin/env sh
# Start Modsmith on macOS / Linux. Needs Python 3.11+ with Tk
# (Linux: sudo apt install python3-tk, or your distro's equivalent).
cd "$(dirname "$0")" || exit 1
for py in python3.13 python3.12 python3.11 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHONPATH="$(pwd)/src" exec "$py" -m modsmith "${@:-gui}"
  fi
done
echo "Python 3.11+ with Tkinter not found. Install it from https://www.python.org/downloads/" >&2
exit 1
