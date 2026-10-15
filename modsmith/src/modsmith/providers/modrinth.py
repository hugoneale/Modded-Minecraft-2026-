"""Modrinth API v2 (https://docs.modrinth.com/api/). No key required."""
from __future__ import annotations

from typing import Optional

from ..http import Http, HttpError
from ..models import Dependency, DependencyKind, JarInfo, Project, RemoteFile
from .base import Provider

API = "https://api.modrinth.com/v2"


class Modrinth(Provider):
    name = "modrinth"

    def __init__(self, http: Http, api: str = API):
        self.http = http
        self.api = api

    def get_project(self, ident: str) -> Optional[Project]:
        try:
            d = self.http.get_json(f"{self.api}/project/{ident}")
        except HttpError as e:
            if e.status == 404:
                return None
            raise
        return self._project(d)

    def get_projects(self, ids: list[str]) -> list[Project]:
        out = []
        for i in range(0, len(ids), 100):
            data = self.http.get_json(f"{self.api}/projects", params={"ids": ids[i:i + 100]})
            out.extend(self._project(d) for d in data)
        return out

    @staticmethod
    def _project(d: dict) -> Project:
        lic = d.get("license") or {}
        return Project(
            provider="modrinth",
            project_id=d["id"],
            slug=d.get("slug", d["id"]),
            name=d.get("title", d.get("slug", "")),
            source_url=d.get("source_url"),
            license=lic.get("id") if isinstance(lic, dict) else lic,
            loaders=d.get("loaders", []),
            game_versions=d.get("game_versions", []),
        )

    def search(self, query: str, loader: Optional[str] = None, limit: int = 5) -> list[Project]:
        facets = [["project_type:mod"]]
        if loader:
            facets.append([f"categories:{loader}"])
        d = self.http.get_json(f"{self.api}/search", params={
            "query": query, "facets": facets, "limit": limit})
        return [Project(provider="modrinth", project_id=h["project_id"], slug=h["slug"],
                        name=h["title"], license=h.get("license"),
                        loaders=[c for c in h.get("categories", []) if c in
                                 ("forge", "neoforge", "fabric", "quilt")],
                        game_versions=h.get("versions", []))
                for h in d.get("hits", [])]

    def list_files(self, project_id: str, game_version: Optional[str] = None,
                   loaders: Optional[list[str]] = None) -> list[RemoteFile]:
        params = {}
        if game_version:
            params["game_versions"] = [game_version]
        if loaders:
            params["loaders"] = loaders
        try:
            data = self.http.get_json(f"{self.api}/project/{project_id}/version", params=params)
        except HttpError as e:
            if e.status == 404:
                return []
            raise
        return [self._file(v) for v in data]

    def get_file(self, project_id: str, version_id: str) -> Optional[RemoteFile]:
        try:
            return self._file(self.http.get_json(f"{self.api}/version/{version_id}"))
        except HttpError as e:
            if e.status == 404:
                return None
            raise

    @staticmethod
    def _file(v: dict) -> RemoteFile:
        files = v.get("files", [])
        primary = next((f for f in files if f.get("primary")), files[0] if files else {})
        deps = []
        for d in v.get("dependencies", []):
            kind = {"required": DependencyKind.REQUIRED, "optional": DependencyKind.OPTIONAL,
                    "incompatible": DependencyKind.INCOMPATIBLE,
                    "embedded": DependencyKind.EMBEDDED}.get(d.get("dependency_type"), DependencyKind.OPTIONAL)
            deps.append(Dependency(mod_id=d.get("project_id") or d.get("file_name") or "?",
                                   kind=kind, provider="modrinth", project_id=d.get("project_id")))
        return RemoteFile(
            provider="modrinth",
            project_id=v["project_id"],
            version_id=v["id"],
            version_number=v.get("version_number", ""),
            filename=primary.get("filename", ""),
            url=primary.get("url"),
            game_versions=v.get("game_versions", []),
            loaders=v.get("loaders", []),
            release_type=v.get("version_type", "release"),
            sha1=(primary.get("hashes") or {}).get("sha1"),
            sha512=(primary.get("hashes") or {}).get("sha512"),
            size=primary.get("size", 0),
            dependencies=deps,
            date=v.get("date_published", ""),
        )

    def identify(self, jars: list[JarInfo]) -> dict[str, RemoteFile]:
        hashes = [j.sha1 for j in jars if j.sha1]
        if not hashes:
            return {}
        out: dict[str, RemoteFile] = {}
        for i in range(0, len(hashes), 500):
            data = self.http.post_json(f"{self.api}/version_files",
                                       {"hashes": hashes[i:i + 500], "algorithm": "sha1"})
            for sha, v in (data or {}).items():
                out[sha] = self._file(v)
        return out

    def latest_for_hashes(self, hashes: list[str], game_version: str,
                          loaders: list[str]) -> dict[str, RemoteFile]:
        """Modrinth's bulk 'latest version matching filters' for many jars at once."""
        if not hashes:
            return {}
        data = self.http.post_json(f"{self.api}/version_files/update", {
            "hashes": hashes, "algorithm": "sha1",
            "loaders": loaders, "game_versions": [game_version]})
        return {sha: self._file(v) for sha, v in (data or {}).items()}
