"""The local daemon: REST under /api, a Server-Sent Events stream at /api/events, /v1 for scripts, and the console."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from nemo import __version__
from nemo.app import App
from nemo.avatar import MOODS
from nemo.config import PROVIDERS, Config
from nemo.memory.store import CATEGORIES
from nemo.sentinel import SCOPES, sandbox
from nemo.vault import VaultError

CONSOLE = Path(__file__).resolve().parent.parent / "static" / "console"


class TextIn(BaseModel):
    text: str


class Decision(BaseModel):
    allow: bool | None = None
    scope: str = "once"
    decision: str | None = None  # shorthand: once | conversation | session | 24h | always | deny


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def host_name(header: str) -> str:
    if header.startswith("["):
        return header.split("]")[0] + "]"
    return header.rsplit(":", 1)[0]


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def create_app(app: App | None = None, host: str = "127.0.0.1", token: str | None = None) -> FastAPI:
    core = app or App()
    token = token or (None if is_loopback(host) else secrets.token_urlsafe(16))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await core.start()
        yield
        await core.shutdown()

    api = FastAPI(title="Nemo", version=__version__, lifespan=lifespan)
    api.state.core = core
    api.state.token = token

    @api.middleware("http")
    async def guard(request: Request, call_next):
        query = request.query_params.get("token")
        if token:
            if not secrets.compare_digest(request.cookies.get("nemo_token") or query or "", token):
                return PlainTextResponse("Forbidden: open the link with ?token=… that Nemo printed when it started.", 403)
        elif not is_loopback(host_name(request.headers.get("host", ""))):
            return PlainTextResponse("Forbidden host.", 403)  # DNS rebinding
        origin = request.headers.get("origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS") and origin.split("://", 1)[-1] != request.headers.get("host"):
            return PlainTextResponse("Cross-origin request refused.", 403)
        response = await call_next(request)
        if token and query == token:
            response.set_cookie("nemo_token", token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 365)
        return response

    @api.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    def thread_or_404(thread_id: str):
        try:
            return core.threads.get(thread_id)
        except KeyError:
            raise HTTPException(404, "No such conversation.")

    # state & settings ---------------------------------------------------------------

    def hub_state() -> dict:
        h = core.hub
        return {"configured": bool(core.config.hub.url and core.config.hub.token), "url": core.config.hub.url,
                "online": bool(h and h.online), "error": h.last_error if h else "",
                "devices": h.devices if h else [], "device_id": core.config.hub.device_id}

    @api.get("/api/state")
    async def state() -> dict:
        return {
            "version": __version__,
            "config": core.config.public(),
            "providers": PROVIDERS,
            "categories": CATEGORIES,
            "scopes": SCOPES,
            "moods": MOODS,
            "running": list(core.tasks),
            "approvals": core.approvals(),
            "hands": core.hands,
            "takeovers": list(core.takeovers),
            "tools": [{"name": t.name, "description": t.description, "risk": t.risk.value} for t in core.tools.tools.values()],
            "mcp": core.mcp.status,
            "hub": hub_state(),
            "usage": core.usage,
            "sandbox": sandbox.available(),
            "vault": core.vault.list(),
            "avatar": {**core.avatar.meta(), "faces": core.avatar.faces()},
        }

    @api.put("/api/config")
    async def put_config(body: dict[str, Any]) -> dict:
        current = core.config.model_dump()
        llm = body.get("llm") or {}
        key = llm.pop("api_key", "") if isinstance(llm, dict) else ""
        if key:
            # keys never sit in config.json in the clear: they go to the vault, the config keeps a placeholder
            core.vault.set("LLM_API_KEY", key)
            llm["api_key"] = "{{vault:LLM_API_KEY}}"
        if isinstance(llm, dict) and llm.get("embeddings_api_key"):
            core.vault.set("EMBEDDINGS_API_KEY", llm["embeddings_api_key"])
            llm["embeddings_api_key"] = "{{vault:EMBEDDINGS_API_KEY}}"
        elif isinstance(llm, dict):
            llm.pop("embeddings_api_key", None)
        if body.get("clear_api_key"):
            llm["api_key"] = ""
        body.pop("clear_api_key", None)
        if isinstance(body.get("hub"), dict):
            body["hub"].pop("token", None)
            body["hub"].pop("signed_in", None)
        body.pop("ready", None)
        if not body.get("workspace"):
            body.pop("workspace", None)  # the console shows the resolved default; keep it unset
        if isinstance(llm, dict):
            for k in ("api_key_set", "api_key_hint"):
                llm.pop(k, None)
            body["llm"] = llm
        try:
            core.update_config(Config.model_validate(deep_merge(current, body)))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return core.config.public()

    # conversations --------------------------------------------------------------------

    @api.get("/api/threads")
    async def threads() -> list[dict]:
        return [{**t, "running": t["id"] in core.tasks} for t in core.threads.list()]

    @api.post("/api/threads")
    async def new_thread(body: dict[str, Any] | None = None) -> dict:
        return core.threads.create(title=str((body or {}).get("title") or "New chat")).summary()

    @api.get("/api/threads/{thread_id}")
    async def get_thread(thread_id: str) -> dict:
        t = thread_or_404(thread_id)
        data = json.loads(t.model_dump_json())
        for m in data["messages"]:
            for a in m.get("attachments", []):
                a["data"] = ""  # images stay on disk; the console asks for them one by one
        return {**data, "running": t.id in core.tasks, "tainted": core.sentinel.taint.get(t.id, [])}

    @api.get("/api/threads/{thread_id}/attachments/{message_id}/{index}")
    async def attachment(thread_id: str, message_id: str, index: int):
        t = thread_or_404(thread_id)
        for m in t.messages:
            if m.id == message_id and 0 <= index < len(m.attachments) and m.attachments[index].data:
                a = m.attachments[index]
                return StreamingResponse(iter([base64.b64decode(a.data)]), media_type=a.mime)
        raise HTTPException(404, "No such image.")

    @api.patch("/api/threads/{thread_id}")
    async def rename_thread(thread_id: str, body: dict[str, Any]) -> dict:
        t = thread_or_404(thread_id)
        if body.get("title"):
            t.title = str(body["title"])[:100]
            core.threads.save(t)
        return t.summary()

    @api.delete("/api/threads/{thread_id}")
    async def delete_thread(thread_id: str) -> dict:
        thread_or_404(thread_id)
        core.stop(thread_id)
        core.threads.delete(thread_id)
        core.agents.pop(thread_id, None)
        if core.sync:
            asyncio.ensure_future(core.sync.delete(thread_id))
        return {"ok": True}

    @api.post("/api/threads/{thread_id}/messages")
    async def send(thread_id: str, body: TextIn) -> dict:
        thread_or_404(thread_id)
        if not body.text.strip():
            raise HTTPException(400, "Empty message.")
        if not core.config.is_ready():
            raise HTTPException(400, "Set up a model first, in Settings.")
        return {"status": await core.send(thread_id, body.text)}

    @api.post("/api/threads/{thread_id}/stop")
    async def stop(thread_id: str) -> dict:
        if core.hub:
            for aid, m in list(core.mirrors.items()):
                if m["thread"] == thread_id:
                    await core.resolve_approval(aid, False)
        return {"stopped": core.stop(thread_id)}

    @api.post("/api/threads/{thread_id}/takeover")
    async def takeover_done(thread_id: str, body: dict[str, Any] | None = None) -> dict:
        if not core.resolve_takeover(thread_id, str((body or {}).get("note", ""))):
            raise HTTPException(404, "Nothing is waiting for you in this conversation.")
        return {"ok": True}

    # approvals & safety ------------------------------------------------------------------

    @api.get("/api/approvals")
    async def approvals() -> list[dict]:
        return core.approvals()

    @api.post("/api/approvals/{approval_id}")
    async def decide(approval_id: str, body: Decision) -> dict:
        allow, scope = body.allow, body.scope
        if body.decision:
            allow, scope = body.decision != "deny", body.decision if body.decision != "deny" else "once"
        if allow is None or scope not in SCOPES:
            raise HTTPException(400, f"Send allow and a scope ({', '.join(SCOPES)}).")
        if not await core.resolve_approval(approval_id, allow, scope):
            raise HTTPException(404, "That request was already answered.")
        return {"ok": True}

    @api.get("/api/grants")
    async def grants() -> list[dict]:
        return core.grants.list()

    @api.delete("/api/grants/{key:path}")
    async def revoke(key: str) -> dict:
        return {"revoked": core.grants.revoke(key)}

    @api.get("/api/audit")
    async def audit(n: int = 200) -> list[dict]:
        return core.audit.tail(min(n, 2000))

    @api.get("/api/vault")
    async def vault_list() -> list[str]:
        return core.vault.list()

    @api.put("/api/vault/{name}")
    async def vault_set(name: str, body: dict[str, Any]) -> dict:
        try:
            core.vault.set(name, str(body.get("value", "")))
        except VaultError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    @api.delete("/api/vault/{name}")
    async def vault_delete(name: str) -> dict:
        return {"deleted": core.vault.delete(name)}

    # memory ---------------------------------------------------------------------------------

    @api.get("/api/memory")
    async def memories(q: str = "", category: str | None = None) -> list[dict]:
        items = core.memory.search(q, limit=100, category=category) if q else core.memory.all(category)
        return [m.to_dict() for m in items]

    @api.post("/api/memory")
    async def memory_add(body: dict[str, Any]) -> dict:
        try:
            item = core.memory.add(str(body.get("content", "")), str(body.get("category", "general")), "user",
                                   float(body.get("importance", 0.5)), reason="added by the user")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return item.to_dict()

    @api.patch("/api/memory/{memory_id}")
    async def memory_edit(memory_id: str, body: dict[str, Any]) -> dict:
        try:
            return core.memory.update(memory_id, body.get("content"), body.get("category"), body.get("importance"),
                                      reason="edited by the user").to_dict()
        except KeyError:
            raise HTTPException(404, "No such memory.")

    @api.delete("/api/memory/{memory_id}")
    async def memory_forget(memory_id: str) -> dict:
        return {"ok": core.memory.forget(memory_id, reason="removed by the user")}

    @api.get("/api/memory/changes")
    async def memory_changes(limit: int = 100) -> list[dict]:
        return [c.__dict__ for c in core.memory.changes(limit)]

    @api.post("/api/memory/changes/{change_id}/undo")
    async def memory_undo(change_id: str) -> dict:
        if not core.memory.undo(change_id):
            raise HTTPException(400, "That change was already undone or does not exist.")
        return {"ok": True}

    @api.post("/api/memory/dream")
    async def memory_dream() -> list[dict]:
        return await core.dream_now()

    @api.get("/api/profile")
    async def profile() -> list[dict]:
        return [{"name": n, "content": core.profile.read(n)} for n in core.profile.list()]

    @api.put("/api/profile/{name}")
    async def profile_put(name: str, body: dict[str, Any]) -> dict:
        return {"name": core.profile.write(name, str(body.get("content", "")))}

    @api.delete("/api/profile/{name}")
    async def profile_delete(name: str) -> dict:
        if not core.profile.delete(name):
            raise HTTPException(400, "Built-in profile files cannot be deleted.")
        return {"ok": True}

    # goals & routines -------------------------------------------------------------------------

    @api.get("/api/goals")
    async def goals() -> list[dict]:
        return [g.to_dict() for g in core.goals.list()]

    @api.post("/api/goals")
    async def goal_add(body: dict[str, Any]) -> dict:
        try:
            g = core.goals.create(str(body.get("title", "")), str(body.get("description", "")),
                                  list(body.get("steps") or []), str(body.get("check", "")))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return g.to_dict()

    @api.patch("/api/goals/{goal_id}")
    async def goal_edit(goal_id: str, body: dict[str, Any]) -> dict:
        try:
            return core.goals.update(goal_id, status=body.get("status"), done_steps=body.get("done_steps"),
                                     add_steps=body.get("add_steps"), note=str(body.get("note", "")),
                                     check=body.get("check"), title=body.get("title")).to_dict()
        except KeyError:
            raise HTTPException(404, "No such goal.")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @api.delete("/api/goals/{goal_id}")
    async def goal_delete(goal_id: str) -> dict:
        return {"ok": core.goals.delete(goal_id)}

    @api.post("/api/goals/{goal_id}/check")
    async def goal_check(goal_id: str) -> dict:
        if goal_id not in core.goals.items:
            raise HTTPException(404, "No such goal.")
        asyncio.create_task(core.run_goal_check(goal_id))
        return {"ok": True}

    def routine_dict(r) -> dict:
        return {**r.__dict__, "running": bool(r.chat_id and r.chat_id in core.tasks)}

    @api.get("/api/routines")
    async def routines() -> list[dict]:
        return [routine_dict(r) for r in core.routines.list()]

    @api.post("/api/routines")
    async def routine_add(body: dict[str, Any]) -> dict:
        try:
            return routine_dict(core.routines.add(str(body.get("name", "")), str(body.get("schedule", "")), str(body.get("prompt", ""))))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @api.patch("/api/routines/{routine_id}")
    async def routine_edit(routine_id: str, body: dict[str, Any]) -> dict:
        try:
            return routine_dict(core.routines.update(routine_id, **{k: body.get(k) for k in ("name", "schedule", "prompt", "enabled")}))
        except KeyError:
            raise HTTPException(404, "No such routine.")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @api.delete("/api/routines/{routine_id}")
    async def routine_delete(routine_id: str) -> dict:
        return {"ok": core.routines.remove(routine_id)}

    @api.post("/api/routines/{routine_id}/run")
    async def routine_run(routine_id: str) -> dict:
        if routine_id not in core.routines.items:
            raise HTTPException(404, "No such routine.")
        asyncio.create_task(core.run_routine(routine_id))
        return {"ok": True}

    # devices & coding -------------------------------------------------------------------------

    @api.get("/api/devices")
    async def devices() -> dict:
        return hub_state()

    @api.post("/api/devices/{device_id}/call")
    async def device_call(device_id: str, body: dict[str, Any]) -> dict:
        if not core.hub or not core.hub.online:
            raise HTTPException(400, "Not connected to the hub.")
        action = str(body.get("action", ""))
        if action not in ("info", "screen", "files", "notify", "coding.sessions"):
            raise HTTPException(400, "From the console, devices can be asked for info, screen, files, notify and coding.sessions; ask Nemo for the rest.")
        return await core.hub.call(device_id, action, body.get("args") or {}, timeout=60)

    @api.get("/api/coding")
    async def coding() -> dict:
        return {"agents": core.coding.agents(), "sessions": core.coding.sessions(),
                "runs": [r.public() for r in core.coding.runs.values()]}

    @api.get("/api/coding/transcript")
    async def coding_transcript(path: str, last: int = 60) -> list[dict]:
        try:
            return core.coding.transcript(path, last)
        except (PermissionError, OSError) as exc:
            raise HTTPException(400, str(exc))

    @api.post("/api/coding/runs")
    async def coding_run(body: dict[str, Any]) -> dict:
        try:
            run = core.coding.start(str(body.get("agent", "")), str(body.get("prompt", "")),
                                    str(body.get("cwd") or core.config.workspace_path()), body.get("resume"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return run.public()

    @api.post("/api/coding/runs/{run_id}/stop")
    async def coding_stop(run_id: str) -> dict:
        return {"stopped": core.coding.stop(run_id)}

    # avatar ---------------------------------------------------------------------------------------

    @api.put("/api/avatar")
    async def avatar_meta(body: dict[str, Any]) -> dict:
        return core.avatar.set_meta(**body)

    @api.put("/api/avatar/{mood}")
    async def avatar_face(mood: str, body: dict[str, Any]) -> dict:
        data = str(body.get("image", ""))
        if "," in data:
            data = data.split(",", 1)[1]
        try:
            core.avatar.set_face(mood, base64.b64decode(data))
        except (ValueError, OSError) as exc:
            raise HTTPException(400, f"Could not use that image: {exc}")
        return {"ok": True}

    @api.delete("/api/avatar/{mood}")
    async def avatar_clear(mood: str) -> dict:
        return {"ok": core.avatar.clear_face(mood)}

    @api.post("/api/avatar/generate")
    async def avatar_generate(body: dict[str, Any]) -> dict:
        s = core.config.llm
        if not s.image_model or s.kind != "openai":
            raise HTTPException(400, "Your provider has no image model set (Settings → Model → image model). Upload a picture instead.")
        mood = str(body.get("mood", "neutral"))
        prompt = f"{body.get('prompt') or core.avatar.meta().get('description') or 'a small friendly nautilus'}, {mood} expression, " \
                 "a single character portrait, simple flat background, app avatar style"
        try:
            key = core.vault.resolve(core.config.api_key_source())
            async with httpx.AsyncClient(timeout=180) as client:
                r = await client.post(s.base_url.rstrip("/") + "/images/generations",
                                      headers={"Authorization": f"Bearer {key}"},
                                      json={"model": s.image_model, "prompt": prompt, "n": 1, "size": "1024x1024",
                                            "response_format": "b64_json"})
                r.raise_for_status()
                item = r.json()["data"][0]
                image = base64.b64decode(item["b64_json"]) if item.get("b64_json") else (await client.get(item["url"])).content
            core.avatar.set_face(mood, image)
        except (httpx.HTTPError, KeyError, ValueError, VaultError) as exc:
            raise HTTPException(502, f"Image generation failed: {exc}")
        return {"ok": True}

    # account (relay sign-in) -------------------------------------------------------------------------

    @api.post("/api/account/code")
    async def account_code(body: dict[str, Any]) -> dict:
        url = str(body.get("url") or core.config.hub.url).rstrip("/")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{url}/v1/auth/code", json={"identifier": body.get("identifier", "")})
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.json().get("error", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text)
        return r.json()

    @api.post("/api/account/verify")
    async def account_verify(body: dict[str, Any]) -> dict:
        url = str(body.get("url") or core.config.hub.url).rstrip("/")
        cfg = core.config.model_copy(deep=True)
        if not cfg.hub.device_id:
            import uuid

            cfg.hub.device_id = uuid.uuid4().hex
        payload = {"identifier": body.get("identifier", ""), "code": body.get("code", ""), "password": body.get("password", ""),
                   "device": {"id": cfg.hub.device_id, "name": cfg.hub.device_name or body.get("device_name") or "", "kind": "computer"}}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{url}/v1/auth/verify", json=payload)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
        if r.status_code >= 400:
            raise HTTPException(r.status_code, data.get("error", "Sign-in failed."))
        core.vault.set("HUB_TOKEN", data["token"])
        cfg.hub.url, cfg.hub.token = url, "{{vault:HUB_TOKEN}}"
        core.update_config(cfg)
        return {"ok": True, "account": data.get("account", {})}

    @api.post("/api/account/signout")
    async def account_signout() -> dict:
        cfg = core.config.model_copy(deep=True)
        cfg.hub.token = ""
        core.vault.delete("HUB_TOKEN")
        if cfg.llm.provider == "relay":
            cfg.llm.provider = "custom"
        core.update_config(cfg)
        return {"ok": True}

    # live events (SSE) --------------------------------------------------------------------------------

    @api.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        queue = core.subscribe()

        async def stream():
            try:
                hello = {"type": "hello", "running": list(core.tasks), "approvals": core.approvals(),
                         "hands": core.hands, "takeovers": list(core.takeovers), "hub": hub_state()}
                yield f"data: {json.dumps(hello, ensure_ascii=False, default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), 20)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            finally:
                core.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # /v1: for scripts and other programs ---------------------------------------------------------------

    @api.get("/v1/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__, "ready": core.config.is_ready()}

    @api.post("/v1/run")
    async def run(body: dict[str, Any]) -> dict:
        """Run one turn to completion: {"text": "...", "thread": optional id} → {"thread", "status", "text"}."""
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "Send some text.")
        tid = str(body.get("thread") or "")
        thread = core.threads.get(tid) if tid and core.threads.exists(tid) else core.threads.create(title=text[:60])
        agent = core.agent_for(thread)
        if agent.state.value != "idle":
            raise HTTPException(409, "That conversation is busy.")
        task = asyncio.create_task(core._turn(agent, text))
        core.tasks[thread.id] = task
        status, final = await task
        return {"thread": thread.id, "status": status, "text": final}

    # the console ---------------------------------------------------------------------------------------

    @api.get("/{path:path}", include_in_schema=False)
    async def console(path: str):
        if path.startswith(("api/", "v1/")):
            raise HTTPException(404, "Not found.")
        target = (CONSOLE / path).resolve()
        if path and target.is_file() and str(target).startswith(str(CONSOLE)):
            return FileResponse(target)
        index = CONSOLE / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>Nemo</h1><p>The console is not built yet: <code>cd web && npm install && npm run build</code>.</p>")

    return api
