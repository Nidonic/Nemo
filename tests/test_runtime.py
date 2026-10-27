"""Vault, schedules, goals, hands grid, LLM wire formats, and the local server API."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from nemo.computer.grid import GRID, to_screen, validate
from nemo.llm.anthropic import AnthropicChat
from nemo.llm.mock import reply
from nemo.llm.openai_chat import OpenAIChat
from nemo.reminders import next_run
from nemo.schema import Attachment, Message, Role, ToolCall
from nemo.server import create_app
from nemo.vault import Vault, VaultError, has_placeholder


def test_vault_roundtrip_and_encryption(tmp_path):
    v = Vault(tmp_path)
    v.set("GITHUB_TOKEN", "ghp_abc123")
    assert Vault(tmp_path).get("GITHUB_TOKEN") == "ghp_abc123"
    assert b"ghp_abc123" not in (tmp_path / "vault.enc").read_bytes()
    assert v.resolve({"h": "Bearer {{vault:GITHUB_TOKEN}}", "l": ["{{ vault:GITHUB_TOKEN }}"]}) == \
        {"h": "Bearer ghp_abc123", "l": ["ghp_abc123"]}
    with pytest.raises(VaultError):
        v.resolve("{{vault:MISSING}}")
    assert v.list() == ["GITHUB_TOKEN"] and v.delete("GITHUB_TOKEN") and v.list() == []
    assert has_placeholder({"a": ["x", "{{vault:X}}"]}) and not has_placeholder({"a": "{{other}}"})


def test_vault_wrong_key(tmp_path, monkeypatch):
    Vault(tmp_path).set("A", "1")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("NEMO_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(VaultError):
        Vault(tmp_path).get("A")


NOW = datetime(2026, 10, 27, 10, 0)  # a Tuesday


@pytest.mark.parametrize("schedule, expected", [
    ("every 30m", datetime(2026, 10, 27, 10, 30)), ("daily 08:00", datetime(2026, 10, 28, 8, 0)),
    ("weekdays 09:30", datetime(2026, 10, 28, 9, 30)), ("weekly mon 18:00", datetime(2026, 11, 2, 18, 0)),
    ("once 2026-11-02 14:00", datetime(2026, 11, 2, 14, 0)), ("once 2026-01-01 14:00", None),
])
def test_schedules(schedule, expected):
    assert next_run(schedule, NOW) == expected


def test_goal_progress(make_app):
    app = make_app()
    g = app.goals.create("Ship v1", steps=["write", "test", "release"], check="every 2h")
    assert g.progress == 0 and g.next_check
    app.goals.update(g.id, done_steps=["s1", "s2"], note="halfway")
    assert round(app.goals.get(g.id).progress, 2) == 0.67
    app.goals.update(g.id, done_steps=["s3"])
    assert app.goals.get(g.id).status == "done" and app.goals.get(g.id).next_check is None


@pytest.mark.parametrize("x, y, w, h, expected", [
    (0, 0, 1080, 2400, (0, 0)), (999, 999, 1080, 2400, (1079, 2399)),
    (500, 500, 1920, 1080, (960, 540)), (250, 750, 1000, 1000, (250, 750)),
])
def test_grid_to_screen(x, y, w, h, expected):
    assert to_screen(x, y, w, h) == expected


def test_grid_validation():
    assert validate({"action": "tap", "x": 1, "y": 2}) is None
    assert "needs" in validate({"action": "swipe", "x": 1, "y": 2})
    assert "between" in validate({"action": "tap", "x": 1000, "y": 2})
    assert validate({"action": "fly"})
    assert GRID == 999


def test_openai_wire_format_with_images():
    llm = OpenAIChat("http://x/v1", "k", "m")
    msgs = [Message.user("hi"),
            Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="1", name="computer_screen", arguments={})]),
            Message(role=Role.TOOL, tool_call_id="1", name="computer_screen", content="shot",
                    attachments=[Attachment(mime="image/jpeg", data="QUJD")]),
            Message(role=Role.ASSISTANT, content="")]
    out = llm.convert(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user"]
    assert out[3]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_anthropic_wire_format_merges_and_marks_errors():
    llm = AnthropicChat("http://x", "k", "m")
    msgs = [Message.user("hi"),
            Message(role=Role.ASSISTANT, content="ok", tool_calls=[ToolCall(id="1", name="a"), ToolCall(id="2", name="b")]),
            Message(role=Role.TOOL, tool_call_id="1", content="x", ok=False),
            Message(role=Role.TOOL, tool_call_id="2", content="", attachments=[Attachment(data="QUJD")]),
            Message.user("next")]
    out = llm.convert(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    blocks = out[2]["content"]
    assert blocks[0]["is_error"] is True and blocks[1]["content"][1]["type"] == "image"
    assert blocks[-1] == {"type": "text", "text": "next"}


# --- server -----------------------------------------------------------------------------------

@pytest.fixture
def client(make_app):
    app = make_app("Hello from Nemo.")
    with TestClient(create_app(app), base_url="http://127.0.0.1:7860") as c:
        c.core = app
        yield c


def test_server_state_and_turn(client):
    state = client.get("/api/state").json()
    assert state["config"]["ready"] and len(state["tools"]) > 20
    t = client.post("/api/threads").json()
    r = client.post("/v1/run", json={"text": "hi", "thread": t["id"]}).json()
    assert r == {"thread": t["id"], "status": "ok", "text": "Hello from Nemo."}
    msgs = client.get(f"/api/threads/{t['id']}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_server_api_key_goes_to_vault(client):
    client.put("/api/config", json={"llm": {"api_key": "sk-test-1234567890"}})
    raw = (client.core.config.data_dir() / "config.json").read_text()
    assert "sk-test-1234567890" not in raw and "{{vault:LLM_API_KEY}}" in raw
    assert client.core.vault.get("LLM_API_KEY") == "sk-test-1234567890"
    assert client.get("/api/state").json()["config"]["llm"]["api_key"] == ""


def test_server_memory_goals_routines(client):
    m = client.post("/api/memory", json={"content": "Prefers tea", "category": "preference"}).json()
    assert client.get("/api/memory", params={"q": "tea"}).json()[0]["id"] == m["id"]
    client.delete(f"/api/memory/{m['id']}")
    change = client.get("/api/memory/changes").json()[0]
    assert client.post(f"/api/memory/changes/{change['id']}/undo").json() == {"ok": True}
    assert len(client.get("/api/memory").json()) == 1
    g = client.post("/api/goals", json={"title": "Learn Go", "steps": ["tour", "project"]}).json()
    assert client.patch(f"/api/goals/{g['id']}", json={"done_steps": ["s1"]}).json()["progress"] == 0.5
    assert client.post("/api/routines", json={"name": "x", "schedule": "nope", "prompt": "p"}).status_code == 400


def test_server_guards(client):
    assert client.get("/api/state", headers={"host": "evil.example"}).status_code == 403
    r = client.post("/api/threads", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_server_avatar(client):
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "teal").save(buf, "PNG")
    data = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    assert client.put("/api/avatar/happy", json={"image": data}).json() == {"ok": True}
    faces = client.get("/api/state").json()["avatar"]["faces"]
    assert faces["happy"].startswith("data:image/webp;base64,")
