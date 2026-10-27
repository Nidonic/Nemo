"""Hands (rung 4, last resort): look at a screen and act on it in the 0..999 grid.

computer_screen / phone_screen  → a screenshot (SAFE)
computer_act / phone_act        → one MemGUI action (MODERATE; SENSITIVE when the target looks like paying,
                                  sending or deleting), followed by a fresh screenshot
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from nemo.computer.backend import HandsUnavailable
from nemo.computer.grid import ACTIONS, GRID, KEYS, sensitive_hit, validate
from nemo.schema import Attachment, RiskLevel, ToolResult
from nemo.tools.base import BaseTool, CallAssessment, ToolContext, obj

LADDER = ("Last resort: use only when an API, CLI, MCP server, web_fetch or the browser cannot do it. ")

ACT_PARAMS = obj({
    "action": {"type": "string", "enum": list(ACTIONS)},
    "x": {"type": "integer", "description": f"0..{GRID}, left to right"},
    "y": {"type": "integer", "description": f"0..{GRID}, top to bottom"},
    "x2": {"type": "integer", "description": "swipe end x"},
    "y2": {"type": "integer", "description": "swipe end y"},
    "label": {"type": "string", "description": "What you are acting on, as shown on screen (e.g. 'Send button')."},
    "text": {"type": "string", "description": "Text for type."},
    "clear_first": {"type": "boolean"},
    "key": {"type": "string", "enum": list(KEYS)},
    "duration_ms": {"type": "integer"},
    "reason": {"type": "string", "description": "takeover: what the user must do (log in, solve a CAPTCHA, …)."},
    "summary": {"type": "string", "description": "finish: what was done."},
}, ["action"])


def _shot_result(text: str, backend: Any) -> ToolResult:
    import base64

    shot = backend.screenshot()
    return ToolResult(call_id="", name="", content=f"{text}\nScreen {shot.width}x{shot.height}; coordinates are 0..{GRID}.",
                      attachments=[Attachment(mime=shot.mime, data=base64.b64encode(shot.image).decode(),
                                              width=shot.width, height=shot.height)])


class ScreenTool(BaseTool):
    risk = RiskLevel.SAFE
    parameters = obj({})

    def __init__(self, name: str, what: str, backend: Callable[[], Any]):
        self.name = name
        self.description = f"{LADDER}Take a screenshot of {what}. Coordinates for actions use a 0..{GRID} grid."
        self._backend = backend

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> "str | ToolResult":
        try:
            backend = await asyncio.to_thread(self._backend)
            return await asyncio.to_thread(_shot_result, "Screenshot.", backend)
        except HandsUnavailable as exc:
            return f"Error: {exc}"


class ActTool(BaseTool):
    risk = RiskLevel.MODERATE
    parameters = ACT_PARAMS

    def __init__(self, name: str, what: str, backend: Callable[[], Any], device: str):
        self.name = name
        self.description = (f"{LADDER}Do one action on {what}: tap, double_tap, long_press, swipe, type, key; "
                            "takeover hands control to the user (logins, passwords, CAPTCHAs) and waits; finish ends "
                            "the screen task. Returns a fresh screenshot.")
        self._backend = backend
        self.device = device

    def target(self, args: dict[str, Any]) -> str:
        return str(args.get("action", ""))

    def summary(self, args: dict[str, Any]) -> str:
        action = args.get("action", "")
        what = args.get("label") or args.get("text") or args.get("key") or args.get("reason") or ""
        where = f" at ({args.get('x')}, {args.get('y')})" if args.get("x") is not None else ""
        return f"{action} {what!r}{where}".strip() if what else f"{action}{where}"

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        a = CallAssessment(risk=self.risk, target=self.target(args), summary=self.summary(args))
        word = sensitive_hit(args.get("label"))
        if word:
            a.warn(f"The target looks like a “{word}” action.")
        if args.get("action") in ("takeover", "finish"):
            a.risk = RiskLevel.SAFE
        return a

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> "str | ToolResult":
        problem = validate(args)
        if problem:
            return f"Error: {problem}."
        action = args["action"]
        hud = ctx.app.hands_status
        if action == "finish":
            await hud(ctx.conversation_id, self.device, "", active=False)
            return f"Screen task finished: {args.get('summary', '')}"
        if action == "takeover":
            await hud(ctx.conversation_id, self.device, "Waiting for you", active=True)
            note = await ctx.app.takeover(ctx.conversation_id, str(args.get("reason") or "Please take over."))
            return f"The user took over and is done. {note}".strip()
        try:
            backend = await asyncio.to_thread(self._backend)
        except HandsUnavailable as exc:
            return f"Error: {exc}"
        await hud(ctx.conversation_id, self.device, self.summary(args), active=True)
        ms = int(args.get("duration_ms") or 0)

        def run() -> ToolResult:
            if action == "tap":
                backend.tap(args["x"], args["y"])
            elif action == "double_tap":
                backend.double_tap(args["x"], args["y"])
            elif action == "long_press":
                backend.long_press(args["x"], args["y"], ms or 800)
            elif action == "swipe":
                backend.swipe(args["x"], args["y"], args["x2"], args["y2"], ms or 400)
            elif action == "type":
                backend.type(str(args["text"]), bool(args.get("clear_first")))
            elif action == "key":
                backend.key(str(args["key"]))
            import time

            time.sleep(0.8)  # let the screen settle before looking again
            return _shot_result(f"Did {self.summary(args)}.", backend)

        try:
            return await asyncio.to_thread(run)
        except HandsUnavailable as exc:
            return f"Error: {exc}"


_computer = None
_phone = None


def computer_backend():
    global _computer
    if _computer is None:
        from nemo.computer.backend import ComputerBackend

        _computer = ComputerBackend()
    return _computer


def phone_backend():
    global _phone
    if _phone is None:
        from nemo.phone.adb import PhoneBackend

        _phone = PhoneBackend()
    return _phone


def hands_tools() -> list[BaseTool]:
    return [
        ScreenTool("computer_screen", "this computer's screen", computer_backend),
        ActTool("computer_act", "this computer's screen", computer_backend, "computer"),
        ScreenTool("phone_screen", "the Android phone connected over adb", phone_backend),
        ActTool("phone_act", "the Android phone connected over adb", phone_backend, "phone"),
    ]
