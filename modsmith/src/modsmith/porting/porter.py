"""Agent-driven port loop with build and feature-parity gates.

    agent edits  ->  gradle build  ->  jar metadata check  ->  feature diff
         ^                 |                    |                    |
         +---- errors -----+------ problems ----+---- missing ids ---+

A port is only accepted when the build passes, the jar targets the requested
Minecraft version and loader, and every baseline feature is still present.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..features import diff_features, extract_features
from ..jarscan import read_jar
from ..models import FeatureDiff, FeatureSet
from ..platform import TargetInfo
from ..versions import loader_aliases, satisfies
from . import prompts
from .agent import Agent
from .builder import find_java, run_gradle

Log = Callable[[str], None]


@dataclass
class PortResult:
    ok: bool
    jar: Optional[Path] = None
    diff: Optional[FeatureDiff] = None
    attempts: int = 0
    notes: list[str] = field(default_factory=list)
    workdir: Optional[Path] = None


def _git(workdir: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workdir, capture_output=True, text=True)


def _snapshot(workdir: Path, message: str) -> None:
    if not (workdir / ".git").exists():
        _git(workdir, "init", "-q")
    _git(workdir, "add", "-A")
    _git(workdir, "-c", "user.name=modsmith", "-c", "user.email=modsmith@localhost",
         "commit", "-q", "--allow-empty", "-m", message)


def check_metadata(jar: Path, target: TargetInfo) -> Optional[str]:
    try:
        info = read_jar(jar, with_hashes=False)
    except Exception as e:  # noqa: BLE001
        return f"built jar has no readable mod metadata ({e})"
    if info.loader and info.loader.value not in loader_aliases(target.loader, target.minecraft):
        return f"jar is a {info.loader.value} mod, expected {target.loader}"
    if info.minecraft_range not in ("", "*") and not satisfies(target.minecraft, info.minecraft_range):
        return f"jar declares Minecraft {info.minecraft_range}, which excludes {target.minecraft}"
    return None


def port(*, workdir: Path, name: str, mod_id: str, from_mc: str, from_loader: str,
         target: TargetInfo, baseline: FeatureSet, agent: Agent, java_homes: dict,
         max_attempts: int = 6, build_timeout: int = 1800, decompiled: bool = False,
         ref_note: str = "", log: Log = print) -> PortResult:
    workdir = Path(workdir)
    meta = workdir / ".modsmith"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "baseline-features.json").write_text(json.dumps(baseline.to_json(), indent=1))
    (meta / "target.json").write_text(json.dumps(target.__dict__, indent=1))
    exclude = workdir / ".git" / "info" / "exclude"
    _snapshot(workdir, "modsmith: baseline before port")
    if exclude.parent.exists():
        with open(exclude, "a") as f:
            f.write("\n.modsmith/*.log\nbuild/\n.gradle/\n")

    java = find_java(target.java, java_homes)
    result = PortResult(ok=False, workdir=workdir)
    if java is None:
        result.notes.append(f"JDK {target.java} not found; install it or set java_home.{target.java} in settings")
        log(f"  ! {result.notes[-1]}")
        return result
    env = {"JAVA_HOME": str(java)}

    ok, why = agent.available()
    if not ok:
        result.notes.append(why)
        return result

    prompt = prompts.initial_prompt(name, mod_id, from_mc, from_loader, target, baseline.total(),
                                    decompiled, ref_note)
    agent_log = meta / "agent.log"
    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        log(f"  agent pass {attempt}/{max_attempts}...")
        agent.run(prompt, workdir, env=env, log_file=agent_log)
        _snapshot(workdir, f"modsmith: agent pass {attempt}")

        log("  building...")
        build = run_gradle(workdir, java, timeout=build_timeout, loader=target.loader,
                           log_file=meta / "build.log")
        if not build.ok:
            log(f"  build failed ({len(build.errors)} error lines)")
            prompt = prompts.build_failed_prompt(build.summary(), attempt, max_attempts)
            continue
        jar = build.jars[0]
        problem = check_metadata(jar, target)
        if problem:
            log(f"  metadata problem: {problem}")
            prompt = prompts.metadata_prompt(problem)
            continue
        candidate = extract_features(jar, namespaces=baseline_namespaces(baseline) or None)
        diff = diff_features(baseline, candidate)
        (meta / "feature-diff.json").write_text(json.dumps(
            {"missing": diff.missing, "added": diff.added}, indent=1))
        notes_path = meta / "PORT_NOTES.md"
        explained = notes_path.read_text(errors="replace") if notes_path.exists() else ""
        if diff.missing.get("mixins"):
            # A mixin replaced by an equivalent loader API is fine if the agent says so.
            still = [m for m in diff.missing["mixins"] if m not in explained]
            if still:
                diff.missing["mixins"] = still
            else:
                del diff.missing["mixins"]
        result.diff, result.jar = diff, jar
        if diff.is_lossless:
            result.ok = True
            log(f"  build OK, all {diff.baseline_total} features present")
            break
        log(f"  build OK but {diff.missing_count} features missing; asking agent to restore them")
        prompt = prompts.feature_loss_prompt(diff)

    notes_file = meta / "PORT_NOTES.md"
    if notes_file.exists():
        result.notes.append(f"Agent notes: {notes_file}")
    if not result.ok:
        result.notes.append(f"Port did not pass all checks after {result.attempts} passes; logs in {meta}")
    return result


def baseline_namespaces(fs: FeatureSet) -> set[str]:
    ns = set()
    for ids in fs.categories.values():
        for i in ids:
            # "create:cogwheel", "block/create:cogwheel", "configured_feature/create:ore"
            if ":" in i:
                ns.add(i.split(":", 1)[0].split("/")[-1])
    return ns


def copy_output(jar: Path, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / name
    shutil.copy2(jar, dest)
    return dest
