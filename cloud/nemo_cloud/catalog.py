"""Model catalog: which aliases this relay serves, and the last probe of each upstream model."""

from __future__ import annotations

import json
import time

import httpx

from nemo_cloud.db import DB, now
from nemo_cloud.settings import Settings


class Catalog:
    def __init__(self, db: DB, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.db, self.settings, self.transport = db, settings, transport

    def models(self) -> list[dict]:
        probes = {r["model"]: r for r in self.db.all("SELECT * FROM model_probes")}
        out = []
        for alias, model in self.settings.model_aliases.items():
            p = probes.get(model)
            out.append({"id": alias, "object": "model", "upstream": model, "owned_by": "nemo-relay",
                        "ok": bool(p["ok"]) if p else None, "latency_ms": p["latency_ms"] if p else None,
                        "capabilities": json.loads(p["capabilities"]) if p else {}})
        return out

    async def probe(self) -> list[dict]:
        """Send each upstream model a one-token request and record whether, and how fast, it answered."""
        results = []
        if not self.settings.upstream_base_url:
            return results
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            for model in sorted(set(self.settings.model_aliases.values())):
                started = time.time()
                ok, error, caps = False, "", {}
                try:
                    r = await client.post(f"{self.settings.upstream_base_url.rstrip('/')}/chat/completions",
                                          headers={"Authorization": f"Bearer {self.settings.upstream_api_key}"},
                                          json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1})
                    ok = r.status_code < 400
                    error = "" if ok else r.text[:300]
                    caps = {"chat": ok, "tools": ok}
                except httpx.HTTPError as exc:
                    error = str(exc)
                latency = int((time.time() - started) * 1000)
                self.db.run("INSERT INTO model_probes (model, ts, ok, latency_ms, capabilities, error) VALUES (?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(model) DO UPDATE SET ts=excluded.ts, ok=excluded.ok, latency_ms=excluded.latency_ms, "
                            "capabilities=excluded.capabilities, error=excluded.error",
                            model, now(), int(ok), latency, json.dumps(caps), error)
                results.append({"model": model, "ok": ok, "latency_ms": latency, "error": error})
        return results
