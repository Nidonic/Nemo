"""Standing approvals bound to grant keys ("tool:target") with lifetimes once, conversation, session, 24h, always."""

from __future__ import annotations

import json
import time
from pathlib import Path

SCOPES = ("once", "conversation", "session", "24h", "always")
DAY = 24 * 3600


def grant_key(tool: str, target: str = "") -> str:
    return f"{tool}:{target}" if target else tool


def _covers(granted: str, key: str) -> bool:
    """A grant covers its exact key, and "tool:*" (or a bare "tool") covers every target of that tool."""
    if granted == key:
        return True
    tool = key.split(":", 1)[0]
    return granted in (tool, f"{tool}:*")


class GrantStore:
    def __init__(self, path: Path):
        self.path = path  # approvals.json holds the 24h and always grants
        self.conversation: dict[str, set[str]] = {}
        self.session: set[str] = set()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
        now = time.time()
        return [g for g in items if not g.get("expires") or g["expires"] > now]

    def _save(self, items: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, key: str, scope: str, conversation_id: str = "") -> None:
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        if scope == "once":
            return
        if scope == "conversation":
            self.conversation.setdefault(conversation_id, set()).add(key)
        elif scope == "session":
            self.session.add(key)
        else:
            items = [g for g in self._load() if g["key"] != key]
            items.append({"key": key, "scope": scope, "created": time.time(),
                          "expires": time.time() + DAY if scope == "24h" else None})
            self._save(items)

    def covers(self, key: str, conversation_id: str = "") -> str | None:
        """The scope of a grant covering key, or None."""
        if any(_covers(g, key) for g in self.conversation.get(conversation_id, ())):
            return "conversation"
        if any(_covers(g, key) for g in self.session):
            return "session"
        for g in self._load():
            if _covers(g["key"], key):
                return g["scope"]
        return None

    def list(self) -> list[dict]:
        out = [{"key": k, "scope": "session"} for k in sorted(self.session)]
        return out + self._load()

    def revoke(self, key: str) -> bool:
        found = key in self.session
        self.session.discard(key)
        for keys in self.conversation.values():
            if key in keys:
                keys.discard(key)
                found = True
        items = self._load()
        kept = [g for g in items if g["key"] != key]
        if len(kept) != len(items):
            self._save(kept)
            found = True
        return found
