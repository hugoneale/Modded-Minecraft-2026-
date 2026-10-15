"""Modsmith project file (``modsmith.toml``) and modpack imports.

Example::

    minecraft = "26.3"
    loader = "neoforge"
    mods_dir = "mods"

    mods = [
      "modrinth:jei",
      "curseforge:238222",
      { spec = "modrinth:create", version = "Nvb3Udyh" },
      { spec = "file:mods/old-mod-1.20.1.jar" },
    ]
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import tomllib
import zipfile
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from .models import ModRef

FEATURE_POLICIES = ("warn", "prefer-port", "strict")
BUNDLED_PACK = "16MARSHALST.zip"


def bundled_pack() -> Optional[Path]:
    """The modpack shipped with the app: inside the PyInstaller bundle, or at
    the repository root when running from source."""
    here = Path(__file__).resolve().parents
    roots = [Path(sys._MEIPASS)] if getattr(sys, "_MEIPASS", None) else []  # type: ignore[attr-defined]
    if len(here) > 3:
        roots.append(here[3])  # modsmith/src/modsmith/config.py -> repository root
    return next((r / BUNDLED_PACK for r in roots if (r / BUNDLED_PACK).is_file()), None)


def user_data_dir() -> Path:
    """Where the desktop app remembers its last session."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "modsmith"


def minecraft_dir() -> Path:
    """The vanilla launcher's game folder for this OS."""
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / ".minecraft"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


def default_mods_dir() -> Path:
    mc = minecraft_dir()
    return mc / "mods" if mc.is_dir() else Path.home() / "Modsmith" / "mods"


@dataclass
class Settings:
    minecraft: str = ""                 # target version; empty = latest release
    loader: str = ""                    # target loader; empty = default for that version
    from_minecraft: str = ""            # version the selected mods are currently on (for id-only selections)
    from_loader: str = ""               # loader they're currently on
    verify_features: bool = True        # download builds and compare their content
    mods_dir: str = "mods"
    output_dir: str = "modsmith-out"
    allow_prerelease: bool = True       # accept beta/alpha official builds
    auto_add_dependencies: bool = True  # pull in missing required dependencies
    feature_policy: str = "warn"        # what to do when an official build drops content
    allow_port: bool = True             # rebuild from source when no official build exists
    allow_decompile: bool = False       # fall back to decompiling closed-source jars
    agent: str = "claude"               # porting agent backend: claude | command
    agent_command: str = ""             # for agent = "command"; {prompt_file} and {workdir} are substituted
    agent_model: str = ""               # optional model override for the claude backend
    max_port_attempts: int = 6
    build_timeout: int = 1800
    java_home: dict = field(default_factory=dict)  # {"21": "/path/to/jdk21", "25": "..."}
    curseforge_api_key: str = ""
    mods: list[ModRef] = field(default_factory=list)

    @property
    def cf_key(self) -> Optional[str]:
        return self.curseforge_api_key or os.environ.get("CURSEFORGE_API_KEY") or None

    def validate(self) -> None:
        if self.feature_policy not in FEATURE_POLICIES:
            raise ValueError(f"feature_policy must be one of {FEATURE_POLICIES}")


def _ref(entry) -> ModRef:
    if isinstance(entry, str):
        return ModRef(spec=entry)
    if isinstance(entry, dict):
        return ModRef(spec=entry["spec"], pinned_version=entry.get("version"),
                      enabled=entry.get("enabled", True), hint=entry.get("name"))
    raise ValueError(f"Bad mods entry: {entry!r}")


def load(path: Path) -> Settings:
    data = tomllib.loads(Path(path).read_text())
    s = Settings()
    names = {f.name for f in fields(Settings)}
    for k, v in data.items():
        if k == "mods":
            s.mods = [_ref(e) for e in v]
        elif k in names:
            setattr(s, k, v)
    # Relative paths in the file are relative to the file.
    base = Path(path).resolve().parent
    for attr in ("mods_dir", "output_dir"):
        p = Path(getattr(s, attr))
        if not p.is_absolute():
            setattr(s, attr, str(base / p))
    for r in s.mods:
        if r.spec.startswith("file:"):
            p = Path(r.ident)
            if not p.is_absolute():
                r.spec = f"file:{base / p}"
    s.validate()
    return s


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(str(k))} = {_toml_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    # JSON escaping is valid TOML, except surrogate pairs, so keep non-ASCII as-is.
    return json.dumps(str(v), ensure_ascii=False)


def dump(s: Settings, path: Path) -> None:
    lines = ["# Modsmith project file. See modsmith/README.md for every option.", ""]
    for f in fields(Settings):
        if f.name == "mods":
            continue
        v = getattr(s, f.name)
        if f.name == "curseforge_api_key" and v:
            v = ""  # never write secrets back to disk
        lines.append(f"{f.name} = {_toml_value(v)}")
    lines += ["", "mods = ["]
    for r in s.mods:
        if r.pinned_version or not r.enabled or r.hint:
            entry = {"spec": r.spec}
            if r.hint:
                entry["name"] = r.hint
            if r.pinned_version:
                entry["version"] = r.pinned_version
            if not r.enabled:
                entry["enabled"] = False
            lines.append(f"  {_toml_value(entry)},")
        else:
            lines.append(f"  {_toml_value(r.spec)},")
    lines.append("]")
    Path(path).write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------- imports

@dataclass
class PackImport:
    name: str
    minecraft: str
    loader: str
    loader_version: str
    mods: list[ModRef]
    overrides: Optional[str]  # name of overrides folder inside the zip
    # CurseForge slugs from modlist.html. Used to look mods up on Modrinth when
    # no CurseForge API key is configured (project ids need the API).
    slugs: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)  # slug -> display name

    def refs(self, have_curseforge_key: bool) -> list[ModRef]:
        if have_curseforge_key or not self.slugs:
            return self.mods
        return [ModRef(spec=s, hint=self.names.get(s)) for s in self.slugs]


def import_curseforge_pack(path: Path) -> PackImport:
    """Read a CurseForge modpack zip (or bare manifest.json)."""
    path = Path(path)
    modlist = ""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            if "modlist.html" in zf.namelist():
                modlist = zf.read("modlist.html").decode("utf-8", "replace")
    else:
        manifest = json.loads(path.read_text())
        side = path.with_name("modlist.html")
        if side.exists():
            modlist = side.read_text(errors="replace")
    entries = re.findall(r'curseforge\.com/minecraft/mc-mods/([\w-]+)"?[^>]*>([^<]*)<', modlist)
    slugs = [slug for slug, _ in entries]
    names = {slug: re.sub(r"\s*\(by [^)]*\)\s*$", "", html.unescape(text)).strip() for slug, text in entries}
    mc = manifest["minecraft"]
    primary = next((l for l in mc.get("modLoaders", []) if l.get("primary")),
                   (mc.get("modLoaders") or [{"id": ""}])[0])
    loader, _, lv = primary.get("id", "").partition("-")
    mods = [ModRef(spec=f"curseforge:{f['projectID']}", pinned_version=str(f["fileID"]),
                   enabled=f.get("required", True))
            for f in manifest.get("files", [])]
    return PackImport(name=manifest.get("name", path.stem), minecraft=mc["version"],
                      loader=loader, loader_version=lv, mods=mods,
                      overrides=manifest.get("overrides"), slugs=slugs, names=names)


def import_modrinth_pack(path: Path) -> PackImport:
    """Read a Modrinth ``.mrpack``."""
    with zipfile.ZipFile(path) as zf:
        index = json.loads(zf.read("modrinth.index.json"))
    deps = index.get("dependencies", {})
    loader = next((k.replace("-loader", "") for k in deps if k != "minecraft"), "")
    mods = []
    for f in index.get("files", []):
        if not f["path"].startswith("mods/"):
            continue
        url = (f.get("downloads") or [""])[0]
        # https://cdn.modrinth.com/data/<project>/versions/<version>/<file>
        parts = url.split("/")
        if "cdn.modrinth.com" in url and len(parts) > 7:
            mods.append(ModRef(spec=f"modrinth:{parts[4]}", pinned_version=parts[6]))
    return PackImport(name=index.get("name", Path(path).stem), minecraft=deps.get("minecraft", ""),
                      loader=loader, loader_version=deps.get(f"{loader}-loader", deps.get(loader, "")),
                      mods=mods, overrides="overrides")
