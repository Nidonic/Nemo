"""What the model sees of a thread: pruned at user turns, older screenshots reduced to stubs, stuck loops caught."""

from __future__ import annotations

import json

from nemo.schema import Message, Role


def repair(messages: list[Message]) -> list[Message]:
    """Only model-visible roles, and every tool call answered (a stopped run leaves some unanswered)."""
    answered = {m.tool_call_id for m in messages if m.role is Role.TOOL}
    seen_calls: set[str] = set()
    out: list[Message] = []
    for m in messages:
        if m.role not in (Role.USER, Role.ASSISTANT, Role.TOOL) or m.name == "event":
            continue
        if m.role is Role.TOOL and m.tool_call_id not in seen_calls:
            continue  # a result whose call was pruned away
        out.append(m)
        for call in m.tool_calls:
            seen_calls.add(call.id)
            if call.id not in answered:
                out.append(Message(role=Role.TOOL, tool_call_id=call.id, name=call.name, ok=False,
                                   content="Cancelled: the run stopped before this call finished."))
    return out


def prune(messages: list[Message], max_messages: int) -> list[Message]:
    """Keep the newest max_messages, cutting only at a user message so no tool exchange is split.

    The cut moves forward to the next user message; if there is none, it moves back to the last user
    message at or before the cut, keeping the whole exchange even if that exceeds the budget.
    """
    if len(messages) <= max_messages:
        return messages
    cut = len(messages) - max_messages
    forward = next((i for i in range(cut, len(messages)) if messages[i].role is Role.USER), None)
    if forward is not None:
        return messages[forward:]
    backward = next((i for i in range(cut, -1, -1) if messages[i].role is Role.USER), 0)
    return messages[backward:]


def compress_images(messages: list[Message], keep: int) -> list[Message]:
    """Full images only on the newest `keep` image-bearing messages; older ones become text stubs."""
    out = list(messages)
    seen = 0
    for i in range(len(out) - 1, -1, -1):
        m = out[i]
        images = [a for a in m.attachments if a.data]
        if not images:
            continue
        seen += 1
        if seen <= keep:
            continue
        stubs = " ".join(f"[Screenshot from step {a.step or i}: {a.width}x{a.height}]" for a in images)
        out[i] = m.model_copy(update={
            "attachments": [a.model_copy(update={"data": ""}) for a in m.attachments],
            "content": f"{m.content}\n{stubs}".strip(),
        })
    return out


def context_messages(messages: list[Message], max_messages: int = 50, max_images: int = 3) -> list[Message]:
    return compress_images(repair(prune(repair(messages), max_messages)), max_images)


def signature(m: Message) -> str:
    return json.dumps([m.content, [(c.name, c.arguments) for c in m.tool_calls]], sort_keys=True, ensure_ascii=False)


def is_stuck(messages: list[Message], window: int = 3) -> bool:
    """The last `window` assistant messages since the user last spoke are identical."""
    recent: list[str] = []
    for m in reversed(messages):
        if m.role is Role.USER:
            break
        if m.role is Role.ASSISTANT:
            recent.append(signature(m))
            if len(recent) == window:
                break
    return len(recent) == window and len(set(recent)) == 1
