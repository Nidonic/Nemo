"""A device's connection to the relay hub: stay online, answer calls from the account's other devices, and call them."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
from typing import Any, Awaitable, Callable

from nemo.hub.protocol import frame, parse

log = logging.getLogger("nemo.hub")

# a handler gets the call frame and an emit(stage, body) for progress events, and returns the result body
Handler = Callable[[dict, Callable[[str, dict], Awaitable[None]]], Awaitable[dict]]


def ws_url(base: str) -> str:
    base = base.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/v1/hub"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/v1/hub"
    return base + "/v1/hub"


class HubClient:
    def __init__(self, url: str, token: str, device: dict[str, Any], handlers: dict[str, Handler],
                 on_event: Callable[[dict], Awaitable[None]] | None = None,
                 on_approve: Callable[[dict], Awaitable[None]] | None = None,
                 on_stop: Callable[[dict], Awaitable[None]] | None = None):
        self.url, self.token, self.device = url, token, device
        self.handlers = handlers
        self.on_event = on_event
        self.on_approve = on_approve
        self.on_stop = on_stop
        self.ws: Any = None
        self.online = False
        self.devices: list[dict] = []
        self.pending: dict[str, asyncio.Future] = {}
        self.streams: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self.running: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.last_error = ""

    # lifecycle ------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping = True
        if self.ws is not None:
            await self.ws.close()
        if self._task:
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        from websockets.asyncio.client import connect

        delay = 1.0
        while not self._stopping:
            try:
                async with connect(ws_url(self.url), additional_headers={"Authorization": f"Bearer {self.token}"},
                                   max_size=16 * 1024 * 1024, ping_interval=25) as ws:
                    self.ws, self.online, delay, self.last_error = ws, True, 1.0, ""
                    await self.send(frame("event", "hello", body={"device": self.device}))
                    async for raw in ws:
                        msg = parse(raw)
                        if msg:
                            asyncio.create_task(self._dispatch(msg))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors of every kind; reconnect with backoff
                self.last_error = str(exc)
                log.info("hub disconnected: %s", exc)
            self.ws, self.online = None, False
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("The hub connection dropped."))
            self.pending.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def send(self, msg: dict) -> None:
        if self.ws is None:
            raise ConnectionError("Not connected to the hub.")
        await self.ws.send(json.dumps(msg, ensure_ascii=False))

    # incoming ---------------------------------------------------------------------

    async def _dispatch(self, msg: dict) -> None:
        kind = msg["type"]
        if kind == "result":
            fut = self.pending.pop(msg["id"], None)
            self.streams.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg["body"])
        elif kind == "event":
            if msg["action"] == "devices":
                self.devices = msg["body"].get("devices", [])
            stream = self.streams.get(msg["id"])
            if stream:
                await stream(msg)
            elif self.on_event:
                await self.on_event(msg)
        elif kind == "approve" and self.on_approve:
            await self.on_approve(msg)
        elif kind == "stop":
            target = msg["args"].get("call")
            if target and target in self.running:
                self.running[target].cancel()
            if self.on_stop:
                await self.on_stop(msg)
        elif kind == "call":
            self.running[msg["id"]] = asyncio.current_task()  # type: ignore[assignment]
            try:
                body = await self._answer(msg)
            except asyncio.CancelledError:
                body = {"ok": False, "error": "stopped"}
            finally:
                self.running.pop(msg["id"], None)
            try:
                await self.send(frame("result", msg["action"], to=msg["from"], id=msg["id"], body=body))
            except ConnectionError:
                pass

    async def _answer(self, msg: dict) -> dict:
        handler = self.handlers.get(msg["action"])
        if handler is None:
            return {"ok": False, "error": f"This device does not support {msg['action']}."}

        async def emit(stage: str, body: dict) -> None:
            await self.send(frame("event", msg["action"], to=msg["from"], id=msg["id"], body={"stage": stage, **body}))

        try:
            return await handler(msg, emit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # report a failing handler to the caller instead of dropping the call
            log.exception("hub handler %s", msg["action"])
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # outgoing -------------------------------------------------------------------------

    async def call(self, to: str, action: str, args: dict | None = None, timeout: float = 60,
                   on_event: Callable[[dict], Awaitable[None]] | None = None) -> dict:
        msg = frame("call", action, to=to, args=args)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[msg["id"]] = fut
        if on_event:
            self.streams[msg["id"]] = on_event
        try:
            await self.send(msg)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"{action} timed out after {int(timeout)}s."}
        finally:
            self.pending.pop(msg["id"], None)
            self.streams.pop(msg["id"], None)

    async def approve(self, to: str, approval_id: str, allow: bool, scope: str = "once") -> None:
        await self.send(frame("approve", to=to, args={"approval_id": approval_id, "allow": allow, "scope": scope}))

    async def stop_remote(self, to: str, call_id: str) -> None:
        await self.send(frame("stop", to=to, args={"call": call_id}))

    def find_device(self, name_or_id: str) -> dict | None:
        key = name_or_id.lower().lstrip("@")
        for d in self.devices:
            if d.get("id") == name_or_id or str(d.get("name", "")).lower() == key:
                return d
        for d in self.devices:
            if str(d.get("name", "")).lower().startswith(key) or str(d.get("os", "")).lower().startswith(key):
                return d
        return None


def this_device(device_id: str, name: str, version: str, actions: list[str]) -> dict[str, Any]:
    return {"id": device_id, "name": name or platform.node() or "computer", "kind": "computer",
            "os": f"{platform.system()} {platform.release()}", "version": version, "actions": actions}
