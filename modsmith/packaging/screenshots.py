"""Regenerate the README screenshots by driving the real app.

    pip install pillow
    python packaging/screenshots.py ../docs/screenshots

Needs a display (on Linux, run under Xvfb). Uses a temporary settings folder,
so it never touches your saved mod list. The "check" shot runs a real check
against Modrinth, so it needs internet access.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="modsmith-shots-")  # fresh first launch
os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"]

import tkinter as tk  # noqa: E402
import tkinter.font as tkfont  # noqa: E402

from PIL import ImageGrab  # noqa: E402

from modsmith import gui  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots").resolve()
CHECK_MODS = ["Mekanism", "Just Enough Items (JEI)", "Create", "Farmer's Delight", "Alex's Mobs", "AppleSkin",
              "Applied Energistics 2", "Botania", "Iron's Spells 'n Spellbooks", "Jade", "Waystones", "Ars Nouveau"]
TARGET = "1.21.1"


def grab(win, name: str):
    win.update()
    time.sleep(0.3)
    win.update()
    x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
    kw = {"xdisplay": os.environ["DISPLAY"]} if sys.platform.startswith("linux") else {}
    ImageGrab.grab(bbox=(x, y, x + w, y + h), **kw).save(OUT / f"{name}.png", optimize=True)
    print("saved", name)


def wait(root, seconds: float, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        root.update()
        time.sleep(0.05)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    for family in ("Segoe UI", "Inter", "DejaVu Sans"):  # look like Windows where possible
        if family in tkfont.families(root):
            gui.UI_FONT = family
            break
    app = gui.App(root)
    root.geometry("1240x800+0+0")
    wait(root, 20, lambda: app.mc_var.get() not in ("loading...", ""))
    app.mc_var.set(TARGET)
    wait(root, 1)
    grab(root, "01-start")

    # Pick mods: filter, untick a few.
    app.filter_var.set("yung")
    wait(root, 0.5)
    shown = app.tree.get_children("")
    for iid in shown[1::3]:
        app._set_checked(iid, False)
    app._restripe()
    app.tree.selection_set(shown[:1])
    grab(root, "03-choose")
    for iid in shown:
        app._set_checked(iid, True)
    app.filter_var.set("")

    # Add mods dialog.
    d = gui.AddModsDialog(app)
    d.win.geometry("840x600+60+60")
    d.query.set("sodium")
    d._search()
    wait(root, 20, lambda: d.tree.get_children())
    d.tree.selection_set(d.tree.get_children()[:2])
    grab(d.win, "02-add-mods")
    d.win.destroy()

    # Settings dialog.
    s = gui.SettingsDialog(root, app.settings)
    s.win.geometry("+60+40")
    grab(s.win, "06-settings")
    s.win.destroy()

    # Run a real check on a handful of well-known mods.
    app._check_all(False)
    wanted = {n.lower() for n in CHECK_MODS}
    for iid in app.order:
        if app.tree.set(iid, "name").lower() in wanted:
            app._set_checked(iid, True)
    app._restripe()
    app._start("check")
    wait(root, 6)
    wait(root, 10, lambda: len(app.banner.sparks) >= 8)
    grab(root, "05-forging")
    wait(root, 600, lambda: not app.busy)
    app.filter_var.set("")
    app.tree.yview_moveto(0)
    # Put the checked mods at the top for the picture.
    app.order.sort(key=lambda i: not app.rows[i]["checked"])
    app._apply_filter()
    grab(root, "04-check")
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
