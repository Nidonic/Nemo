"""MockLLM: plays back scripted replies, for tests and offline demos. No network."""

from __future__ import annotations

from typing import Callable, Union

from nemo.llm.base import BaseLLM, LLMResponse, OnDelta, ThinkFilter, _noop
from nemo.schema import Message, ToolCall

Step = Union[LLMResponse, str, Callable[[list[Message]], LLMResponse]]


def reply(text: str = "", *calls: tuple[str, dict], finish: str = "stop") -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[ToolCall(name=n, arguments=a) for n, a in calls], finish_reason=finish)


class MockLLM(BaseLLM):
    def __init__(self, *script: Step, default: str = "(mock) Done.", chunk: int = 8):
        self.script = list(script)
        self.default = default
        self.chunk = chunk
        self.model = "mock"
        self.calls: list[dict] = []

    async def stream(self, system: str, messages: list[Message], tools: list[dict], *,
                     on_text: OnDelta = _noop, on_reasoning: OnDelta = _noop,
                     max_tokens: int | None = None) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools, "max_tokens": max_tokens})
        step = self.script.pop(0) if self.script else self.default
        if callable(step) and not isinstance(step, LLMResponse):
            step = step(messages)
        if isinstance(step, str):
            step = LLMResponse(text=step)
        # stream the text through the same <think> filter real providers use
        think = ThinkFilter()
        text, reasoning = [], []
        for i in range(0, len(step.text), self.chunk):
            for kind, piece in think.feed(step.text[i:i + self.chunk]):
                (text if kind == "text" else reasoning).append(piece)
                await (on_text if kind == "text" else on_reasoning)(piece)
        for kind, piece in think.flush():
            (text if kind == "text" else reasoning).append(piece)
            await (on_text if kind == "text" else on_reasoning)(piece)
        if step.reasoning:
            reasoning.append(step.reasoning)
            await on_reasoning(step.reasoning)
        calls = [ToolCall(name=c.name, arguments=dict(c.arguments)) for c in step.tool_calls]
        return LLMResponse(text="".join(text), reasoning="".join(reasoning), tool_calls=calls,
                           finish_reason=step.finish_reason, usage={"prompt_tokens": 1, "completion_tokens": 1})
