"""OpenAI-compatible /chat/completions streaming (OpenAI, DeepSeek, Bailian, OpenRouter, Ollama, the relay, …)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from nemo.llm.base import BaseLLM, LLMError, LLMResponse, OnDelta, ThinkFilter, _noop, parse_arguments
from nemo.schema import Message, Role, ToolCall


async def raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = (await response.aread()).decode("utf-8", "replace")
    try:
        err = json.loads(body).get("error", body)
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
    except (ValueError, AttributeError):
        err = body
    raise LLMError(f"{response.status_code}: {str(err)[:500]}")


def timeout() -> httpx.Timeout:
    return httpx.Timeout(300.0, connect=20.0)


def _image_part(a) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{a.mime};base64,{a.data}"}}


class OpenAIChat(BaseLLM):
    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int = 8192, vision: bool = True,
                 headers: dict[str, str] | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.max_tokens, self.vision = api_key, model, max_tokens, vision
        self.extra_headers = headers or {}

    def convert(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            images = [a for a in m.attachments if a.data] if self.vision else []
            if m.role is Role.ASSISTANT:
                msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{"id": c.id, "type": "function",
                                          "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                                         for c in m.tool_calls]
                elif not m.content:
                    continue
                out.append(msg)
            elif m.role is Role.TOOL:
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or "(empty)"})
                if images:  # tool messages cannot carry images here; show them in a user turn
                    out.append({"role": "user", "content": [{"type": "text", "text": f"(image from {m.name})"},
                                                            *[_image_part(a) for a in images]]})
            elif m.role is Role.USER:
                if images:
                    out.append({"role": "user", "content": [{"type": "text", "text": m.content or "(image)"},
                                                            *[_image_part(a) for a in images]]})
                else:
                    out.append({"role": "user", "content": m.content})
        return out

    async def _open(self, client: httpx.AsyncClient, payload: dict, headers: dict) -> httpx.Response:
        """Start the streaming request; servers that reject stream_options get one retry without it."""
        for attempt in (1, 2):
            request = client.build_request("POST", self.url, json=payload, headers=headers)
            response = await client.send(request, stream=True)
            if response.status_code == 400 and attempt == 1 and "stream_options" in payload:
                body = (await response.aread()).decode("utf-8", "replace")
                await response.aclose()
                if "stream_options" in body or "include_usage" in body:
                    payload.pop("stream_options")
                    continue
                raise LLMError(f"400: {body[:500]}")
            try:
                await raise_for_status(response)
            except LLMError:
                await response.aclose()
                raise
            return response
        raise LLMError("The model server refused the request.")

    async def stream(self, system: str, messages: list[Message], tools: list[dict], *,
                     on_text: OnDelta = _noop, on_reasoning: OnDelta = _noop,
                     max_tokens: int | None = None) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *self.convert(messages)],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # OpenAI's newer models only take max_completion_tokens; most compatible servers only max_tokens
        limit_field = "max_completion_tokens" if "api.openai.com" in self.url else "max_tokens"
        payload[limit_field] = max_tokens or self.max_tokens
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        headers = {**self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        think = ThinkFilter()
        text: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish, usage = "stop", {}

        async def route(kind: str, piece: str) -> None:
            if kind == "text":
                text.append(piece)
                await on_text(piece)
            else:
                reasoning.append(piece)
                await on_reasoning(piece)

        async with httpx.AsyncClient(timeout=timeout()) as client:
            response = await self._open(client, payload, headers)
            try:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise LLMError(str(chunk["error"])[:500])
                    if chunk.get("usage"):
                        usage = {k: v for k, v in chunk["usage"].items() if isinstance(v, int)}
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        for field in ("reasoning_content", "reasoning"):
                            if isinstance(delta.get(field), str) and delta[field]:
                                await route("reasoning", delta[field])
                        if delta.get("content"):
                            for kind, piece in think.feed(delta["content"]):
                                await route(kind, piece)
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name") and not slot["name"]:
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
            finally:
                await response.aclose()
        for kind, piece in think.flush():
            await route(kind, piece)

        tool_calls = [ToolCall(id=s["id"] or f"call_{i}", name=s["name"], arguments=parse_arguments(s["args"]))
                      for i, s in sorted(calls.items()) if s["name"]]
        return LLMResponse(text="".join(text), reasoning="".join(reasoning), tool_calls=tool_calls,
                           finish_reason=finish, usage=usage)
