"""File tools. Relative paths are inside the workspace; reading outside it marks the conversation as tainted."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nemo.schema import RiskLevel
from nemo.tools.base import CallAssessment, FunctionTool, ToolContext, clip, obj

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".cache"}


def _outside(args: dict[str, Any], ctx: ToolContext, key: str = "path") -> bool:
    return not ctx.in_workspace(ctx.resolve(str(args.get(key) or ".")))


def _read_assess(args: dict[str, Any], ctx: ToolContext, a: CallAssessment) -> None:
    if _outside(args, ctx):
        a.reads_private = True


def _write_assess(args: dict[str, Any], ctx: ToolContext, a: CallAssessment) -> None:
    path = ctx.resolve(str(args.get("path", "")))
    if not ctx.in_workspace(path):
        a.escalate(RiskLevel.SENSITIVE, f"Writes outside the workspace, to {path}.")
    elif path.exists():
        a.notes.append(f"Overwrites {path}.")


async def read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = ctx.resolve(args["path"])
    if not path.is_file():
        return f"Error: {path} is not a file."
    data = path.read_bytes()
    if b"\0" in data[:4096]:
        return f"{path} is a binary file ({len(data)} bytes)."
    lines = data.decode("utf-8", "replace").splitlines()
    start = max(1, int(args.get("offset", 1)))
    limit = int(args.get("limit", 2000))
    chunk = lines[start - 1:start - 1 + limit]
    body = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(chunk))
    rest = len(lines) - (start - 1 + len(chunk))
    return clip(body + (f"\n… {rest} more lines" if rest > 0 else "")) or "(empty file)"


async def write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = ctx.resolve(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = str(args.get("content", ""))
    path.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {path}."


async def edit_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = ctx.resolve(args["path"])
    if not path.is_file():
        return f"Error: {path} is not a file."
    text = path.read_text(encoding="utf-8")
    old, new = str(args.get("old_text", "")), str(args.get("new_text", ""))
    count = text.count(old) if old else 0
    if count == 0:
        return "Error: old_text was not found. Read the file and copy the text exactly."
    if count > 1 and not args.get("replace_all"):
        return f"Error: old_text appears {count} times. Include more surrounding text, or set replace_all."
    path.write_text(text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1), encoding="utf-8")
    return f"Edited {path}."


async def list_files(args: dict[str, Any], ctx: ToolContext) -> str:
    root = ctx.resolve(args.get("path") or ".")
    if not root.is_dir():
        return f"Error: {root} is not a folder."
    depth = max(1, min(int(args.get("depth", 2)), 6))
    lines: list[str] = []

    def walk(folder, level: int) -> None:
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if len(lines) >= 500 or entry.name in _SKIP:
                continue
            indent = "  " * level
            if entry.is_dir():
                lines.append(f"{indent}{entry.name}/")
                if level + 1 < depth:
                    walk(entry, level + 1)
            else:
                try:
                    st = entry.stat()
                    lines.append(f"{indent}{entry.name}  ({st.st_size} B, {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M})")
                except OSError:
                    lines.append(f"{indent}{entry.name}")

    walk(root, 0)
    return f"{root}\n" + ("\n".join(lines) or "(empty)") + ("\n… (stopped at 500 entries)" if len(lines) >= 500 else "")


async def delete_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = ctx.resolve(args["path"])
    if not path.exists():
        return f"Error: {path} does not exist."
    if path.is_dir():
        if any(path.iterdir()):
            return f"Error: {path} is a folder that is not empty."
        path.rmdir()
    else:
        path.unlink()
    return f"Deleted {path}."


def _path_target(args: dict[str, Any]) -> str:
    return str(args.get("path", ""))


FILE_TOOLS = [
    FunctionTool("read_file", "Read a text file with line numbers; offset/limit page through long files.",
                 obj({"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
                 read_file, RiskLevel.SAFE, assess=_read_assess),
    FunctionTool("list_files", "List a folder as a tree (default depth 2).",
                 obj({"path": {"type": "string"}, "depth": {"type": "integer"}}),
                 list_files, RiskLevel.SAFE, assess=_read_assess),
    FunctionTool("write_file", "Create or replace a whole file.",
                 obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
                 write_file, RiskLevel.MODERATE, assess=_write_assess, target=_path_target),
    FunctionTool("edit_file", "Replace an exact piece of text in a file.",
                 obj({"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
                      "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"]),
                 edit_file, RiskLevel.MODERATE, assess=_write_assess, target=_path_target),
    FunctionTool("delete_file", "Delete a file or an empty folder.",
                 obj({"path": {"type": "string"}}, ["path"]),
                 delete_file, RiskLevel.SENSITIVE, target=_path_target,
                 assess=lambda a, c, x: x.notes.append(f"Deletes {c.resolve(str(a.get('path', '')))}.")),
]
