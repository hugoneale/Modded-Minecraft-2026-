"""Last-resort: rebuild a Gradle project from a compiled jar.

Uses Vineflower (https://github.com/Vineflower/vineflower) to turn bytecode
back into Java. The result is never identical to the original source:
comments, local names and some generic types are lost, and 1.20.1-era Forge
jars use SRG names (``m_12345_``) for Minecraft methods that the porting agent
must translate. Expect a much lower success rate than a source port.
"""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Optional

from ..http import Http
from ..models import JarInfo

VINEFLOWER_VERSION = "1.12.0"
VINEFLOWER_URL = ("https://repo1.maven.org/maven2/org/vineflower/vineflower/"
                  "{v}/vineflower-{v}.jar")

DECOMPILE_WARNING = (
    "No source code was found, so this mod will be DECOMPILED from its jar. "
    "Decompiled code is an approximation of the original and the mod's licence "
    "may not permit it. The result is for your personal use only: do not "
    "redistribute it, and expect bugs. Report problems to Modsmith, never to "
    "the original author."
)


def ensure_vineflower(http: Http, tools_dir: Path) -> Path:
    jar = Path(tools_dir) / f"vineflower-{VINEFLOWER_VERSION}.jar"
    if not jar.exists():
        http.download(VINEFLOWER_URL.format(v=VINEFLOWER_VERSION), jar)
    return jar


def decompile_to_project(jar: JarInfo, workdir: Path, vineflower: Path, java: Optional[Path]) -> Path:
    """Create ``workdir`` with ``src/main/java`` (decompiled) and
    ``src/main/resources`` (copied verbatim), ready for the agent to add build
    files for the target version."""
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    java_src = workdir / "src" / "main" / "java"
    res = workdir / "src" / "main" / "resources"
    java_src.mkdir(parents=True)
    res.mkdir(parents=True)
    tmp = workdir / ".modsmith" / "decompiled"
    tmp.mkdir(parents=True)
    java_bin = str(Path(java) / "bin" / "java") if java else "java"
    r = subprocess.run([java_bin, "-jar", str(vineflower), "-dgs=1", "-rsy=1", "-ind=    ",
                        str(jar.path), str(tmp)], capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"Vineflower failed: {r.stderr[-1000:]}")
    # Vineflower writes either a directory tree or a jar of sources.
    produced = list(tmp.glob("*.jar"))
    if produced:
        with zipfile.ZipFile(produced[0]) as zf:
            zf.extractall(tmp / "out")
        root = tmp / "out"
    else:
        root = tmp
    for f in root.rglob("*"):
        if f.is_file():
            rel = f.relative_to(root)
            dest = (java_src if f.suffix == ".java" else res) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    # Resources that aren't classes come straight from the jar (assets, data, metadata).
    with zipfile.ZipFile(jar.path) as zf:
        for name in zf.namelist():
            if name.endswith("/") or name.endswith(".class") or name.startswith("META-INF/jarjar/"):
                continue
            if name.startswith("META-INF/") and name.endswith((".SF", ".RSA", ".DSA", "MANIFEST.MF")):
                continue
            dest = res / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(name))
    shutil.rmtree(tmp)
    (workdir / "DECOMPILED.txt").write_text(DECOMPILE_WARNING + "\n\nOriginal: " + jar.path.name + "\n")
    return workdir
