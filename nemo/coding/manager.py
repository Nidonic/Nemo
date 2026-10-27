"""Coding agents on this computer: find their sessions on disk, read transcripts, and drive new runs.

Claude Code  ~/.claude/projects/<project>/<session>.jsonl   claude -p --output-format stream-json
Codex        ~/.codex/sessions/**/rollout-*.jsonl           codex exec --json
Cursor       ~/.cursor/projects/**/*.jsonl                  cursor-agent -p --output-format stream-json
dsh          ~/.dsh/sessions/*                              (listed; zstd frames are not decoded here)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from nemo.schema import new_id
from nemo.sentinel.env import scrubbed_env

HOME = Path.home()

AGENTS: dict[str, dict[str, Any]] = {
    "claude": {"label": "Claude Code", "dir": HOME / ".claude" / "projects", "glob": "*/*.jsonl", "exe": "claude",
               "argv": lambda p, resume: ["-p", p, "--output-format", "stream-json", "--verbose",
                                          "--permission-mode", "acceptEdits", *(["--resume", resume] if resume else [])]},
    "codex": {"label": "Codex", "dir": HOME / ".codex" / "sessions", "glob": "**/rollout-*.jsonl", "exe": "codex",
              "argv": lambda p, resume: ["exec", "--json", p]},
    "cursor": {"label": "Cursor", "dir": HOME / ".cursor" / "projects", "glob": "**/*.jsonl", "exe": "cursor-agent",
               "argv": lambda p, resume: ["-p", p, "--output-format", "stream-json", *(["--resume", resume] if resume else [])]},
    "dsh": {"label": "dsh", "dir": HOME / ".dsh" / "sessions", "glob": "*", "exe": "dsh", "argv": None},
}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                value = c.get("text") or c.get("content")
                parts.append(value if isinstance(value, str) else "")
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return _text_of(content.get("content") or content.get("text") or "")
    return ""


def parse_line(line: str) -> dict | None:
    """One transcript line → {role, text} in a shape that fits all three agents' JSONL."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
    role = msg.get("role") or obj.get("type") or ""
    text = _text_of(msg.get("content") if "content" in msg else msg.get("text", ""))
    if role in ("user", "assistant") and text.strip():
        return {"role": role, "text": text.strip()}
    if obj.get("type") == "result" and isinstance(obj.get("result"), str):
        return {"role": "assistant", "text": obj["result"]}
    return None


@dataclass
class Run:
    id: str
    agent: str
    prompt: str
    cwd: str
    started: float = field(default_factory=time.time)
    status: str = "running"
    lines: list[dict] = field(default_factory=list)
    proc: subprocess.Popen | None = None

    def public(self) -> dict:
        return {"id": self.id, "agent": self.agent, "prompt": self.prompt, "cwd": self.cwd, "started": self.started,
                "status": self.status, "lines": self.lines[-200:]}


class CodingManager:
    def __init__(self, on_event: Callable[[dict], None] | None = None):
        self.on_event = on_event or (lambda e: None)
        self.runs: dict[str, Run] = {}

    def agents(self) -> list[dict]:
        return [{"id": k, "label": v["label"], "installed": bool(shutil.which(v["exe"])), "sessions_dir": str(v["dir"]),
                 "has_sessions": v["dir"].exists()} for k, v in AGENTS.items()]

    def sessions(self, limit: int = 40) -> list[dict]:
        found = []
        for key, spec in AGENTS.items():
            root: Path = spec["dir"]
            if not root.exists():
                continue
            for path in root.glob(spec["glob"]):
                if path.is_file():
                    try:
                        found.append((path.stat().st_mtime, key, path))
                    except OSError:
                        continue
        out = []
        for mtime, key, path in sorted(found, reverse=True)[:limit]:
            out.append({"agent": key, "id": path.stem, "path": str(path), "project": path.parent.name,
                        "updated": mtime, "title": self._title(path) if key != "dsh" else path.name})
        return out

    @staticmethod
    def _title(path: Path) -> str:
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for _, line in zip(range(200), f):
                    item = parse_line(line)
                    if item and item["role"] == "user":
                        return item["text"].splitlines()[0][:100]
        except OSError:
            pass
        return path.stem

    def transcript(self, path: str, last: int = 40) -> list[dict]:
        p = Path(path)
        roots = [spec["dir"].resolve() for spec in AGENTS.values()]
        if not any(str(p.resolve()).startswith(str(r)) for r in roots):
            raise PermissionError("Only coding-agent session files can be read here.")
        items = []
        with p.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                item = parse_line(line)
                if item:
                    items.append(item)
        return items[-last:]

    def start(self, agent: str, prompt: str, cwd: str, resume: str | None = None) -> Run:
        spec = AGENTS.get(agent)
        if not spec or not spec["argv"]:
            raise ValueError(f"Nemo cannot drive {agent}.")
        exe = shutil.which(spec["exe"])
        if not exe:
            raise ValueError(f"{spec['label']} is not installed ({spec['exe']} is not on PATH).")
        run = Run(id=new_id("run_"), agent=agent, prompt=prompt, cwd=cwd)
        kwargs: dict[str, Any] = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        # coding agents keep their own credentials; only Nemo's are scrubbed
        env = {**scrubbed_env(), **{k: v for k, v in os.environ.items() if k.startswith(("ANTHROPIC", "OPENAI", "CURSOR"))}}
        run.proc = subprocess.Popen([exe, *spec["argv"](prompt, resume)], cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, **kwargs)
        self.runs[run.id] = run
        threading.Thread(target=self._pump, args=(run,), daemon=True).start()
        self.on_event({"type": "coding", "run": run.public()})
        return run

    def _pump(self, run: Run) -> None:
        assert run.proc and run.proc.stdout
        for raw in run.proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            item = parse_line(line) or ({"role": "log", "text": line} if line and not line.startswith("{") else None)
            if item:
                run.lines.append(item)
                self.on_event({"type": "coding_line", "run_id": run.id, "line": item})
        code = run.proc.wait()
        if run.status == "running":
            run.status = "done" if code == 0 else f"exit {code}"
        self.on_event({"type": "coding", "run": run.public()})

    def stop(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if not run or not run.proc or run.proc.poll() is not None:
            return False
        run.status = "stopped"
        run.proc.terminate()
        return True
