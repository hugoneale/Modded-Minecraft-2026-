"""Modsmith desktop app (Tkinter - ships with Python on Windows and macOS;
on Linux install your distro's ``python3-tk`` package)."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError:  # pragma: no cover
    tk = None

from . import __version__, config, report
from .config import FEATURE_POLICIES, Settings
from .jarscan import read_jar
from .models import ModRef, ModResult, Status
from .porting.decompile import DECOMPILE_WARNING
from .report import STATUS_TEXT

LOADERS = ["neoforge", "forge", "fabric", "quilt"]
STATUS_COLOURS = {
    Status.UP_TO_DATE: "#2e7d32", Status.OFFICIAL: "#1565c0", Status.PORTED: "#6a1b9a",
    Status.PORTED_DECOMPILED: "#8e24aa", Status.OFFICIAL_FEATURE_LOSS: "#ef6c00",
    Status.NO_OFFICIAL: "#ef6c00", Status.PORT_FAILED: "#c62828", Status.NO_SOURCE: "#c62828",
    Status.UNRESOLVED: "#757575", Status.SKIPPED: "#757575",
}


class App:
    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.settings = Settings()
        self.config_path: Path | None = None
        self.rows: dict[str, dict] = {}          # tree item id -> {"ref", "result", "checked"}
        self.results: list[ModResult] = []
        self.engine = None
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False

        root.title(f"Modsmith {__version__}")
        root.geometry("1180x760")
        root.minsize(900, 560)
        self._build()
        self.root.after(100, self._pump)
        threading.Thread(target=self._load_versions, daemon=True).start()

    # ------------------------------------------------------------------ layout
    def _build(self):
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Mods folder").grid(row=0, column=0, sticky="w")
        self.mods_var = tk.StringVar(value=str(Path.cwd() / "mods"))
        ttk.Entry(top, textvariable=self.mods_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="Browse...", command=self._browse_mods).grid(row=0, column=2)
        ttk.Label(top, text="Minecraft").grid(row=0, column=3, sticky="e", padx=(16, 4))
        self.mc_var = tk.StringVar(value="loading...")
        self.mc_box = ttk.Combobox(top, textvariable=self.mc_var, width=14)
        self.mc_box.grid(row=0, column=4)
        ttk.Label(top, text="Loader").grid(row=0, column=5, sticky="e", padx=(12, 4))
        self.loader_var = tk.StringVar(value="neoforge")
        ttk.Combobox(top, textvariable=self.loader_var, values=LOADERS, width=10, state="readonly").grid(row=0, column=6)
        ttk.Button(top, text="Settings...", command=self._settings_dialog).grid(row=0, column=7, padx=(12, 0))
        top.columnconfigure(1, weight=1)

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", **pad)
        for text, cmd in (("Scan folder", self._scan), ("Add by ID...", self._add_ids),
                          ("Import pack...", self._import_pack), ("Open config...", self._open_config),
                          ("Save config...", self._save_config), ("Select all", lambda: self._check_all(True)),
                          ("Select none", lambda: self._check_all(False)), ("Remove", self._remove)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)

        mid = ttk.PanedWindow(self.root, orient="vertical")
        mid.pack(fill="both", expand=True, **pad)
        tree_frame = ttk.Frame(mid)
        cols = ("use", "name", "spec", "current", "status", "build", "content")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        for c, text, w in (("use", "Use", 40), ("name", "Mod", 220), ("spec", "Id", 170),
                           ("current", "Current", 120), ("status", "Status", 190),
                           ("build", "Build for target", 200), ("content", "Content check", 260)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="center" if c == "use" else "w", stretch=c in ("name", "content"))
        for st, colour in STATUS_COLOURS.items():
            self.tree.tag_configure(st.value, foreground=colour)
        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._details)
        mid.add(tree_frame, weight=3)

        log_frame = ttk.Frame(mid)
        self.log_box = ScrolledText(log_frame, height=10, state="disabled", font=("TkFixedFont", 9))
        self.log_box.pack(fill="both", expand=True)
        mid.add(log_frame, weight=1)

        actions = ttk.Frame(self.root)
        actions.pack(fill="x", **pad)
        self.btn_check = ttk.Button(actions, text="1. Check", command=lambda: self._start("check"))
        self.btn_update = ttk.Button(actions, text="2. Install / Update", command=lambda: self._start("install"))
        self.btn_port = ttk.Button(actions, text="3. Force port missing", command=lambda: self._start("port"))
        self.btn_cancel = ttk.Button(actions, text="Cancel", command=self._cancel, state="disabled")
        for b in (self.btn_check, self.btn_update, self.btn_port, self.btn_cancel):
            b.pack(side="left", padx=3)
        ttk.Button(actions, text="Open report", command=self._open_report).pack(side="left", padx=3)
        ttk.Button(actions, text="Open mods folder", command=lambda: _open_path(Path(self.mods_var.get()))).pack(side="left", padx=3)
        self.progress = ttk.Progressbar(actions, mode="determinate", length=220)
        self.progress.pack(side="right", padx=4)
        self.status_var = tk.StringVar(value="Add mods with 'Scan folder', 'Add by ID' or 'Import pack'.")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").pack(fill="x", padx=8, pady=(0, 6))

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
        iid = self.tree.insert("", "end", values=("☑" if ref.enabled else "☐", name or ref.ident,
                                                  ref.spec if not ref.spec.startswith("file:") else Path(ref.ident).name,
                                                  current, "", "", ""))
        self.rows[iid] = {"ref": ref, "result": None, "checked": ref.enabled}
        return iid

    def _selected_refs(self) -> list[ModRef]:
        out = []
        for row in self.rows.values():
            if row["checked"]:
                row["ref"].enabled = True
                out.append(row["ref"])
        return out

    def _on_click(self, event):
        if self.tree.identify_column(event.x) != "#1" or self.tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        if iid in self.rows:
            row = self.rows[iid]
            row["checked"] = not row["checked"]
            self.tree.set(iid, "use", "☑" if row["checked"] else "☐")

    def _check_all(self, value: bool):
        for iid, row in self.rows.items():
            row["checked"] = value
            self.tree.set(iid, "use", "☑" if value else "☐")

    def _remove(self):
        for iid in self.tree.selection():
            self.rows.pop(iid, None)
            self.tree.delete(iid)

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
        self.status_var.set(f"Found {n} mods in {folder}")

    def _add_ids(self):
        text = simpledialog.askstring(
            "Add mods", "Mod ids, one per line or comma-separated.\n"
            "Examples: modrinth:jei   curseforge:238222   sodium", parent=self.root)
        if not text:
            return
        for spec in [s.strip() for s in text.replace(",", "\n").splitlines() if s.strip()]:
            self._add_ref(ModRef(spec=spec))

    def _import_pack(self):
        f = filedialog.askopenfilename(title="Import modpack",
                                       filetypes=[("Modpacks", "*.zip *.mrpack"), ("All files", "*.*")])
        if not f:
            return
        path = Path(f)
        try:
            pack = config.import_modrinth_pack(path) if path.suffix == ".mrpack" else config.import_curseforge_pack(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Modsmith", f"Couldn't read {path.name}: {e}")
            return
        self.settings.from_minecraft, self.settings.from_loader = pack.minecraft, pack.loader
        refs = pack.refs(bool(self.settings.cf_key))
        for r in refs:
            self._add_ref(r)
        self.status_var.set(f"Imported {len(refs)} mods from '{pack.name}' (Minecraft {pack.minecraft}, {pack.loader})")
        if pack.slugs and not self.settings.cf_key:
            self.log("No CurseForge API key: pack mods are looked up on Modrinth by name. "
                     "Add a key in Settings and re-import for exact matches.")

    def _open_config(self):
        f = filedialog.askopenfilename(title="Open modsmith.toml", filetypes=[("Modsmith config", "*.toml")])
        if not f:
            return
        try:
            self.settings = config.load(Path(f))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Modsmith", str(e))
            return
        self.config_path = Path(f)
        self.mods_var.set(self.settings.mods_dir)
        if self.settings.minecraft:
            self.mc_var.set(self.settings.minecraft)
        if self.settings.loader:
            self.loader_var.set(self.settings.loader)
        for iid in list(self.rows):
            self.tree.delete(iid)
        self.rows.clear()
        for r in self.settings.mods:
            self._add_ref(r)
        self.status_var.set(f"Loaded {len(self.settings.mods)} mods from {f}")

    def _save_config(self):
        f = filedialog.asksaveasfilename(title="Save modsmith.toml", defaultextension=".toml",
                                         initialfile="modsmith.toml")
        if not f:
            return
        self._sync_settings()
        self.settings.mods = [row["ref"] for row in self.rows.values()]
        for row in self.rows.values():
            row["ref"].enabled = row["checked"]
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
        for b in (self.btn_check, self.btn_update, self.btn_port):
            b.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress.configure(value=0, maximum=len(refs))
        self.status_var.set({"check": "Checking...", "install": "Checking and installing...",
                             "port": "Checking and porting (this can take a long time)..."}[mode])
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
        self.rows[iid]["result"] = r
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
        self.tree.item(iid, values=("☑" if self.rows[iid]["checked"] else "☐", r.name or r.ref.ident,
                                    self.tree.set(iid, "spec"), current, STATUS_TEXT[r.status], build, content),
                       tags=(r.status.value,))

    def _finish(self, results, issues, installed, md, error):
        self.busy = False
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
            summary += f" | installed {len(installed)}"
        if issues:
            summary += f" | {len(issues)} dependency issues (see report)"
        self.status_var.set(summary)
        self.last_report = md
        for i in issues[:50]:
            self.log(f"! {i.mod} / {i.dependency}: {i.problem} {i.detail}")

    def _open_report(self):
        md = getattr(self, "last_report", None)
        if md and Path(md).exists():
            _open_path(Path(md))
        else:
            messagebox.showinfo("Modsmith", "Run a check first.")

    def _details(self, _event):
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
        win = tk.Toplevel(self.root)
        win.title(r.name or "Details")
        txt = ScrolledText(win, width=100, height=28, wrap="word")
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)


class SettingsDialog:
    def __init__(self, parent, s: Settings):
        self.s = s
        w = self.win = tk.Toplevel(parent)
        w.title("Modsmith settings")
        w.transient(parent)
        f = ttk.Frame(w, padding=12)
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

        check("allow_prerelease", "Accept beta/alpha official builds")
        check("verify_features", "Download builds and compare content with what you have")
        check("auto_add_dependencies", "Automatically add missing required dependencies")
        check("allow_port", "Force-port mods with no official build (needs an AI coding agent + JDK)")
        check("allow_decompile", "Allow decompiling mods with no source code (personal use only!)")
        ttk.Label(f, text="When an official build is missing content").grid(row=row, column=0, sticky="w")
        pv = tk.StringVar(value=s.feature_policy)
        ttk.Combobox(f, textvariable=pv, values=list(FEATURE_POLICIES), state="readonly").grid(row=row, column=1, sticky="w")
        self.vars["feature_policy"] = pv
        row += 1
        ttk.Label(f, text="Porting agent").grid(row=row, column=0, sticky="w")
        av = tk.StringVar(value=s.agent)
        ttk.Combobox(f, textvariable=av, values=["claude", "command"], state="readonly").grid(row=row, column=1, sticky="w")
        self.vars["agent"] = av
        row += 1
        entry("agent_model", "Agent model (optional)")
        entry("agent_command", "Custom agent command ({prompt_file}, {workdir})")
        entry("max_port_attempts", "Max agent passes per mod", width=8)
        entry("curseforge_api_key", "CurseForge API key (console.curseforge.com)", show="•")
        entry("output_dir", "Output folder (reports, ported jars)")
        ttk.Label(f, text="JDK paths (blank = auto-detect)").grid(row=row, column=0, sticky="w", pady=(8, 2))
        row += 1
        self.java = {}
        for major in ("17", "21", "25"):
            ttk.Label(f, text=f"  Java {major}").grid(row=row, column=0, sticky="w")
            v = tk.StringVar(value=s.java_home.get(major, ""))
            ttk.Entry(f, textvariable=v, width=50).grid(row=row, column=1, sticky="ew")
            self.java[major] = v
            row += 1
        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side="right")
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
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam" if sys.platform.startswith("linux") else ttk.Style().theme_use())
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0
