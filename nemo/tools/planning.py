"""Goals, routines and terminate: the tools that keep Nemo going after the conversation."""

from __future__ import annotations

from typing import Any

from nemo.schema import RiskLevel, ToolResult
from nemo.tools.base import FunctionTool, ToolContext, obj

SCHEDULES = "'every 30m', 'every 2h', 'daily 08:00', 'weekdays 09:30', 'weekly mon 18:00', 'once 2026-11-02 14:00'"


async def goal_create(args: dict[str, Any], ctx: ToolContext) -> str:
    g = ctx.app.goals.create(str(args["title"]), str(args.get("description", "")), list(args.get("steps") or []),
                             str(args.get("check", "")))
    g.conversation_id = g.conversation_id or ctx.conversation_id
    ctx.app.goals.save()
    await ctx.app.publish({"type": "goals_changed"})
    return g.describe()


async def goal_update(args: dict[str, Any], ctx: ToolContext) -> str:
    g = ctx.app.goals.update(str(args["id"]), status=args.get("status"), done_steps=args.get("done_steps"),
                             add_steps=args.get("add_steps"), note=str(args.get("note", "")),
                             step_notes=args.get("step_notes"), check=args.get("check"))
    await ctx.app.publish({"type": "goals_changed"})
    return g.describe()


async def goal_list(args: dict[str, Any], ctx: ToolContext) -> str:
    goals = ctx.app.goals.list(args.get("status"))
    return "\n\n".join(g.describe() for g in goals) or "No goals."


async def routine_create(args: dict[str, Any], ctx: ToolContext) -> str:
    r = ctx.app.routines.add(str(args["name"]), str(args["schedule"]), str(args["prompt"]))
    await ctx.app.publish({"type": "routines_changed"})
    return f"Routine '{r.name}' ({r.id}) created; first run {r.next_run}."


async def routine_list(args: dict[str, Any], ctx: ToolContext) -> str:
    items = ctx.app.routines.list()
    return "\n".join(f"- {r.id} · {r.name} · {r.schedule} · {'on' if r.enabled else 'off'} · next {r.next_run or '-'}\n  {r.prompt}"
                     for r in items) or "No routines."


async def routine_delete(args: dict[str, Any], ctx: ToolContext) -> str:
    ok = ctx.app.routines.remove(str(args["id"]))
    await ctx.app.publish({"type": "routines_changed"})
    return "Deleted." if ok else "Error: no routine with that id."


async def terminate(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(call_id="", name="", content=f"Finished: {args.get('summary', '')}")


PLANNING_TOOLS = [
    FunctionTool("goal_create",
                 f"Start a long-running goal with a step checklist. check (optional) is how often you check in on it by yourself: {SCHEDULES}.",
                 obj({"title": {"type": "string"}, "description": {"type": "string"},
                      "steps": {"type": "array", "items": {"type": "string"}}, "check": {"type": "string"}}, ["title"]),
                 goal_create, RiskLevel.MODERATE),
    FunctionTool("goal_update", "Record progress on a goal: mark steps done, add steps, log a note, change status or schedule.",
                 obj({"id": {"type": "string"}, "status": {"type": "string", "enum": ["active", "paused", "done", "failed"]},
                      "done_steps": {"type": "array", "items": {"type": "string"}},
                      "add_steps": {"type": "array", "items": {"type": "string"}},
                      "step_notes": {"type": "object", "additionalProperties": {"type": "string"}},
                      "note": {"type": "string"}, "check": {"type": "string"}}, ["id"]),
                 goal_update, RiskLevel.MODERATE),
    FunctionTool("goal_list", "List goals with their steps and progress.",
                 obj({"status": {"type": "string", "enum": ["active", "paused", "done", "failed"]}}), goal_list),
    FunctionTool("routine_create", f"Run a prompt on a schedule, even when nobody is watching. Schedules: {SCHEDULES} (local time).",
                 obj({"name": {"type": "string"}, "schedule": {"type": "string"},
                      "prompt": {"type": "string", "description": "What to do each time, as an instruction to yourself."}},
                     ["name", "schedule", "prompt"]),
                 routine_create, RiskLevel.MODERATE),
    FunctionTool("routine_list", "List scheduled routines.", obj({}), routine_list),
    FunctionTool("routine_delete", "Delete a routine by id.", obj({"id": {"type": "string"}}, ["id"]),
                 routine_delete, RiskLevel.MODERATE),
    FunctionTool("terminate", "End this task now, with a one-line summary. Use when the work is complete or cannot continue.",
                 obj({"summary": {"type": "string"}}, ["summary"]), terminate),
]
