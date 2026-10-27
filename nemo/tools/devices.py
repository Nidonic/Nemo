"""Reach the user's other devices through the hub, and drive coding agents on this one."""

from __future__ import annotations

import json
from typing import Any

from nemo.hub.protocol import ACTIONS
from nemo.schema import Attachment, RiskLevel, ToolResult
from nemo.tools.base import BaseTool, CallAssessment, FunctionTool, ToolContext, clip, obj

REMOTE_RISK = {
    "info": RiskLevel.SAFE, "files": RiskLevel.SAFE, "screen": RiskLevel.SAFE, "file.get": RiskLevel.SAFE,
    "coding.sessions": RiskLevel.SAFE, "notify": RiskLevel.MODERATE, "open": RiskLevel.MODERATE,
    # the target device runs a task through its own Sentinel, and asks the user itself
    "task": RiskLevel.MODERATE, "coding.stop": RiskLevel.MODERATE,
    "shell": RiskLevel.SENSITIVE, "file.put": RiskLevel.SENSITIVE, "coding.send": RiskLevel.SENSITIVE,
}


async def device_list(args: dict[str, Any], ctx: ToolContext) -> str:
    hub = ctx.app.hub
    if hub is None:
        return "Not signed in to a relay, so there are no other devices. (Settings → Account.)"
    if not hub.online:
        return f"The hub is offline: {hub.last_error or 'connecting…'}"
    if not hub.devices:
        return "No other devices on this account."
    me = ctx.app.config.hub.device_id
    return "\n".join(f"- {d.get('name')} ({d.get('kind')}, {d.get('os')}) id={d.get('id')} "
                     f"{'online' if d.get('online') else 'offline'}{' ← this device' if d.get('id') == me else ''}"
                     f"\n  can: {', '.join(d.get('actions', []))}" for d in hub.devices)


class DeviceCallTool(BaseTool):
    name = "device_call"
    description = (
        "Ask another of the user's devices to do something. action: info, files {path}, file.get {path}, "
        "file.put {path, content_b64}, open {target}, screen, notify {title, body}, shell {command, cwd}, "
        "task {text} (the device's own agent does a whole task and reports back), coding.sessions, coding.send {agent, prompt, cwd}."
    )
    parameters = obj({
        "device": {"type": "string", "description": "Device name or id from device_list."},
        "action": {"type": "string", "enum": [a for a in ACTIONS if a not in ("devices", "hello")]},
        "args": {"type": "object", "description": "Arguments for the action."},
    }, ["device", "action"])
    risk = RiskLevel.MODERATE

    def target(self, args: dict[str, Any]) -> str:
        return f"{args.get('device', '')}/{args.get('action', '')}"

    def summary(self, args: dict[str, Any]) -> str:
        inner = args.get("args") or {}
        detail = inner.get("command") or inner.get("text") or inner.get("path") or inner.get("target") or ""
        return f"{args.get('action')} on {args.get('device')}: {detail}".strip(": ")

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        action = str(args.get("action", ""))
        a = CallAssessment(risk=REMOTE_RISK.get(action, RiskLevel.SENSITIVE), target=self.target(args),
                           summary=self.summary(args))
        if action in ("file.get", "screen", "files", "coding.sessions"):
            a.reads_private = True
        if a.risk is RiskLevel.SENSITIVE:
            a.notes.append(f"Runs {action} on {args.get('device')}.")
        return a

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> "str | ToolResult":
        hub = ctx.app.hub
        if hub is None or not hub.online:
            return "Error: not connected to the hub."
        device = hub.find_device(str(args["device"]))
        if device is None:
            return f"Error: no device called {args['device']}. Use device_list."
        if not device.get("online"):
            return f"Error: {device.get('name')} is offline."
        action = str(args["action"])
        if action == "task":
            text = await ctx.app.remote_task(ctx.conversation_id, device, str((args.get("args") or {}).get("text", "")))
            return text
        body = await hub.call(device["id"], action, args.get("args") or {}, timeout=300 if action == "shell" else 60)
        if not body.get("ok", False):
            return f"Error from {device.get('name')}: {body.get('error', 'failed')}"
        if action == "screen" and body.get("image"):
            return ToolResult(call_id="", name="", content=f"Screen of {device.get('name')}.",
                              attachments=[Attachment(mime=body.get("mime", "image/jpeg"), data=body["image"],
                                                      width=body.get("width", 0), height=body.get("height", 0))])
        body.pop("ok", None)
        return clip(body.get("output") if isinstance(body.get("output"), str) else json.dumps(body, ensure_ascii=False, indent=1))


async def coding_sessions(args: dict[str, Any], ctx: ToolContext) -> str:
    items = ctx.app.coding.sessions(int(args.get("limit", 15)))
    if not items:
        return "No coding-agent sessions found on this computer."
    return "\n".join(f"- {s['agent']} · {s['project']} · {s['title']}\n  {s['path']}" for s in items)


async def coding_send(args: dict[str, Any], ctx: ToolContext) -> str:
    cwd = str(ctx.resolve(args.get("cwd") or "."))
    run = ctx.app.coding.start(str(args["agent"]), str(args["prompt"]), cwd, args.get("resume"))
    return f"Started {run.agent} run {run.id} in {cwd}. Follow it on the Coding screen."


DEVICE_TOOLS: list[BaseTool] = [
    FunctionTool("device_list", "List the user's devices on this account and which are online.", obj({}), device_list),
    DeviceCallTool(),
    FunctionTool("coding_sessions", "List recent Claude Code, Codex and Cursor sessions on this computer.",
                 obj({"limit": {"type": "integer"}}), coding_sessions,
                 assess=lambda a, c, x: setattr(x, "reads_private", True)),
    FunctionTool("coding_send", "Start a coding agent (claude, codex, cursor) on a prompt in a project folder; it edits files on its own.",
                 obj({"agent": {"type": "string", "enum": ["claude", "codex", "cursor"]}, "prompt": {"type": "string"},
                      "cwd": {"type": "string"}, "resume": {"type": "string", "description": "session id to continue"}},
                     ["agent", "prompt"]),
                 coding_send, RiskLevel.SENSITIVE, target=lambda a: str(a.get("agent", ""))),
]
