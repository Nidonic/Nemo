"""Composition root: settings, vault, memory, tools, Sentinel, agents, hub, sync and the background clock."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from nemo import __version__
from nemo.agent.core import NemoAgent, StuckError
from nemo.agent.prompts import os_name
from nemo.agent.threads import Thread, ThreadStore
from nemo.avatar import AvatarStore
from nemo.coding import CodingManager
from nemo.config import Config, load_config, save_config
from nemo.goals import GoalStore
from nemo.hub import HubClient, this_device
from nemo.hub.protocol import ACTIONS
from nemo.llm import BaseLLM, LLMError, LLMResponse, make_llm
from nemo.memory import MemoryStore, ProfileMemory
from nemo.memory.consolidate import dream
from nemo.memory.embeddings import make_embedder
from nemo.reminders import RoutineStore
from nemo.schema import AgentState, Message, Role, ToolCall, ToolResult
from nemo.sentinel import ApprovalDecision, ApprovalRequest, AuditLog, GrantStore, Sentinel
from nemo.sync import SyncClient
from nemo.tools import ToolContext, builtin_tools
from nemo.tools.base import clip
from nemo.tools.hubops import HUB_OPS
from nemo.tools.mcp_tools import MCPManager
from nemo.vault import Vault, VaultError

log = logging.getLogger("nemo")
UI_OUTPUT = 4000
Emit = Callable[[str, dict], Awaitable[None]]


class Busy(Exception):
    pass


@dataclass
class Pending:
    request: ApprovalRequest
    future: asyncio.Future


class EventUI:
    """Streams one thread's turn to the web app (and, for a task sent by another device, back to that device)."""

    def __init__(self, app: "App", thread_id: str, remote: Emit | None = None):
        self.app, self.thread_id, self.remote = app, thread_id, remote

    async def _pub(self, event: dict) -> None:
        await self.app.publish({**event, "thread": self.thread_id})

    async def on_text_delta(self, delta: str) -> None:
        await self._pub({"type": "text", "delta": delta})
        if self.remote:
            await self.remote("text", {"delta": delta})

    async def on_reasoning_delta(self, delta: str) -> None:
        await self._pub({"type": "reasoning", "delta": delta})

    async def on_step_end(self, message: Message) -> None:
        await self._pub({"type": "step_end", "message_id": message.id})

    async def on_tool_start(self, call: ToolCall, summary: str) -> None:
        await self._pub({"type": "tool_start", "id": call.id, "name": call.name, "summary": summary, "args": call.arguments})
        if self.remote:
            await self.remote("tool", {"id": call.id, "name": call.name, "summary": summary})

    async def on_tool_end(self, call: ToolCall, result: ToolResult) -> None:
        await self._pub({"type": "tool_end", "id": call.id, "ok": result.ok, "refused": result.refused,
                         "output": clip(result.content, UI_OUTPUT), "images": len(result.attachments)})
        if self.remote:
            await self.remote("tool_result", {"id": call.id, "ok": result.ok, "summary": clip(result.content, 300)})

    async def on_inbox(self, message: Message) -> None:
        await self._pub({"type": "inbox", "message": message.model_dump(mode="json")})


def event_message(text: str, error: bool = False) -> Message:
    """A notice in the thread (stopped, errors). Shown to the user, never sent to the model."""
    return Message(role=Role.ASSISTANT, name="event", content=text, ok=not error)


class App:
    def __init__(self, config: Config | None = None, llm: BaseLLM | None = None):
        self.config = config or load_config()
        data = self.config.data_dir()
        self.vault = Vault(data)
        self.profile = ProfileMemory(data / "memory", self.config.name)
        self.memory = MemoryStore(data / "memory.db", *self._embedder())
        self.goals = GoalStore(data / "goals.json")
        self.routines = RoutineStore(data / "routines.json")
        self.threads = ThreadStore(data / "threads")
        self.avatar = AvatarStore(data / "avatar")
        self.tools = builtin_tools()
        self.mcp = MCPManager(self.tools, self.vault)
        self.grants = GrantStore(data / "approvals.json")
        self.audit = AuditLog(data / "audit.jsonl")
        self.sentinel = Sentinel(self.config, self.vault, self.grants, self.audit, self._approve)
        self.coding = CodingManager(on_event=self._from_thread)
        self.hub: HubClient | None = None
        self.sync: SyncClient | None = None

        self._llm_override = llm
        self._llm: BaseLLM | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self.agents: dict[str, NemoAgent] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.pending: dict[str, Pending] = {}
        self.mirrors: dict[str, dict] = {}  # approvals of a task running on another device, shown here
        self.remote_origins: dict[str, Emit] = {}  # thread id → how to reach the device that sent the task
        self.takeovers: dict[str, asyncio.Future] = {}
        self.hands: dict[str, dict] = {}
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        self._clock: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_dream = ""
        self._last_pull = 0.0

    # model ------------------------------------------------------------------------

    def _embedder(self) -> tuple[Any, str]:
        s = self.config.llm
        if not (s.embeddings_base_url and s.embeddings_model):
            return None, ""
        try:
            key = self.vault.resolve(s.embeddings_api_key)
        except VaultError:
            key = ""
        return make_embedder(s.embeddings_base_url, key, s.embeddings_model), s.embeddings_model

    def llm(self) -> BaseLLM:
        if self._llm_override is not None:
            return self._llm_override
        if self._llm is None:
            self._llm = make_llm(self.config, self.vault)
        return self._llm

    def record_usage(self, reply: LLMResponse) -> None:
        self.usage["calls"] += 1
        for k in ("prompt_tokens", "completion_tokens"):
            self.usage[k] += int(reply.usage.get(k, 0))

    # events -------------------------------------------------------------------------

    async def publish(self, event: dict) -> None:
        for q in list(self.subscribers):
            q.put_nowait(event)

    def _from_thread(self, event: dict) -> None:
        """Publish from a worker thread (coding runs)."""
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.publish(event)))

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    # settings ---------------------------------------------------------------------------

    def update_config(self, config: Config) -> None:
        hub_changed = config.hub.model_dump() != self.config.hub.model_dump()
        self.config = config
        self.sentinel.config = config
        self._llm = None
        save_config(config)
        if hub_changed and self._loop:
            asyncio.ensure_future(self.restart_hub())

    # approvals ----------------------------------------------------------------------------

    async def _approve(self, request: ApprovalRequest) -> ApprovalDecision:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request.id] = Pending(request, fut)
        agent = self.agents.get(request.conversation_id)
        if agent:
            agent.state = AgentState.WAITING_APPROVAL
        await self.publish({"type": "approval", "thread": request.conversation_id, "approval": request.public()})
        remote = self.remote_origins.get(request.conversation_id)
        if remote:
            await remote("approval", {"approval_id": request.id, "tool": request.tool, "preview": request.preview,
                                      "risk": request.risk, "reason": request.reason, "warnings": request.warnings,
                                      "grant_key": request.grant_key, "scopes": request.scopes})
        try:
            return await fut
        finally:
            self.pending.pop(request.id, None)
            if agent and agent.state is AgentState.WAITING_APPROVAL:
                agent.state = AgentState.RUNNING
            await self.publish({"type": "approval_done", "thread": request.conversation_id, "id": request.id})

    async def resolve_approval(self, approval_id: str, allow: bool, scope: str = "once") -> bool:
        item = self.pending.get(approval_id)
        if item and not item.future.done():
            item.future.set_result(ApprovalDecision(allow=allow, scope=scope))
            return True
        mirror = self.mirrors.pop(approval_id, None)
        if mirror and self.hub:
            await self.hub.approve(mirror["device"], approval_id, allow, scope)
            await self.publish({"type": "approval_done", "thread": mirror["thread"], "id": approval_id})
            return True
        return False

    def approvals(self) -> list[dict]:
        return [p.request.public() for p in self.pending.values()] + [m["approval"] for m in self.mirrors.values()]

    # hands HUD & takeover ------------------------------------------------------------------------

    async def hands_status(self, thread_id: str, device: str, label: str, active: bool) -> None:
        if active:
            self.hands[thread_id] = {"device": device, "label": label, "active": True}
        else:
            self.hands.pop(thread_id, None)
        await self.publish({"type": "hands", "thread": thread_id, "device": device, "label": label, "active": active})

    async def takeover(self, thread_id: str, reason: str) -> str:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.takeovers[thread_id] = fut
        await self.publish({"type": "takeover", "thread": thread_id, "reason": reason})
        try:
            return await fut
        finally:
            self.takeovers.pop(thread_id, None)
            await self.publish({"type": "takeover_done", "thread": thread_id})

    def resolve_takeover(self, thread_id: str, note: str = "") -> bool:
        fut = self.takeovers.get(thread_id)
        if fut is None or fut.done():
            return False
        fut.set_result(note)
        return True

    # turns ----------------------------------------------------------------------------------------

    def agent_for(self, thread: Thread) -> NemoAgent:
        agent = self.agents.get(thread.id)
        if agent is None or agent.thread is not thread:
            agent = NemoAgent(self, thread, llm=self._llm_override, device=self.config.hub.device_id or "local")
            self.agents[thread.id] = agent
        return agent

    async def send(self, thread_id: str, text: str) -> str:
        """Start a turn, or fold the text into the running one. Returns 'started' or 'queued'."""
        thread = self.threads.get(thread_id)
        agent = self.agent_for(thread)
        if agent.state is not AgentState.IDLE:
            await agent.run(text)
            await self.publish({"type": "queued", "thread": thread_id, "text": text})
            return "queued"
        target = self._mention(text)
        if target:
            device, rest = target
            self.tasks[thread_id] = asyncio.create_task(self._relay_turn(thread, device, rest, text))
        else:
            self.tasks[thread_id] = asyncio.create_task(self._turn(agent, text))
        return "started"

    def _mention(self, text: str) -> tuple[dict, str] | None:
        if not text.startswith("@") or self.hub is None:
            return None
        name, _, rest = text[1:].partition(" ")
        device = self.hub.find_device(name)
        if device and device.get("id") != self.config.hub.device_id and rest.strip():
            return device, rest.strip()
        return None

    async def _turn(self, agent: NemoAgent, text: str, remote: Emit | None = None) -> tuple[str, str]:
        """Run a turn to the end; returns (status, final text). status is "ok", "stopped" or "error: …"."""
        tid = agent.thread.id
        await self.publish({"type": "turn_start", "thread": tid})
        status, final = "ok", ""
        try:
            final = await agent.run(text, EventUI(self, tid, remote))
        except asyncio.CancelledError:
            status = "stopped"
            agent.thread.messages.append(event_message("Stopped."))
        except (LLMError, StuckError) as exc:
            status = f"error: {exc}"
            agent.thread.messages.append(event_message(str(exc), error=True))
        except Exception as exc:  # keep the app alive and show what broke
            log.exception("turn failed")
            status = f"error: {exc}"
            agent.thread.messages.append(event_message(f"Something went wrong: {exc}", error=True))
        finally:
            self.threads.save(agent.thread)
            self.tasks.pop(tid, None)
            if tid in self.hands:
                await self.hands_status(tid, "", "", active=False)
            await self.publish({"type": "turn_end", "thread": tid, "status": status})
            asyncio.ensure_future(self._push(tid))
        return status, final

    async def _relay_turn(self, thread: Thread, device: dict, task: str, original: str) -> None:
        """`@Device …`: the other device does the whole task; its progress streams into this thread."""
        thread.messages.append(Message.user(original))
        if thread.title == "New chat":
            thread.title = original[:60]
        self.threads.save(thread)
        await self.publish({"type": "turn_start", "thread": thread.id})
        status = "ok"
        try:
            text = await self.remote_task(thread.id, device, task)
            thread.messages.append(Message(role=Role.ASSISTANT, content=text, device=device.get("id", "")))
        except asyncio.CancelledError:
            status = "stopped"
            thread.messages.append(event_message("Stopped."))
        finally:
            self.threads.save(thread)
            self.tasks.pop(thread.id, None)
            await self.publish({"type": "turn_end", "thread": thread.id, "status": status})

    def stop(self, thread_id: str) -> bool:
        task = self.tasks.get(thread_id)
        if task is None:
            return False
        task.cancel()
        return True

    # devices ---------------------------------------------------------------------------------------

    async def remote_task(self, thread_id: str, device: dict, text: str) -> str:
        if self.hub is None:
            return "Error: not connected to the hub."
        name = device.get("name", device.get("id"))
        await self.publish({"type": "remote", "thread": thread_id, "device": name, "stage": "start", "text": text})

        async def on_event(msg: dict) -> None:
            body = msg["body"]
            stage = body.get("stage")
            if stage == "approval":
                approval = {"id": body["approval_id"], "conversation_id": thread_id, "tool": body.get("tool", ""),
                            "preview": body.get("preview", ""), "args": {}, "risk": body.get("risk", ""),
                            "reason": body.get("reason", ""), "warnings": body.get("warnings", []),
                            "grant_key": body.get("grant_key", ""), "scopes": body.get("scopes", ["once"]),
                            "device": name, "created": time.time()}
                self.mirrors[body["approval_id"]] = {"device": device["id"], "thread": thread_id, "approval": approval}
                await self.publish({"type": "approval", "thread": thread_id, "approval": approval})
            else:
                await self.publish({"type": "remote", "thread": thread_id, "device": name, **body})

        call = self.hub.call(device["id"], "task", {"text": text, "conversation": thread_id, "stream": True},
                             timeout=3600, on_event=on_event)
        try:
            body = await call
        finally:
            for aid in [a for a, m in self.mirrors.items() if m["thread"] == thread_id]:
                self.mirrors.pop(aid, None)
                await self.publish({"type": "approval_done", "thread": thread_id, "id": aid})
        if not body.get("ok"):
            return f"Error from {name}: {body.get('error', 'failed')}"
        return f"**{name}:** {body.get('text', '')}"

    def _hub_handlers(self) -> dict:
        async def info(msg: dict, emit: Emit) -> dict:
            return {"ok": True, **this_device(self.config.hub.device_id, self.config.hub.device_name, __version__,
                                              list(ACTIONS)), "busy": list(self.tasks), "workspace": str(self.config.workspace_path())}

        async def guarded(msg: dict, emit: Emit, tool_name: str, args: dict, tool=None) -> ToolResult:
            cid = f"hub-{msg['from'][:24]}"
            self.remote_origins[cid] = emit
            try:
                tool = tool or self.tools.get(tool_name)
                ctx = ToolContext(app=self, conversation_id=cid, workspace=self.config.workspace_path(), device=f"hub:{msg['from']}")
                return await self.sentinel.guard(ToolCall(name=tool.name, arguments=args), tool, ctx)
            finally:
                self.remote_origins.pop(cid, None)

        async def shell(msg: dict, emit: Emit) -> dict:
            args = {"command": str(msg["args"].get("command", ""))}
            if msg["args"].get("cwd"):
                args["cwd"] = str(msg["args"]["cwd"])
            r = await guarded(msg, emit, "shell", args)
            return {"ok": r.ok, "output": r.content}

        async def files(msg: dict, emit: Emit) -> dict:
            r = await guarded(msg, emit, "list_files", {"path": msg["args"].get("path", "."), "depth": msg["args"].get("depth", 1)})
            return {"ok": r.ok, "output": r.content}

        def op(action: str):
            async def handler(msg: dict, emit: Emit) -> dict:
                r = await guarded(msg, emit, "", dict(msg["args"]), tool=HUB_OPS[action])
                return {"ok": r.ok, ("content_b64" if action == "file.get" and r.ok else "output"): r.content}
            return handler

        async def screen(msg: dict, emit: Emit) -> dict:
            r = await guarded(msg, emit, "computer_screen", {})
            if not r.ok or not r.attachments:
                return {"ok": False, "error": r.content}
            a = r.attachments[0]
            return {"ok": True, "image": a.data, "mime": a.mime, "width": a.width, "height": a.height}

        async def notify(msg: dict, emit: Emit) -> dict:
            await self.publish({"type": "notify", "title": msg["args"].get("title", self.config.name),
                                "body": msg["args"].get("body", ""), "from": msg["from"]})
            return {"ok": True}

        async def task(msg: dict, emit: Emit) -> dict:
            cid = str(msg["args"].get("conversation") or "")
            tid = f"t_hub_{(msg['from'] + cid)[-20:]}".replace("-", "")
            thread = self.threads.get(tid) if self.threads.exists(tid) else self.threads.create(
                title=f"From another device: {str(msg['args'].get('text', ''))[:40]}", id=tid, kind="side",
                origin=f"hub:{msg['from']}")
            agent = self.agent_for(thread)
            if agent.state is not AgentState.IDLE:
                return {"ok": False, "error": "busy with another task from you"}
            self.remote_origins[thread.id] = emit
            try:
                current = asyncio.current_task()
                if current:
                    self.tasks[thread.id] = current
                status, text = await self._turn(agent, str(msg["args"].get("text", "")), remote=emit)
                if status != "ok":
                    return {"ok": False, "error": status, "conversation": thread.id}
                return {"ok": True, "text": text, "conversation": thread.id}
            finally:
                self.remote_origins.pop(thread.id, None)

        async def coding_sessions(msg: dict, emit: Emit) -> dict:
            return {"ok": True, "sessions": self.coding.sessions(), "agents": self.coding.agents()}

        async def coding_send(msg: dict, emit: Emit) -> dict:
            r = await guarded(msg, emit, "coding_send", dict(msg["args"]))
            return {"ok": r.ok, "output": r.content}

        async def coding_stop(msg: dict, emit: Emit) -> dict:
            return {"ok": self.coding.stop(str(msg["args"].get("run", "")))}

        return {"info": info, "shell": shell, "files": files, "file.get": op("file.get"), "file.put": op("file.put"),
                "open": op("open"), "screen": screen, "notify": notify, "task": task,
                "coding.sessions": coding_sessions, "coding.send": coding_send, "coding.stop": coding_stop}

    async def _hub_approve(self, msg: dict) -> None:
        a = msg["args"]
        await self.resolve_approval(str(a.get("approval_id", "")), bool(a.get("allow")), str(a.get("scope") or "once"))

    async def _hub_event(self, msg: dict) -> None:
        if msg["action"] == "devices":
            await self.publish({"type": "devices", "devices": msg["body"].get("devices", [])})

    async def restart_hub(self) -> None:
        if self.hub:
            await self.hub.stop()
            self.hub = None
        self.sync = None
        h = self.config.hub
        if not (h.url and h.token):
            return
        try:
            token = self.vault.resolve(h.token)
        except VaultError as exc:
            log.warning("hub token: %s", exc)
            return
        if not h.device_id:
            h.device_id = uuid.uuid4().hex
            save_config(self.config)
        device = this_device(h.device_id, h.device_name, __version__, [a for a in ACTIONS if a not in ("devices", "hello")])
        self.hub = HubClient(h.url, token, device, self._hub_handlers(), on_event=self._hub_event,
                             on_approve=self._hub_approve)
        self.hub.start()
        if h.sync:
            self.sync = SyncClient(h.url, token, h.device_id, self.threads, self.config.data_dir() / "sync_state.json")

    async def _push(self, thread_id: str) -> None:
        if self.sync is None or not self.threads.exists(thread_id):
            return
        try:
            await self.sync.push(thread_id)
        except Exception as exc:  # sync is best effort; the next push catches up
            log.info("sync push: %s", exc)

    # routines, goals, dreams ------------------------------------------------------------------------

    async def run_routine(self, routine_id: str) -> str:
        r = self.routines.get(routine_id)
        thread = self.threads.get(r.chat_id) if r.chat_id and self.threads.exists(r.chat_id) else None
        if thread is None:
            thread = self.threads.create(title=f"⏰ {r.name}", kind="side", routine_id=r.id)
            r.chat_id = thread.id
            self.routines.save()
        agent = self.agent_for(thread)
        if agent.state is not AgentState.IDLE:
            self.routines.mark_ran(r.id, datetime.now(), "skipped: previous run still going")
            return "skipped"
        task = asyncio.create_task(self._turn(agent, f"[Routine '{r.name}', {datetime.now():%Y-%m-%d %H:%M}]\n{r.prompt}"))
        self.tasks[thread.id] = task
        try:
            status, _ = await task
        except asyncio.CancelledError:
            status = "stopped"
        self.routines.mark_ran(r.id, datetime.now(), status)
        await self.publish({"type": "routines_changed"})
        return status

    async def run_goal_check(self, goal_id: str) -> None:
        g = self.goals.get(goal_id)
        thread = self.threads.get(g.conversation_id) if g.conversation_id and self.threads.exists(g.conversation_id) else None
        if thread is None or thread.goal_id != g.id:
            thread = self.threads.create(title=f"🎯 {g.title}", kind="side", goal_id=g.id)
            g.conversation_id = thread.id
            self.goals.save()
        agent = self.agent_for(thread)
        self.goals.checked(goal_id)
        if agent.state is not AgentState.IDLE:
            return
        task = asyncio.create_task(self._turn(agent, f"[Check-in on goal {g.id}]\n{g.describe()}"))
        self.tasks[thread.id] = task
        try:
            await task
        except asyncio.CancelledError:
            pass
        await self.publish({"type": "goals_changed"})

    async def dream_now(self) -> list[dict]:
        writer = None
        try:
            llm = self.llm()
            writer = llm.complete
        except LLMError:
            pass
        report = await dream(self.memory, writer)
        self._last_dream = datetime.now().strftime("%Y-%m-%d")
        await self.publish({"type": "memory_changed", "dream": report})
        return report

    async def _tick(self) -> None:
        while True:
            try:
                now = datetime.now()
                for r in self.routines.due(now):
                    self.routines.mark_ran(r.id, now, "running")  # moves next_run on, so a slow run is not doubled
                    asyncio.create_task(self.run_routine(r.id))
                for g in self.goals.due(now):
                    asyncio.create_task(self.run_goal_check(g.id))
                if now.hour == 3 and self._last_dream != now.strftime("%Y-%m-%d") and not self.tasks:
                    asyncio.create_task(self.dream_now())
                if self.sync and time.time() - self._last_pull > 30:
                    self._last_pull = time.time()
                    try:
                        changed = await self.sync.pull()
                        if changed:
                            await self.publish({"type": "threads_changed", "threads": changed})
                    except Exception as exc:  # offline relay: try again next tick
                        log.info("sync pull: %s", exc)
            except Exception:
                log.exception("clock")
            await asyncio.sleep(15)

    # lifecycle ---------------------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(os_name)
        await self.mcp.start(self.config.mcp_servers)
        await self.restart_hub()
        self._clock = asyncio.create_task(self._tick())

    async def shutdown(self) -> None:
        if self._clock:
            self._clock.cancel()
        for task in list(self.tasks.values()):
            task.cancel()
        if self.hub:
            await self.hub.stop()
        browser = self.tools.get("browser")
        if browser is not None and hasattr(browser, "close"):
            try:
                await browser.close()
            except Exception:  # the browser may already be gone
                pass
        await self.mcp.stop()
        self.memory.close()

