from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import JarInfo, Project, RemoteFile


class Provider(ABC):
    name: str = ""

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def get_project(self, ident: str) -> Optional[Project]:
        """Look up a project by id or slug."""

    @abstractmethod
    def search(self, query: str, loader: Optional[str] = None, limit: int = 5) -> list[Project]:
        ...

    @abstractmethod
    def list_files(self, project_id: str, game_version: Optional[str] = None,
                   loaders: Optional[list[str]] = None) -> list[RemoteFile]:
        """Builds of a project, newest first, optionally filtered."""

    @abstractmethod
    def get_file(self, project_id: str, version_id: str) -> Optional[RemoteFile]:
        ...

    @abstractmethod
    def identify(self, jars: list[JarInfo]) -> dict[str, RemoteFile]:
        """Map jar sha1 -> RemoteFile for jars the provider recognises."""

    def download_headers(self) -> dict:
        return {}


def pick_best(files: list[RemoteFile], allow_prerelease: bool = True) -> Optional[RemoteFile]:
    """Prefer releases, then betas, then alphas; newest within each."""
    if not files:
        return None
    rank = {"release": 0, "beta": 1, "alpha": 2}
    candidates = files if allow_prerelease else [f for f in files if f.release_type == "release"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: (rank.get(f.release_type, 3), _neg_date(f.date)))[0]


def _neg_date(date: str) -> str:
    # Sort newest first using string comparison on ISO dates.
    return "".join(chr(0x10FFFF - ord(c)) for c in date)
