"""End to end across devices: a real relay, two Nemo runtimes, a task sent from one to the other, and the
approval for its risky step answered on the device that sent it."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from nemo.app import App
from nemo.config import Config
from nemo.llm.mock import MockLLM, reply

pytest.importorskip("nemo_cloud")

from nemo_cloud import service  # noqa: E402
from nemo_cloud.settings import Settings  # noqa: E402

CODES: dict[str, str] = {}


async def capture(settings, channel, identifier, code):
    CODES[identifier] = code
    return "test"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def relay_url(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "deliver", capture)
    port = free_port()
    app = service.create_app(Settings(db_path=str(tmp_path / "relay.db"), secret="s", code_interval=0))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


async def sign_in(url: str, device_id: str, name: str, kind: str) -> str:
    async with httpx.AsyncClient() as c:
        (await c.post(f"{url}/v1/auth/code", json={"identifier": "me@example.com"})).raise_for_status()
        r = await c.post(f"{url}/v1/auth/verify", json={"identifier": "me@example.com", "code": CODES["me@example.com"],
                                                        "device": {"id": device_id, "name": name, "kind": kind}})
        r.raise_for_status()
        return r.json()["token"]


async def device(tmp_path, url: str, name: str, kind: str, llm: MockLLM) -> App:
    token = await sign_in(url, f"id-{name}", name, kind)
    cfg = Config(home=str(tmp_path / name), workspace=str(tmp_path / name / "ws"))
    cfg.llm.kind, cfg.llm.provider = "mock", "mock"
    cfg.hub.url, cfg.hub.token, cfg.hub.device_id, cfg.hub.device_name = url, token, f"id-{name}", name
    cfg.hub.sync = True
    app = App(cfg, llm=llm)
    await app.start()
    return app


async def wait_for(predicate, timeout: float = 10) -> None:
    deadline = time.time() + timeout
    while not predicate():
        if time.time() > deadline:
            raise TimeoutError("condition not met")
        await asyncio.sleep(0.05)


async def test_task_runs_on_the_other_device_and_asks_the_phone(tmp_path, relay_url):
    pc = await device(tmp_path, relay_url, "pc", "computer",
                      MockLLM(reply("", ("delete_file", {"path": "old.txt"})), reply("Deleted old.txt.")))
    phone = await device(tmp_path, relay_url, "phone", "phone", MockLLM())
    try:
        target = pc.config.workspace_path() / "old.txt"
        target.write_text("x")
        await wait_for(lambda: phone.hub.online and pc.hub.online and
                       sum(d["online"] for d in phone.hub.devices) == 2)

        events = phone.subscribe()
        thread = phone.threads.create()
        assert await phone.send(thread.id, "@pc delete old.txt") == "started"

        # the PC's Sentinel asks; the question arrives on the phone
        approval = None
        while approval is None:
            event = await asyncio.wait_for(events.get(), 10)
            if event["type"] == "approval":
                approval = event["approval"]
        assert approval["tool"] == "delete_file" and approval["device"] == "pc"
        assert target.exists()  # nothing happened yet
        assert await phone.resolve_approval(approval["id"], True, "once")

        await asyncio.wait_for(phone.tasks[thread.id], 15)
        assert not target.exists()
        last = phone.threads.get(thread.id).messages[-1]
        assert last.content == "**pc:** Deleted old.txt."

        # the PC kept its own record of the task, marked as coming from the phone
        remote = [t for t in pc.threads.list() if t["origin"] == "hub:id-phone"]
        assert remote and pc.audit.tail(5)[-1]["tool"] == "delete_file"

        # conversation sync: the phone's chat reaches the PC
        await phone._push(thread.id)
        changed = await pc.sync.pull()
        assert thread.id in changed
        assert [m.content for m in pc.threads.get(thread.id).messages][:1] == ["@pc delete old.txt"]
    finally:
        await phone.shutdown()
        await pc.shutdown()


async def test_denied_on_the_phone_means_nothing_runs(tmp_path, relay_url):
    pc = await device(tmp_path, relay_url, "pc", "computer",
                      MockLLM(reply("", ("delete_file", {"path": "keep.txt"})), reply("Okay, I kept it.")))
    phone = await device(tmp_path, relay_url, "phone", "phone", MockLLM())
    try:
        target = pc.config.workspace_path() / "keep.txt"
        target.write_text("x")
        await wait_for(lambda: phone.hub.online and sum(d["online"] for d in phone.hub.devices) == 2)
        events = phone.subscribe()
        thread = phone.threads.create()
        pc_device = phone.hub.find_device("pc")
        task = asyncio.create_task(phone.remote_task(thread.id, pc_device, "delete keep.txt"))
        while True:
            event = await asyncio.wait_for(events.get(), 10)
            if event["type"] == "approval":
                await phone.resolve_approval(event["approval"]["id"], False)
                break
        assert await asyncio.wait_for(task, 15) == "**pc:** Okay, I kept it."
        assert target.exists()
    finally:
        await phone.shutdown()
        await pc.shutdown()


async def test_direct_calls_info_and_shell_approval(tmp_path, relay_url):
    pc = await device(tmp_path, relay_url, "pc", "computer", MockLLM())
    phone = await device(tmp_path, relay_url, "phone", "phone", MockLLM())
    try:
        await wait_for(lambda: phone.hub.online and sum(d["online"] for d in phone.hub.devices) == 2)
        info = await phone.hub.call("id-pc", "info")
        assert info["ok"] and info["name"] == "pc"
        # a remote shell goes through the PC's Sentinel; nobody answers in time here, so it is refused
        pc.sentinel.approver = lambda request: _deny(request)
        res = await phone.hub.call("id-pc", "shell", {"command": "npm install left-pad"})
        assert res["ok"] is False and "declined" in res["output"]
        offline = await phone.hub.call("id-nowhere", "info")
        assert offline["ok"] is False and "offline" in offline["error"]
    finally:
        await phone.shutdown()
        await pc.shutdown()


async def _deny(request):
    from nemo.sentinel import ApprovalDecision

    return ApprovalDecision(allow=False)
