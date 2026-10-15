"""Run a mod's Gradle build with the right JDK and collect results."""
from __future__ import annotations

import glob
import os
import platform as _platform
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_SKIP_JAR = re.compile(r"-(sources|javadoc|dev|api|slim|all-sources|shadow-dev|dev-shadow)\.jar$")


def java_major(home: Path) -> Optional[int]:
    rel = Path(home) / "release"
    if rel.exists():
        m = re.search(r'JAVA_VERSION="(\d+)(?:\.(\d+))?', rel.read_text(errors="replace"))
        if m:
            major = int(m.group(1))
            return int(m.group(2)) if major == 1 and m.group(2) else major
    java = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
    if java.exists():
        try:
            out = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=30).stderr
            m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
            if m:
                major = int(m.group(1))
                return int(m.group(2)) if major == 1 and m.group(2) else major
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _candidate_homes() -> list[Path]:
    pats = []
    home = Path.home()
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "BellSoft", "Amazon Corretto"):
                pats.append(os.path.join(base, vendor, "*"))
    elif _platform.system() == "Darwin":
        pats += ["/Library/Java/JavaVirtualMachines/*/Contents/Home",
                 str(home / "Library/Java/JavaVirtualMachines/*/Contents/Home")]
    else:
        pats += ["/usr/lib/jvm/*", "/opt/java/*", "/opt/jdk*", "/usr/local/lib/jvm/*"]
    pats += [str(home / ".gradle/jdks/*"), str(home / ".sdkman/candidates/java/*"),
             str(home / ".jdks/*"), str(home / ".modsmith/jdks/*")]
    out = []
    for p in pats:
        out += [Path(x) for x in glob.glob(p) if Path(x, "bin").is_dir()]
    if os.environ.get("JAVA_HOME"):
        out.insert(0, Path(os.environ["JAVA_HOME"]))
    return out


def find_java(major: int, configured: Optional[dict] = None) -> Optional[Path]:
    """Locate a JDK of exactly ``major`` (or newer when none exact is found)."""
    configured = configured or {}
    for key in (str(major), major):
        if key in configured and Path(configured[key]).exists():
            return Path(configured[key])
    env = os.environ.get(f"JAVA_{major}_HOME")
    if env and Path(env).exists():
        return Path(env)
    newer = []
    for h in _candidate_homes():
        v = java_major(h)
        if v == major:
            return h
        if v and v > major:
            newer.append((v, h))
    return min(newer)[1] if newer else None


@dataclass
class BuildResult:
    ok: bool
    log: str
    errors: list[str] = field(default_factory=list)
    jars: list[Path] = field(default_factory=list)
    timed_out: bool = False

    def summary(self, limit: int = 60) -> str:
        if self.ok:
            return "BUILD SUCCESSFUL"
        lines = self.errors[:limit] or self.log.splitlines()[-limit:]
        return "\n".join(lines)


def extract_errors(log: str) -> list[str]:
    out = []
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"\.(java|kt):\d+:(?:\d+:)? error:|^e: |error: ", line):
            out.extend(lines[i:i + 3])
    m = re.search(r"\* What went wrong:\n(.*?)(?:\n\* Try:|\Z)", log, re.S)
    if m:
        out.extend(m.group(1).strip().splitlines()[:30])
    # Deduplicate while keeping order.
    seen, uniq = set(), []
    for l in out:
        if l not in seen:
            seen.add(l)
            uniq.append(l)
    return uniq


def output_jars(project: Path, loader: Optional[str] = None) -> list[Path]:
    project = Path(project)
    dirs = [project / "build" / "libs"]
    if loader:
        dirs.insert(0, project / loader / "build" / "libs")
    dirs += [Path(p) for p in glob.glob(str(project / "*" / "build" / "libs"))]
    for d in dirs:
        jars = [j for j in sorted(d.glob("*.jar")) if not _SKIP_JAR.search(j.name)] if d.is_dir() else []
        if jars:
            return sorted(jars, key=lambda j: j.stat().st_mtime, reverse=True)
    return []


def run_gradle(project: Path, java_home: Optional[Path], tasks: list[str] = None,
               timeout: int = 1800, loader: Optional[str] = None, log_file: Optional[Path] = None) -> BuildResult:
    project = Path(project)
    tasks = tasks or ["build", "-x", "test"]
    wrapper = project / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if wrapper.exists():
        if os.name != "nt":
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        cmd = [str(wrapper)]
    else:
        cmd = ["gradle"]
    cmd += [*tasks, "--no-daemon", "--console=plain", "--stacktrace"]
    env = dict(os.environ)
    if java_home:
        env["JAVA_HOME"] = str(java_home)
        env["PATH"] = str(Path(java_home) / "bin") + os.pathsep + env.get("PATH", "")
        cmd.append(f"-Dorg.gradle.java.home={java_home}")
    try:
        r = subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=timeout, env=env)
        log = r.stdout + "\n" + r.stderr
        ok = r.returncode == 0
        timed_out = False
    except subprocess.TimeoutExpired as e:
        log = (e.stdout or "") + "\n" + (e.stderr or "") if isinstance(e.stdout, str) else "build timed out"
        ok, timed_out = False, True
    except FileNotFoundError:
        log = "Gradle not found: the project has no gradlew wrapper and 'gradle' isn't on PATH."
        ok, timed_out = False, False
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text(log)
    return BuildResult(ok=ok, log=log, errors=[] if ok else extract_errors(log),
                       jars=output_jars(project, loader) if ok else [], timed_out=timed_out)


# Oldest Gradle that can run on each JDK.
_MIN_GRADLE = [(25, "9.1.0"), (24, "8.14"), (23, "8.10"), (21, "8.5"), (17, "7.3"), (16, "7.0"), (8, "6.0")]
GRADLE_FALLBACK = "9.8.1"


def min_gradle_for(java: int) -> str:
    for major, ver in _MIN_GRADLE:
        if java >= major:
            return ver
    return "6.0"


def wrapper_version(project: Path) -> Optional[str]:
    props = Path(project) / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not props.exists():
        return None
    m = re.search(r"gradle-([\d.]+(?:-rc-\d+)?)-(?:bin|all)\.zip", props.read_text(errors="replace"))
    return m.group(1) if m else None


def ensure_wrapper(project: Path, java: int, latest: Optional[str] = None) -> Optional[str]:
    """Make sure the project's Gradle wrapper can run on ``java``. Coding agents
    are often not allowed to touch wrapper files, so Modsmith does it. Returns
    the new version when it changed something."""
    from ..versions import Version
    project = Path(project)
    need = min_gradle_for(java)
    current = wrapper_version(project)
    if current and Version(current) >= Version(need):
        return None
    new = latest or GRADLE_FALLBACK
    if Version(new) < Version(need):
        new = need
    props = project / "gradle" / "wrapper" / "gradle-wrapper.properties"
    props.parent.mkdir(parents=True, exist_ok=True)
    url = f"https\\://services.gradle.org/distributions/gradle-{new}-bin.zip"
    if props.exists():
        text = props.read_text(errors="replace")
        text = re.sub(r"^distributionUrl=.*$", f"distributionUrl={url}", text, flags=re.M)
        # The old checksum (if pinned) no longer matches the new distribution.
        text = re.sub(r"^distributionSha256Sum=.*\n?", "", text, flags=re.M)
    else:
        text = ("distributionBase=GRADLE_USER_HOME\ndistributionPath=wrapper/dists\n"
                f"distributionUrl={url}\nzipStoreBase=GRADLE_USER_HOME\nzipStorePath=wrapper/dists\n")
    props.write_text(text)
    return new
