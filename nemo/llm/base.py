"""BaseLLM: one streaming call with tools, reasoning split from answer text."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from nemo.schema import Message, ToolCall

OnDelta = Callable[[str], Awaitable[None]]


class LLMError(Exception):
    pass


@dataclass
class LLMResponse:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


async def _noop(_: str) -> None:
    return None


class ThinkFilter:
    """Splits a token stream into answer text and <think>…</think> reasoning, across chunk boundaries."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self.buf += chunk
        out: list[tuple[str, str]] = []
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            kind = "reasoning" if self.inside else "text"
            at = self.buf.find(tag)
            if at >= 0:
                if at:
                    out.append((kind, self.buf[:at]))
                self.buf = self.buf[at + len(tag):]
                self.inside = not self.inside
                continue
            # keep a possible partial tag at the end for the next chunk
            keep = 0
            for n in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-n:]):
                    keep = n
                    break
            emit = self.buf[:len(self.buf) - keep]
            if emit:
                out.append((kind, emit))
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self.buf = self.buf, ""
        return [("reasoning" if self.inside else "text", rest)] if rest else []


def parse_arguments(raw: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_invalid_json": raw}
    return value if isinstance(value, dict) else {"_invalid_json": raw}


class BaseLLM(ABC):
    model: str = ""
    max_tokens: int = 8192
    vision: bool = True

    @abstractmethod
    async def stream(self, system: str, messages: list[Message], tools: list[dict], *,
                     on_text: OnDelta = _noop, on_reasoning: OnDelta = _noop,
                     max_tokens: int | None = None) -> LLMResponse: ...

    def roomier_max_tokens(self) -> int:
        return min(max(self.max_tokens * 2, 4096), 65536)

    async def complete(self, prompt: str, system: str = "You are a careful assistant.") -> str:
        """One-shot text completion (used by the dream cycle and summaries)."""
        reply = await self.stream(system, [Message.user(prompt)], [])
        return reply.text
