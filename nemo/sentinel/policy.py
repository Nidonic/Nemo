"""Policy.evaluate: deny list → rules → explicit lists → mode matrix → taint rule → warning override."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatchcase
from typing import Any

from nemo.config import SentinelSettings
from nemo.schema import RiskLevel
from nemo.tools.base import CallAssessment


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class Verdict:
    decision: Decision
    reason: str
    # set by the taint rule or by warnings: no mode and no standing grant can wave the call through
    must_ask: bool = False
    stage: str = ""


MODE_MATRIX: dict[str, dict[RiskLevel, Decision]] = {
    "ask": {RiskLevel.SAFE: Decision.ALLOW, RiskLevel.MODERATE: Decision.ALLOW, RiskLevel.SENSITIVE: Decision.ASK},
    "strict": {RiskLevel.SAFE: Decision.ALLOW, RiskLevel.MODERATE: Decision.ASK, RiskLevel.SENSITIVE: Decision.ASK},
    "auto": {RiskLevel.SAFE: Decision.ALLOW, RiskLevel.MODERATE: Decision.ALLOW, RiskLevel.SENSITIVE: Decision.ALLOW},
}


def host_allowed(host: str, allowlist: list[str]) -> bool:
    host = host.lower().strip(".")
    for entry in allowlist:
        entry = entry.lower().strip(".")
        if entry.startswith("*."):
            entry = entry[2:]
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _matches(patterns: list[str], name: str) -> bool:
    return any(fnmatchcase(name, p) for p in patterns)


class Policy:
    def __init__(self, settings: SentinelSettings):
        self.settings = settings

    def evaluate(self, tool: str, args: dict[str, Any], a: CallAssessment, tainted: bool) -> Verdict:
        s = self.settings

        # 1. deny list
        if _matches(s.deny_tools, tool):
            return Verdict(Decision.DENY, f"{tool} is on the deny list.", stage="deny_list")

        verdict: Verdict | None = None
        # 2. rules: the first match decides
        for rule in s.rules:
            if fnmatchcase(tool, rule.tool) and all(
                fnmatchcase(str(args.get(k, "")), pattern) for k, pattern in rule.args.items()
            ):
                decision = Decision(rule.action)
                reason = rule.reason or f"Matched a sentinel rule ({rule.tool} → {rule.action})."
                if decision is Decision.DENY:
                    return Verdict(decision, reason, stage="rule")
                verdict = Verdict(decision, reason, stage="rule")
                break

        # 3. explicit lists
        if verdict is None:
            if _matches(s.always_allow_tools, tool):
                verdict = Verdict(Decision.ALLOW, f"{tool} is always allowed.", stage="list")
            elif _matches(s.always_ask_tools, tool):
                verdict = Verdict(Decision.ASK, f"{tool} always asks.", stage="list")

        # 4. mode matrix
        if verdict is None:
            decision = MODE_MATRIX[s.mode][a.risk]
            if decision is Decision.ASK:
                reason = " ".join(a.notes) or f"{a.risk.value.capitalize()} action."
            else:
                reason = f"{a.risk.value} action allowed in {s.mode} mode."
            verdict = Verdict(decision, reason, stage="mode")

        # 5. taint: private data read in this conversation may not leave for an unknown host unasked
        if tainted and a.egress and (a.egress == "*" or not host_allowed(a.egress, s.egress_allowlist)):
            where = "an arbitrary destination" if a.egress == "*" else a.egress
            return Verdict(Decision.ASK, f"This conversation has read private data, and this call can send data to {where}.",
                           must_ask=True, stage="taint")

        # 6. warnings always ask
        if a.warnings:
            return Verdict(Decision.ASK, " ".join(a.warnings), must_ask=True, stage="warning")

        return verdict
