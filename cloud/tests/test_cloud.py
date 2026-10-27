import json

import httpx
import pytest
from fastapi.testclient import TestClient

from nemo_cloud import service
from nemo_cloud.allowance import cost_uy
from nemo_cloud.auth import AuthError, normalise
from nemo_cloud.crypto import Crypto, hash_password, verify_password
from nemo_cloud.db import TABLES
from nemo_cloud.settings import Settings

SENT: dict[str, str] = {}


async def fake_deliver(settings, channel, identifier, code):
    SENT[identifier] = code
    return "test"


def upstream(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("stream"):
        chunks = [
            {"id": "x", "choices": [{"delta": {"content": "Hel"}}]},
            {"id": "x", "choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
            {"id": "x", "choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}},
        ]
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={"id": "y", "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
                                     "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})


@pytest.fixture
def relay(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "deliver", fake_deliver)
    s = Settings(db_path=str(tmp_path / "relay.db"), secret="test-secret", admin_token="admin",
                 upstream_base_url="https://upstream.test/v1", upstream_api_key="up",
                 prices={"deepseek-chat": [2_000_000, 8_000_000]}, signup_grant_uy=5_000_000, daily_cap_uy=1_000_000,
                 code_interval=0)
    app = service.create_app(s, transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        client.settings = s
        yield client


def sign_in(client, identifier="a@example.com", device_id="dev-a", kind="computer", invite=""):
    client.post("/v1/auth/code", json={"identifier": identifier}).raise_for_status()
    r = client.post("/v1/auth/verify", json={"identifier": identifier, "code": SENT[normalise(identifier).value],
                                             "device": {"id": device_id, "name": device_id, "kind": kind}, "invite": invite})
    r.raise_for_status()
    return r.json()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_schema_has_13_tables(relay):
    db = relay.app.state.db
    assert set(TABLES) <= set(db.tables()) and len(TABLES) == 13
    assert db.one("PRAGMA journal_mode")[0] == "wal"


def test_identifiers():
    assert normalise("138 1234 5678").value == "+8613812345678"
    assert normalise("+86 13812345678").hint == "138****5678"
    assert normalise("Alice@Example.com").value == "alice@example.com"
    assert normalise("alice@example.com").hint == "a***@example.com"
    with pytest.raises(AuthError):
        normalise("+1 555 0100")


def test_crypto():
    c = Crypto("s")
    assert c.id_hash("x") == Crypto("s").id_hash("x") != Crypto("t").id_hash("x")
    assert c.decrypt(c.encrypt("+8613812345678")) == "+8613812345678"
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and verify_password("correct horse", stored) and not verify_password("x", stored)


def test_signup_and_account(relay):
    r = sign_in(relay)
    assert r["created"] and r["token"].startswith("nm_")
    me = relay.get("/v1/me", headers=auth(r["token"])).json()
    assert me["hint"] == "a***@example.com" and me["allowance"]["balance_uy"] == 5_000_000
    db = relay.app.state.db
    row = db.one("SELECT identifier_enc, id_hash FROM accounts")
    assert "example.com" not in row["identifier_enc"] and "example.com" not in row["id_hash"]
    # signing in again reaches the same account with a new key
    r2 = sign_in(relay, device_id="dev-b")
    assert not r2["created"] and r2["account"]["id"] == r["account"]["id"]


def test_code_rules(relay):
    relay.settings.code_interval = 60
    relay.post("/v1/auth/code", json={"identifier": "b@example.com"}).raise_for_status()
    assert relay.post("/v1/auth/code", json={"identifier": "b@example.com"}).status_code == 429  # too soon
    for _ in range(5):
        assert relay.post("/v1/auth/verify", json={"identifier": "b@example.com", "code": "000000x"}).status_code == 401
    r = relay.post("/v1/auth/verify", json={"identifier": "b@example.com", "code": SENT["b@example.com"]})
    assert r.status_code == 429  # attempts exhausted, even with the right code
    assert relay.post("/v1/auth/code", json={"identifier": "not-an-id"}).status_code == 400


def test_password_and_lockout(relay):
    token = sign_in(relay)["token"]
    assert relay.post("/v1/auth/password", json={"password": "short"}, headers=auth(token)).status_code == 400
    relay.post("/v1/auth/password", json={"password": "a long password"}, headers=auth(token)).raise_for_status()
    ok = relay.post("/v1/auth/verify", json={"identifier": "a@example.com", "password": "a long password"})
    assert ok.status_code == 200 and ok.json()["token"]
    for _ in range(5):
        assert relay.post("/v1/auth/verify", json={"identifier": "a@example.com", "password": "wrong"}).status_code == 401
    locked = relay.post("/v1/auth/verify", json={"identifier": "a@example.com", "password": "a long password"})
    assert locked.status_code == 429


def test_signout_revokes_key(relay):
    token = sign_in(relay)["token"]
    relay.post("/v1/auth/signout", headers=auth(token)).raise_for_status()
    assert relay.get("/v1/me", headers=auth(token)).status_code == 401


def test_invite_bonus(relay):
    a = sign_in(relay)
    code = relay.get("/v1/me", headers=auth(a["token"])).json()["invite_code"]
    sign_in(relay, "friend@example.com", invite=code)
    me = relay.get("/v1/me", headers=auth(a["token"])).json()
    assert me["allowance"]["pool_uy"] == 5_000_000 + relay.settings.invite_bonus_uy


def test_cost_formula():
    s = Settings(secret="x", prices={"m": [2_000_000, 8_000_000]})
    # 1000 × 2 uy + 500 × 8 uy = 6000 uy
    assert cost_uy(s, "m", 1000, 500) == 6000
    assert cost_uy(s, "m", 1, 0) == 2


def test_proxy_charges_ledger(relay):
    token = sign_in(relay)["token"]
    r = relay.post("/v1/chat/completions", headers=auth(token), json={"model": "default", "messages": [{"role": "user", "content": "hi"}]})
    assert r.json()["choices"][0]["message"]["content"] == "Hi"
    with relay.stream("POST", "/v1/chat/completions", headers=auth(token),
                      json={"model": "default", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as s:
        body = "".join(s.iter_text())
    assert "Hel" in body and "[DONE]" in body
    me = relay.get("/v1/me", headers=auth(token)).json()
    assert me["allowance"]["used_uy"] == 12000
    ledger = relay.get("/v1/me/ledger", headers=auth(token)).json()
    assert [e["kind"] for e in ledger[:2]] == ["chat", "chat"] and ledger[0]["model"] == "deepseek-chat"


def test_byok_is_not_charged(relay):
    token = sign_in(relay)["token"]
    relay.post("/v1/chat/completions", headers={**auth(token), "X-Upstream-Base-Url": "https://mine.test/v1", "X-Upstream-Key": "k"},
               json={"model": "my-model", "messages": []}).raise_for_status()
    assert relay.get("/v1/me", headers=auth(token)).json()["allowance"]["used_uy"] == 0


def test_daily_cap_and_pool_refuse(relay):
    token = sign_in(relay)["token"]
    acct = relay.get("/v1/me", headers=auth(token)).json()["id"]
    relay.app.state.allowance.charge(acct, "chat", "deepseek-chat", 500_000, 0)  # 1,000,000 uy: the daily cap
    r = relay.post("/v1/chat/completions", headers=auth(token), json={"messages": []})
    assert r.status_code == 402 and r.json()["error"]["type"] == "insufficient_quota"
    events = relay.get("/admin/events", headers=auth("admin")).json()
    assert any(e["kind"] == "refusal" for e in events)
    # an admin top-up does not lift the daily cap, but unlimited does
    relay.app.state.db.run("UPDATE accounts SET unlimited=1 WHERE id=?", acct)
    assert relay.post("/v1/chat/completions", headers=auth(token), json={"messages": []}).status_code == 200


def test_admin_requires_token(relay):
    assert relay.get("/admin/accounts").status_code == 403
    token = sign_in(relay)["token"]
    acct = relay.get("/v1/me", headers=auth(token)).json()["id"]
    status = relay.post(f"/admin/accounts/{acct}/adjust", headers=auth("admin"), json={"delta_uy": 1_000_000}).json()
    assert status["pool_uy"] == 6_000_000


def test_sync_sequence_and_tombstones(relay):
    a = sign_in(relay, device_id="phone", kind="phone")["token"]
    b = sign_in(relay, device_id="pc")["token"]
    relay.post("/v1/sync/push", headers=auth(a), json={
        "conversations": [{"cid": "c1", "title": "Trip", "kind": "main"}],
        "messages": [{"mid": "m1", "cid": "c1", "role": "user", "content": "Book a train"},
                     {"mid": "m2", "cid": "c1", "role": "assistant", "content": "Which day?"}]}).raise_for_status()
    pulled = relay.get("/v1/sync/pull", params={"since": 0}, headers=auth(b)).json()
    assert [m["mid"] for m in pulled["messages"]] == ["m1", "m2"] and pulled["seq"] == 3
    assert {m["device"] for m in pulled["messages"]} == {"phone"}
    assert relay.get("/v1/sync/pull", params={"since": 3}, headers=auth(b)).json()["messages"] == []
    relay.post("/v1/sync/push", headers=auth(b), json={"conversations": [{"cid": "c1", "deleted": True}], "messages": []})
    later = relay.get("/v1/sync/pull", params={"since": 3}, headers=auth(a)).json()
    assert later["conversations"][0]["deleted"] == 1
    # another account sees nothing
    other = sign_in(relay, "z@example.com")["token"]
    assert relay.get("/v1/sync/pull", params={"since": 0}, headers=auth(other)).json()["messages"] == []


def test_profile_revisions(relay):
    token = sign_in(relay)["token"]
    assert relay.put("/v1/profile", headers=auth(token), json={"body": {"name": "Nemo"}, "face": {"happy": "AAAA"}}).json() == {"rev": 1}
    assert relay.put("/v1/profile", headers=auth(token), json={"rev": 0, "body": {}}).status_code == 409
    p = relay.get("/v1/profile", headers=auth(token)).json()
    assert p["rev"] == 1 and p["face"]["happy"] == "AAAA"


def frame(type, action="", to="", id="f1", args=None, body=None):
    return {"type": type, "id": id, "to": to, "action": action, "args": args or {}, "body": body or {}}


def test_hub_routes_within_an_account_only(relay):
    a = sign_in(relay, device_id="phone", kind="phone")["token"]
    b = sign_in(relay, device_id="pc")["token"]
    x = sign_in(relay, "x@example.com", device_id="intruder")["token"]
    with relay.websocket_connect("/v1/hub", headers=auth(a)) as phone, \
            relay.websocket_connect("/v1/hub", headers=auth(b)) as pc, \
            relay.websocket_connect("/v1/hub", headers=auth(x)) as intruder:
        def drain_devices(ws):
            msg = ws.receive_json()
            while msg["type"] == "event" and msg["action"] == "devices":
                msg = ws.receive_json()
            return msg

        phone.send_json(frame("call", "info", to="pc", id="c1"))
        got = drain_devices(pc)
        assert got["type"] == "call" and got["from"] == "phone" and got["id"] == "c1"
        pc.send_json(frame("result", "info", to="phone", id="c1", body={"ok": True, "os": "Windows"}))
        res = drain_devices(phone)
        assert res["type"] == "result" and res["body"]["os"] == "Windows" and res["from"] == "pc"

        # a forged sender is overwritten, and another account's device is unreachable
        intruder.send_json({**frame("call", "shell", to="pc", id="evil", args={"command": "whoami"}), "from": "phone"})
        res = drain_devices(intruder)
        assert res["type"] == "result" and res["body"]["ok"] is False and "offline" in res["body"]["error"]

        # approvals travel back to the device that asked
        pc.send_json(frame("event", "task", to="phone", id="t1", body={"stage": "approval", "approval_id": "ap1"}))
        assert drain_devices(phone)["body"]["approval_id"] == "ap1"
        phone.send_json(frame("approve", to="pc", args={"approval_id": "ap1", "allow": True, "scope": "once"}))
        verdict = drain_devices(pc)
        assert verdict["type"] == "approve" and verdict["args"]["allow"] is True

        phone.send_json(frame("call", "devices", id="d1"))
        listed = drain_devices(phone)
        names = {d["id"]: d["online"] for d in listed["body"]["devices"]}
        assert names == {"phone": True, "pc": True}


def test_hub_rejects_bad_token(relay):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with relay.websocket_connect("/v1/hub", headers=auth("nm_nope")) as ws:
            ws.receive_json()
