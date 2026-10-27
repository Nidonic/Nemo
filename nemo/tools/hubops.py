"""Operations other devices may ask this one for. They are not offered to the model; the hub handlers run
them through the Sentinel so a remote request is judged like a local one."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import webbrowser
from typing import Any

from nemo.schema import RiskLevel
from nemo.tools.base import FunctionTool, ToolContext, obj

MAX_FILE = 10 * 1024 * 1024


async def file_get(args: dict[str, Any], ctx: ToolContext) -> str:
    path = ctx.resolve(str(args["path"]))
    if not path.is_file():
        return f"Error: {path} is not a file."
    if path.stat().st_size > MAX_FILE:
        return f"Error: {path} is larger than 10 MB."
    return base64.b64encode(path.read_bytes()).decode()


async def file_put(args: dict[str, Any], ctx: ToolContext) -> str:
    path = ctx.resolve(str(args["path"]))
    data = base64.b64decode(str(args.get("content_b64", "")))
    if len(data) > MAX_FILE:
        return "Error: larger than 10 MB."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"Saved {len(data)} bytes to {path}."


async def open_target(args: dict[str, Any], ctx: ToolContext) -> str:
    target = str(args["target"])
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
        return f"Opened {target}."
    path = ctx.resolve(target)
    if not path.exists():
        return f"Error: {path} does not exist."
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return f"Opened {path}."


def _private(args: dict[str, Any], ctx: ToolContext, a) -> None:
    a.reads_private = True


HUB_OPS = {
    "file.get": FunctionTool("hub_file_get", "Send a file to another device.", obj({"path": {"type": "string"}}, ["path"]),
                             file_get, RiskLevel.MODERATE, assess=_private, target=lambda a: str(a.get("path", ""))),
    "file.put": FunctionTool("hub_file_put", "Save a file sent by another device.",
                             obj({"path": {"type": "string"}, "content_b64": {"type": "string"}}, ["path"]),
                             file_put, RiskLevel.SENSITIVE, target=lambda a: str(a.get("path", ""))),
    "open": FunctionTool("hub_open", "Open a URL or file with its default app.", obj({"target": {"type": "string"}}, ["target"]),
                         open_target, RiskLevel.MODERATE, target=lambda a: str(a.get("target", ""))[:80]),
}
