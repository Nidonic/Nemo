"""The Sentinel. Exports load on first use, so light imports (e.g. scrubbed_env) do not pull in every tool."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AuditLog": "audit", "scrubbed_env": "env",
    "ApprovalDecision": "gate", "ApprovalRequest": "gate", "Sentinel": "gate", "url_host": "gate",
    "SCOPES": "grants", "GrantStore": "grants", "grant_key": "grants",
    "Decision": "policy", "Policy": "policy", "Verdict": "policy", "host_allowed": "policy",
    "sandbox": None,
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module = import_module(f"nemo.sentinel.{_EXPORTS[name] or name}")
    return module if _EXPORTS[name] is None else getattr(module, name)
