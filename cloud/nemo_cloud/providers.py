"""OpenAI-compatible /v1/chat/completions proxy: checks the allowance, streams the upstream reply through,
and charges the ledger from the usage the upstream reports.

Bring-your-own-key: a request with X-Upstream-Base-Url and X-Upstream-Key goes to that provider instead,
recorded in the ledger but not charged to the allowance.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, AsyncIterator

import httpx

from nemo_cloud.allowance import Allowance
from nemo_cloud.settings import Settings


class UpstreamError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def estimate_tokens(payload: dict) -> int:
    """Rough prompt size for upstreams that report no usage: ~4 characters a token."""
    return max(1, len(json.dumps(payload.get("messages", []), ensure_ascii=False)) // 4)


class Proxy:
    def __init__(self, settings: Settings, allowance: Allowance, transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.allowance, self.transport = settings, allowance, transport

    def route(self, payload: dict, headers: dict[str, str]) -> tuple[str, str, str, bool]:
        """(base_url, api_key, upstream model, byok)."""
        byok_base, byok_key = headers.get("x-upstream-base-url", ""), headers.get("x-upstream-key", "")
        if byok_base and byok_key:
            return byok_base.rstrip("/"), byok_key, str(payload.get("model", "")), True
        if not self.settings.upstream_base_url:
            raise UpstreamError(503, "This relay has no upstream model configured. Use your own key.")
        alias = str(payload.get("model") or "default")
        model = self.settings.model_aliases.get(alias, self.settings.model_aliases.get("default", alias))
        return self.settings.upstream_base_url.rstrip("/"), self.settings.upstream_api_key, model, False

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20), transport=self.transport)

    async def complete(self, account_id: str, payload: dict, headers: dict[str, str]) -> dict:
        base, key, model, byok = self.route(payload, headers)
        if not byok:
            self.allowance.check(account_id)
        body = {**payload, "model": model, "stream": False}
        async with self._client() as client:
            r = await client.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            raise UpstreamError(r.status_code, r.text[:500])
        data = r.json()
        usage = data.get("usage") or {}
        self.allowance.charge(account_id, "chat", model, int(usage.get("prompt_tokens") or estimate_tokens(payload)),
                              int(usage.get("completion_tokens") or 0), data.get("id", ""), byok)
        return data

    async def stream(self, account_id: str, payload: dict, headers: dict[str, str]) -> AsyncIterator[bytes]:
        base, key, model, byok = self.route(payload, headers)
        if not byok:
            self.allowance.check(account_id)
        body: dict[str, Any] = {**payload, "model": model, "stream": True,
                                "stream_options": {**(payload.get("stream_options") or {}), "include_usage": True}}
        request_id = "req_" + secrets.token_hex(8)
        client = self._client()
        response = await client.send(client.build_request("POST", f"{base}/chat/completions", json=body,
                                                          headers={"Authorization": f"Bearer {key}"}), stream=True)
        if response.status_code >= 400:
            text = (await response.aread()).decode("utf-8", "replace")
            await response.aclose()
            await client.aclose()
            raise UpstreamError(response.status_code, text[:500])

        async def gen() -> AsyncIterator[bytes]:
            usage: dict = {}
            completion_chars = 0
            try:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data and data != "[DONE]":
                            try:
                                chunk = json.loads(data)
                                usage = chunk.get("usage") or usage
                                for ch in chunk.get("choices") or []:
                                    completion_chars += len(((ch.get("delta") or {}).get("content")) or "")
                            except ValueError:
                                pass
                    yield (line + "\n").encode()
            finally:
                await response.aclose()
                await client.aclose()
                self.allowance.charge(account_id, "chat", model,
                                      int(usage.get("prompt_tokens") or estimate_tokens(payload)),
                                      int(usage.get("completion_tokens") or completion_chars // 4), request_id, byok)

        return gen()
