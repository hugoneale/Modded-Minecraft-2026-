"""Human (Markdown) and machine (JSON) reports of a run."""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .features import describe_diff
from .models import DependencyIssue, ModResult, Status
from .platform import TargetInfo

STATUS_TEXT = {
    Status.UP_TO_DATE: "Up to date",
    Status.OFFICIAL: "Official build available",
    Status.OFFICIAL_FEATURE_LOSS: "Official build, missing content",
    Status.NO_OFFICIAL: "No official build yet",
    Status.PORTED: "Ported from source",
    Status.PORTED_DECOMPILED: "Ported from decompiled jar",
    Status.PORT_FAILED: "Port failed",
    Status.NO_SOURCE: "No build, no source",
    Status.UNRESOLVED: "Not found",
    Status.SKIPPED: "Skipped",
}


def write(results: list[ModResult], issues: list[DependencyIssue], target: TargetInfo, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "target": target.__dict__,
        "summary": {k.value: v for k, v in Counter(r.status for r in results).items()},
        "mods": [r.to_json() for r in results],
        "dependency_issues": [i.__dict__ for i in issues],
    }, indent=1, default=str), encoding="utf-8")
    md = out_dir / "report.md"
    md.write_text(markdown(results, issues, target), encoding="utf-8")
    return md


def markdown(results: list[ModResult], issues: list[DependencyIssue], target: TargetInfo) -> str:
    counts = Counter(r.status for r in results)
    lines = [f"# Modsmith report: Minecraft {target.minecraft} ({target.loader})", "",
             f"{len(results)} mods checked.", ""]
    for st, text in STATUS_TEXT.items():
        if counts.get(st):
            lines.append(f"- **{text}:** {counts[st]}")
    lines += ["", "| Mod | Result | Build | Content check | Notes |", "|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (list(STATUS_TEXT).index(r.status), (r.name or "").lower())):
        build = ""
        if r.official and r.status in (Status.OFFICIAL, Status.OFFICIAL_FEATURE_LOSS, Status.UP_TO_DATE):
            build = f"{r.official.version_number} ({r.official.provider}, {r.official.release_type})"
        elif r.output_jar:
            build = r.output_jar.name
        check = describe_diff(r.feature_diff) if r.feature_diff else ""
        notes = "; ".join(r.warnings + r.notes).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {(r.name or r.ref.spec).replace('|', '/')} | {STATUS_TEXT[r.status]} | {build} | {check} | {notes} |")
    from .pipeline import coverage
    cov = coverage(results)
    if cov:
        lines += ["", "## Which Minecraft version has the most of these mods?", "",
                  "From each project's listed versions (any loader), so treat it as a guide:", ""]
        lines += [f"- {v}: {n} of {len(results)}" for v, n in cov]
    if issues:
        lines += ["", "## Dependency and compatibility issues", ""]
        for i in issues:
            fix = f" -> add `{i.suggestion}`" if i.suggestion and ":" in i.suggestion else ""
            lines.append(f"- **{i.mod}** / {i.dependency}: {i.problem}. {i.detail}{fix}")
    return "\n".join(lines) + "\n"
