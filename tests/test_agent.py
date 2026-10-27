import asyncio

import pytest

from nemo.agent import StuckError
from nemo.agent.context import compress_images, context_messages, is_stuck, prune
from nemo.llm import LLMResponse, ThinkFilter, reply
from nemo.schema import AgentState, Attachment, Message, Role, ToolCall
from nemo.sentinel import ApprovalDecision

from conftest import Approver


class Recorder:
    def __init__(self):
        self.text, self.reasoning, self.tools, self.inbox = [], [], [], []

    async def on_text_delta(self, d): self.text.append(d)
    async def on_reasoning_delta(self, d): self.reasoning.append(d)
    async def on_step_end(self, m): pass
    async def on_tool_start(self, c, s): self.tools.append(c.name)
    async def on_tool_end(self, c, r): pass
    async def on_inbox(self, m): self.inbox.append(m.content)


async def test_tool_then_answer(make_app):
    app = make_app(reply("", ("list_files", {})), reply("Your workspace has hello.txt."))
    (app.config.workspace_path() / "hello.txt").write_text("hi")
    thread = app.threads.create()
    ui = Recorder()
    text = await app.agent_for(thread).run("what's in my workspace?", ui)
    assert text == "Your workspace has hello.txt."
    assert [m.role for m in thread.messages] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert "hello.txt" in thread.messages[2].content
    assert ui.tools == ["list_files"] and "".join(ui.text) == text
    assert thread.title == "what's in my workspace?"
    # the model saw the tool result on its second call
    assert app.llm().calls[1]["messages"][-1].role is Role.TOOL


async def test_think_blocks_stream_separately(make_app):
    app = make_app("<think>The user wants a greeting.</think>Hello!")
    ui = Recorder()
    thread = app.threads.create()
    assert await app.agent_for(thread).run("hi", ui) == "Hello!"
    assert "".join(ui.reasoning) == "The user wants a greeting."
    assert thread.messages[-1].reasoning == "The user wants a greeting."


def test_think_filter_across_chunks():
    f = ThinkFilter()
    out = []
    for chunk in ["Hi <thi", "nk>secret", " plan</th", "ink> there", "<"]:
        out += f.feed(chunk)
    out += f.flush()
    text = "".join(p for k, p in out if k == "text")
    reasoning = "".join(p for k, p in out if k == "reasoning")
    assert text == "Hi  there<" and reasoning == "secret plan"


async def test_exhausted_thinking_budget_retries_with_more_room(make_app):
    app = make_app(LLMResponse(text="", reasoning="thinking…", finish_reason="length"), reply("Done."))
    thread = app.threads.create()
    assert await app.agent_for(thread).run("hard question") == "Done."
    calls = app.llm().calls
    assert calls[0]["max_tokens"] is None and calls[1]["max_tokens"] == app.llm().roomier_max_tokens()


async def test_stuck_detection_stops_the_loop(make_app):
    same = [reply("", ("list_files", {"path": "."})) for _ in range(5)]
    app = make_app(*same)
    thread = app.threads.create()
    with pytest.raises(StuckError):
        await app.agent_for(thread).run("loop forever")
    assert len([m for m in thread.messages if m.role is Role.ASSISTANT]) == 3


def test_is_stuck_resets_on_user_message():
    a = Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="1", name="x", arguments={"a": 1})])
    b = Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="2", name="x", arguments={"a": 1})])
    assert is_stuck([Message.user("go"), a, b, a])
    assert not is_stuck([a, b, Message.user("again"), a])
    assert not is_stuck([Message.user("go"), a, b])


async def test_inbox_folds_mid_flight_messages(make_app):
    gate = asyncio.Event()

    async def slow_tool(args, ctx):
        await gate.wait()
        return "done"

    from nemo.tools.base import FunctionTool, obj

    def second_call(messages):
        assert any(m.role is Role.USER and m.content == "also check the time" for m in messages)
        return reply("Done, and it is noon.")

    app = make_app(reply("", ("slow", {})), second_call)
    app.tools.add(FunctionTool("slow", "slow", obj({}), slow_tool))
    thread = app.threads.create()
    agent = app.agent_for(thread)
    ui = Recorder()
    run = asyncio.create_task(agent.run("do the slow thing", ui))
    await asyncio.sleep(0.05)
    assert agent.state is AgentState.RUNNING
    assert await agent.run("also check the time") == ""  # queued, not a second run
    gate.set()
    assert await run == "Done, and it is noon."
    assert ui.inbox == ["also check the time"]
    assert agent.state is AgentState.IDLE


async def test_refused_call_reaches_the_model(make_app):
    approver = Approver(ApprovalDecision(allow=False))
    app = make_app(reply("", ("delete_file", {"path": "x.txt"})), reply("Okay, I left it."), approver=approver)
    (app.config.workspace_path() / "x.txt").write_text("x")
    thread = app.threads.create()
    await app.agent_for(thread).run("delete x.txt")
    tool_msg = thread.messages[2]
    assert tool_msg.ok is False and "Do not retry" in tool_msg.content
    assert (app.config.workspace_path() / "x.txt").exists()


async def test_terminate_ends_the_turn(make_app):
    app = make_app(reply("", ("terminate", {"summary": "All done"})), reply("never reached"))
    thread = app.threads.create()
    assert await app.agent_for(thread).run("finish") == "All done"
    assert len(app.llm().calls) == 1


async def test_shell_tool_runs_for_real(make_app):
    app = make_app(reply("", ("shell", {"command": "echo nemo-ok"})), reply("ok"),
                   approver=Approver(ApprovalDecision(allow=True)))
    thread = app.threads.create()
    await app.agent_for(thread).run("echo")
    assert "nemo-ok" in thread.messages[2].content


# --- context shaping -------------------------------------------------------------------------

def exchange(i: int) -> list[Message]:
    call = ToolCall(id=f"c{i}", name="list_files", arguments={})
    return [Message.user(f"q{i}"), Message(role=Role.ASSISTANT, tool_calls=[call]),
            Message(role=Role.TOOL, tool_call_id=f"c{i}", name="list_files", content="r"),
            Message(role=Role.ASSISTANT, content=f"a{i}")]


def test_prune_never_splits_a_tool_exchange():
    msgs = sum((exchange(i) for i in range(10)), [])
    for budget in range(1, 40):
        kept = prune(msgs, budget)
        assert kept[0].role is Role.USER
        ids = {m.tool_call_id for m in kept if m.role is Role.TOOL}
        calls = {c.id for m in kept for c in m.tool_calls}
        assert ids == calls


def test_prune_moves_back_when_no_user_after_cut():
    msgs = [Message.user("start")] + [Message(role=Role.ASSISTANT, content=str(i)) for i in range(10)]
    assert prune(msgs, 3)[0].content == "start"


def test_context_drops_events_and_answers_stopped_calls():
    msgs = [Message.user("go"), Message(role=Role.ASSISTANT, tool_calls=[ToolCall(id="z", name="shell", arguments={})]),
            Message(role=Role.ASSISTANT, name="event", content="Stopped."), Message.user("again")]
    out = context_messages(msgs)
    assert [m.role for m in out] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.USER]
    assert "Cancelled" in out[2].content


def test_old_screenshots_become_stubs():
    msgs = []
    for i in range(5):
        msgs.append(Message(role=Role.TOOL, tool_call_id=str(i), content=f"shot {i}",
                            attachments=[Attachment(data="AAAA", width=1080, height=2400, step=i + 1)]))
    out = compress_images(msgs, keep=3)
    assert [bool(m.attachments[0].data) for m in out] == [False, False, True, True, True]
    assert "[Screenshot from step 1: 1080x2400]" in out[0].content
    assert msgs[0].attachments[0].data == "AAAA"  # the stored thread keeps its images
