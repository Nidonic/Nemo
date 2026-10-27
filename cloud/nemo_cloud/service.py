"""The relay's HTTP API (FastAPI): sign-in, account, model proxy, sync, profile, admin, and the /v1/hub socket."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from nemo_cloud import __version__
from nemo_cloud.allowance import Allowance, OutOfAllowance
from nemo_cloud.auth import Auth, AuthError, normalise, to_account
from nemo_cloud.catalog import Catalog
from nemo_cloud.crypto import Crypto
from nemo_cloud.db import DB, now
from nemo_cloud.hub import Hub
from nemo_cloud.providers import Proxy, UpstreamError
from nemo_cloud.senders import DeliveryError, deliver
from nemo_cloud.settings import Settings
from nemo_cloud.sync import Sync

log = logging.getLogger("nemo_cloud")


def bearer(header: str | None) -> str:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    s = settings or Settings()
    db = DB(s.db_path)
    crypto = Crypto(s.secret)
    auth = Auth(db, crypto, s)
    allowance = Allowance(db, s)
    proxy = Proxy(s, allowance, transport)
    catalog = Catalog(db, s, transport)
    hub = Hub(db)
    sync = Sync(db)
    if s.ephemeral_secret:
        log.warning("NEMO_CLOUD_SECRET is not set: using a throwaway secret (development only).")

    api = FastAPI(title="Nemo relay", version=__version__)
    api.state.db, api.state.hub, api.state.auth, api.state.allowance = db, hub, auth, allowance

    @api.exception_handler(AuthError)
    async def auth_error(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @api.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    def key_row(request: Request):
        row = auth.authenticate(bearer(request.headers.get("authorization")))
        if row is None:
            raise HTTPException(401, "Sign in again: this key is missing, revoked or unknown.")
        return row

    def admin(request: Request) -> None:
        if not s.admin_token or bearer(request.headers.get("authorization")) != s.admin_token:
            raise HTTPException(403, "Admin token required.")

    @api.get("/v1/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__, "tables": len(db.tables())}

    # sign-in ---------------------------------------------------------------------------------

    @api.post("/v1/auth/code")
    async def send_code(body: dict[str, Any], request: Request) -> dict:
        identity = normalise(str(body.get("identifier", "")))
        code = auth.issue_code(identity, request.client.host if request.client else "")
        try:
            via = await deliver(s, identity.channel, identity.value, code)
        except DeliveryError as exc:
            raise HTTPException(502, str(exc))
        return {"ok": True, "channel": identity.channel, "hint": identity.hint, "via": via, "expires_in": s.code_ttl}

    @api.post("/v1/auth/verify")
    async def verify(body: dict[str, Any]) -> dict:
        identity = normalise(str(body.get("identifier", "")))
        device = body.get("device") if isinstance(body.get("device"), dict) else {}
        if body.get("password"):
            row = auth.check_password(identity, str(body["password"]))
            via, created = "password", False
        else:
            auth.check_code(identity, str(body.get("code", "")))
            row, created = auth.find_or_create(identity, str(body.get("invite", "")))
            via = "code"
        if row["disabled"]:
            raise HTTPException(403, "This account is disabled.")
        token = auth.issue_key(row["id"], device, via)
        account = to_account(row)
        return {"token": token, "created": created,
                "account": {"id": account.id, "hint": account.hint, "channel": account.channel, "invite_code": account.invite_code}}

    @api.post("/v1/auth/password")
    async def set_password(body: dict[str, Any], k=Depends(key_row)) -> dict:
        auth.set_password(k["account_id"], str(body.get("password", "")))
        return {"ok": True}

    @api.post("/v1/auth/signout")
    async def signout(k=Depends(key_row)) -> dict:
        return {"ok": auth.revoke(k["key_hash"], k["account_id"])}

    # account -------------------------------------------------------------------------------------

    @api.get("/v1/me")
    async def me(k=Depends(key_row)) -> dict:
        account = auth.get(k["account_id"])
        assert account is not None
        return {"id": account.id, "hint": account.hint, "channel": account.channel, "invite_code": account.invite_code,
                "has_password": account.has_password, "allowance": allowance.status(account.id),
                "devices": hub.devices(account.id), "keys": auth.keys(account.id),
                "this_device": k["hub_device_id"]}

    @api.get("/v1/me/ledger")
    async def ledger(k=Depends(key_row), limit: int = 100) -> list[dict]:
        return allowance.history(k["account_id"], min(limit, 1000))

    @api.get("/v1/profile")
    async def get_profile(k=Depends(key_row)) -> dict:
        row = db.one("SELECT * FROM profiles WHERE account_id=?", k["account_id"])
        if row is None:
            return {"rev": 0, "body": {}, "face": {}, "connectors": []}
        return {"rev": row["rev"], "updated_at": row["updated_at"], "device": row["device"], "body": json.loads(row["body"]),
                "face": json.loads(row["face"] or "{}"), "connectors": json.loads(row["connectors"])}

    @api.put("/v1/profile")
    async def put_profile(body: dict[str, Any], k=Depends(key_row)) -> dict:
        row = db.one("SELECT rev FROM profiles WHERE account_id=?", k["account_id"])
        rev = row["rev"] if row else 0
        if "rev" in body and int(body["rev"]) != rev:
            raise HTTPException(409, f"The profile changed on another device (now rev {rev}). Reload and try again.")
        face = body.get("face") or {}
        if len(json.dumps(face)) > 4_000_000:
            raise HTTPException(413, "The avatar stills are too large (4 MB in all).")
        db.run("INSERT INTO profiles (account_id, rev, updated_at, device, body, face, connectors) VALUES (?, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(account_id) DO UPDATE SET rev=excluded.rev, updated_at=excluded.updated_at, device=excluded.device, "
               "body=excluded.body, face=excluded.face, connectors=excluded.connectors",
               k["account_id"], rev + 1, now(), k["hub_device_id"], json.dumps(body.get("body") or {}, ensure_ascii=False),
               json.dumps(face), json.dumps(body.get("connectors") or []))
        return {"rev": rev + 1}

    # models ------------------------------------------------------------------------------------------

    @api.get("/v1/models")
    async def models(k=Depends(key_row)) -> dict:
        return {"object": "list", "data": catalog.models()}

    @api.post("/v1/chat/completions")
    async def chat(request: Request, k=Depends(key_row)):
        payload = await request.json()
        headers = {h: v for h, v in request.headers.items() if h.startswith("x-upstream-")}
        try:
            if payload.get("stream"):
                gen = await proxy.stream(k["account_id"], payload, headers)
                return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
            return await proxy.complete(k["account_id"], payload, headers)
        except OutOfAllowance as exc:
            return JSONResponse({"error": {"message": str(exc), "type": "insufficient_quota"}}, status_code=402)
        except UpstreamError as exc:
            return JSONResponse({"error": {"message": str(exc), "type": "upstream_error"}}, status_code=exc.status)

    # sync -------------------------------------------------------------------------------------------------

    @api.post("/v1/sync/push")
    async def sync_push(body: dict[str, Any], k=Depends(key_row)) -> dict:
        seq = sync.push(k["account_id"], k["hub_device_id"], list(body.get("conversations") or []), list(body.get("messages") or []))
        return {"seq": seq}

    @api.get("/v1/sync/pull")
    async def sync_pull(since: int = 0, k=Depends(key_row)) -> dict:
        return sync.pull(k["account_id"], since)

    # hub ----------------------------------------------------------------------------------------------------

    @api.websocket("/v1/hub")
    async def hub_socket(ws: WebSocket) -> None:
        row = auth.authenticate(bearer(ws.headers.get("authorization")) or ws.query_params.get("token", ""))
        if row is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        await hub.serve(ws, row)

    # admin -----------------------------------------------------------------------------------------------------

    @api.get("/admin/accounts", dependencies=[Depends(admin)])
    async def admin_accounts(limit: int = 100) -> list[dict]:
        rows = db.all("SELECT id, channel, hint, created_at, grant_uy, used, disabled, unlimited, invites FROM accounts "
                      "ORDER BY created_at DESC LIMIT ?", limit)
        return [dict(r) for r in rows]

    @api.post("/admin/accounts/{account_id}/adjust", dependencies=[Depends(admin)])
    async def admin_adjust(account_id: str, body: dict[str, Any]) -> dict:
        if auth.get(account_id) is None:
            raise HTTPException(404, "No such account.")
        allowance.adjust(account_id, int(body.get("delta_uy", 0)), str(body.get("reason", "admin")))
        return allowance.status(account_id)

    @api.post("/admin/accounts/{account_id}/disable", dependencies=[Depends(admin)])
    async def admin_disable(account_id: str, body: dict[str, Any]) -> dict:
        db.run("UPDATE accounts SET disabled=? WHERE id=?", int(bool(body.get("disabled", True))), account_id)
        db.event(account_id, "disabled" if body.get("disabled", True) else "enabled")
        return {"ok": True}

    @api.get("/admin/events", dependencies=[Depends(admin)])
    async def admin_events(limit: int = 200) -> list[dict]:
        return [dict(r) for r in db.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", limit)]

    @api.post("/admin/probe", dependencies=[Depends(admin)])
    async def admin_probe() -> list[dict]:
        return await catalog.probe()

    return api


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=os.environ.get("NEMO_CLOUD_HOST", "0.0.0.0"), port=int(os.environ.get("NEMO_CLOUD_PORT", "8787")))


if __name__ == "__main__":
    main()
