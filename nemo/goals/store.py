"""Long-running goals: a checklist of steps, progress, and a schedule on which the agent checks in by itself."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from nemo.reminders.routines import next_run
from nemo.schema import new_id

STATUSES = ("active", "paused", "done", "failed")


@dataclass
class Step:
    id: str
    text: str
    done: bool = False
    note: str = ""


@dataclass
class Goal:
    id: str
    title: str
    description: str = ""
    status: str = "active"
    steps: list[Step] = field(default_factory=list)
    check: str = ""  # a routine schedule ("every 2h", "daily 09:00"); empty = only when asked
    next_check: str | None = None
    conversation_id: str | None = None
    log: list[dict] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def progress(self) -> float:
        if self.status == "done":
            return 1.0
        return sum(s.done for s in self.steps) / len(self.steps) if self.steps else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["progress"] = round(self.progress, 3)
        return d

    def describe(self) -> str:
        lines = [f"Goal {self.id}: {self.title} [{self.status}, {round(self.progress * 100)}%]"]
        if self.description:
            lines.append(self.description)
        for s in self.steps:
            lines.append(f"  [{'x' if s.done else ' '}] {s.id} {s.text}" + (f" — {s.note}" if s.note else ""))
        for entry in self.log[-5:]:
            lines.append(f"  log {entry['at']}: {entry['text']}")
        return "\n".join(lines)


class GoalStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.items: dict[str, Goal] = {}
        if path.exists():
            try:
                for raw in json.loads(path.read_text(encoding="utf-8")):
                    raw["steps"] = [Step(**s) for s in raw.get("steps", [])]
                    raw.pop("progress", None)
                    g = Goal(**raw)
                    self.items[g.id] = g
            except (ValueError, TypeError):
                pass

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(g) for g in self.items.values()], indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def list(self, status: str | None = None) -> list[Goal]:
        goals = sorted(self.items.values(), key=lambda g: -g.updated)
        return [g for g in goals if not status or g.status == status]

    def get(self, goal_id: str) -> Goal:
        return self.items[goal_id]

    def _schedule(self, g: Goal) -> None:
        when = next_run(g.check, datetime.now()) if g.check and g.status == "active" else None
        g.next_check = when.isoformat(timespec="seconds") if when else None

    def create(self, title: str, description: str = "", steps: list[str] | None = None, check: str = "") -> Goal:
        if check:
            next_run(check, datetime.now())  # validates the schedule
        g = Goal(id=new_id("goal_"), title=title.strip(), description=description.strip(),
                 steps=[Step(id=f"s{i + 1}", text=t.strip()) for i, t in enumerate(steps or []) if t.strip()],
                 check=check.strip())
        self._schedule(g)
        self.items[g.id] = g
        self.save()
        return g

    def update(self, goal_id: str, *, status: str | None = None, done_steps: list[str] | None = None,
               add_steps: list[str] | None = None, note: str = "", step_notes: dict[str, str] | None = None,
               check: str | None = None, title: str | None = None) -> Goal:
        g = self.items[goal_id]
        if status:
            if status not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
            g.status = status
        for sid in done_steps or []:
            for s in g.steps:
                if s.id == sid:
                    s.done = True
        for sid, text in (step_notes or {}).items():
            for s in g.steps:
                if s.id == sid:
                    s.note = text
        for text in add_steps or []:
            g.steps.append(Step(id=f"s{len(g.steps) + 1}", text=text.strip()))
        if check is not None:
            if check:
                next_run(check, datetime.now())
            g.check = check
        if title:
            g.title = title
        if note:
            g.log.append({"at": datetime.now().isoformat(timespec="minutes"), "text": note})
        if g.steps and all(s.done for s in g.steps) and g.status == "active":
            g.status = "done"
        g.updated = time.time()
        self._schedule(g)
        self.save()
        return g

    def delete(self, goal_id: str) -> bool:
        if self.items.pop(goal_id, None) is None:
            return False
        self.save()
        return True

    def due(self, now: datetime) -> list[Goal]:
        return [g for g in self.items.values()
                if g.status == "active" and g.next_check and datetime.fromisoformat(g.next_check) <= now]

    def checked(self, goal_id: str) -> None:
        g = self.items.get(goal_id)
        if g:
            self._schedule(g)
            self.save()
