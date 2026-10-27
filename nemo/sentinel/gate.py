"""Sentinel.guard: the only way the agent runs a tool.

placeholder check → assess → policy → grants / ask the user → resolve secrets → run → taint → redact → audit
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from nemo.config import Config
from nemo.schema import RiskLevel, ToolCall, ToolResult, new_id
from nemo.sentinel.audit import AuditLog
from nemo.sentinel.grants import SCOPES, GrantStore, grant_key
from nemo.sentinel.policy import Decision, Policy, Verdict
from nemo.tools.base import BaseTool, CallAssessment, ToolContext, safe_execute, url_host
from nemo.vault import Vault, VaultError, has_placeholder


@dataclass
class ApprovalRequest:
    id: str
    conversation_id: str
    tool: str
    preview: str
    args: dict[str, Any]
    risk: str
    reason: str
    warnings: list[str]
    grant_key: str
    scopes: list[str]
    device: str = "local"
    created: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ApprovalDecision:
    allow: bool
    scope: str = "once"


Approver = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


def refused(call: ToolCall, text: str) -> ToolResult:
    return ToolResult(call_id=call.id, name=call.name, ok=False, refused=True,
                      content=f"Refused by the Sentinel: {text} Do not retry this call; tell the user or try another way.")


class Sentinel:
    def __init__(self, config: Config, vault: Vault, grants: GrantStore, audit: AuditLog, approver: Approver):
        self.config = config
        self.vault = vault
        self.grants = grants
        self.audit = audit
        self.approver = approver
        self.taint: dict[str, list[str]] = {}

    @property
    def policy(self) -> Policy:
        return Policy(self.config.sentinel)

    def tainted(self, conversation_id: str) -> bool:
        return bool(self.taint.get(conversation_id))

    def mark_tainted(self, conversation_id: str, why: str) -> None:
        reasons = self.taint.setdefault(conversation_id, [])
        if why not in reasons:
            reasons.append(why)

    def check(self, call: ToolCall, tool: BaseTool, ctx: ToolContext) -> tuple[CallAssessment, Verdict]:
        a = tool.assess(call.arguments, ctx)
        return a, self.policy.evaluate(call.name, call.arguments, a, self.tainted(ctx.conversation_id))

    def _secrets_for(self, a: CallAssessment) -> dict[str, Any]:
        """Trusted config the call needs, with {{vault:…}} resolved at the last moment."""
        out: dict[str, Any] = {}
        if a.egress and a.egress != "*":
            for host, headers in self.config.http_auth.items():
                if a.egress == host or a.egress.endswith("." + host):
                    out["headers"] = self.vault.resolve(headers)
        return out

    async def guard(self, call: ToolCall, tool: BaseTool, ctx: ToolContext) -> ToolResult:
        cid = ctx.conversation_id
        if has_placeholder(call.arguments):
            self.audit.write(conversation=cid, tool=call.name, decision="deny", stage="placeholder")
            return refused(call, "vault placeholders may not appear in tool arguments written by the model.")
        try:
            a, verdict = self.check(call, tool, ctx)
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(call_id=call.id, name=call.name, ok=False, content=f"Error: bad arguments ({exc}).")

        key = grant_key(call.name, a.target)
        via = verdict.stage
        if verdict.decision is Decision.DENY:
            self.audit.write(conversation=cid, tool=call.name, args=call.arguments, decision="deny", reason=verdict.reason)
            return refused(call, verdict.reason)

        if verdict.decision is Decision.ASK:
            scope = None if verdict.must_ask else self.grants.covers(key, cid)
            if scope:
                via = f"grant:{scope}"
            else:
                request = ApprovalRequest(
                    id=new_id("ap_"), conversation_id=cid, tool=call.name, preview=a.summary or tool.summary(call.arguments),
                    args=call.arguments, risk=a.risk.value, reason=verdict.reason, warnings=a.warnings, grant_key=key,
                    scopes=["once"] if verdict.must_ask else list(SCOPES), device=ctx.device,
                )
                decision = await self.approver(request)
                self.audit.write(conversation=cid, tool=call.name, args=call.arguments, decision="allow" if decision.allow else "deny",
                                 stage="user", scope=decision.scope, reason=verdict.reason, grant_key=key)
                if not decision.allow:
                    return refused(call, "the user declined this action.")
                if not verdict.must_ask and decision.scope in SCOPES:
                    self.grants.add(key, decision.scope, cid)
                via = f"user:{decision.scope}"

        try:
            ctx.secrets = self._secrets_for(a)
        except VaultError as exc:
            return refused(call, str(exc))
        started = time.time()
        result = await safe_execute(tool, call, ctx)
        ctx.secrets = {}

        if a.reads_private and result.ok:
            self.mark_tainted(cid, f"{call.name}: {a.summary or a.target}"[:120])
        result.content = self.vault.redact(result.content)
        self.audit.write(conversation=cid, tool=call.name, args=call.arguments, risk=a.risk.value, via=via,
                         ok=result.ok, ms=int((time.time() - started) * 1000))
        return result


__all__ = ["ApprovalDecision", "ApprovalRequest", "Approver", "Sentinel", "RiskLevel", "url_host"]
