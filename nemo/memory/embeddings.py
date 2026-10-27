"""Optional embeddings over an OpenAI-compatible /embeddings endpoint, for semantic memory recall."""

from __future__ import annotations

import httpx


def make_embedder(base_url: str, api_key: str, model: str):
    if not (base_url and model):
        return None
    url = base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def embed(texts: list[str]) -> list[list[float]]:
        response = httpx.post(url, json={"model": model, "input": texts}, headers=headers, timeout=30)
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]

    return embed
