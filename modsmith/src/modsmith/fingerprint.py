"""File hashes used by mod providers to identify a jar.

CurseForge identifies files with MurmurHash2 (seed 1) computed over the file
with whitespace bytes (tab, LF, CR, space) removed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_CF_SKIP = {9, 10, 13, 32}


def murmur2(data: bytes, seed: int = 1) -> int:
    m = 0x5BD1E995
    length = len(data)
    h = (seed ^ length) & 0xFFFFFFFF
    i = 0
    while length - i >= 4:
        k = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16) | (data[i + 3] << 24)
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> 24
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
        i += 4
    rem = length - i
    if rem == 3:
        h ^= data[i + 2] << 16
    if rem >= 2:
        h ^= data[i + 1] << 8
    if rem >= 1:
        h ^= data[i]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h


def curseforge_fingerprint(data: bytes) -> int:
    return murmur2(bytes(b for b in data if b not in _CF_SKIP))


def file_hashes(path: Path) -> tuple[str, str, int]:
    """Return (sha1, sha512, curseforge fingerprint) for a file."""
    data = Path(path).read_bytes()
    return (
        hashlib.sha1(data).hexdigest(),
        hashlib.sha512(data).hexdigest(),
        curseforge_fingerprint(data),
    )
