"""python_execute: run a Python snippet in a subprocess, inside bubblewrap on Linux, with a scrubbed environment.

MODERATE by default; the snippet is read to escalate to SENSITIVE when it reaches the network, starts processes,
reads environment variables or touches paths outside the workspace.
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from nemo.schema import RiskLevel
from nemo.sentinel.env import scrubbed_env
from nemo.sentinel.sandbox import wrap
from nemo.tools.base import BaseTool, CallAssessment, ToolContext, clip, obj
from nemo.tools.shell import run_process

NETWORK = re.compile(r"^\s*(import|from)\s+(requests|httpx|aiohttp|socket|urllib\d?|http\.client|ftplib|smtplib|"
                     r"paramiko|websockets?|telnetlib|imaplib|poplib)\b", re.MULTILINE)
PROCESS = re.compile(r"\bsubprocess\b|\bos\.(system|popen|exec\w*|spawn\w*|fork|kill)\b|\bpty\b|\bmultiprocessing\b")
ENVIRON = re.compile(r"\bos\.environ\b|\bos\.getenv\b|\bgetenv\(")
PATH_LITERAL = re.compile(r"""["']((?:[A-Za-z]:[\\/]|/|~[\\/])[^"'\n]*)["']""")


class PythonTool(BaseTool):
    name = "python_execute"
    description = (
        "Run a Python 3 snippet and return what it prints. Runs in the workspace folder, sandboxed where the OS "
        "allows; network, subprocess and environment access need approval. Use print() to show results."
    )
    parameters = obj({
        "code": {"type": "string", "description": "Python source to run."},
        "timeout": {"type": "integer", "description": "Seconds (default 60, max 600)."},
    }, ["code"])
    risk = RiskLevel.MODERATE

    def summary(self, args: dict[str, Any]) -> str:
        return str(args.get("code", "")).strip().splitlines()[0][:200] if args.get("code") else ""

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        code = str(args.get("code", ""))
        a = CallAssessment(risk=self.risk, target="", summary=code[:600])
        if NETWORK.search(code):
            a.escalate(RiskLevel.SENSITIVE, "The script uses the network.")
            a.network, a.egress = True, "*"
        if PROCESS.search(code):
            a.escalate(RiskLevel.SENSITIVE, "The script starts other programs.")
            a.egress = "*"
        if ENVIRON.search(code):
            a.escalate(RiskLevel.SENSITIVE, "The script reads environment variables.")
        for literal in PATH_LITERAL.findall(code):
            path = Path(literal).expanduser()
            if not ctx.in_workspace(path):
                a.escalate(RiskLevel.SENSITIVE, f"The script touches {literal}, outside the workspace.")
                a.reads_private = True
                break
        return a

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> str:
        code = str(args.get("code", ""))
        timeout = max(1, min(int(args.get("timeout", 60)), 600))
        a = self.assess(args, ctx)
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=ctx.workspace, delete=False, encoding="utf-8") as f:
            f.write(code)
            script = Path(f.name)
        try:
            argv, _ = wrap([sys.executable, "-I", str(script)], ctx.workspace,
                           enabled=ctx.app.config.sentinel.sandbox, network=a.network)
            code_, out = await asyncio.to_thread(run_process, argv, str(ctx.workspace), timeout, scrubbed_env())
        finally:
            script.unlink(missing_ok=True)
        out = clip(out.strip()) or "(no output)"
        if code_ is None:
            return f"{out}\n\n[timed out after {timeout}s]"
        return out if code_ == 0 else f"{out}\n\n[exit code {code_}]"
