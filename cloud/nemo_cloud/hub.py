"""The hub broker at /v1/hub: devices of one account reach each other here.

A device connects with its bearer key and introduces itself with a hello frame. The broker stamps every frame
with the sender's device id (`from`), routes it to `to` within the same account only, answers calls to the
broker itself (action "devices"), and tells every device when the account's device list changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from nemo_cloud.db import DB, now

log = logging.getLogger("nemo_cloud.hub")

TYPES = ("call", "result", "event", "approve", "stop")
MAX_FRAME = 16 * 1024 * 1024


@dataclass
class Peer:
    account_id: str
    device_id: str
    kind: str
    ws: WebSocket
    info: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, frame: dict) -> None:
        async with self.lock:
            await self.ws.send_text(json.dumps(frame, ensure_ascii=False))


class Hub:
    def __init__(self, db: DB):
        self.db = db
        self.peers: dict[str, dict[str, Peer]] = {}  # account → device id → peer

    def online(self, account_id: str) -> dict[str, Peer]:
        return self.peers.get(account_id, {})

    def devices(self, account_id: str) -> list[dict]:
        live = self.online(account_id)
        rows = self.db.all("SELECT * FROM devices WHERE account_id=? ORDER BY last_seen DESC", account_id)
        out = []
        for r in rows:
            out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "os": r["os"], "version": r["version"],
                        "actions": json.loads(r["actions"] or "[]"), "last_seen": r["last_seen"], "online": r["id"] in live})
        return out

    def _register(self, peer: Peer, info: dict) -> None:
        t = now()
        self.db.run(
            "INSERT INTO devices (account_id, id, name, kind, os, version, actions, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, id) DO UPDATE SET name=excluded.name, kind=excluded.kind, os=excluded.os, "
            "version=excluded.version, actions=excluded.actions, last_seen=excluded.last_seen",
            peer.account_id, peer.device_id, str(info.get("name") or peer.device_id)[:80], peer.kind,
            str(info.get("os", ""))[:80], str(info.get("version", ""))[:40], json.dumps(list(info.get("actions", []))[:50]), t, t)

    async def broadcast_devices(self, account_id: str) -> None:
        body = {"devices": self.devices(account_id)}
        for peer in list(self.online(account_id).values()):
            try:
                await peer.send({"type": "event", "id": "devices", "from": "hub", "to": peer.device_id,
                                 "action": "devices", "args": {}, "body": body})
            except Exception:  # a closing socket; its own handler cleans up
                pass

    async def serve(self, ws: WebSocket, key_row: Any) -> None:
        """Run one device's connection until it closes."""
        account_id = key_row["account_id"]
        device_id = key_row["hub_device_id"] or key_row["key_hash"][:16]
        kind = key_row["hub_device_kind"] or "computer"
        peer = Peer(account_id, device_id, kind, ws)
        previous = self.online(account_id).get(device_id)
        if previous is not None:
            try:
                await previous.ws.close(code=4000, reason="replaced by a newer connection")
            except Exception:  # already gone
                pass
        self.peers.setdefault(account_id, {})[device_id] = peer
        self._register(peer, {"name": key_row["device"]})
        await self.broadcast_devices(account_id)
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_FRAME:
                    continue
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(frame, dict) or frame.get("type") not in TYPES:
                    continue
                await self.handle(peer, frame)
        except WebSocketDisconnect:
            pass
        finally:
            if self.online(account_id).get(device_id) is peer:
                del self.peers[account_id][device_id]
                if not self.peers[account_id]:
                    del self.peers[account_id]
            self.db.run("UPDATE devices SET last_seen=? WHERE account_id=? AND id=?", now(), account_id, device_id)
            await self.broadcast_devices(account_id)

    async def handle(self, peer: Peer, frame: dict) -> None:
        frame["from"] = peer.device_id  # never trust a claimed sender
        frame.setdefault("args", {})
        frame.setdefault("body", {})
        to = str(frame.get("to") or "")
        action = str(frame.get("action") or "")

        if frame["type"] == "event" and action == "hello" and not to:
            info = frame["body"].get("device") or {}
            peer.info = info
            self._register(peer, info)
            await self.broadcast_devices(peer.account_id)
            return
        if not to or to == "hub":
            if frame["type"] == "call" and action == "devices":
                await peer.send({"type": "result", "id": frame.get("id"), "from": "hub", "to": peer.device_id,
                                 "action": action, "args": {}, "body": {"ok": True, "devices": self.devices(peer.account_id)}})
            return

        target = self.online(peer.account_id).get(to)  # routing never leaves the account
        if target is None:
            if frame["type"] == "call":
                await peer.send({"type": "result", "id": frame.get("id"), "from": "hub", "to": peer.device_id,
                                 "action": action, "args": {}, "body": {"ok": False, "error": "That device is offline."}})
            return
        try:
            await target.send(frame)
        except Exception:  # the target dropped mid-send
            if frame["type"] == "call":
                await peer.send({"type": "result", "id": frame.get("id"), "from": "hub", "to": peer.device_id,
                                 "action": action, "args": {}, "body": {"ok": False, "error": "That device went offline."}})
