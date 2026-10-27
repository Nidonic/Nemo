"""Anthropic Messages API streaming, with tool use, images in tool results, and thinking deltas."""

from __future__ import annotations

import json
from typing import Any

import httpx

from nemo.llm.base import BaseLLM, LLMError, LLMResponse, OnDelta, ThinkFilter, _noop, parse_arguments
from nemo.llm.openai_chat import raise_for_status, timeout
from nemo.schema import Message, Role, ToolCall


def _image(a) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": a.mime, "data": a.data}}


class AnthropicChat(BaseLLM):
    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int = 8192, vision: bool = True):
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.api_key, self.model, self.max_tokens, self.vision = api_key, model, max_tokens, vision

    def convert(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []

        def push(role: str, blocks: list[dict]) -> None:
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})

        for m in messages:
            images = [_image(a) for a in m.attachments if a.data] if self.vision else []
            if m.role is Role.ASSISTANT:
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for c in m.tool_calls:
                    blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
                if blocks:
                    push("assistant", blocks)
            elif m.role is Role.TOOL:
                content: list[dict] = [{"type": "text", "text": m.content or "(empty)"}, *images]
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": content}
                if m.ok is False:
                    block["is_error"] = True
                push("user", [block])
            elif m.role is Role.USER and (m.content or images):
                push("user", [*images, {"type": "text", "text": m.content or "(image)"}])
        return out

    async def stream(self, system: str, messages: list[Message], tools: list[dict], *,
                     on_text: OnDelta = _noop, on_reasoning: OnDelta = _noop,
                     max_tokens: int | None = None) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system,
            "messages": self.convert(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                                for t in tools]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}

        think = ThinkFilter()
        text: list[str] = []
        reasoning: list[str] = []
        blocks: dict[int, dict[str, str]] = {}
        finish, usage = "stop", {}

        async def route(kind: str, piece: str) -> None:
            if kind == "text":
                text.append(piece)
                await on_text(piece)
            else:
                reasoning.append(piece)
                await on_reasoning(piece)

        async with httpx.AsyncClient(timeout=timeout()) as client:
            async with client.stream("POST", self.url, json=payload, headers=headers) as response:
                await raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type")
                    if kind == "error":
                        raise LLMError(str(event.get("error"))[:500])
                    if kind == "message_start":
                        usage["prompt_tokens"] = (event.get("message", {}).get("usage") or {}).get("input_tokens", 0)
                    elif kind == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            blocks[event["index"]] = {"id": block["id"], "name": block["name"], "args": ""}
                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            for k, piece in think.feed(delta["text"]):
                                await route(k, piece)
                        elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                            await route("reasoning", delta["thinking"])
                        elif delta.get("type") == "input_json_delta" and event["index"] in blocks:
                            blocks[event["index"]]["args"] += delta.get("partial_json", "")
                    elif kind == "message_delta":
                        stop = (event.get("delta") or {}).get("stop_reason")
                        if stop:
                            finish = {"max_tokens": "length", "tool_use": "tool_calls", "end_turn": "stop"}.get(stop, stop)
                        usage["completion_tokens"] = (event.get("usage") or {}).get("output_tokens", 0)
                    elif kind == "message_stop":
                        break
        for k, piece in think.flush():
            await route(k, piece)

        tool_calls = [ToolCall(id=b["id"], name=b["name"], arguments=parse_arguments(b["args"]))
                      for _, b in sorted(blocks.items())]
        return LLMResponse(text="".join(text), reasoning="".join(reasoning), tool_calls=tool_calls,
                           finish_reason=finish, usage=usage)
