"""What a tool is: a schema the model sees, a baseline risk, an argument-aware assessment, and an executor."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import urlparse

from nemo.schema import Attachment, RiskLevel, ToolCall, ToolResult

if TYPE_CHECKING:
    from nemo.app import App

log = logging.getLogger("nemo.tools")
MAX_OUTPUT = 20_000


@dataclass
class CallAssessment:
    """A tool's own view of one call, which the Sentinel combines with policy."""

    risk: RiskLevel
    target: str = ""  # grant scope: grant_key = f"{tool}:{target}"
    warnings: list[str] = field(default_factory=list)
    egress: str | None = None  # host data is sent to; "*" for arbitrary egress (e.g. a shell)
    reads_private: bool = False  # marks the conversation as tainted once it runs
    network: bool = False  # the sandbox keeps the network namespace when True
    summary: str = ""
    notes: list[str] = field(default_factory=list)  # why the risk was raised, shown with the approval

    def escalate(self, risk: RiskLevel, note: str) -> None:
        self.risk = self.risk.max(risk)
        if note not in self.notes:
            self.notes.append(note)

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)
        self.risk = RiskLevel.SENSITIVE


@dataclass
class ToolContext:
    app: "App"
    conversation_id: str
    workspace: Path
    device: str = "local"
    # set by the Sentinel for this call only: trusted config with vault placeholders resolved
    secrets: dict[str, Any] = field(default_factory=dict)
    cancel: asyncio.Event | None = None
    on_progress: Callable[[str], Awaitable[None]] | None = None

    def resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        return (p if p.is_absolute() else self.workspace / p).resolve()

    def in_workspace(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.workspace.resolve())
            return True
        except ValueError:
            return False


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    risk: RiskLevel = RiskLevel.SAFE

    def target(self, args: dict[str, Any]) -> str:
        return ""

    def summary(self, args: dict[str, Any]) -> str:
        for key in ("command", "path", "query", "url", "text", "name", "action", "id"):
            if args.get(key):
                return str(args[key])[:200]
        return ""

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        return CallAssessment(risk=self.risk, target=self.target(args), summary=self.summary(args))

    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> "str | ToolResult": ...

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class FunctionTool(BaseTool):
    """A tool from a plain async function, for the simple cases."""

    def __init__(self, name: str, description: str, parameters: dict[str, Any],
                 fn: Callable[[dict[str, Any], ToolContext], Awaitable["str | ToolResult"]],
                 risk: RiskLevel = RiskLevel.SAFE,
                 assess: Callable[[dict[str, Any], ToolContext, CallAssessment], None] | None = None,
                 target: Callable[[dict[str, Any]], str] | None = None):
        self.name, self.description, self.parameters, self.risk = name, description, parameters, risk
        self._fn, self._assess, self._target = fn, assess, target

    def target(self, args: dict[str, Any]) -> str:
        return self._target(args) if self._target else ""

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        a = super().assess(args, ctx)
        if self._assess:
            self._assess(args, ctx, a)
        return a

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> "str | ToolResult":
        return await self._fn(args, ctx)


def url_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


def clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return f"{text[:head]}\n\n… [{len(text) - limit} characters cut] …\n\n{text[-(limit - head):]}"


class ToolCollection:
    def __init__(self, *tools: BaseTool):
        self.tools: dict[str, BaseTool] = {}
        self.add(*tools)

    def add(self, *tools: BaseTool) -> None:
        for tool in tools:
            self.tools[tool.name] = tool

    def remove_prefix(self, prefix: str) -> None:
        for name in [n for n in self.tools if n.startswith(prefix)]:
            del self.tools[name]

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self.tools


async def safe_execute(tool: BaseTool, call: ToolCall, ctx: ToolContext) -> ToolResult:
    """Run a tool, turning every failure into a ToolResult the model can read."""
    missing = [k for k in tool.parameters.get("required", []) if k not in call.arguments]
    if missing:
        return ToolResult(call_id=call.id, name=call.name, ok=False,
                          content=f"Error: missing argument(s): {', '.join(missing)}.")
    try:
        out = await tool.execute(call.arguments, ctx)
    except asyncio.CancelledError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        return ToolResult(call_id=call.id, name=call.name, ok=False, content=f"Error: bad arguments ({exc}).")
    except Exception as exc:  # a failing tool must never take the agent down
        log.exception("tool %s failed", call.name)
        return ToolResult(call_id=call.id, name=call.name, ok=False, content=f"Error: {type(exc).__name__}: {exc}")
    if isinstance(out, ToolResult):
        out.call_id, out.name = call.id, call.name
        out.content = clip(out.content)
        return out
    text = str(out)
    return ToolResult(call_id=call.id, name=call.name, ok=not text.startswith("Error"), content=clip(text) or "(no output)")


def image_result(text: str, png: bytes, width: int, height: int, mime: str = "image/png") -> ToolResult:
    import base64

    return ToolResult(call_id="", name="", content=text,
                      attachments=[Attachment(mime=mime, data=base64.b64encode(png).decode(), width=width, height=height)])
