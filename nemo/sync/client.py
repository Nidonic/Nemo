"""Conversation sync with the relay: push this device's chat text, pull what other devices wrote.

Only user and assistant text travels; tool output, files and screenshots stay on the device that made them.
The relay numbers every change with a per-account sequence; a device pulls everything after the last seq it saw.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from nemo.schema import Message, Role

if TYPE_CHECKING:
    from nemo.agent.threads import ThreadStore


class SyncClient:
    def __init__(self, base_url: str, token: str, device_id: str, threads: "ThreadStore", state_path: Path):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.device_id = device_id
        self.threads = threads
        self.state_path = state_path
        self.state = {"since": 0, "pushed": {}}
        if state_path.exists():
            try:
                self.state = json.loads(state_path.read_text(encoding="utf-8"))
            except ValueError:
                pass

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    async def push(self, thread_id: str) -> int:
        thread = self.threads.get(thread_id)
        pushed: dict = self.state.setdefault("pushed", {})
        messages = [m for m in thread.messages
                    if m.role in (Role.USER, Role.ASSISTANT) and m.content and m.id not in pushed]
        body = {
            "conversations": [{"cid": thread.id, "kind": thread.kind, "title": thread.title,
                               "created_at": int(thread.created), "updated_at": int(thread.updated)}],
            "messages": [{"mid": m.id, "cid": thread.id, "role": m.role.value, "content": m.content,
                          "created_at": int(m.created_at)} for m in messages],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{self.base}/v1/sync/push", json=body, headers=self.headers)
            r.raise_for_status()
        for m in messages:
            pushed[m.id] = 1
        self._save()
        return len(messages)

    async def delete(self, thread_id: str) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(f"{self.base}/v1/sync/push", headers=self.headers,
                              json={"conversations": [{"cid": thread_id, "deleted": True}], "messages": []})

    async def pull(self) -> list[str]:
        """Apply remote changes, page by page; returns ids of threads that changed."""
        changed: set[str] = set()
        for _ in range(50):
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"{self.base}/v1/sync/pull", params={"since": self.state.get("since", 0)},
                                     headers=self.headers)
                r.raise_for_status()
                data = r.json()
            changed |= self._apply(data)
            if not data.get("more"):
                break
        return sorted(changed)

    def _apply(self, data: dict) -> set[str]:
        changed: set[str] = set()
        for conv in data.get("conversations", []):
            cid = conv["cid"]
            if conv.get("deleted"):
                if self.threads.exists(cid):
                    self.threads.delete(cid)
                    changed.add(cid)
                continue
            if not self.threads.exists(cid):
                self.threads.create(title=conv.get("title") or "Synced chat", id=cid, kind=conv.get("kind", "main"),
                                    created=conv.get("created_at") or 0)
                changed.add(cid)
            else:
                t = self.threads.get(cid)
                if conv.get("title") and t.title != conv["title"]:
                    t.title = conv["title"]
                    self.threads.save(t)
        for msg in data.get("messages", []):
            cid = msg["cid"]
            if msg.get("device") == self.device_id or not self.threads.exists(cid):
                continue
            t = self.threads.get(cid)
            if msg.get("deleted"):
                before = len(t.messages)
                t.messages = [m for m in t.messages if m.id != msg["mid"]]
                if len(t.messages) != before:
                    self.threads.save(t)
                    changed.add(cid)
                continue
            if any(m.id == msg["mid"] for m in t.messages):
                continue
            t.messages.append(Message(id=msg["mid"], role=Role(msg["role"]), content=msg.get("content", ""),
                                      created_at=msg.get("created_at") or 0, device=msg.get("device", "")))
            t.messages.sort(key=lambda m: m.created_at)
            self.state.setdefault("pushed", {})[msg["mid"]] = 1
            self.threads.save(t)
            changed.add(cid)
        self.state["since"] = data.get("seq", self.state.get("since", 0))
        self._save()
        return changed
