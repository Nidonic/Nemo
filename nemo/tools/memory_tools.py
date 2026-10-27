"""remember / recall / forget atomic facts, and read or edit the Markdown profile files."""

from __future__ import annotations

from typing import Any

from nemo.memory.store import CATEGORIES
from nemo.schema import RiskLevel
from nemo.tools.base import FunctionTool, ToolContext, obj


async def remember(args: dict[str, Any], ctx: ToolContext) -> str:
    item = ctx.app.memory.add(str(args["content"]), category=str(args.get("category", "general")), source="chat",
                              importance=float(args.get("importance", 0.5)), reason=f"conversation {ctx.conversation_id}")
    return f"Remembered ({item.id}, {item.category}): {item.content}"


async def recall(args: dict[str, Any], ctx: ToolContext) -> str:
    items = ctx.app.memory.search(str(args.get("query", "")), limit=int(args.get("limit", 8)), category=args.get("category"))
    if not items:
        return "Nothing remembered about that."
    return "\n".join(f"- [{m.id}] ({m.category}) {m.content}" for m in items)


async def memory_list(args: dict[str, Any], ctx: ToolContext) -> str:
    items = ctx.app.memory.all(args.get("category"))
    if not items:
        return "No memories yet."
    return "\n".join(f"- [{m.id}] ({m.category}) {m.content}" for m in items[:200])


async def forget(args: dict[str, Any], ctx: ToolContext) -> str:
    ok = ctx.app.memory.forget(str(args["id"]), reason=str(args.get("reason", "asked to forget")))
    return "Forgotten. (It can be restored from Memory → History.)" if ok else "Error: no memory with that id."


async def profile_read(args: dict[str, Any], ctx: ToolContext) -> str:
    name = args.get("file")
    if not name:
        return "Profile files: " + ", ".join(ctx.app.profile.list())
    return ctx.app.profile.read(name) or f"{name} is empty or does not exist."


async def profile_write(args: dict[str, Any], ctx: ToolContext) -> str:
    if args.get("append"):
        return f"Added to {ctx.app.profile.append(args['file'], str(args['content']))}."
    return f"Saved {ctx.app.profile.write(args['file'], str(args['content']))}."


MEMORY_TOOLS = [
    FunctionTool("remember", "Save one lasting fact about the user (a preference, who they are, their work, a habit).",
                 obj({"content": {"type": "string", "description": "One self-contained fact."},
                      "category": {"type": "string", "enum": list(CATEGORIES)},
                      "importance": {"type": "number", "description": "0..1"}}, ["content"]),
                 remember, RiskLevel.MODERATE),
    FunctionTool("recall", "Search what you remember about the user.",
                 obj({"query": {"type": "string"}, "category": {"type": "string", "enum": list(CATEGORIES)},
                      "limit": {"type": "integer"}}, ["query"]),
                 recall, RiskLevel.SAFE, assess=lambda a, c, x: setattr(x, "reads_private", True)),
    FunctionTool("memory_list", "List remembered facts, newest first.",
                 obj({"category": {"type": "string", "enum": list(CATEGORIES)}}),
                 memory_list, RiskLevel.SAFE, assess=lambda a, c, x: setattr(x, "reads_private", True)),
    FunctionTool("forget", "Forget a remembered fact by id.",
                 obj({"id": {"type": "string"}, "reason": {"type": "string"}}, ["id"]),
                 forget, RiskLevel.MODERATE),
    FunctionTool("profile_read", "Read a profile file (SOUL.md, USER.md, GLOBAL.md, …); without a file, list them.",
                 obj({"file": {"type": "string"}}), profile_read, RiskLevel.SAFE),
    FunctionTool("profile_write", "Replace (or append to, with append=true) a profile file.",
                 obj({"file": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
                     ["file", "content"]),
                 profile_write, RiskLevel.MODERATE, target=lambda a: str(a.get("file", ""))),
]
