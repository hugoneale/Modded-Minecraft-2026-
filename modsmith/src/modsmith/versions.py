"""Version parsing, comparison and range matching.

Handles the three range syntaxes mods use:

* Maven ranges (Forge / NeoForge ``mods.toml``): ``[1.20.1,1.21)``, ``[47,)``, ``1.20.1``
* Fabric/Quilt predicates: ``>=0.14``, ``~1.20.1``, ``^1.2``, ``1.20.x``, ``*``,
  space-separated (AND) or lists (OR)
* Minecraft's own numbering, including the year-based ``26.x`` scheme and
  snapshots like ``26.4-snapshot-3`` / ``24w14a`` / ``1.21-pre1``.
"""
from __future__ import annotations

import re
from functools import total_ordering
from typing import Iterable, Optional, Union

_TOKEN = re.compile(r"\d+|[a-zA-Z]+")
# Qualifiers ordered from least to most "final".
_QUALIFIER_ORDER = {
    "snapshot": -5, "alpha": -4, "a": -4, "beta": -3, "b": -3,
    "pre": -2, "rc": -1, "": 0,
}


@total_ordering
class Version:
    """Lenient, comparable version.

    Numeric release components are compared numerically; a pre-release tail
    (``-beta.2``, ``-pre1``, ``-snapshot-3``, ``-rc1``) sorts before the release.
    Build metadata after ``+`` is ignored.
    """

    __slots__ = ("raw", "release", "pre")

    def __init__(self, raw: str):
        self.raw = raw.strip()
        core = self.raw.split("+", 1)[0]
        core = re.sub(r"^[vV]", "", core)
        # Split release digits from the qualifier tail.
        m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", core)
        if m:
            self.release = tuple(int(p) for p in m.group(1).split("."))
            tail = m.group(2).lstrip(".-_")
        else:
            self.release = ()
            tail = core
        self.pre = self._parse_tail(tail)

    @staticmethod
    def _parse_tail(tail: str) -> tuple:
        if not tail:
            return ()
        out = []
        for tok in _TOKEN.findall(tail.lower()):
            if tok.isdigit():
                out.append((1, int(tok), ""))
            else:
                out.append((0, _QUALIFIER_ORDER.get(tok, -6), tok))
        return tuple(out)

    def _key(self):
        rel = self.release
        # Trailing zeros don't matter: 1.20 == 1.20.0
        while rel and rel[-1] == 0:
            rel = rel[:-1]
        # A version with a pre-release tail sorts before the bare release.
        return (rel, 0 if self.pre else 1, self.pre)

    def __eq__(self, other):
        if not isinstance(other, Version):
            other = Version(str(other))
        return self._key() == other._key()

    def __lt__(self, other):
        if not isinstance(other, Version):
            other = Version(str(other))
        return self._key() < other._key()

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return f"Version({self.raw!r})"

    def __str__(self):
        return self.raw

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)


VersionLike = Union[str, Version]


def _v(x: VersionLike) -> Version:
    return x if isinstance(x, Version) else Version(x)


# --------------------------------------------------------------------------- ranges

class Range:
    """Union of intervals; ``contains`` answers whether a version satisfies it."""

    def __init__(self, raw: str, intervals: list[tuple]):
        self.raw = raw
        # each interval: (lo: Version|None, lo_incl, hi: Version|None, hi_incl)
        self.intervals = intervals

    def contains(self, version: VersionLike) -> bool:
        v = _v(version)
        for lo, lo_inc, hi, hi_inc in self.intervals:
            if lo is not None and (v < lo or (v == lo and not lo_inc)):
                continue
            if hi is not None and (v > hi or (v == hi and not hi_inc)):
                continue
            return True
        return False

    def __repr__(self):
        return f"Range({self.raw!r})"


ANY = Range("*", [(None, True, None, True)])


def parse_range(raw: Union[str, Iterable[str], None]) -> Range:
    """Parse a Maven range, a Fabric predicate, or a list of either (OR)."""
    if raw is None:
        return ANY
    if not isinstance(raw, str):
        parts = list(raw)
        if not parts:
            return ANY
        intervals = []
        for p in parts:
            intervals.extend(parse_range(p).intervals)
        return Range(" || ".join(parts), intervals)
    s = raw.strip()
    if s in ("", "*", "any"):
        return ANY
    if s[0] in "[(":
        return _parse_maven(s)
    if "||" in s:
        intervals = []
        for p in s.split("||"):
            intervals.extend(parse_range(p).intervals)
        return Range(s, intervals)
    return _parse_predicates(s)


def _parse_maven(s: str) -> Range:
    intervals = []
    for m in re.finditer(r"([\[(])([^\[\]()]*)([\])])", s):
        lo_inc = m.group(1) == "["
        hi_inc = m.group(3) == "]"
        body = m.group(2)
        if "," not in body:
            v = Version(body)
            intervals.append((v, True, v, True))
            continue
        lo_s, hi_s = (x.strip() for x in body.split(",", 1))
        lo = Version(lo_s) if lo_s else None
        hi = Version(hi_s) if hi_s else None
        intervals.append((lo, lo_inc, hi, hi_inc))
    return Range(s, intervals or [(None, True, None, True)])


def _bump(release: tuple, index: int) -> Version:
    parts = list(release[: index + 1]) or [0]
    while len(parts) < index + 1:
        parts.append(0)
    parts[index] += 1
    return Version(".".join(str(p) for p in parts))


def _parse_predicates(s: str) -> Range:
    lo, lo_inc, hi, hi_inc = None, True, None, True

    def tighten_lo(v, inc):
        nonlocal lo, lo_inc
        if lo is None or v > lo or (v == lo and not inc):
            lo, lo_inc = v, inc

    def tighten_hi(v, inc):
        nonlocal hi, hi_inc
        if hi is None or v < hi or (v == hi and not inc):
            hi, hi_inc = v, inc

    for tok in s.replace(",", " ").split():
        m = re.match(r"^(>=|<=|>|<|=|~|\^)?(.+)$", tok)
        op, ver = m.group(1) or "", m.group(2)
        if ver in ("*", "x", "X"):
            continue
        if re.search(r"\.[xX*]$", ver) or re.search(r"\.[xX*]\.", ver):
            prefix = re.split(r"\.[xX*]", ver)[0]
            base = Version(prefix)
            tighten_lo(base, True)
            tighten_hi(_bump(base.release, len(base.release) - 1), False)
            continue
        v = Version(ver)
        if op == ">=":
            tighten_lo(v, True)
        elif op == ">":
            tighten_lo(v, False)
        elif op == "<=":
            tighten_hi(v, True)
        elif op == "<":
            tighten_hi(v, False)
        elif op == "~":
            tighten_lo(v, True)
            tighten_hi(_bump(v.release, 1 if len(v.release) > 1 else 0), False)
        elif op == "^":
            tighten_lo(v, True)
            first_nonzero = next((i for i, p in enumerate(v.release) if p), 0)
            tighten_hi(_bump(v.release, first_nonzero), False)
        else:  # exact
            tighten_lo(v, True)
            tighten_hi(v, True)
    return Range(s, [(lo, lo_inc, hi, hi_inc)])


def satisfies(version: VersionLike, rng: Union[str, Iterable[str], None]) -> bool:
    return parse_range(rng).contains(version)


def newest(versions: Iterable[VersionLike]) -> Optional[str]:
    vs = [str(v) for v in versions]
    return max(vs, key=Version) if vs else None


# ----------------------------------------------------------------- Minecraft facts

def is_year_versioned(mc: str) -> bool:
    """True for the 2026+ ``YY.drop[.hotfix]`` scheme (26.1, 26.3.1, ...)."""
    v = Version(mc)
    return bool(v.release) and v.release[0] >= 25 and v.release[0] < 100


def required_java(mc: str) -> int:
    """Java major version a given Minecraft release requires."""
    v = Version(mc)
    if is_year_versioned(mc):
        return 25
    if v >= Version("1.20.5"):
        return 21
    if v >= Version("1.18"):
        return 17
    if v >= Version("1.17"):
        return 16
    return 8


def default_loader_for(mc: str) -> str:
    """Loader most of the Forge ecosystem uses at that Minecraft version."""
    return "neoforge" if Version(mc) >= Version("1.20.2") else "forge"


def loader_aliases(loader: str, mc: str) -> list[str]:
    """Loaders whose builds can run on ``loader`` at ``mc``.

    NeoForge for 1.20.1 is a fork of Forge 47 and still loads Forge mods; from
    1.20.2 onward they diverged. Quilt can run most Fabric mods.
    """
    loader = loader.lower()
    if loader == "neoforge" and Version(mc) == Version("1.20.1"):
        return ["neoforge", "forge"]
    if loader == "quilt":
        return ["quilt", "fabric"]
    return [loader]
