"""CurseForge Core API (https://docs.curseforge.com/rest-api/).

Requires a free API key from https://console.curseforge.com, supplied via the
``CURSEFORGE_API_KEY`` environment variable or the ``curseforge_api_key``
setting. Without a key this provider is disabled and Modsmith uses Modrinth.
"""
from __future__ import annotations

import urllib.parse
from typing import Optional

from ..http import Http, HttpError
from ..models import Dependency, DependencyKind, JarInfo, Project, RemoteFile
from .base import Provider

API = "https://api.curseforge.com/v1"
GAME_ID = 432          # Minecraft
CLASS_MODS = 6
LOADER_TYPES = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
LOADER_NAMES = {"forge": "Forge", "fabric": "Fabric", "quilt": "Quilt", "neoforge": "NeoForge"}
RELEASE_TYPES = {1: "release", 2: "beta", 3: "alpha"}
RELATIONS = {1: DependencyKind.EMBEDDED, 2: DependencyKind.OPTIONAL, 3: DependencyKind.REQUIRED,
             4: DependencyKind.OPTIONAL, 5: DependencyKind.INCOMPATIBLE, 6: DependencyKind.EMBEDDED}


class CurseForge(Provider):
    name = "curseforge"

    def __init__(self, http: Http, api_key: Optional[str], api: str = API):
        self.http = http
        self.api_key = api_key
        self.api = api

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _h(self) -> dict:
        return {"x-api-key": self.api_key or ""}

    def _get(self, path: str, params: Optional[dict] = None):
        return self.http.get_json(f"{self.api}{path}", params=params, headers=self._h())

    def get_project(self, ident: str) -> Optional[Project]:
        if not self.available:
            return None
        try:
            if ident.isdigit():
                d = self._get(f"/mods/{ident}")["data"]
            else:
                hits = self._get("/mods/search", {"gameId": GAME_ID, "classId": CLASS_MODS, "slug": ident})["data"]
                if not hits:
                    return None
                d = hits[0]
        except HttpError as e:
            if e.status == 404:
                return None
            raise
        return self._project(d)

    @staticmethod
    def _project(d: dict) -> Project:
        links = d.get("links") or {}
        versions = sorted({gv["gameVersion"] for gv in d.get("latestFilesIndexes", []) if gv.get("gameVersion")})
        loaders = sorted({k for k, v in LOADER_TYPES.items()
                          for gv in d.get("latestFilesIndexes", []) if gv.get("modLoader") == v})
        return Project(provider="curseforge", project_id=str(d["id"]), slug=d.get("slug", str(d["id"])),
                       name=d.get("name", ""), source_url=links.get("sourceUrl") or None,
                       loaders=loaders, game_versions=versions)

    def search(self, query: str, loader: Optional[str] = None, limit: int = 5) -> list[Project]:
        if not self.available:
            return []
        params = {"gameId": GAME_ID, "classId": CLASS_MODS, "searchFilter": query,
                  "pageSize": limit, "sortField": 2, "sortOrder": "desc"}
        if loader in LOADER_TYPES:
            params["modLoaderType"] = LOADER_TYPES[loader]
        return [self._project(d) for d in self._get("/mods/search", params)["data"]]

    def list_files(self, project_id: str, game_version: Optional[str] = None,
                   loaders: Optional[list[str]] = None) -> list[RemoteFile]:
        if not self.available:
            return []
        out: list[RemoteFile] = []
        for loader in (loaders or [None]):
            params = {"pageSize": 50}
            if game_version:
                params["gameVersion"] = game_version
            if loader in LOADER_TYPES:
                params["modLoaderType"] = LOADER_TYPES[loader]
            try:
                data = self._get(f"/mods/{project_id}/files", params)["data"]
            except HttpError as e:
                if e.status == 404:
                    return []
                raise
            out.extend(self._file(f) for f in data)
        # De-duplicate and keep newest first.
        seen, uniq = set(), []
        for f in sorted(out, key=lambda f: f.date, reverse=True):
            if f.version_id not in seen:
                seen.add(f.version_id)
                uniq.append(f)
        if loaders:
            uniq = [f for f in uniq if set(f.loaders) & set(loaders)]
        return uniq

    def get_file(self, project_id: str, version_id: str) -> Optional[RemoteFile]:
        if not self.available:
            return None
        try:
            return self._file(self._get(f"/mods/{project_id}/files/{version_id}")["data"])
        except HttpError as e:
            if e.status == 404:
                return None
            raise

    @staticmethod
    def _file(f: dict) -> RemoteFile:
        gvs = f.get("gameVersions", [])
        loaders = [k for k, name in LOADER_NAMES.items() if name in gvs]
        mc = [g for g in gvs if g and (g[0].isdigit())]
        sha1 = next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)
        url = f.get("downloadUrl")
        if not url and f.get("id") and f.get("fileName"):
            # Authors who disabled 3rd-party distribution return null here; the
            # CDN path still works for most files but may be refused.
            fid = int(f["id"])
            url = (f"https://edge.forgecdn.net/files/{fid // 1000}/{fid % 1000}/"
                   f"{urllib.parse.quote(f['fileName'])}")
        deps = [Dependency(mod_id=str(d["modId"]), kind=RELATIONS.get(d.get("relationType"), DependencyKind.OPTIONAL),
                           provider="curseforge", project_id=str(d["modId"]))
                for d in f.get("dependencies", [])]
        return RemoteFile(
            provider="curseforge", project_id=str(f.get("modId")), version_id=str(f["id"]),
            version_number=f.get("displayName", f.get("fileName", "")), filename=f.get("fileName", ""),
            url=url, game_versions=mc, loaders=loaders,
            release_type=RELEASE_TYPES.get(f.get("releaseType"), "release"),
            sha1=sha1, size=f.get("fileLength", 0), dependencies=deps, date=f.get("fileDate", ""))

    def identify(self, jars: list[JarInfo]) -> dict[str, RemoteFile]:
        if not self.available:
            return {}
        by_fp = {j.cf_fingerprint: j for j in jars if j.cf_fingerprint}
        if not by_fp:
            return {}
        data = self.http.post_json(f"{self.api}/fingerprints/{GAME_ID}",
                                   {"fingerprints": list(by_fp)}, headers=self._h())["data"]
        out = {}
        for match in data.get("exactMatches", []):
            f = match["file"]
            jar = by_fp.get(f.get("fileFingerprint"))
            if jar:
                out[jar.sha1] = self._file(f)
        return out

    def download_headers(self) -> dict:
        return {}
