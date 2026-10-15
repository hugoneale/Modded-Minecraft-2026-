"""Dependency and compatibility checks.

Two levels:

* ``check_jars`` works offline on jar metadata (mod ids + version ranges), so
  it catches what the game itself would refuse to load.
* ``check_remote`` works on provider metadata (project ids) for builds that
  haven't been downloaded yet, so missing dependencies can be added before
  anything is installed.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .models import DependencyIssue, DependencyKind, JarInfo, RemoteFile
from .versions import Version, loader_aliases, parse_range

PLATFORM_IDS = {"minecraft", "forge", "neoforge", "fabricloader", "fabric-loader", "quilt_loader",
                "java", "fml", "javafml", "mixinextras"}
# Ids provided by the loader itself or by ubiquitous bundled libraries.
BUILTIN_PROVIDED = {
    "fabric": {"fabricloader", "java", "minecraft"},
    "quilt": {"quilt_loader", "fabricloader", "java", "minecraft"},
    "forge": {"forge", "minecraft", "java"},
    "neoforge": {"neoforge", "minecraft", "java"},
}


def check_jars(jars: Iterable[JarInfo], minecraft: str, loader: str) -> list[DependencyIssue]:
    jars = list(jars)
    provided: dict[str, str] = {}
    for j in jars:
        provided[j.mod_id] = j.version
        for extra in j.extra_mod_ids:
            provided.setdefault(extra, j.version)
    builtin = BUILTIN_PROVIDED.get(loader, set())
    ok_loaders = set(loader_aliases(loader, minecraft))
    issues: list[DependencyIssue] = []
    for j in jars:
        if j.loader and j.loader.value not in ok_loaders:
            issues.append(DependencyIssue(j.mod_id, j.loader.value, "wrong-loader",
                                          f"{j.name} is a {j.loader.value} mod; target is {loader}"))
        if j.minecraft_range not in ("", "*") and not _safe_contains(j.minecraft_range, minecraft):
            issues.append(DependencyIssue(j.mod_id, "minecraft", "wrong-minecraft",
                                          f"{j.name} {j.version} supports Minecraft {j.minecraft_range}, not {minecraft}"))
        for d in j.dependencies:
            if d.mod_id in PLATFORM_IDS or d.mod_id in builtin:
                continue
            have = provided.get(d.mod_id)
            if d.kind == DependencyKind.INCOMPATIBLE:
                if have is not None and _safe_contains(d.version_range, have):
                    issues.append(DependencyIssue(j.mod_id, d.mod_id, "incompatible",
                                                  f"{j.name} is incompatible with {d.mod_id} {have}"))
                continue
            if d.kind != DependencyKind.REQUIRED:
                continue
            if have is None:
                issues.append(DependencyIssue(j.mod_id, d.mod_id, "missing",
                                              f"{j.name} requires {d.mod_id} {d.version_range}".strip(),
                                              suggestion=d.mod_id))
            elif d.version_range not in ("", "*") and not _safe_contains(d.version_range, have):
                issues.append(DependencyIssue(j.mod_id, d.mod_id, "version-mismatch",
                                              f"{j.name} needs {d.mod_id} {d.version_range}, found {have}"))
    return issues


def _safe_contains(rng: str, version: str) -> bool:
    try:
        return parse_range(rng).contains(Version(version))
    except Exception:  # noqa: BLE001 - unparseable ranges shouldn't block the user
        return True


def check_remote(selected: dict[str, RemoteFile], names: Optional[dict[str, str]] = None
                 ) -> list[DependencyIssue]:
    """``selected`` maps project id -> chosen build. Reports required project
    dependencies that aren't selected, and selected projects another build
    declares incompatible."""
    names = names or {}
    issues = []
    for pid, f in selected.items():
        for d in f.dependencies:
            if not d.project_id:
                continue
            if d.kind == DependencyKind.REQUIRED and d.project_id not in selected:
                issues.append(DependencyIssue(names.get(pid, pid), names.get(d.project_id, d.project_id),
                                              "missing", "required dependency not selected",
                                              suggestion=f"{d.provider}:{d.project_id}"))
            elif d.kind == DependencyKind.INCOMPATIBLE and d.project_id in selected:
                issues.append(DependencyIssue(names.get(pid, pid), names.get(d.project_id, d.project_id),
                                              "incompatible", "declared incompatible"))
    return issues


def build_order(deps: dict[str, set[str]]) -> list[str]:
    """Topological order (dependencies first) for porting. Cycles are broken
    deterministically by name so every mod still appears once."""
    order, state = [], {}

    def visit(n: str):
        if state.get(n) == 2:
            return
        if state.get(n) == 1:  # cycle
            return
        state[n] = 1
        for m in sorted(deps.get(n, ())):
            if m in deps:
                visit(m)
        state[n] = 2
        order.append(n)

    for n in sorted(deps):
        visit(n)
    return order
