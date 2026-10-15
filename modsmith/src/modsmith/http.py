"""Tiny HTTP client on top of urllib: JSON helpers, retries, rate-limit backoff,
streamed downloads with hash verification, and an on-disk cache for GETs."""
from __future__ import annotations

import hashlib
import json
import os
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__

USER_AGENT = f"modsmith/{__version__} (+https://github.com/hugoneale/Modded-Minecraft-2026-)"


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.url = url
        self.body = body


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if bundle and Path(bundle).exists():
        ctx.load_verify_locations(bundle)
    return ctx


class Http:
    def __init__(self, cache_dir: Optional[Path] = None, cache_ttl: int = 3600,
                 retries: int = 4, timeout: int = 60):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.retries = retries
        self.timeout = timeout
        self._ctx = _ssl_context()

    # ---------------------------------------------------------------- core
    def request(self, method: str, url: str, *, params: Optional[dict] = None,
                json_body: Any = None, headers: Optional[dict] = None,
                data: Optional[bytes] = None) -> bytes:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
                 for k, v in params.items() if v is not None})
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        hdrs.update(headers or {})
        if json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace") if e.fp else ""
                if e.code == 429 or e.code >= 500:
                    wait = float(e.headers.get("Retry-After") or e.headers.get("X-Ratelimit-Reset") or 0)
                    last = HttpError(e.code, url, body)
                    time.sleep(min(max(wait, 2 ** attempt), 60) + random.random())
                    continue
                raise HttpError(e.code, url, body) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                time.sleep(2 ** attempt + random.random())
        raise RuntimeError(f"Request failed after {self.retries + 1} attempts: {url}: {last}")

    def _cache_path(self, key: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        return self.cache_dir / "http" / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    def get_json(self, url: str, *, params: Optional[dict] = None,
                 headers: Optional[dict] = None, cache: bool = True) -> Any:
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        cp = self._cache_path(key) if cache else None
        if cp and cp.exists() and time.time() - cp.stat().st_mtime < self.cache_ttl:
            return json.loads(cp.read_text(encoding="utf-8"))
        raw = self.request("GET", url, params=params, headers=headers)
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_bytes(raw)
        return json.loads(raw) if raw else None

    def get_text(self, url: str, headers: Optional[dict] = None) -> str:
        return self.request("GET", url, headers={"Accept": "*/*", **(headers or {})}).decode("utf-8", "replace")

    def post_json(self, url: str, body: Any, headers: Optional[dict] = None) -> Any:
        raw = self.request("POST", url, json_body=body, headers=headers)
        return json.loads(raw) if raw else None

    def download(self, url: str, dest: Path, *, sha1: Optional[str] = None,
                 sha512: Optional[str] = None, headers: Optional[dict] = None,
                 progress: Optional[Callable[[int, int], None]] = None) -> Path:
        """Stream ``url`` to ``dest`` and verify hashes. Skips the download when
        ``dest`` already exists with a matching hash."""
        dest = Path(dest)
        if dest.exists() and (sha1 or sha512) and _matches(dest, sha1, sha512):
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers=hdrs)
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r, open(tmp, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    done = 0
                    while chunk := r.read(1 << 16):
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
                break
            except urllib.error.HTTPError as e:
                if e.code < 500 and e.code != 429:
                    raise HttpError(e.code, url) from None
                last = e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
            time.sleep(2 ** attempt + random.random())
        else:
            raise RuntimeError(f"Download failed: {url}: {last}")
        if (sha1 or sha512) and not _matches(tmp, sha1, sha512):
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Hash mismatch downloading {url}")
        tmp.replace(dest)
        return dest


def _matches(path: Path, sha1: Optional[str], sha512: Optional[str]) -> bool:
    data = path.read_bytes()
    if sha512:
        return hashlib.sha512(data).hexdigest() == sha512.lower()
    if sha1:
        return hashlib.sha1(data).hexdigest() == sha1.lower()
    return True
