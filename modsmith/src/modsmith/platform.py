"""Facts about Minecraft and mod loaders, fetched from their official sources."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .http import Http
from .versions import Version, default_loader_for, required_java

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
FABRIC_META = "https://meta.fabricmc.net/v2"


@dataclass
class TargetInfo:
    minecraft: str
    loader: str
    loader_version: Optional[str]
    java: int
    # Extra toolchain hints for the porting agent.
    fabric_api: Optional[str] = None
    notes: str = ""


class Platform:
    def __init__(self, http: Http):
        self.http = http

    def minecraft_versions(self, include_snapshots: bool = False) -> list[str]:
        d = self.http.get_json(MOJANG_MANIFEST)
        return [v["id"] for v in d["versions"]
                if v["type"] == "release" or (include_snapshots and v["type"] == "snapshot")]

    def latest_minecraft(self, snapshot: bool = False) -> str:
        d = self.http.get_json(MOJANG_MANIFEST)
        return d["latest"]["snapshot" if snapshot else "release"]

    def _maven_versions(self, url: str) -> list[str]:
        xml = self.http.get_text(url)
        return re.findall(r"<version>([^<]+)</version>", xml)

    @staticmethod
    def neoforge_prefix(mc: str) -> str:
        """NeoForge versions encode the MC version: 1.21.1 -> 21.1., 26.3 -> 26.3.0."""
        v = Version(mc)
        rel = list(v.release)
        if rel and rel[0] == 1:  # 1.x.y scheme drops the leading 1
            rel = rel[1:]
            while len(rel) < 2:
                rel.append(0)
            return ".".join(map(str, rel[:2])) + "."
        while len(rel) < 3:
            rel.append(0)
        return ".".join(map(str, rel[:3])) + "."

    def neoforge_version(self, mc: str) -> Optional[str]:
        prefix = self.neoforge_prefix(mc)
        matches = [v for v in self._maven_versions(NEOFORGE_MAVEN) if v.startswith(prefix)]
        stable = [v for v in matches if "beta" not in v and "alpha" not in v]
        pool = stable or matches
        return max(pool, key=Version) if pool else None

    def forge_version(self, mc: str) -> Optional[str]:
        matches = [v.split("-", 1)[1] for v in self._maven_versions(FORGE_MAVEN)
                   if v.split("-", 1)[0] == mc and "-" in v]
        return max(matches, key=Version) if matches else None

    def fabric_loader_version(self) -> Optional[str]:
        data = self.http.get_json(f"{FABRIC_META}/versions/loader")
        stable = [d["version"] for d in data if d.get("stable")]
        return stable[0] if stable else (data[0]["version"] if data else None)

    def fabric_supports(self, mc: str) -> bool:
        return any(g["version"] == mc for g in self.http.get_json(f"{FABRIC_META}/versions/game"))

    def target(self, mc: str, loader: Optional[str] = None) -> TargetInfo:
        loader = (loader or default_loader_for(mc)).lower()
        lv = None
        try:
            if loader == "neoforge":
                lv = self.neoforge_version(mc)
            elif loader == "forge":
                lv = self.forge_version(mc)
            elif loader in ("fabric", "quilt"):
                lv = self.fabric_loader_version()
        except Exception:  # noqa: BLE001 - offline is fine, we just lack a hint
            lv = None
        return TargetInfo(minecraft=mc, loader=loader, loader_version=lv, java=required_java(mc))
