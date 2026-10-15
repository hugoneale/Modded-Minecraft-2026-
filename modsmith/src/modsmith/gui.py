"""Modsmith desktop app (Tkinter - ships with Python on Windows and macOS;
on Linux install your distro's ``python3-tk`` package).

Styled as a blacksmith's forge: stone walls, iron controls, ember accents,
and an anvil that gets hammered while Modsmith works."""
from __future__ import annotations

import os
import queue
import random
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:  # pragma: no cover
    tk = None

from . import __version__, art, config, report
from .config import FEATURE_POLICIES, Settings
from .jarscan import read_jar
from .models import ModRef, ModResult, Status
from .porting.decompile import DECOMPILE_WARNING
from .report import STATUS_TEXT

LOADERS = ["neoforge", "forge", "fabric", "quilt"]

# Forge theme.
BG, PANEL, FIELD = "#1c1916", "#26221e", "#141210"
TEXT, MUTED = "#ece3d0", "#a89f8f"
IRON, IRON_HI, IRON_LO = "#4a5059", "#5f6670", "#2c3036"
EMBER, EMBER_HI, EMBER_LO = "#ff7b1c", "#ff9a45", "#b5380c"
WOOD, WOOD_HI, SELECT = "#5a3a20", "#6e4828", "#7a3410"
STRIPE_A, STRIPE_B = "#26221e", "#2d2823"

STATUS_COLOURS = {
    Status.UP_TO_DATE: "#6fcf73", Status.OFFICIAL: "#6cb6ff", Status.PORTED: "#c792ea",
    Status.PORTED_DECOMPILED: "#e0a3ff", Status.OFFICIAL_FEATURE_LOSS: "#ffb347",
    Status.NO_OFFICIAL: "#ffb347", Status.PORT_FAILED: "#ff6b5b", Status.NO_SOURCE: "#ff6b5b",
    Status.UNRESOLVED: "#9a948a", Status.SKIPPED: "#9a948a",
}

if sys.platform.startswith("win"):
    UI_FONT, MONO_FONT = "Segoe UI", "Consolas"
elif sys.platform == "darwin":
    UI_FONT, MONO_FONT = "Helvetica Neue", "Menlo"
else:
    UI_FONT, MONO_FONT = "DejaVu Sans", "DejaVu Sans Mono"

SESSION_FILE = "session.toml"


def apply_theme(root: "tk.Tk") -> float:
    """Style every ttk widget as iron and embers. Returns the UI scale factor."""
    scale = max(1.0, root.winfo_fpixels("1i") / 96)
    st = ttk.Style(root)
    st.theme_use("clam")
    base = (UI_FONT, 10)
    bold = (UI_FONT, 10, "bold")
    st.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD, bordercolor=IRON_LO,
                 lightcolor=IRON, darkcolor=IRON_LO, troughcolor=FIELD, focuscolor=EMBER,
                 selectbackground=SELECT, selectforeground=TEXT, insertcolor=TEXT, font=base)
    st.configure("TButton", background=IRON, foreground=TEXT, padding=(10, 5), borderwidth=1,
                 lightcolor=IRON_HI, darkcolor=IRON_LO)
    st.map("TButton", background=[("disabled", "#2a2d31"), ("pressed", IRON_LO), ("active", IRON_HI)],
           foreground=[("disabled", "#6e675c")])
    st.configure("Forge.TButton", background=EMBER, foreground="#1a0f06", font=bold,
                 lightcolor=EMBER_HI, darkcolor=EMBER_LO, bordercolor=EMBER_LO)
    st.map("Forge.TButton", background=[("disabled", "#5a3a26"), ("pressed", EMBER_LO), ("active", EMBER_HI)],
           foreground=[("disabled", "#9a7a62")])
    st.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, padding=4)
    st.configure("TCombobox", fieldbackground=FIELD, background=IRON, foreground=TEXT,
                 arrowcolor=TEXT, padding=3)
    st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
           selectbackground=[("readonly", FIELD)], background=[("active", IRON_HI)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                 rowheight=int(24 * scale), borderwidth=0)
    st.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", "#fff4e0")])
    st.configure("Treeview.Heading", background=WOOD, foreground=TEXT, font=bold, relief="flat",
                 lightcolor=WOOD_HI, darkcolor="#3d2714", padding=(6, 4))
    st.map("Treeview.Heading", background=[("active", WOOD_HI)])
    for orient in ("Vertical", "Horizontal"):
        st.configure(f"{orient}.TScrollbar", background=IRON, troughcolor=FIELD, arrowcolor=TEXT,
                     bordercolor=IRON_LO, lightcolor=IRON_HI, darkcolor=IRON_LO)
        st.map(f"{orient}.TScrollbar", background=[("active", IRON_HI)])
    st.configure("Ember.Horizontal.TProgressbar", background=EMBER, troughcolor=FIELD,
                 lightcolor=EMBER_HI, darkcolor=EMBER_LO, bordercolor=IRON_LO)
    st.configure("TCheckbutton", background=BG, foreground=TEXT, indicatorbackground=FIELD,
                 indicatorforeground=EMBER)
    st.map("TCheckbutton", background=[("active", BG)], indicatorbackground=[("selected", FIELD)])
    st.configure("TLabelframe", background=BG, bordercolor=IRON)
    st.configure("TLabelframe.Label", background=BG, foreground=EMBER_HI, font=bold)
    st.configure("Muted.TLabel", foreground=MUTED)
    st.configure("Ember.TLabel", foreground=EMBER_HI, font=bold)
    st.configure("Status.TLabel", background=FIELD, foreground=TEXT, padding=(10, 5))
    st.configure("Sash", sashthickness=int(6 * scale))
    st.configure("TPanedwindow", background=BG)
    root.configure(bg=BG)
    return scale


def _dark_titlebar(win: "tk.Misc") -> None:
    """Ask Windows 10/11 for a dark title bar to match the theme."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), 4) == 0:
                break
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


def _toplevel(parent, title: str) -> "tk.Toplevel":
    w = tk.Toplevel(parent)
    w.title(title)
    w.configure(bg=BG)
    w.transient(parent)
    _dark_titlebar(w)
    return w


def _text_box(parent, **kw) -> "ScrolledText":
    box = ScrolledText(parent, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat",
                       selectbackground=SELECT, highlightthickness=1, highlightbackground=IRON_LO,
                       highlightcolor=EMBER, font=(MONO_FONT, 9), **kw)
    box.vbar.configure(bg=IRON, troughcolor=FIELD, activebackground=IRON_HI, relief="flat")
    return box


class Banner:
    """Stone-wall header: an anvil with a glowing ingot, the MODSMITH logo,
    and a hammer that strikes (throwing sparks) while work is running."""

    def __init__(self, parent, scale: float):
        self.px = max(3, round(4 * scale))       # screen pixels per art pixel
        self.lpx = max(4, round(6 * scale))      # logo pixel size
        self.pad = round(12 * scale)
        self.height = 32 * self.px + self.pad
        self.c = tk.Canvas(parent, height=self.height, bg=BG, highlightthickness=0, bd=0)
        self.busy = False
        self.frame = 0
        self.sparks: list[list] = []
        self.hammer_dy = 0
        self.info = ""
        self.c.bind("<Configure>", lambda e: self._draw_wall(e.width))
        self._draw_static()
        self.c.after(90, self._tick)

    def pack(self, **kw):
        self.c.pack(**kw)

    def _draw_wall(self, width: int):
        c = self.c
        c.delete("wall")
        rng = random.Random(16)
        bw, bh = 16 * self.px // 2, 8 * self.px // 2
        for row in range(self.height // bh + 1):
            off = (bw // 2) if row % 2 else 0
            for col in range(-1, width // bw + 2):
                x, y = col * bw + off, row * bh
                shade = rng.choice(("#25211d", "#28241f", "#2b2621", "#231f1b", "#2e2923"))
                c.create_rectangle(x, y, x + bw - 2, y + bh - 2, fill=shade, outline="", tags="wall")
                c.create_rectangle(x, y, x + bw - 2, y + 1, fill="#332d27", outline="", tags="wall")
        # Forge glow along the bottom edge.
        for i, col in enumerate(("#ff7b1c", "#c4470f", "#7a2a0a", "#3a1a0a")):
            c.create_rectangle(0, self.height - 4 + i, width, self.height - 3 + i, fill=col, outline="", tags="wall")
        c.tag_lower("wall")

    def _blit(self, pixels: dict, ox: int, oy: int, size: int, tag: str):
        for (x, y), key in pixels.items():
            colour = art.PALETTE[key]
            tags = (tag, "glow") if tag == "scene" and key == "Y" and 10 <= y <= 13 else (tag,)
            self.c.create_rectangle(ox + x * size, oy + y * size, ox + (x + 1) * size, oy + (y + 1) * size,
                                    fill=colour, outline="", tags=tags)

    def _draw_static(self):
        p, pad = self.px, self.pad
        self.ox, self.oy = pad, pad // 2 - p
        self._blit(art.scene(with_hammer=False), self.ox, self.oy, p, "scene")
        self._blit(art.hammer(), self.ox, self.oy, p, "hammer")
        logo, w = art.logo()
        lx = self.ox + 32 * p + pad * 2
        ly = pad + 2 * p
        self._blit(logo, lx, ly, self.lpx, "logo")
        self.c.create_text(lx, ly + 8 * self.lpx + pad // 2, anchor="nw", fill=EMBER_HI,
                           font=(UI_FONT, 12, "bold"), text="Forge, check & port your Minecraft mods")
        self.info_id = self.c.create_text(lx, ly + 8 * self.lpx + pad // 2 + 26 * self.px // 4, anchor="nw",
                                          fill=MUTED, font=(UI_FONT, 10), text="")
        self.c.create_text(lx + w * self.lpx + pad, ly, anchor="nw", fill=MUTED,
                           font=(UI_FONT, 9), text=f"v{__version__}")

    def set_info(self, text: str):
        self.c.itemconfigure(self.info_id, text=text)

    def set_busy(self, busy: bool):
        self.busy = busy
        if not busy:
            self._move_hammer(0)

    def _move_hammer(self, dy: int):
        if dy != self.hammer_dy:
            self.c.move("hammer", 0, (dy - self.hammer_dy) * self.px)
            self.hammer_dy = dy

    def _strike_sparks(self):
        p = self.px
        for _ in range(9):
            x = self.ox + random.uniform(14, 20) * p
            y = self.oy + 10 * p
            vx, vy = random.uniform(-2.2, 2.2) * p / 2, random.uniform(-3.6, -1.4) * p / 2
            colour = random.choice((art.PALETTE["Y"], art.PALETTE["O"], art.PALETTE["O"], "#fff2b0"))
            item = self.c.create_rectangle(x, y, x + p, y + p, fill=colour, outline="", tags="spark")
            self.sparks.append([item, vx, vy, random.randint(8, 16)])

    def _tick(self):
        self.frame += 1
        if self.busy:
            cycle = [0, 0, 0, 1, 2, 2, 1, 0, 0, 0, 0]
            dy = cycle[self.frame % len(cycle)]
            if dy == 2 and self.hammer_dy == 1:
                self._strike_sparks()
            self._move_hammer(dy)
        if self.frame % 6 == 0:  # the ingot glows
            self.c.itemconfigure("glow", fill=random.choice((art.PALETTE["Y"], "#ffe27a", "#ffc23d")))
        alive = []
        for s in self.sparks:
            item, vx, vy, life = s
            self.c.move(item, vx, vy)
            s[2] = vy + self.px * 0.35
            s[3] = life - 1
            if s[3] > 0:
                alive.append(s)
            else:
                self.c.delete(item)
        self.sparks = alive
        self.c.after(70, self._tick)


class App:
    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.scale = apply_theme(root)
        self.settings = Settings()
        self.config_path: Path | None = None
        self.rows: dict[str, dict] = {}          # tree item id -> {"ref", "result", "checked", "tag"}
        self.order: list[str] = []               # every row, in list order (some may be filtered out)
        self.results: list[ModResult] = []
        self.engine = None
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False

        root.title(f"Modsmith {__version__} - the mod forge")
        root.geometry(f"{int(1240 * self.scale)}x{int(840 * self.scale)}")
        root.minsize(int(980 * self.scale), int(640 * self.scale))
        try:
            self._icon = tk.PhotoImage(data=art.icon_png_base64(64))
            root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        _dark_titlebar(root)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._quit)
        self._restore_session()
        self.root.after(100, self._pump)
        threading.Thread(target=self._load_versions, daemon=True).start()

    # ------------------------------------------------------------------ layout
    def _build(self):
        s = self.scale
        pad = {"padx": int(10 * s), "pady": int(4 * s)}
        self.banner = Banner(self.root, s)
        self.banner.pack(fill="x")

        bench = ttk.LabelFrame(self.root, text=" Workbench ", padding=(int(10 * s), int(6 * s)))
        bench.pack(fill="x", padx=pad["padx"], pady=(int(8 * s), int(4 * s)))
        ttk.Label(bench, text="Mods folder").grid(row=0, column=0, sticky="w")
        self.mods_var = tk.StringVar(value=str(config.default_mods_dir()))
        ttk.Entry(bench, textvariable=self.mods_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(bench, text="Browse...", command=self._browse_mods).grid(row=0, column=2)
        ttk.Label(bench, text="Minecraft").grid(row=0, column=3, sticky="e", padx=(16, 4))
        self.mc_var = tk.StringVar(value="loading...")
        self.mc_box = ttk.Combobox(bench, textvariable=self.mc_var, width=12)
        self.mc_box.grid(row=0, column=4)
        ttk.Label(bench, text="Loader").grid(row=0, column=5, sticky="e", padx=(12, 4))
        self.loader_var = tk.StringVar(value="neoforge")
        ttk.Combobox(bench, textvariable=self.loader_var, values=LOADERS, width=10,
                     state="readonly").grid(row=0, column=6)
        ttk.Button(bench, text="Settings...", command=self._settings_dialog).grid(row=0, column=7, padx=(12, 0))
        bench.columnconfigure(1, weight=1)

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", **pad)
        ttk.Button(bar, text="+ Add mods...", style="Forge.TButton", command=self._add_mods).pack(side="left", padx=2)
        for text, cmd in (("Scan folder", self._scan), ("Import pack...", self._import_pack),
                          ("16MARSHAL pack", self._load_bundled_pack), ("Open list...", self._open_config),
                          ("Save list...", self._save_config)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)
        for text, cmd in (("Remove", self._remove), ("None", lambda: self._check_all(False)),
                          ("All", lambda: self._check_all(True))):
            ttk.Button(bar, text=text, command=cmd).pack(side="right", padx=2)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(bar, textvariable=self.filter_var, width=22).pack(side="right", padx=(4, 10))
        ttk.Label(bar, text="Filter", style="Muted.TLabel").pack(side="right")

        mid = ttk.PanedWindow(self.root, orient="vertical")
        mid.pack(fill="both", expand=True, **pad)
        tree_frame = ttk.Frame(mid)
        cols = ("use", "name", "spec", "current", "status", "build", "content")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        for c, text, w in (("use", "Use", 44), ("name", "Mod", 240), ("spec", "Id", 170),
                           ("current", "Current", 120), ("status", "Status", 190),
                           ("build", "Build for target", 200), ("content", "Content check", 240)):
            self.tree.heading(c, text=text, command=lambda c=c: self._sort(c))
            self.tree.column(c, width=int(w * s), anchor="center" if c == "use" else "w",
                             stretch=c in ("name", "content"))
        for st, colour in STATUS_COLOURS.items():
            self.tree.tag_configure(st.value, foreground=colour)
        self.tree.tag_configure("even", background=STRIPE_A)
        self.tree.tag_configure("odd", background=STRIPE_B)
        self.tree.tag_configure("off", foreground="#6e675c")
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._details)
        self.tree.bind("<space>", self._toggle_selected)
        self.tree.bind("<Delete>", lambda e: self._remove())
        mid.add(tree_frame, weight=4)

        log_frame = ttk.Frame(mid)
        self.log_box = _text_box(log_frame, height=8, state="disabled")
        self.log_box.pack(fill="both", expand=True)
        mid.add(log_frame, weight=1)

        actions = ttk.Frame(self.root)
        actions.pack(fill="x", **pad)
        self.btn_check = ttk.Button(actions, text="1. Check", command=lambda: self._start("check"))
        self.btn_update = ttk.Button(actions, text="2. Forge it! (Install / Update)", style="Forge.TButton",
                                     command=lambda: self._start("install"))
        self.btn_port = ttk.Button(actions, text="3. Force port missing", command=lambda: self._start("port"))
        self.btn_cancel = ttk.Button(actions, text="Cancel", command=self._cancel, state="disabled")
        for b in (self.btn_check, self.btn_update, self.btn_port, self.btn_cancel):
            b.pack(side="left", padx=3)
        ttk.Button(actions, text="Open report", command=self._open_report).pack(side="left", padx=3)
        ttk.Button(actions, text="Open mods folder",
                   command=lambda: _open_path(Path(self.mods_var.get()))).pack(side="left", padx=3)
        self.progress = ttk.Progressbar(actions, mode="determinate", length=int(240 * s),
                                        style="Ember.Horizontal.TProgressbar")
        self.progress.pack(side="right", padx=4)
        self.count_var = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.count_var, style="Muted.TLabel").pack(side="right", padx=8)
        self.status_var = tk.StringVar(value="Add mods with '+ Add mods', 'Scan folder' or 'Import pack', "
                                             "pick a Minecraft version, then press Check.")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w", style="Status.TLabel").pack(fill="x")

    # ----------------------------------------------------------------- helpers
    def log(self, msg: str):
        self.events.put(("log", msg))

    def _pump(self):
        try:
            while True:
                kind, *payload = self.events.get_nowait()
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", payload[0] + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "versions":
                    vals = payload[0]
                    self.mc_box.configure(values=vals)
                    if self.mc_var.get() in ("loading...", ""):
                        self.mc_var.set(vals[0] if vals else "")
                elif kind == "result":
                    self._show_result(payload[0])
                elif kind == "progress":
                    self.progress.configure(value=payload[0], maximum=max(payload[1], 1))
                elif kind == "done":
                    self._finish(*payload)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _load_versions(self):
        try:
            from .http import Http
            from .platform import Platform
            vals = Platform(Http()).minecraft_versions()
        except Exception as e:  # noqa: BLE001
            self.log(f"Couldn't load Minecraft versions ({e}); type one in.")
            vals = []
        self.events.put(("versions", vals))

    def _add_ref(self, ref: ModRef, current: str = "", name: str = ""):
        for iid, row in self.rows.items():
            if row["ref"].spec == ref.spec:
                return iid
        iid = self.tree.insert("", "end", values=("[x]" if ref.enabled else "[ ]", name or ref.hint or ref.ident,
                                                  ref.spec if not ref.spec.startswith("file:") else Path(ref.ident).name,
                                                  current, "", "", ""))
        self.rows[iid] = {"ref": ref, "result": None, "checked": ref.enabled, "tag": None}
        self.order.append(iid)
        return iid

    def _clear(self):
        for iid in self.order:
            self.tree.delete(iid)
        self.rows.clear()
        self.order.clear()
        self._restripe()

    def _tag_row(self, iid: str, index: int):
        row = self.rows[iid]
        tags = [row["tag"]] if row["tag"] else []
        if not row["checked"]:
            tags.append("off")
        tags.append("odd" if index % 2 else "even")
        self.tree.item(iid, tags=tuple(tags))

    def _restripe(self):
        for i, iid in enumerate(self.tree.get_children("")):
            self._tag_row(iid, i)
        self._update_count()

    def _update_count(self):
        on = sum(1 for r in self.rows.values() if r["checked"])
        shown = len(self.tree.get_children(""))
        self.count_var.set(f"{on} of {len(self.rows)} mods selected"
                           + (f" ({shown} shown)" if shown != len(self.rows) else ""))

    def _apply_filter(self):
        q = self.filter_var.get().strip().lower()
        pos = 0
        for iid in self.order:
            vals = self.tree.item(iid, "values")
            hit = not q or any(q in str(v).lower() for v in vals[1:3])
            if hit:
                self.tree.move(iid, "", pos)
                pos += 1
            else:
                self.tree.detach(iid)
        self._restripe()

    def _sort(self, col: str):
        rev = getattr(self, "_sort_state", (None, False)) == (col, False)
        self._sort_state = (col, rev)
        self.order.sort(key=lambda i: str(self.tree.set(i, col)).lower(), reverse=rev)
        self._apply_filter()

    def _selected_refs(self) -> list[ModRef]:
        out = []
        for iid in self.order:
            row = self.rows[iid]
            if row["checked"]:
                row["ref"].enabled = True
                out.append(row["ref"])
        return out

    def _set_checked(self, iid: str, value: bool):
        row = self.rows[iid]
        row["checked"] = value
        row["ref"].enabled = value
        self.tree.set(iid, "use", "[x]" if value else "[ ]")

    def _on_click(self, event):
        if self.tree.identify_column(event.x) != "#1" or self.tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        if iid in self.rows:
            self._set_checked(iid, not self.rows[iid]["checked"])
            self._tag_row(iid, self.tree.index(iid))
            self._update_count()

    def _toggle_selected(self, _event=None):
        sel = [i for i in self.tree.selection() if i in self.rows]
        if sel:
            value = not all(self.rows[i]["checked"] for i in sel)
            for iid in sel:
                self._set_checked(iid, value)
            self._restripe()
        return "break"

    def _check_all(self, value: bool):
        for iid in self.tree.get_children(""):   # only what the filter shows
            self._set_checked(iid, value)
        self._restripe()

    def _remove(self):
        for iid in self.tree.selection():
            self.rows.pop(iid, None)
            if iid in self.order:
                self.order.remove(iid)
            self.tree.delete(iid)
        self._restripe()

    # ----------------------------------------------------------------- session
    def _session_path(self) -> Path:
        return config.user_data_dir() / SESSION_FILE

    def _restore_session(self):
        path = self._session_path()
        if path.is_file():
            try:
                self._apply_settings(config.load(path))
                self.status_var.set(f"Welcome back - {len(self.rows)} mods on the workbench.")
                return
            except Exception as e:  # noqa: BLE001
                self.log(f"Couldn't restore the last session ({e}).")
        pack = config.bundled_pack()
        if pack:
            self._load_pack(pack, replace=True)
            self.status_var.set(f"Loaded the bundled 16MARSHAL pack ({len(self.rows)} mods). Untick what you "
                                "don't want, add more with '+ Add mods', pick a Minecraft version, then Check.")

    def _save_session(self):
        try:
            path = self._session_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_settings()
            self.settings.mods = [self.rows[i]["ref"] for i in self.order]
            config.dump(self.settings, path)
        except Exception as e:  # noqa: BLE001
            print(f"Couldn't save session: {e}", file=sys.stderr)

    def _quit(self):
        if self.busy and not messagebox.askokcancel("Modsmith", "Modsmith is still working. Quit anyway?"):
            return
        self._save_session()
        self.root.destroy()

    def _apply_settings(self, s: Settings):
        key = self.settings.curseforge_api_key
        self.settings = s
        if key and not s.curseforge_api_key:
            s.curseforge_api_key = key
        if s.mods_dir and s.mods_dir != "mods":
            self.mods_var.set(s.mods_dir)
        if s.minecraft:
            self.mc_var.set(s.minecraft)
        if s.loader:
            self.loader_var.set(s.loader)
        self._clear()
        for r in s.mods:
            self._add_ref(r, name=r.hint or "")
        self._apply_filter()
        self._update_banner()

    def _update_banner(self):
        s = self.settings
        if s.from_minecraft:
            self.banner.set_info(f"Mods currently on Minecraft {s.from_minecraft} "
                                 f"{(s.from_loader or '').capitalize()}  ->  pick a target version below")
        else:
            self.banner.set_info("Pick mods and a Minecraft version below")

    # ----------------------------------------------------------------- actions
    def _browse_mods(self):
        d = filedialog.askdirectory(title="Choose your mods folder", initialdir=self.mods_var.get())
        if d:
            self.mods_var.set(d)

    def _scan(self):
        folder = Path(self.mods_var.get())
        if not folder.is_dir():
            messagebox.showerror("Modsmith", f"{folder} is not a folder")
            return
        n = 0
        for jar in sorted(folder.glob("*.jar")):
            try:
                info = read_jar(jar, with_hashes=False)
                self._add_ref(ModRef(spec=f"file:{jar}"), current=info.version, name=info.name)
                n += 1
            except Exception as e:  # noqa: BLE001
                self.log(f"Skipped {jar.name}: {e}")
        self._apply_filter()
        self.status_var.set(f"Found {n} mods in {folder}")

    def _add_mods(self):
        AddModsDialog(self)

    def _import_pack(self):
        f = filedialog.askopenfilename(title="Import modpack",
                                       filetypes=[("Modpacks", "*.zip *.mrpack"), ("All files", "*.*")])
        if f:
            self._load_pack(Path(f), replace=False)

    def _load_bundled_pack(self):
        pack = config.bundled_pack()
        if not pack:
            messagebox.showerror("Modsmith", f"{config.BUNDLED_PACK} isn't bundled with this copy.")
            return
        replace = True
        if self.rows:
            answer = messagebox.askyesnocancel(
                "16MARSHAL pack", "Replace the current list with the 16MARSHAL pack?\n\n"
                "Yes = replace, No = add its mods to the current list.")
            if answer is None:
                return
            replace = answer
        self._load_pack(pack, replace=replace)

    def _load_pack(self, path: Path, replace: bool):
        try:
            pack = config.import_modrinth_pack(path) if path.suffix == ".mrpack" else config.import_curseforge_pack(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Modsmith", f"Couldn't read {path.name}: {e}")
            return
        if replace:
            self._clear()
        self.settings.from_minecraft, self.settings.from_loader = pack.minecraft, pack.loader
        refs = pack.refs(bool(self.settings.cf_key))
        for r in refs:
            self._add_ref(r, name=r.hint or "")
        self._apply_filter()
        self._update_banner()
        self.status_var.set(f"Imported {len(refs)} mods from '{pack.name}' (Minecraft {pack.minecraft}, {pack.loader})")
        if pack.slugs and not self.settings.cf_key:
            self.log("No CurseForge API key: pack mods are looked up on Modrinth by name. "
                     "Add a key in Settings and re-import for exact matches.")

    def _open_config(self):
        f = filedialog.askopenfilename(title="Open modsmith.toml", filetypes=[("Modsmith list", "*.toml")])
        if not f:
            return
        try:
            s = config.load(Path(f))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Modsmith", str(e))
            return
        self.config_path = Path(f)
        self._apply_settings(s)
        self.status_var.set(f"Loaded {len(s.mods)} mods from {f}")

    def _save_config(self):
        f = filedialog.asksaveasfilename(title="Save modsmith.toml", defaultextension=".toml",
                                         initialfile="modsmith.toml")
        if not f:
            return
        self._sync_settings()
        self.settings.mods = [self.rows[i]["ref"] for i in self.order]
        config.dump(self.settings, Path(f))
        self.status_var.set(f"Saved {f}")

    def _sync_settings(self):
        self.settings.mods_dir = self.mods_var.get()
        self.settings.minecraft = self.mc_var.get().strip() if self.mc_var.get() != "loading..." else ""
        self.settings.loader = self.loader_var.get()
        if not self.settings.output_dir or self.settings.output_dir == "modsmith-out":
            self.settings.output_dir = str(Path(self.settings.mods_dir).parent / "modsmith-out")

    def _settings_dialog(self):
        SettingsDialog(self.root, self.settings)

    def _start(self, mode: str):
        if self.busy:
            return
        refs = self._selected_refs()
        if not refs:
            messagebox.showinfo("Modsmith", "Select at least one mod first.")
            return
        self._sync_settings()
        if mode == "port" and self.settings.allow_decompile:
            if not messagebox.askokcancel("Decompiling enabled", DECOMPILE_WARNING + "\n\nContinue?",
                                          icon="warning"):
                return
        if mode == "port":
            from .porting import make_agent
            ok, why = make_agent(self.settings.agent, self.settings.agent_command, self.settings.agent_model).available()
            if not ok:
                messagebox.showerror("Modsmith", why)
                return
        self.busy = True
        self.banner.set_busy(True)
        for b in (self.btn_check, self.btn_update, self.btn_port):
            b.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.configure(value=0, maximum=len(refs))
        self.status_var.set({"check": "Inspecting the metal... (checking)",
                             "install": "Hammering away... (checking and installing)",
                             "port": "Reforging... (checking and porting - this can take a long time)"}[mode])
        threading.Thread(target=self._work, args=(mode, refs), daemon=True).start()

    def _work(self, mode: str, refs: list[ModRef]):
        from .pipeline import Cancelled, Modsmith
        done = [0]

        def progress(r: ModResult):
            done[0] += 1
            self.events.put(("result", r))
            self.events.put(("progress", done[0], len(refs)))

        try:
            self.engine = Modsmith(self.settings, log=self.log)
            results, issues = self.engine.run(refs, do_port=(mode == "port"), progress=progress)
            installed = []
            if mode in ("install", "port"):
                installed = self.engine.install(results, include_feature_loss=self.settings.feature_policy != "strict")
            md = report.write(results, issues, self.engine.target(), self.engine.out_dir)
            self.results = results
            self.events.put(("done", results, issues, installed, md, None))
        except Cancelled:
            self.events.put(("done", [], [], [], None, "Cancelled"))
        except Exception as e:  # noqa: BLE001
            self.events.put(("done", [], [], [], None, f"{type(e).__name__}: {e}"))

    def _cancel(self):
        if self.engine:
            self.engine.cancel.set()
            self.status_var.set("Cancelling after the current step...")

    def _show_result(self, r: ModResult):
        iid = next((i for i, row in self.rows.items() if row["ref"] is r.ref), None)
        if iid is None:  # dependency added automatically
            iid = self._add_ref(r.ref, name=r.name)
            self._apply_filter()
        self.rows[iid]["result"] = r
        self.rows[iid]["tag"] = r.status.value
        build = ""
        if r.official and r.status in (Status.OFFICIAL, Status.OFFICIAL_FEATURE_LOSS, Status.UP_TO_DATE):
            build = f"{r.official.version_number} ({r.official.release_type})"
        elif r.output_jar:
            build = r.output_jar.name
        content = ""
        if r.feature_diff:
            content = ("all %d kept" % r.feature_diff.baseline_total if r.feature_diff.is_lossless
                       else f"{r.feature_diff.missing_count} missing of {r.feature_diff.baseline_total}")
        current = r.current.version if r.current else (r.current_remote.version_number if r.current_remote else "")
        self.tree.item(iid, values=("[x]" if self.rows[iid]["checked"] else "[ ]", r.name or r.ref.ident,
                                    self.tree.set(iid, "spec"), current, STATUS_TEXT[r.status], build, content))
        if self.tree.exists(iid) and self.tree.parent(iid) == "" and iid in self.tree.get_children(""):
            self._tag_row(iid, self.tree.index(iid))

    def _finish(self, results, issues, installed, md, error):
        self.busy = False
        self.banner.set_busy(False)
        for b in (self.btn_check, self.btn_update, self.btn_port):
            b.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        for r in results:
            self._show_result(r)
        if error:
            self.status_var.set(error)
            self.log(error)
            return
        counts = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        summary = ", ".join(f"{STATUS_TEXT[s]}: {n}" for s, n in counts.items())
        if installed:
            summary += f" | forged {len(installed)} into the mods folder"
        if issues:
            summary += f" | {len(issues)} dependency issues (see report)"
        self.status_var.set(summary)
        self.last_report = md
        for i in issues[:50]:
            self.log(f"! {i.mod} / {i.dependency}: {i.problem} {i.detail}")
        self._save_session()

    def _open_report(self):
        md = getattr(self, "last_report", None)
        if md and Path(md).exists():
            _open_path(Path(md))
        else:
            messagebox.showinfo("Modsmith", "Run a check first.")

    def _details(self, event):
        if self.tree.identify_column(event.x) == "#1":
            return
        sel = self.tree.selection()
        if not sel or not self.rows[sel[0]]["result"]:
            return
        r: ModResult = self.rows[sel[0]]["result"]
        lines = [f"{r.name}  [{STATUS_TEXT[r.status]}]", ""]
        if r.project:
            lines.append(f"Project: {r.project.provider}/{r.project.slug}  licence: {r.project.license or '?'}")
        if r.source_repo:
            lines.append(f"Source: {r.source_repo}")
        if r.output_jar:
            lines.append(f"Jar: {r.output_jar}")
        if r.manual_download:
            lines.append(f"Manual download: {r.manual_download}")
        if r.feature_diff and r.feature_diff.missing:
            lines += ["", "Missing content:"]
            for cat, ids in r.feature_diff.missing.items():
                lines.append(f"  {cat}: " + ", ".join(ids[:30]) + (" ..." if len(ids) > 30 else ""))
        if r.warnings:
            lines += ["", "Warnings:"] + [f"  - {w}" for w in r.warnings]
        if r.notes:
            lines += ["", "Notes:"] + [f"  - {n}" for n in r.notes]
        win = _toplevel(self.root, r.name or "Details")
        txt = _text_box(win, width=100, height=28, wrap="word")
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=8, pady=8)


class AddModsDialog:
    """Search Modrinth or CurseForge by name, or type ids, and add mods to the list."""

    def __init__(self, app: App):
        self.app = app
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.results: dict[str, object] = {}
        w = self.win = _toplevel(app.root, "Add mods")
        w.geometry(f"{int(820 * app.scale)}x{int(520 * app.scale)}")
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        self.query = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.query)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda _e: self._search())
        e.focus_set()
        self.source = tk.StringVar(value="Modrinth")
        ttk.Combobox(top, textvariable=self.source, values=["Modrinth", "CurseForge"], width=11,
                     state="readonly").pack(side="left")
        self.only_loader = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text=f"Only {app.loader_var.get()} mods", variable=self.only_loader).pack(side="left", padx=8)
        ttk.Button(top, text="Search", style="Forge.TButton", command=self._search).pack(side="left")

        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, pady=8)
        cols = ("name", "source", "id", "loaders", "target")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        mc = app.mc_var.get()
        for c, text, width in (("name", "Mod", 260), ("source", "Source", 90), ("id", "Id", 170),
                               ("loaders", "Loaders", 150), ("target", f"Has {mc}?", 80)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=int(width * app.scale), stretch=c == "name",
                             anchor="center" if c == "target" else "w")
        self.tree.tag_configure("yes", foreground=STATUS_COLOURS[Status.UP_TO_DATE])
        ysb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._add_selected())

        btns = ttk.Frame(f)
        btns.pack(fill="x")
        ttk.Button(btns, text="Add selected", style="Forge.TButton", command=self._add_selected).pack(side="left")
        ttk.Button(btns, text="Close", command=w.destroy).pack(side="right")

        ids = ttk.LabelFrame(f, text=" Or add by id ", padding=8)
        ids.pack(fill="x", pady=(10, 0))
        self.ids = tk.StringVar()
        ie = ttk.Entry(ids, textvariable=self.ids)
        ie.pack(side="left", fill="x", expand=True)
        ie.bind("<Return>", lambda _e: self._add_ids())
        ttk.Button(ids, text="Add", command=self._add_ids).pack(side="left", padx=(6, 0))
        ttk.Label(f, text="Examples:  modrinth:jei   curseforge:238222   sodium   (comma-separated for several)",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.status = tk.StringVar(value="Type a mod name and press Enter.")
        ttk.Label(f, textvariable=self.status, style="Ember.TLabel").pack(anchor="w", pady=(6, 0))

    def _search(self):
        text = self.query.get().strip()
        if not text:
            return
        source = self.source.get()
        if source == "CurseForge" and not self.app.settings.cf_key:
            self.status.set("CurseForge search needs an API key: add one in Settings (free at console.curseforge.com).")
            return
        loader = self.app.loader_var.get() if self.only_loader.get() else None
        self.status.set(f"Searching {source}...")
        threading.Thread(target=self._search_worker, args=(source, text, loader), daemon=True).start()
        self.win.after(100, self._poll)

    def _search_worker(self, source: str, text: str, loader):
        try:
            from .http import Http
            if source == "CurseForge":
                from .providers.curseforge import CurseForge
                provider = CurseForge(Http(), self.app.settings.cf_key)
            else:
                from .providers.modrinth import Modrinth
                provider = Modrinth(Http())
            self.q.put(("ok", provider.search(text, loader=loader, limit=25)))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"Search failed: {e}"))

    def _poll(self):
        try:
            kind, payload = self.q.get_nowait()
        except queue.Empty:
            if self.win.winfo_exists():
                self.win.after(100, self._poll)
            return
        if not self.win.winfo_exists():
            return
        if kind == "error":
            self.status.set(payload)
            return
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.results.clear()
        mc = self.app.mc_var.get()
        for p in payload:
            has = mc in (p.game_versions or [])
            iid = self.tree.insert("", "end", values=(p.name, p.provider.capitalize(), p.slug,
                                                      ", ".join(p.loaders), "yes" if has else ""),
                                   tags=("yes",) if has else ())
            self.results[iid] = p
        self.status.set(f"{len(payload)} results. Select one or more and press 'Add selected'."
                        if payload else "Nothing found. Try another name, or untick the loader filter.")

    def _add_selected(self):
        n = 0
        for iid in self.tree.selection():
            p = self.results.get(iid)
            if not p:
                continue
            ident = p.project_id if p.provider == "curseforge" else p.slug
            self.app._add_ref(ModRef(spec=f"{p.provider}:{ident}", hint=p.name), name=p.name)
            n += 1
        self.app._apply_filter()
        if n:
            self.status.set(f"Added {n} mod{'s' if n != 1 else ''} to the workbench.")

    def _add_ids(self):
        specs = [s.strip() for s in self.ids.get().replace(",", "\n").splitlines() if s.strip()]
        for spec in specs:
            self.app._add_ref(ModRef(spec=spec))
        self.app._apply_filter()
        if specs:
            self.ids.set("")
            self.status.set(f"Added {len(specs)} id{'s' if len(specs) != 1 else ''}.")


class SettingsDialog:
    def __init__(self, parent, s: Settings):
        self.s = s
        w = self.win = _toplevel(parent, "Modsmith settings")
        f = ttk.Frame(w, padding=14)
        f.pack(fill="both", expand=True)
        self.vars = {}
        row = 0

        def check(name, label):
            nonlocal row
            v = tk.BooleanVar(value=getattr(s, name))
            ttk.Checkbutton(f, text=label, variable=v).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
            self.vars[name] = v
            row += 1

        def entry(name, label, show=None, width=50):
            nonlocal row
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=str(getattr(s, name)))
            ttk.Entry(f, textvariable=v, show=show, width=width).grid(row=row, column=1, sticky="ew", pady=2)
            self.vars[name] = v
            row += 1

        def heading(text):
            nonlocal row
            ttk.Label(f, text=text, style="Ember.TLabel").grid(row=row, column=0, columnspan=2, sticky="w",
                                                               pady=(10 if row else 0, 4))
            row += 1

        heading("Checking & installing")
        check("allow_prerelease", "Accept beta/alpha official builds")
        check("verify_features", "Download builds and compare content with what you have")
        check("auto_add_dependencies", "Automatically add missing required dependencies")
        ttk.Label(f, text="When an official build is missing content").grid(row=row, column=0, sticky="w")
        pv = tk.StringVar(value=s.feature_policy)
        ttk.Combobox(f, textvariable=pv, values=list(FEATURE_POLICIES), state="readonly").grid(row=row, column=1, sticky="w")
        self.vars["feature_policy"] = pv
        row += 1
        entry("curseforge_api_key", "CurseForge API key (console.curseforge.com)", show="•")
        entry("output_dir", "Output folder (reports, ported jars)")
        heading("Force porting")
        check("allow_port", "Force-port mods with no official build (needs an AI coding agent + JDK)")
        check("allow_decompile", "Allow decompiling mods with no source code (personal use only!)")
        ttk.Label(f, text="Porting agent").grid(row=row, column=0, sticky="w")
        av = tk.StringVar(value=s.agent)
        ttk.Combobox(f, textvariable=av, values=["claude", "command"], state="readonly").grid(row=row, column=1, sticky="w")
        self.vars["agent"] = av
        row += 1
        entry("agent_model", "Agent model (optional)")
        entry("agent_command", "Custom agent command ({prompt_file}, {workdir})")
        entry("max_port_attempts", "Max agent passes per mod", width=8)
        heading("JDK paths (blank = auto-detect)")
        self.java = {}
        for major in ("17", "21", "25"):
            ttk.Label(f, text=f"  Java {major}").grid(row=row, column=0, sticky="w")
            v = tk.StringVar(value=s.java_home.get(major, ""))
            ttk.Entry(f, textvariable=v, width=50).grid(row=row, column=1, sticky="ew")
            self.java[major] = v
            row += 1
        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", style="Forge.TButton", command=self._save).pack(side="right")
        f.columnconfigure(1, weight=1)

    def _save(self):
        for name, v in self.vars.items():
            val = v.get()
            if name == "max_port_attempts":
                try:
                    val = max(1, int(val))
                except ValueError:
                    val = 6
            setattr(self.s, name, val)
        if self.s.allow_decompile:
            messagebox.showwarning("Decompiling", DECOMPILE_WARNING, parent=self.win)
        self.s.java_home = {k: v.get().strip() for k, v in self.java.items() if v.get().strip()}
        self.win.destroy()


def _open_path(p: Path):
    if not p.exists():
        return
    if sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        try:
            subprocess.Popen(["xdg-open", str(p)])
        except FileNotFoundError:
            webbrowser.open(p.as_uri())


def main() -> int:
    if tk is None:
        print("Tkinter is not available. On Linux install python3-tk; or use the command line: modsmith --help")
        return 1
    if sys.platform.startswith("win"):
        try:  # crisp text on high-DPI screens, and our own taskbar icon
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Modsmith.ModForge")
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
