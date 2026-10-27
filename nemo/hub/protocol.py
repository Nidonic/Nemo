"""Hub wire protocol, shared by devices and the relay broker.

Frame: {"type": "call|result|event|approve|stop", "id": str, "from": str, "to": str,
        "action": str, "args": {}, "body": {}}

call    ask a device to do something (info, shell, files, file.get, file.put, open, screen, notify, task, coding.*)
result  the answer to a call (same id); body = {"ok": bool, ...} or {"ok": false, "error": str}
event   progress for a call (same id); for task: body.stage ∈ tool | tool_result | approval | text | reasoning
        broker → device: action "devices" lists the account's devices and who is online
approve the user's verdict for a remote approval: args = {approval_id, allow, scope}
stop    cancel a running task: args = {call?: id, conversation?: id}
"""

from __future__ import annotations

import json
import uuid
from typing import Any

TYPES = ("call", "result", "event", "approve", "stop")
ACTIONS = ("info", "shell", "files", "file.get", "file.put", "open", "screen", "notify", "task",
           "coding.sessions", "coding.send", "coding.stop", "devices", "hello")
MAX_FRAME = 16 * 1024 * 1024


def frame(type: str, action: str = "", *, to: str = "", id: str | None = None, args: dict | None = None,
          body: dict | None = None, sender: str = "") -> dict[str, Any]:
    if type not in TYPES:
        raise ValueError(f"frame type must be one of {TYPES}")
    return {"type": type, "id": id or uuid.uuid4().hex[:16], "from": sender, "to": to, "action": action,
            "args": args or {}, "body": body or {}}


def parse(raw: str | bytes) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if len(raw) > MAX_FRAME:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") not in TYPES:
        return None
    data.setdefault("id", uuid.uuid4().hex[:16])
    for key in ("from", "to", "action"):
        data[key] = str(data.get(key) or "")
    for key in ("args", "body"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data
