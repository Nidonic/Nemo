"""Domain models shared by every layer: messages, tool calls, results, risk and agent state."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class RiskLevel(str, Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    SENSITIVE = "sensitive"

    @property
    def rank(self) -> int:
        return {"safe": 0, "moderate": 1, "sensitive": 2}[self.value]

    def max(self, other: "RiskLevel") -> "RiskLevel":
        return self if self.rank >= other.rank else other


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"


class Attachment(BaseModel):
    """An image (usually a screenshot) carried by a message."""

    kind: str = "image"
    mime: str = "image/png"
    data: str = ""  # base64; emptied when the image is compressed to a stub
    width: int = 0
    height: int = 0
    step: int = 0


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: new_id("call_"))
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def signature(self) -> tuple[str, str]:
        import json

        return self.name, json.dumps(self.arguments, sort_keys=True, ensure_ascii=False)


class ToolResult(BaseModel):
    call_id: str
    name: str
    ok: bool = True
    content: str = ""
    attachments: list[Attachment] = Field(default_factory=list)
    # set when the Sentinel stopped the call; the agent must not retry it
    refused: bool = False


class Message(BaseModel):
    id: str = Field(default_factory=lambda: new_id("m_"))
    role: Role
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    ok: bool | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    device: str = ""

    @classmethod
    def user(cls, text: str, **kw: Any) -> "Message":
        return cls(role=Role.USER, content=text, **kw)

    @classmethod
    def tool(cls, result: ToolResult) -> "Message":
        return cls(role=Role.TOOL, content=result.content, tool_call_id=result.call_id, name=result.name,
                   ok=result.ok, attachments=result.attachments)
