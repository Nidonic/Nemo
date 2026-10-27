"""shell: run a command (PowerShell on Windows, bash elsewhere) with a scrubbed environment.

Baseline SENSITIVE (arbitrary commands, arbitrary egress). Plainly read-only commands drop to MODERATE.
Dangerous patterns attach warnings, which always ask.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
from typing import Any

from nemo.schema import RiskLevel
from nemo.sentinel.env import scrubbed_env
from nemo.tools.base import BaseTool, CallAssessment, ToolContext, clip, obj

IS_WINDOWS = sys.platform == "win32"

DANGEROUS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b|\brm\s+-r\b.*\s/(\s|$)", "rm -rf deletes files recursively"),
    (r"\bsudo\b|\bdoas\b|\brunas\b|-verb\s+runas", "runs with administrator rights"),
    (r"\bmkfs(\.\w+)?\b|\bformat(\.com)?\s+[a-z]:|\bdiskpart\b", "can wipe a disk"),
    (r"\bdd\s+[^|]*\bof=", "dd writes raw data to a device or file"),
    (r"\b(curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b[^|;&]*\|\s*(sh|bash|zsh|python\d?|iex|invoke-expression)\b",
     "pipes a downloaded script straight into a shell"),
    (r"\bchmod\s+(-R\s+)?0?777\b|\bchmod\s+-R\b", "changes permissions recursively"),
    (r"remove-item\b.*-recurse|\bdel\s+/s\b|\brd\s+/s\b|\brmdir\s+/s\b", "deletes a folder tree"),
    (r"\bgit\s+push\b.*(--force|-f\b)|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*f", "rewrites or discards git history"),
    (r"\bshutdown\b|\breboot\b|restart-computer|stop-computer|\bpoweroff\b", "shuts the computer down"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "is a fork bomb"),
    (r">\s*/dev/(sd|nvme|disk)", "writes directly to a disk device"),
    (r"\breg\s+delete\b|remove-itemproperty\b.*hk(lm|cu)", "deletes registry entries"),
]

# plain read-only commands (no chaining, no redirection) are MODERATE instead of SENSITIVE
READ_ONLY = re.compile(
    r"^\s*(ls|dir|pwd|cat|type|head|tail|wc|echo|whoami|hostname|date|uname|which|where|tree|"
    r"get-childitem|gci|get-content|gc|get-location|get-date|get-process|get-command|"
    r"git\s+(status|log|diff|show|branch|remote\s+-v)|"
    r"(python3?|py|node|npm|pip3?|git|java|go|cargo|rustc|dotnet)\s+(--version|-v|-V|version))\b[^;&|<>`$]*$",
    re.IGNORECASE,
)


def first_word(command: str) -> str:
    words = re.findall(r"[\w.+-]+", command)
    if not words:
        return "?"
    first = words[0].lower()
    if first in ("git", "npm", "pip", "pip3", "docker", "cargo", "kubectl", "gh") and len(words) > 1:
        return f"{first} {words[1].lower()}"
    return first


def _argv(command: str) -> list[str]:
    if IS_WINDOWS:
        exe = shutil.which("pwsh") or "powershell"
        prelude = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
        return [exe, "-NoProfile", "-NonInteractive", "-Command", prelude + command]
    return [shutil.which("bash") or "/bin/sh", "-c", command]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        proc.kill()


def run_process(argv: list[str], cwd: str, timeout: int, env: dict[str, str], cancel: "asyncio.Event | None" = None,
                stdin: bytes | None = None) -> tuple[int | None, str]:
    kwargs: dict[str, Any] = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {"start_new_session": True}
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    try:
        out, _ = proc.communicate(input=stdin, timeout=timeout)
        return proc.returncode, out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out = b""
        return None, out.decode("utf-8", "replace")


class ShellTool(BaseTool):
    name = "shell"
    description = (
        f"Run a command in {'PowerShell (Windows)' if IS_WINDOWS else 'bash'} and return its output. "
        "Starts in the workspace unless cwd is given. Non-interactive: no prompts, no TTY. Credentials are removed "
        "from its environment. Prefer this (rung 1) over driving apps through the screen."
    )
    parameters = obj({
        "command": {"type": "string", "description": "The command line to run."},
        "cwd": {"type": "string", "description": "Working folder, absolute or relative to the workspace."},
        "timeout": {"type": "integer", "description": "Seconds before the command is stopped (default 120, max 1800)."},
    }, ["command"])
    risk = RiskLevel.SENSITIVE

    def target(self, args: dict[str, Any]) -> str:
        return first_word(str(args.get("command", "")))

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        command = str(args.get("command", ""))
        a = CallAssessment(risk=self.risk, target=self.target(args), summary=command[:300], egress="*", network=True)
        if READ_ONLY.match(command):
            a.risk, a.egress = RiskLevel.MODERATE, None
        else:
            a.notes.append("Runs a command on this computer.")
        for pattern, why in DANGEROUS:
            if re.search(pattern, command, re.IGNORECASE):
                a.warn(f"This command {why}.")
        return a

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> str:
        command = str(args.get("command", "")).strip()
        if not command:
            return "Error: no command given."
        timeout = max(1, min(int(args.get("timeout", 120)), 1800))
        cwd = ctx.resolve(args["cwd"]) if args.get("cwd") else ctx.workspace
        if not cwd.is_dir():
            return f"Error: {cwd} is not a directory."
        code, output = await asyncio.to_thread(run_process, _argv(command), str(cwd), timeout, scrubbed_env())
        output = clip(output.strip()) or "(no output)"
        if code is None:
            return f"{output}\n\n[timed out after {timeout}s and was stopped]"
        return output if code == 0 else f"{output}\n\n[exit code {code}]"
