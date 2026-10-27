"""NemoAgent: Think (LLM) → Gate (Sentinel) → Execute (tools) → Observe → repeat.

One run at a time per thread. Messages that arrive mid-run go to the inbox and are folded in before the next
model call. Reasoning streams separately from the answer. A reply cut off while still thinking is retried
with a roomier token ceiling. Three identical steps in a row stop the run.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from nemo.agent.context import context_messages, is_stuck
from nemo.agent.prompts import build_system_prompt
from nemo.agent.threads import Thread
from nemo.llm.base import BaseLLM, LLMResponse
from nemo.schema import AgentState, Message, Role, ToolCall, ToolResult
from nemo.tools.base import ToolContext

if TYPE_CHECKING:
    from nemo.app import App


class StuckError(Exception):
    pass


class UI(Protocol):
    async def on_text_delta(self, delta: str) -> None: ...
    async def on_reasoning_delta(self, delta: str) -> None: ...
    async def on_step_end(self, message: Message) -> None: ...
    async def on_tool_start(self, call: ToolCall, summary: str) -> None: ...
    async def on_tool_end(self, call: ToolCall, result: ToolResult) -> None: ...
    async def on_inbox(self, message: Message) -> None: ...


class SilentUI:
    async def on_text_delta(self, delta: str) -> None: ...
    async def on_reasoning_delta(self, delta: str) -> None: ...
    async def on_step_end(self, message: Message) -> None: ...
    async def on_tool_start(self, call: ToolCall, summary: str) -> None: ...
    async def on_tool_end(self, call: ToolCall, result: ToolResult) -> None: ...
    async def on_inbox(self, message: Message) -> None: ...


class NemoAgent:
    def __init__(self, app: "App", thread: Thread, llm: BaseLLM | None = None, device: str = "local"):
        self.app = app
        self.thread = thread
        self.llm = llm
        self.device = device
        self.state = AgentState.IDLE
        self.inbox: asyncio.Queue[Message] = asyncio.Queue()

    def _save(self) -> None:
        self.app.threads.save(self.thread)

    def _append(self, message: Message) -> Message:
        self.thread.messages.append(message)
        self._save()
        return message

    async def _drain_inbox(self, ui: UI) -> int:
        n = 0
        while not self.inbox.empty():
            msg = self.inbox.get_nowait()
            self._append(msg)
            await ui.on_inbox(msg)
            n += 1
        return n

    async def _think(self, llm: BaseLLM, ui: UI) -> LLMResponse:
        cfg = self.app.config.agent
        system = build_system_prompt(self.app, self.thread)
        msgs = context_messages(self.thread.messages, cfg.max_context_messages, cfg.max_context_images)
        tools = self.app.tools.schemas()
        reply = await llm.stream(system, msgs, tools, on_text=ui.on_text_delta, on_reasoning=ui.on_reasoning_delta)
        if reply.finish_reason == "length" and not reply.text.strip() and not reply.tool_calls:
            # the thinking budget ran out before an answer: ask again with more room
            reply = await llm.stream(system, msgs, tools, on_text=ui.on_text_delta, on_reasoning=ui.on_reasoning_delta,
                                     max_tokens=llm.roomier_max_tokens())
        self.app.record_usage(reply)
        return reply

    async def run(self, text: str, ui: UI | None = None) -> str:
        """Run one turn. If a turn is already running, the text joins the inbox instead."""
        ui = ui or SilentUI()
        message = Message.user(text, device=self.device)
        if self.state is not AgentState.IDLE:
            await self.inbox.put(message)
            return ""
        self.state = AgentState.RUNNING
        try:
            self._append(message)
            if self.thread.title == "New chat":
                self.thread.title = (text.strip().splitlines() or ["New chat"])[0][:60]
                self._save()
            return await self._loop(ui)
        finally:
            self.state = AgentState.IDLE

    async def _loop(self, ui: UI) -> str:
        llm = self.llm or self.app.llm()
        final = ""
        for _ in range(self.app.config.agent.max_steps):
            await self._drain_inbox(ui)
            reply = await self._think(llm, ui)
            message = self._append(Message(role=Role.ASSISTANT, content=reply.text, reasoning=reply.reasoning,
                                           tool_calls=reply.tool_calls, device=self.device))
            await ui.on_step_end(message)
            if reply.text:
                final = reply.text
            if is_stuck(self.thread.messages, self.app.config.agent.stuck_window):
                raise StuckError("I was repeating the same step, so I stopped. Tell me how to proceed.")
            if not reply.tool_calls:
                if not self.inbox.empty():
                    continue
                return final

            ctx = ToolContext(app=self.app, conversation_id=self.thread.id, workspace=self.app.config.workspace_path(),
                              device=self.thread.origin if self.thread.origin != "local" else self.device)
            finished = False
            for call in reply.tool_calls:
                tool = self.app.tools.get(call.name)
                summary = tool.summary(call.arguments) if tool else ""
                await ui.on_tool_start(call, summary)
                if tool is None:
                    result = ToolResult(call_id=call.id, name=call.name, ok=False,
                                        content=f"Error: there is no tool named {call.name}.")
                elif "_invalid_json" in call.arguments:
                    result = ToolResult(call_id=call.id, name=call.name, ok=False,
                                        content="Error: the arguments were not valid JSON. Try again.")
                else:
                    result = await self.app.sentinel.guard(call, tool, ctx)
                for i, a in enumerate(result.attachments):
                    a.step = len(self.thread.messages) + i
                self._append(Message.tool(result))
                await ui.on_tool_end(call, result)
                if call.name == "terminate" and result.ok:
                    final = final or str(call.arguments.get("summary", ""))
                    finished = True
            if finished:
                return final

        self._append(Message(role=Role.ASSISTANT,
                             content=f"I stopped after {self.app.config.agent.max_steps} steps. Say *continue* to keep going."))
        return final
