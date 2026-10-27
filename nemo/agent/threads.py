"""Conversation threads on disk, one JSON file each under <data_dir>/threads."""

from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, Field

from nemo.schema import Message, new_id


class Thread(BaseModel):
    id: str = Field(default_factory=lambda: new_id("t_"))
    title: str = "New chat"
    kind: str = "main"  # main | side (routines, goals, remote tasks)
    created: float = Field(default_factory=time.time)
    updated: float = Field(default_factory=time.time)
    messages: list[Message] = Field(default_factory=list)
    routine_id: str | None = None
    goal_id: str | None = None
    origin: str = "local"  # "local", or "hub:<device id>" for a task sent by another device
    synced_seq: int = 0

    def summary(self) -> dict:
        return {"id": self.id, "title": self.title, "kind": self.kind, "created": self.created, "updated": self.updated,
                "routine_id": self.routine_id, "goal_id": self.goal_id, "origin": self.origin}


class ThreadStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Thread] = {}

    def _path(self, thread_id: str) -> Path:
        if not thread_id.replace("_", "").replace("-", "").isalnum():
            raise KeyError(thread_id)
        return self.root / f"{thread_id}.json"

    def create(self, title: str = "New chat", **fields) -> Thread:
        thread = Thread(title=title, **fields)
        self.save(thread)
        return thread

    def get(self, thread_id: str) -> Thread:
        if thread_id in self._cache:
            return self._cache[thread_id]
        path = self._path(thread_id)
        if not path.exists():
            raise KeyError(thread_id)
        thread = Thread.model_validate_json(path.read_text(encoding="utf-8"))
        self._cache[thread_id] = thread
        return thread

    def exists(self, thread_id: str) -> bool:
        try:
            self.get(thread_id)
            return True
        except KeyError:
            return False

    def save(self, thread: Thread) -> None:
        thread.updated = time.time()
        self._cache[thread.id] = thread
        path = self._path(thread.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(thread.model_dump_json(), encoding="utf-8")
        tmp.replace(path)

    def delete(self, thread_id: str) -> None:
        self._cache.pop(thread_id, None)
        path = self._path(thread_id)
        if path.exists():
            path.unlink()

    def list(self) -> list[dict]:
        out = []
        for path in self.root.glob("*.json"):
            try:
                out.append(self.get(path.stem).summary())
            except (KeyError, ValueError):
                continue
        return sorted(out, key=lambda t: -t["updated"])

    def export(self, thread_id: str) -> str:
        return json.dumps(self.get(thread_id).model_dump(), ensure_ascii=False, indent=2)
