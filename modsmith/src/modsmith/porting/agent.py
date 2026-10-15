"""Coding-agent backends that edit a mod's source tree.

* ``claude``  - Claude Code in headless mode (``claude -p``). Install from
  https://claude.com/claude-code and sign in once interactively.
* ``command`` - any other agent CLI. ``agent_command`` is a shell template;
  ``{prompt_file}`` and ``{workdir}`` are substituted.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CLAUDE_ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite",
    "Bash(./gradlew:*)", "Bash(gradlew.bat:*)", "Bash(gradle:*)", "Bash(git:*)",
    "Bash(ls:*)", "Bash(find:*)", "Bash(grep:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)",
    "Bash(java:*)", "Bash(javap:*)", "Bash(unzip:*)", "Bash(jar:*)", "Bash(mkdir:*)", "Bash(mv:*)",
    "Bash(cp:*)", "Bash(sed:*)", "Bash(curl:*)",
]


@dataclass
class AgentRun:
    ok: bool
    output: str


class Agent:
    name = "agent"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, prompt: str, workdir: Path, env: Optional[dict] = None,
            log_file: Optional[Path] = None, timeout: int = 7200) -> AgentRun:
        raise NotImplementedError


def _run(cmd, workdir: Path, env: Optional[dict], log_file: Optional[Path], timeout: int,
         stdin: Optional[str] = None, shell: bool = False) -> AgentRun:
    full_env = {**os.environ, **(env or {})}
    try:
        r = subprocess.run(cmd, cwd=workdir, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=full_env, shell=shell)
        out, ok = r.stdout + ("\n" + r.stderr if r.stderr else ""), r.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = "agent timed out", False
    except FileNotFoundError as e:
        out, ok = f"agent executable not found: {e}", False
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(out + "\n" + "=" * 80 + "\n")
    return AgentRun(ok, out)


class ClaudeCodeAgent(Agent):
    name = "claude"

    def __init__(self, model: str = "", executable: str = "claude", max_turns: int = 400):
        self.model = model
        self.executable = executable
        self.max_turns = max_turns

    def available(self) -> tuple[bool, str]:
        if shutil.which(self.executable):
            return True, ""
        return False, ("Claude Code CLI not found. Install it (https://claude.com/claude-code), "
                       "run `claude` once to sign in, then retry.")

    def run(self, prompt, workdir, env=None, log_file=None, timeout=7200):
        # Resolve to a full path so Windows finds claude.cmd without a shell.
        cmd = [shutil.which(self.executable) or self.executable, "-p", "--output-format", "text",
               "--permission-mode", "acceptEdits",
               "--max-turns", str(self.max_turns),
               "--allowedTools", *CLAUDE_ALLOWED_TOOLS]
        if self.model:
            cmd += ["--model", self.model]
        return _run(cmd, Path(workdir), env, log_file, timeout, stdin=prompt)


class CommandAgent(Agent):
    name = "command"

    def __init__(self, template: str):
        self.template = template

    def available(self) -> tuple[bool, str]:
        if not self.template.strip():
            return False, "agent = \"command\" needs agent_command, e.g. 'my-agent --prompt-file {prompt_file}'"
        return True, ""

    def run(self, prompt, workdir, env=None, log_file=None, timeout=7200):
        workdir = Path(workdir)
        pf = workdir / ".modsmith" / "prompt.md"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(prompt, encoding="utf-8")
        quote = (lambda x: subprocess.list2cmdline([x])) if os.name == "nt" else shlex.quote
        cmd = self.template.format(prompt_file=quote(str(pf)), workdir=quote(str(workdir)))
        return _run(cmd, workdir, env, log_file, timeout, shell=True)


def make_agent(kind: str, command: str = "", model: str = "") -> Agent:
    if kind == "command":
        return CommandAgent(command)
    return ClaudeCodeAgent(model=model)
