"""Read mod metadata from jar files.

Supports Forge ``META-INF/mods.toml``, NeoForge ``META-INF/neoforge.mods.toml``,
Fabric ``fabric.mod.json`` and Quilt ``quilt.mod.json``.
"""
from __future__ import annotations

import json
import re
import tomllib
import zipfile
from pathlib import Path
from typing import Iterable, Optional

from .fingerprint import file_hashes
from .models import Dependency, DependencyKind, JarInfo, Loader

_PLATFORM_IDS = {"minecraft", "forge", "neoforge", "fabricloader", "fabric-loader",
                 "quilt_loader", "java", "fml", "javafml"}


class NotAModError(ValueError):
    pass


def _read_toml(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # Some mods ship slightly invalid TOML (e.g. unquoted ${file.jarVersion}).
        fixed = re.sub(r'=\s*\$\{([^}]+)\}', r'= "${\1}"', text)
        return tomllib.loads(fixed)


def _manifest_version(zf: zipfile.ZipFile) -> Optional[str]:
    try:
        manifest = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except KeyError:
        return None
    m = re.search(r"^Implementation-Version:\s*(\S+)", manifest, re.M)
    return m.group(1) if m else None


def _forge_like(zf: zipfile.ZipFile, name: str, loader: Loader, path: Path) -> JarInfo:
    data = _read_toml(zf.read(name).decode("utf-8", "replace"))
    mods = data.get("mods") or [{}]
    main = mods[0]
    mod_id = main.get("modId", path.stem)
    version = str(main.get("version", ""))
    if "${" in version or not version:
        version = _manifest_version(zf) or version
    deps_table = data.get("dependencies", {}) or {}
    raw_deps = deps_table.get(mod_id, []) if isinstance(deps_table, dict) else []
    deps: list[Dependency] = []
    mc_range = loader_range = "*"
    for d in raw_deps:
        dep_id = d.get("modId", "")
        rng = str(d.get("versionRange", "*") or "*")
        if dep_id == "minecraft":
            mc_range = rng
            continue
        if dep_id in ("forge", "neoforge"):
            loader_range = rng
            continue
        dep_type = str(d.get("type", "")).lower()
        if dep_type:
            kind = {
                "required": DependencyKind.REQUIRED,
                "optional": DependencyKind.OPTIONAL,
                "incompatible": DependencyKind.INCOMPATIBLE,
                "discouraged": DependencyKind.OPTIONAL,
            }.get(dep_type, DependencyKind.OPTIONAL)
        else:
            kind = DependencyKind.REQUIRED if d.get("mandatory", False) else DependencyKind.OPTIONAL
        deps.append(Dependency(mod_id=dep_id, kind=kind, version_range=rng))
    return JarInfo(
        path=path,
        mod_id=mod_id,
        name=main.get("displayName", mod_id),
        version=version,
        loader=loader,
        minecraft_range=mc_range,
        loader_range=loader_range,
        dependencies=deps,
        extra_mod_ids=[m.get("modId") for m in mods[1:] if m.get("modId")],
        homepage=main.get("displayURL"),
        source_url=None,
        license=data.get("license"),
    )


def _fabric_like(zf: zipfile.ZipFile, name: str, path: Path) -> JarInfo:
    data = json.loads(zf.read(name).decode("utf-8", "replace"))
    if name == "quilt.mod.json":
        ql = data.get("quilt_loader", {})
        meta = ql.get("metadata", {})
        deps_raw = {}
        for d in ql.get("depends", []):
            if isinstance(d, str):
                deps_raw[d] = "*"
            else:
                deps_raw[d.get("id")] = d.get("versions", "*")
        mod_id, version = ql.get("id", path.stem), ql.get("version", "")
        display = meta.get("name", mod_id)
        contact = meta.get("contact", {})
        lic = meta.get("license")
        loader = Loader.QUILT
        recommends, breaks = {}, {}
    else:
        deps_raw = data.get("depends", {}) or {}
        recommends = {**(data.get("recommends", {}) or {}), **(data.get("suggests", {}) or {})}
        breaks = data.get("breaks", {}) or {}
        mod_id, version = data.get("id", path.stem), data.get("version", "")
        display = data.get("name", mod_id)
        contact = data.get("contact", {}) or {}
        lic = data.get("license")
        loader = Loader.FABRIC
    if isinstance(lic, list):
        lic = " OR ".join(str(x) for x in lic)
    deps, mc_range, loader_range = [], "*", "*"
    for dep_id, rng in deps_raw.items():
        if dep_id == "minecraft":
            mc_range = rng if isinstance(rng, str) else " || ".join(rng)
        elif dep_id in ("fabricloader", "quilt_loader"):
            loader_range = rng if isinstance(rng, str) else " || ".join(rng)
        elif dep_id not in _PLATFORM_IDS:
            deps.append(Dependency(dep_id, DependencyKind.REQUIRED,
                                   rng if isinstance(rng, str) else " || ".join(rng)))
    for dep_id, rng in recommends.items():
        deps.append(Dependency(dep_id, DependencyKind.OPTIONAL, rng if isinstance(rng, str) else "*"))
    for dep_id, rng in breaks.items():
        deps.append(Dependency(dep_id, DependencyKind.INCOMPATIBLE, rng if isinstance(rng, str) else "*"))
    if "${" in str(version):
        version = _manifest_version(zf) or version
    return JarInfo(
        path=path,
        mod_id=mod_id,
        name=display,
        version=str(version),
        loader=loader,
        minecraft_range=mc_range,
        loader_range=loader_range,
        dependencies=deps,
        homepage=contact.get("homepage"),
        source_url=contact.get("sources"),
        license=lic,
    )


def read_jar(path: Path, with_hashes: bool = True) -> JarInfo:
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise NotAModError(f"{path.name} is not a valid jar") from e
    with zf:
        names = set(zf.namelist())
        if "META-INF/neoforge.mods.toml" in names:
            info = _forge_like(zf, "META-INF/neoforge.mods.toml", Loader.NEOFORGE, path)
        elif "META-INF/mods.toml" in names:
            info = _forge_like(zf, "META-INF/mods.toml", Loader.FORGE, path)
        elif "fabric.mod.json" in names:
            info = _fabric_like(zf, "fabric.mod.json", path)
        elif "quilt.mod.json" in names:
            info = _fabric_like(zf, "quilt.mod.json", path)
        else:
            raise NotAModError(f"{path.name} has no recognised mod metadata")
    if with_hashes:
        info.sha1, info.sha512, info.cf_fingerprint = file_hashes(path)
    return info


def scan_folder(folder: Path, on_error=None) -> list[JarInfo]:
    """Read every ``.jar`` directly inside ``folder`` (not recursive)."""
    out = []
    for jar in sorted(Path(folder).glob("*.jar")):
        try:
            out.append(read_jar(jar))
        except Exception as e:  # noqa: BLE001 - report and continue
            if on_error:
                on_error(jar, e)
    return out


def provided_ids(jars: Iterable[JarInfo]) -> set[str]:
    ids: set[str] = set()
    for j in jars:
        ids.add(j.mod_id)
        ids.update(j.extra_mod_ids)
    return ids
