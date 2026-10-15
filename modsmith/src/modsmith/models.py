"""Core data types shared across Modsmith."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class Loader(str, Enum):
    FORGE = "forge"
    NEOFORGE = "neoforge"
    FABRIC = "fabric"
    QUILT = "quilt"

    @classmethod
    def parse(cls, value: str) -> "Loader":
        return cls(value.strip().lower())


class DependencyKind(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    INCOMPATIBLE = "incompatible"
    EMBEDDED = "embedded"


@dataclass
class Dependency:
    mod_id: str
    kind: DependencyKind = DependencyKind.REQUIRED
    version_range: str = "*"
    # Provider-level identifiers, when known (e.g. Modrinth project id).
    provider: Optional[str] = None
    project_id: Optional[str] = None


@dataclass
class ModRef:
    """A mod the user asked for, before it has been resolved.

    ``spec`` accepts ``modrinth:<slug-or-id>``, ``curseforge:<project-id>``,
    ``file:<path-to-jar>`` or a bare slug (looked up on Modrinth, then CurseForge).
    """

    spec: str
    pinned_version: Optional[str] = None  # provider version/file id of the "current" build
    enabled: bool = True
    hint: Optional[str] = None  # display name, used to verify slug lookups ("Create", "JEI")

    @property
    def provider(self) -> Optional[str]:
        return self.spec.split(":", 1)[0] if ":" in self.spec else None

    @property
    def ident(self) -> str:
        return self.spec.split(":", 1)[1] if ":" in self.spec else self.spec


@dataclass
class JarInfo:
    """Metadata read from inside a mod jar."""

    path: Path
    mod_id: str
    name: str
    version: str
    loader: Optional[Loader]
    minecraft_range: str = "*"
    loader_range: str = "*"
    dependencies: list[Dependency] = field(default_factory=list)
    extra_mod_ids: list[str] = field(default_factory=list)  # jar-in-jar / multi-mod jars
    source_url: Optional[str] = None
    homepage: Optional[str] = None
    license: Optional[str] = None
    sha1: str = ""
    sha512: str = ""
    cf_fingerprint: int = 0


@dataclass
class RemoteFile:
    """A downloadable build of a mod on a provider."""

    provider: str
    project_id: str
    version_id: str
    version_number: str
    filename: str
    url: Optional[str]
    game_versions: list[str]
    loaders: list[str]
    release_type: str = "release"
    sha1: Optional[str] = None
    sha512: Optional[str] = None
    size: int = 0
    dependencies: list[Dependency] = field(default_factory=list)
    date: str = ""


@dataclass
class Project:
    provider: str
    project_id: str
    slug: str
    name: str
    source_url: Optional[str] = None
    license: Optional[str] = None
    loaders: list[str] = field(default_factory=list)
    game_versions: list[str] = field(default_factory=list)


@dataclass
class FeatureSet:
    """Inventory of user-visible content a mod adds (items, blocks, ...).

    Categories map to sets of namespaced ids such as ``create:cogwheel``.
    """

    categories: dict[str, set[str]] = field(default_factory=dict)

    def add(self, category: str, ident: str) -> None:
        self.categories.setdefault(category, set()).add(ident)

    def total(self) -> int:
        return sum(len(v) for v in self.categories.values())

    def to_json(self) -> dict[str, list[str]]:
        return {k: sorted(v) for k, v in sorted(self.categories.items())}


@dataclass
class FeatureDiff:
    missing: dict[str, list[str]] = field(default_factory=dict)
    added: dict[str, list[str]] = field(default_factory=dict)
    baseline_total: int = 0

    @property
    def missing_count(self) -> int:
        return sum(len(v) for v in self.missing.values())

    @property
    def added_count(self) -> int:
        return sum(len(v) for v in self.added.values())

    @property
    def retained_ratio(self) -> float:
        if self.baseline_total == 0:
            return 1.0
        return 1.0 - self.missing_count / self.baseline_total

    @property
    def is_lossless(self) -> bool:
        return self.missing_count == 0


class Status(str, Enum):
    UP_TO_DATE = "up-to-date"          # the installed build already targets the selected version
    OFFICIAL = "official"              # an official build exists for the target
    OFFICIAL_FEATURE_LOSS = "official-feature-loss"  # official build exists but drops content
    NO_OFFICIAL = "no-official-build"  # nothing official for the target yet (port candidate)
    PORTED = "ported"                  # rebuilt from source for the target
    PORTED_DECOMPILED = "ported-decompiled"  # rebuilt from decompiled bytecode
    PORT_FAILED = "port-failed"
    NO_SOURCE = "no-source"            # no official build, no source, decompiling disabled
    UNRESOLVED = "unresolved"          # could not identify the mod at all
    SKIPPED = "skipped"


@dataclass
class ModResult:
    ref: ModRef
    name: str = ""
    mod_id: str = ""
    status: Status = Status.UNRESOLVED
    current: Optional[JarInfo] = None            # baseline jar (installed, or downloaded build the user is on)
    current_remote: Optional[RemoteFile] = None  # provider record of the baseline build
    project: Optional[Project] = None
    official: Optional[RemoteFile] = None        # best official build for the target
    output_jar: Optional[Path] = None            # jar to install (official download or port)
    manual_download: Optional[str] = None        # page to fetch it from when the author blocks 3rd-party downloads
    feature_diff: Optional[FeatureDiff] = None
    source_repo: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        def clean(o):
            if isinstance(o, Path):
                return str(o)
            if isinstance(o, Enum):
                return o.value
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            return o

        data = asdict(self)
        data["feature_diff"] = (
            None
            if self.feature_diff is None
            else {
                "missing": self.feature_diff.missing,
                "added": self.feature_diff.added,
                "retained_ratio": round(self.feature_diff.retained_ratio, 4),
            }
        )
        return clean(data)


@dataclass
class DependencyIssue:
    mod: str
    dependency: str
    problem: str  # "missing" | "version-mismatch" | "incompatible" | "wrong-minecraft" | "wrong-loader"
    detail: str = ""
    suggestion: Optional[str] = None  # a spec that would fix it, e.g. "modrinth:fabric-api"
