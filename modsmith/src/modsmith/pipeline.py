"""The Modsmith workflow.

    select mods -> pick Minecraft version -> resolve each mod
      -> official build for the target?  -> compare features with the baseline
      -> otherwise find source code       -> agent port + build + feature gate
      -> otherwise (opt-in) decompile     -> agent port + build + feature gate
    -> dependency / compatibility check (auto-adding missing dependencies)
    -> install into the mods folder (old jars backed up) + report
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import sources
from .config import Settings
from .features import describe_diff, diff_features, extract_features
from .http import Http, HttpError
from .jarscan import read_jar
from .models import (DependencyIssue, DependencyKind, FeatureSet, JarInfo, ModRef, ModResult,
                     Project, RemoteFile, Status)
from .platform import Platform, TargetInfo
from .porting import make_agent, port
from .porting.builder import find_java
from .porting.decompile import DECOMPILE_WARNING, decompile_to_project, ensure_vineflower
from .porting.porter import baseline_namespaces, copy_output
from .providers import CurseForge, Modrinth, Provider, pick_best
from .resolver import build_order, check_jars, check_remote
from .versions import Version, default_loader_for, loader_aliases

Log = Callable[[str], None]


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif os.uname().sysname == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "modsmith"


def clean_name(name: str) -> str:
    """'YUNG's Better Mineshafts (Forge/NeoForge)' -> "YUNG's Better Mineshafts"."""
    name = name or ""
    while True:  # strip (possibly nested) bracketed qualifiers, innermost first
        stripped = re.sub(r"\s*(\([^()]*\)|\[[^\[\]]*\])", "", name)
        if stripped == name:
            break
        name = stripped
    name = re.sub(r"\s*[|:-]\s*(forge|neoforge|fabric|quilt)\b.*$", "", name, flags=re.I)
    return name.strip() or (name or "")


def names_match(a: str, b: str, threshold: float = 0.9) -> bool:
    from difflib import SequenceMatcher
    na = re.sub(r"[^a-z0-9]", "", clean_name(a).lower())
    nb = re.sub(r"[^a-z0-9]", "", clean_name(b).lower())
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "mod"


def newest_supported(project: Project, loader: str) -> Optional[str]:
    """Newest release-numbered Minecraft version a project lists (any loader
    when the project doesn't list ``loader``)."""
    vs = [v for v in project.game_versions if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", v)]
    return max(vs, key=Version) if vs else None


def coverage(results: list[ModResult], top: int = 8) -> list[tuple[str, int]]:
    """How many of the selected mods have a build for each Minecraft version
    (from project metadata; any loader)."""
    counts: dict[str, int] = {}
    for r in results:
        if not r.project:
            continue
        for v in set(r.project.game_versions):
            if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", v):
                counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], [-x for x in Version(kv[0]).release]))[:top]


class Cancelled(Exception):
    pass


class Modsmith:
    def __init__(self, settings: Settings, log: Log = print, cache_dir: Optional[Path] = None,
                 http: Optional[Http] = None, workers: int = 8):
        self.s = settings
        self.log = log
        self.cache = Path(cache_dir) if cache_dir else default_cache_dir()
        self.http = http or Http(cache_dir=self.cache)
        self.modrinth = Modrinth(self.http)
        self.curseforge = CurseForge(self.http, settings.cf_key)
        self.platform = Platform(self.http)
        self.workers = workers
        self.cancel = threading.Event()
        self._target: Optional[TargetInfo] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ basics
    def provider(self, name: str) -> Provider:
        return self.modrinth if name == "modrinth" else self.curseforge

    def target(self) -> TargetInfo:
        if self._target is None:
            mc = self.s.minecraft or self.platform.latest_minecraft()
            loader = self.s.loader or default_loader_for(mc)
            self._target = self.platform.target(mc, loader)
        return self._target

    def _check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    @property
    def out_dir(self) -> Path:
        return Path(self.s.output_dir)

    # --------------------------------------------------------------- resolving
    def resolve_all(self, refs: Iterable[ModRef]) -> list[ModResult]:
        refs = [r for r in refs if r.enabled]
        results = [ModResult(ref=r) for r in refs]
        # Local jars: read metadata and identify them in bulk by hash.
        local = [r for r in results if r.ref.provider == "file"]
        for r in local:
            try:
                r.current = read_jar(Path(r.ref.ident))
                r.name, r.mod_id = r.current.name, r.current.mod_id
            except Exception as e:  # noqa: BLE001
                r.warnings.append(f"Could not read jar: {e}")
        jars = [r.current for r in local if r.current]
        known: dict[str, RemoteFile] = {}
        for prov in (self.modrinth, self.curseforge):
            if not prov.available or not jars:
                continue
            try:
                found = prov.identify([j for j in jars if j.sha1 not in known])
                known.update(found)
            except Exception as e:  # noqa: BLE001
                self.log(f"! {prov.name} lookup failed: {e}")
        for r in local:
            if r.current and r.current.sha1 in known:
                r.current_remote = known[r.current.sha1]

        def work(r: ModResult):
            self._check_cancel()
            try:
                self._resolve_one(r)
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                r.warnings.append(f"Lookup failed: {e}")
            return r

        with ThreadPoolExecutor(self.workers) as pool:
            list(pool.map(work, results))
        return results

    def _resolve_one(self, r: ModResult) -> None:
        ref = r.ref
        prov, ident = ref.provider, ref.ident
        project: Optional[Project] = None
        if r.current_remote:
            project = self.provider(r.current_remote.provider).get_project(r.current_remote.project_id)
        elif prov == "file":
            if r.current:  # unknown to providers: try a search by mod id / name
                project = self._search(r.current.mod_id, r.current.name)
        elif prov == "modrinth":
            project = self.modrinth.get_project(ident)
        elif prov == "curseforge":
            if not self.curseforge.available:
                r.warnings.append("CurseForge project ids need a CurseForge API key "
                                  "(set CURSEFORGE_API_KEY or curseforge_api_key)")
                return
            project = self.curseforge.get_project(ident)
        else:
            project = self._lookup_bare(ident, ref.hint)
        r.project = project
        if project is None:
            if not r.current:
                r.status = Status.UNRESOLVED
                r.warnings.append(f"Could not find '{ref.spec}' on Modrinth or CurseForge")
            r.name = r.name or ident
            return
        r.name = r.name or project.name
        r.mod_id = r.mod_id or project.slug
        if r.current_remote is None and ref.pinned_version:
            r.current_remote = self.provider(project.provider).get_file(project.project_id, ref.pinned_version)
        if r.current_remote is None and r.current is None and self.s.from_minecraft:
            from_loader = self.s.from_loader or default_loader_for(self.s.from_minecraft)
            files = self.provider(project.provider).list_files(
                project.project_id, self.s.from_minecraft, loader_aliases(from_loader, self.s.from_minecraft))
            r.current_remote = pick_best(files)

    def _lookup_bare(self, ident: str, hint: Optional[str]) -> Optional[Project]:
        """A bare slug may mean different mods on different sites, so when we
        know the mod's display name we only accept a project whose name matches."""
        for prov in (self.modrinth, self.curseforge):
            if not prov.available:
                continue
            try:
                p = prov.get_project(ident)
            except Exception:  # noqa: BLE001
                p = None
            if p and (not hint or names_match(p.name, hint)) and self._loader_ok(p):
                return p
        if hint:
            return self._search(ident, hint)
        return None

    def _loader_ok(self, p: Project) -> bool:
        """Reject e.g. a Fabric-only fork when the user's mod is a Forge mod."""
        if not p.loaders:
            return True
        wanted = {self.target().loader}
        if self.s.from_loader and self.s.from_minecraft:
            wanted |= set(loader_aliases(self.s.from_loader, self.s.from_minecraft))
        if wanted & {"forge", "neoforge"}:
            wanted |= {"forge", "neoforge"}
        return bool(set(p.loaders) & wanted)

    def _search(self, mod_id: str, name: str) -> Optional[Project]:
        norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
        for prov in (self.modrinth, self.curseforge):
            if not prov.available:
                continue
            for query in dict.fromkeys([clean_name(name), name, mod_id]):
                try:
                    hits = prov.search(query, limit=10)
                except Exception:  # noqa: BLE001
                    continue
                for hit in hits:
                    # With a real display name, only the name decides: the same slug can be
                    # a different mod on another site.
                    slug_ok = name == mod_id and norm(hit.slug) == norm(mod_id)
                    if (slug_ok or names_match(hit.name, name)) and self._loader_ok(hit):
                        # Search hits lack source/licence details; fetch the full record.
                        try:
                            return prov.get_project(hit.project_id) or hit
                        except Exception:  # noqa: BLE001
                            return hit
        return None

    # ---------------------------------------------------------------- download
    def fetch(self, f: RemoteFile) -> Path:
        dest = self.cache / "jars" / f.provider / _safe(f.version_id) / (f.filename or "mod.jar")
        if not f.url:
            raise RuntimeError("no download URL")
        return self.http.download(f.url, dest, sha1=f.sha1, sha512=f.sha512,
                                  headers=self.provider(f.provider).download_headers())

    def ensure_baseline(self, r: ModResult) -> Optional[JarInfo]:
        if r.current:
            return r.current
        if r.current_remote:
            try:
                r.current = read_jar(self.fetch(r.current_remote))
                r.mod_id = r.current.mod_id
            except Exception as e:  # noqa: BLE001
                r.warnings.append(f"Couldn't download the current build for comparison: {e}")
        return r.current

    # ----------------------------------------------------------- official path
    def find_official(self, r: ModResult) -> Optional[RemoteFile]:
        t = self.target()
        loaders = loader_aliases(t.loader, t.minecraft)
        files: list[RemoteFile] = []
        tried = set()
        projects = [r.project] if r.project else []
        # Same mod on the other platform (authors sometimes publish to only one).
        if r.project:
            other = self.curseforge if r.project.provider == "modrinth" else self.modrinth
            if other.available:
                try:
                    alt = other.get_project(r.project.slug)
                    if alt and re.sub(r"\W", "", alt.name.lower()) == re.sub(r"\W", "", r.project.name.lower()):
                        projects.append(alt)
                except Exception:  # noqa: BLE001
                    pass
        for p in projects:
            if (p.provider, p.project_id) in tried:
                continue
            tried.add((p.provider, p.project_id))
            try:
                files = self.provider(p.provider).list_files(p.project_id, t.minecraft, loaders)
            except HttpError as e:
                r.warnings.append(f"{p.provider}: {e}")
                continue
            # Belt and braces: providers sometimes return near-matches.
            files = [f for f in files if t.minecraft in f.game_versions and set(f.loaders) & set(loaders)]
            if files:
                if p is not r.project:
                    r.notes.append(f"Found on {p.provider} as '{p.slug}'")
                break
        return pick_best(files, self.s.allow_prerelease)

    def compare(self, r: ModResult, candidate_jar: Path):
        base = self.ensure_baseline(r)
        if not base:
            return None
        bfs = extract_features(base.path)
        if bfs.total() == 0:
            r.notes.append("Mod has no data-driven content to compare (library or code-only mod)")
        cfs = extract_features(candidate_jar, namespaces=baseline_namespaces(bfs) or None)
        return diff_features(bfs, cfs)

    # ---------------------------------------------------------------- process
    def check(self, r: ModResult) -> ModResult:
        """Official-build lookup and feature comparison (no porting)."""
        if r.status == Status.UNRESOLVED and not r.project and not r.current:
            return r
        t = self.target()
        best = self.find_official(r) if r.project else None
        r.official = best
        current_sha = r.current.sha1 if r.current else (r.current_remote.sha1 if r.current_remote else None)
        if best and ((r.current_remote and best.version_id == r.current_remote.version_id)
                     or (current_sha and best.sha1 == current_sha)):
            r.status = Status.UP_TO_DATE
            if r.current is None:  # selected by id, not installed yet
                self._take_official(r, best)
            return r
        if best is None:
            r.status = Status.NO_OFFICIAL
            r.notes.append(f"No official build for Minecraft {t.minecraft} ({t.loader})")
            newest = newest_supported(r.project, t.loader) if r.project else None
            if newest:
                r.notes.append(f"Newest version it supports: {newest}")
            return r
        r.status = Status.OFFICIAL
        if best.release_type != "release":
            r.notes.append(f"Official build is a {best.release_type}")
        if not self.s.verify_features:
            return r
        try:
            jar = self.fetch(best)
            r.output_jar = jar
        except Exception as e:  # noqa: BLE001
            r.manual_download = self._manual_url(r, best)
            r.warnings.append(f"Official build can't be downloaded automatically ({e}); "
                              f"get it from {r.manual_download}")
            return r
        diff = self.compare(r, jar)
        if diff is not None:
            r.feature_diff = diff
            if not diff.is_lossless:
                r.status = Status.OFFICIAL_FEATURE_LOSS
                r.warnings.append("Official build is missing content: " + describe_diff(diff))
        return r

    def _take_official(self, r: ModResult, f: RemoteFile):
        try:
            r.output_jar = self.fetch(f)
        except Exception as e:  # noqa: BLE001
            r.manual_download = self._manual_url(r, f)
            r.warnings.append(f"Download blocked ({e}); get it from {r.manual_download}")

    @staticmethod
    def _manual_url(r: ModResult, f: RemoteFile) -> str:
        if f.provider == "curseforge" and r.project:
            return f"https://www.curseforge.com/minecraft/mc-mods/{r.project.slug}/files/{f.version_id}"
        if r.project:
            return f"https://modrinth.com/mod/{r.project.slug}/version/{f.version_id}"
        return f.url or ""

    def needs_port(self, r: ModResult) -> bool:
        if r.status == Status.NO_OFFICIAL:
            return True
        if r.status == Status.OFFICIAL_FEATURE_LOSS:
            return self.s.feature_policy in ("prefer-port", "strict")
        return False

    def port_mod(self, r: ModResult) -> ModResult:
        """Rebuild the mod for the target from source (or decompiled bytecode)."""
        t = self.target()
        fallback_official = r.output_jar if r.status == Status.OFFICIAL_FEATURE_LOSS else None
        base = self.ensure_baseline(r)
        if not base:
            r.status = Status.PORT_FAILED
            r.warnings.append("No baseline jar to port from")
            return r
        from_loader = base.loader.value if base.loader else (self.s.from_loader or "forge")
        from_mc = self.s.from_minecraft or self._guess_mc(r)
        work = self.cache / "ports" / _safe(r.mod_id or r.name) / f"{t.minecraft}-{t.loader}"
        urls = sources.candidate_urls(r.project.source_url if r.project else None,
                                      base.source_url, base.homepage)
        checkout = None
        for url in urls:
            try:
                self.log(f"  source: {url}")
                tags, branches = sources.remote_refs(url)
                newer = [v for v in (r.project.game_versions if r.project else [])
                         if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", v) and Version(v) > Version(from_mc or "0")]
                start = sources.best_start_ref(tags, branches, t.minecraft, newer)
                checkout = sources.fetch_source(url, work, base.version, from_mc,
                                                prefer_branch=start[0] if start else None)
                if start:
                    from_mc = start[1]
                r.source_repo = url
                r.notes.append(checkout.note)
                break
            except Exception as e:  # noqa: BLE001
                r.warnings.append(f"Source {url} unusable: {e}")
        decompiled = False
        if checkout is None:
            if not self.s.allow_decompile:
                r.status = Status.NO_SOURCE
                r.notes.append("No source code found; enable 'allow_decompile' to try reverse engineering")
                return self._fallback(r, fallback_official)
            self.log("  ! " + DECOMPILE_WARNING)
            r.warnings.append(DECOMPILE_WARNING)
            java = find_java(t.java, self.s.java_home) or find_java(17, self.s.java_home)
            try:
                vf = ensure_vineflower(self.http, self.cache / "tools")
                decompile_to_project(base, work, vf, java)
            except Exception as e:  # noqa: BLE001
                r.status = Status.PORT_FAILED
                r.warnings.append(f"Decompile failed: {e}")
                return self._fallback(r, fallback_official)
            decompiled = True
        bfs = extract_features(base.path)
        agent = make_agent(self.s.agent, self.s.agent_command, self.s.agent_model)
        res = port(workdir=work, name=r.name or r.mod_id, mod_id=r.mod_id, from_mc=from_mc,
                   from_loader=from_loader, target=t, baseline=bfs, agent=agent,
                   java_homes=self.s.java_home, max_attempts=self.s.max_port_attempts,
                   build_timeout=self.s.build_timeout, decompiled=decompiled,
                   ref_note=checkout.note if checkout else "decompiled from jar", log=self.log,
                   gradle_version=self._latest_gradle())
        r.notes.extend(res.notes)
        if res.ok and res.jar:
            name = f"{_safe(r.mod_id)}-{base.version}-mc{t.minecraft}-{t.loader}-modsmith.jar"
            r.output_jar = copy_output(res.jar, self.out_dir / "ported", name)
            r.feature_diff = res.diff
            r.status = Status.PORTED_DECOMPILED if decompiled else Status.PORTED
            lic = (r.project.license if r.project else None) or base.license
            if decompiled or not sources.is_redistributable(lic):
                r.warnings.append(f"Licence '{lic or 'unknown'}': this port is for personal use only - don't share it")
            r.notes.append("Builds and keeps all content; not play-tested - test in a copy of your world first")
            return r
        r.status = Status.PORT_FAILED
        if res.diff is not None and res.jar:
            r.notes.append("Partial port: " + describe_diff(res.diff))
        return self._fallback(r, fallback_official)

    def _latest_gradle(self) -> Optional[str]:
        try:
            return self.http.get_json("https://services.gradle.org/versions/current")["version"]
        except Exception:  # noqa: BLE001
            return None

    def _fallback(self, r: ModResult, official_jar: Optional[Path]) -> ModResult:
        if official_jar and self.s.feature_policy == "prefer-port":
            r.output_jar = official_jar
            r.status = Status.OFFICIAL_FEATURE_LOSS
            r.notes.append("Port failed; falling back to the official build (with missing content)")
        elif r.status in (Status.PORT_FAILED, Status.NO_SOURCE):
            r.output_jar = None
        return r

    def _guess_mc(self, r: ModResult) -> str:
        if r.current_remote and r.current_remote.game_versions:
            return max(r.current_remote.game_versions, key=Version)
        if r.current and r.current.minecraft_range not in ("", "*"):
            m = re.search(r"\d+\.\d+(?:\.\d+)?", r.current.minecraft_range)
            if m:
                return m.group(0)
        return "unknown"

    # -------------------------------------------------------------- top level
    def run(self, refs: Iterable[ModRef], do_port: bool = False,
            progress: Optional[Callable[[ModResult], None]] = None) -> tuple[list[ModResult], list[DependencyIssue]]:
        t = self.target()
        self.log(f"Target: Minecraft {t.minecraft}, {t.loader} {t.loader_version or '?'} (Java {t.java})")
        self.log("Resolving mods...")
        results = self.resolve_all(refs)
        results = self._check_all(results, progress)

        issues = self.dependency_check(results)
        if self.s.auto_add_dependencies:
            results, issues = self._add_missing(results, issues, progress)

        if do_port and self.s.allow_port:
            todo = [r for r in results if self.needs_port(r)]
            if todo:
                deps = {r.mod_id: {d.mod_id for d in (r.current.dependencies if r.current else [])}
                        for r in todo}
                order = build_order(deps)
                by_id = {r.mod_id: r for r in todo}
                for mid in order:
                    self._check_cancel()
                    r = by_id[mid]
                    self.log(f"Porting {r.name or mid}...")
                    try:
                        self.port_mod(r)
                    except Cancelled:
                        raise
                    except Exception as e:  # noqa: BLE001
                        r.status = Status.PORT_FAILED
                        r.warnings.append(f"Port crashed: {e}")
                    self.log(f"  -> {r.status.value}")
                    if progress:
                        progress(r)
                issues = self.dependency_check(results)
        return results, issues

    def _check_all(self, results, progress):
        def work(r):
            self._check_cancel()
            try:
                self.check(r)
            except Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                r.warnings.append(f"Check failed: {e}")
            with self._lock:
                self.log(f"  {r.name or r.ref.spec}: {r.status.value}")
            if progress:
                progress(r)
            return r

        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(work, results))

    def dependency_check(self, results: list[ModResult]) -> list[DependencyIssue]:
        t = self.target()
        issues: list[DependencyIssue] = []
        selected, names = {}, {}
        for r in results:
            ids = set()
            if r.project:
                ids.add(r.project.project_id)
            if r.current_remote:
                ids.add(r.current_remote.project_id)
            if r.official:
                ids.add(r.official.project_id)
            for i in ids:
                selected[i] = r.official or r.current_remote or RemoteFile(
                    provider="", project_id=i, version_id="", version_number="", filename="",
                    url=None, game_versions=[], loaders=[])
                names[i] = r.name
        # Only official builds' declared dependencies describe the target version.
        remote = {r.official.project_id: r.official for r in results
                  if r.official and r.status in (Status.OFFICIAL, Status.OFFICIAL_FEATURE_LOSS, Status.UP_TO_DATE)}
        issues += [i for i in check_remote(remote, names)
                   if not (i.problem == "missing" and i.suggestion and i.suggestion.split(":", 1)[1] in selected)]
        final_jars = []
        for r in results:
            if r.output_jar and r.output_jar.exists():
                try:
                    final_jars.append(read_jar(r.output_jar, with_hashes=False))
                except Exception:  # noqa: BLE001
                    pass
            elif r.status == Status.UP_TO_DATE and r.current:
                final_jars.append(r.current)
        issues += check_jars(final_jars, t.minecraft, t.loader)
        for r in results:
            if r.status in (Status.NO_SOURCE, Status.NO_OFFICIAL, Status.PORT_FAILED, Status.UNRESOLVED):
                issues.append(DependencyIssue(r.name or r.ref.spec, "-", "unavailable",
                                              f"No working build for {t.minecraft}"))
        return issues

    def _add_missing(self, results, issues, progress, rounds: int = 3):
        known = {r.ref.spec for r in results}
        for _ in range(rounds):
            new = []
            for i in issues:
                if i.problem == "missing" and i.suggestion and ":" in i.suggestion and i.suggestion not in known:
                    known.add(i.suggestion)
                    new.append(ModRef(spec=i.suggestion))
            if not new:
                break
            self.log(f"Adding {len(new)} missing dependencies...")
            norm = lambda x: re.sub(r"[^a-z0-9]", "", (x or "").lower())  # noqa: E731
            have = {norm(r.name) for r in results} | {norm(r.project.slug) for r in results if r.project}
            resolved = [a for a in self.resolve_all(new)
                        if a.project and norm(a.project.slug) not in have and norm(a.project.name) not in have]
            added = self._check_all(resolved, progress)
            for a in added:
                a.notes.append("Added automatically as a required dependency")
            results = results + added
            issues = self.dependency_check(results)
        return results, issues

    # ----------------------------------------------------------------- install
    def install(self, results: list[ModResult], mods_dir: Optional[Path] = None,
                include_feature_loss: bool = True) -> list[str]:
        """Copy each mod's final jar into ``mods_dir``. Jars being replaced are
        moved to ``mods/.modsmith-backup/<timestamp>/``."""
        mods_dir = Path(mods_dir or self.s.mods_dir)
        mods_dir.mkdir(parents=True, exist_ok=True)
        backup = mods_dir / ".modsmith-backup" / time.strftime("%Y%m%d-%H%M%S")
        done = []
        for r in results:
            ok_status = {Status.OFFICIAL, Status.PORTED, Status.PORTED_DECOMPILED, Status.UP_TO_DATE}
            if include_feature_loss:
                ok_status.add(Status.OFFICIAL_FEATURE_LOSS)
            if r.status not in ok_status:
                continue
            if r.status == Status.UP_TO_DATE and r.current and r.current.path.parent.resolve() == mods_dir.resolve():
                continue
            if r.output_jar is None and r.official and not r.manual_download:
                self._take_official(r, r.official)
            if r.output_jar is None:
                continue
            old = r.current.path if r.current else None
            if old and old.exists() and old.parent.resolve() == mods_dir.resolve():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), backup / old.name)
            dest = mods_dir / r.output_jar.name
            shutil.copy2(r.output_jar, dest)
            done.append(dest.name)
            self.log(f"Installed {dest.name}")
        if backup.exists():
            self.log(f"Replaced jars backed up to {backup}")
        return done
