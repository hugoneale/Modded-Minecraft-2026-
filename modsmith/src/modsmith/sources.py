"""Locate and fetch a mod's source code."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .versions import Version

_REPO_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "git.sr.ht", "bitbucket.org")
# Licences under which a port may be shared with others. Anything else is
# treated as personal-use only.
REDISTRIBUTABLE = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "CC0-1.0", "Zlib",
    "MPL-2.0", "LGPL-2.1", "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0", "LGPL-3.0-only",
    "LGPL-3.0-or-later", "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0", "GPL-3.0-only",
    "GPL-3.0-or-later", "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later", "EUPL-1.2", "OSL-3.0",
    "CC-BY-4.0", "CC-BY-SA-4.0", "BSL-1.0", "Artistic-2.0",
}


def normalize_repo_url(url: Optional[str]) -> Optional[str]:
    """Turn a project 'source' link into a clonable https URL, or None."""
    if not url:
        return None
    url = url.strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/]+)/([^/#?]+)/([^/#?]+)", url)
    if not m or not any(m.group(1).endswith(h) for h in _REPO_HOSTS):
        return None
    repo = re.sub(r"\.git$", "", m.group(3))
    return f"https://{m.group(1)}/{m.group(2)}/{repo}"


def candidate_urls(*urls: Optional[str]) -> list[str]:
    out = []
    for u in urls:
        n = normalize_repo_url(u)
        if n and n not in out:
            out.append(n)
    return out


def is_redistributable(license_id: Optional[str]) -> bool:
    if not license_id:
        return False
    ids = re.split(r"\s+(?:OR|AND)\s+|/", license_id.strip())
    return any(i.strip() in REDISTRIBUTABLE for i in ids)


def _git(*args: str, cwd: Optional[Path] = None, timeout: int = 600) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:500]}")
    return r.stdout


def remote_refs(url: str) -> tuple[list[str], list[str]]:
    """(tags, branches) of a remote repository."""
    out = _git("ls-remote", "--tags", "--heads", url, timeout=120)
    tags, heads = [], []
    for line in out.splitlines():
        ref = line.split("\t", 1)[-1]
        if ref.startswith("refs/tags/"):
            t = ref[len("refs/tags/"):].removesuffix("^{}")
            if t not in tags:
                tags.append(t)
        elif ref.startswith("refs/heads/"):
            heads.append(ref[len("refs/heads/"):])
    return tags, heads


def _mc_in(name: str, mc: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(mc)}(?![\d])", name) is not None


def match_tag(tags: Iterable[str], mod_version: str, minecraft: Optional[str] = None) -> Optional[str]:
    """Find the tag corresponding to ``mod_version`` (e.g. jar version
    ``0.5.1.f`` matches tags ``v0.5.1.f``, ``mc1.20.1/0.5.1.f``,
    ``1.20.1-0.5.1.f``)."""
    tags = list(tags)
    if not mod_version:
        return None
    plain = re.sub(r"^v", "", mod_version)
    exact = [t for t in tags if re.sub(r"^(?:v|release[-/]?)", "", t) == plain]
    if exact:
        return exact[0]
    contains = [t for t in tags if re.search(rf"(?<![\w.]){re.escape(plain)}(?![\w])", t)]
    if minecraft:
        with_mc = [t for t in contains if _mc_in(t, minecraft)]
        if with_mc:
            return with_mc[0]
    return contains[0] if contains else None


def match_branch(branches: Iterable[str], minecraft: str) -> Optional[str]:
    """Branch dedicated to a Minecraft version, e.g. ``1.21.1``, ``mc1.20.1/dev``,
    ``26.3``, or a wildcard line like ``1.21.x`` / ``1.21`` covering it."""
    branches = list(branches)
    hits = [b for b in branches if _mc_in(b, minecraft)]
    if not hits:
        parts = minecraft.split(".")
        if len(parts) >= 2:
            line = ".".join(parts[:2])
            wild = re.compile(rf"(?<![\d.]){re.escape(line)}\.[xX*](?![\w])")
            hits = [b for b in branches if wild.search(b)]
            if not hits and len(parts) == 3:
                hits = [b for b in branches if re.search(rf"(?<![\d.]){re.escape(line)}(?![\d.])", b)]
    if not hits:
        return None
    # Prefer shorter / more canonical names (``1.20.1`` over ``1.20.1-old-experiment``).
    return sorted(hits, key=lambda b: (("dev" not in b and b != minecraft and not b.endswith(minecraft)), len(b)))[0]


def best_start_ref(tags: list[str], branches: list[str], target_mc: str,
                   newer_versions: Iterable[str]) -> Optional[tuple[str, str]]:
    """Closest starting point to the target: the author's own branch for the
    target, else the branch for the newest version they released for.
    Returns (branch, minecraft version) or None. Porting from 1.21.1 to 26.3
    is far less work than from 1.20.1, and the feature check still compares
    against the user's original build."""
    b = match_branch(branches, target_mc)
    if b:
        return b, target_mc
    for mc in sorted(set(newer_versions), key=Version, reverse=True):
        b = match_branch(branches, mc)
        if b:
            return b, mc
    return None


@dataclass
class Checkout:
    url: str
    path: Path
    ref: str
    ref_kind: str   # "tag" | "branch" | "default"
    note: str = ""


def fetch_source(url: str, dest: Path, mod_version: str, minecraft_from: Optional[str],
                 prefer_branch: Optional[str] = None) -> Checkout:
    """Clone ``url`` and check out the commit matching the user's build, so the
    port starts from exactly the features they have. ``prefer_branch`` (the
    author's own branch for the target version) wins when given."""
    tags, branches = remote_refs(url)
    tag = match_tag(tags, mod_version, minecraft_from)
    dest = Path(dest)
    if dest.exists() and (dest / ".git").exists():
        _git("fetch", "--tags", "--force", "origin", cwd=dest)
        _git("reset", "--hard", cwd=dest)
        _git("clean", "-fdx", "-e", ".modsmith", cwd=dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--filter=blob:none", url, str(dest), timeout=1800)
    if prefer_branch and prefer_branch in branches:
        _git("checkout", "--force", f"origin/{prefer_branch}", cwd=dest)
        return Checkout(url, dest, prefer_branch, "branch",
                        f"started from the author's newest branch '{prefer_branch}' (closest to the target); "
                        "content is still checked against your installed build")
    if tag:
        _git("checkout", "--force", f"tags/{tag}", cwd=dest)
        return Checkout(url, dest, tag, "tag", f"checked out tag {tag} matching installed {mod_version}")
    branch = match_branch(branches, minecraft_from) if minecraft_from else None
    if branch:
        _git("checkout", "--force", f"origin/{branch}", cwd=dest)
        return Checkout(url, dest, branch, "branch",
                        f"no tag for {mod_version}; using branch {branch} (may differ slightly from the installed build)")
    head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=dest).strip()
    return Checkout(url, dest, head, "default",
                    f"no tag or {minecraft_from} branch found; using default branch {head} - features may differ")


def upstream_target_branch(url: str, minecraft_to: str) -> Optional[str]:
    """If the author already has a branch for the target version (work in
    progress, not released yet), return it - building that is better than
    porting ourselves."""
    try:
        _, branches = remote_refs(url)
    except Exception:  # noqa: BLE001
        return None
    return match_branch(branches, minecraft_to)
