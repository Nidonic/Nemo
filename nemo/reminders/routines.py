"""Routines: prompts Nemo runs on a schedule, even when no browser tab is open.

Schedules, in local time:
  every 30m | every 2h | every 1d
  daily 08:00
  weekdays 09:30
  weekly mon 18:00
  once 2026-11-02 14:00
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path



DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIME = r"([01]?\d|2[0-3]):([0-5]\d)"


def _at(day: datetime, hh: str, mm: str) -> datetime:
    return day.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)


def next_run(schedule: str, after: datetime) -> datetime | None:
    """The first time strictly after `after` the schedule fires; None when it never will again."""
    s = schedule.strip().lower()

    if m := re.fullmatch(r"every\s+(\d+)\s*(m|min|mins|minutes?|h|hours?|d|days?)", s):
        n = int(m.group(1))
        if n <= 0:
            raise ValueError("The interval must be at least 1.")
        unit = m.group(2)[0]
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        if delta < timedelta(minutes=1):
            raise ValueError("The shortest interval is one minute.")
        return after + delta

    if m := re.fullmatch(rf"daily\s+{_TIME}", s):
        candidate = _at(after, m.group(1), m.group(2))
        return candidate if candidate > after else candidate + timedelta(days=1)

    if m := re.fullmatch(rf"weekdays\s+{_TIME}", s):
        candidate = _at(after, m.group(1), m.group(2))
        while candidate <= after or candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    if m := re.fullmatch(rf"weekly\s+({'|'.join(DAYS)})\w*\s+{_TIME}", s):
        target = DAYS.index(m.group(1))
        candidate = _at(after, m.group(2), m.group(3))
        while candidate <= after or candidate.weekday() != target:
            candidate += timedelta(days=1)
        return candidate

    if m := re.fullmatch(rf"once\s+(\d{{4}}-\d{{2}}-\d{{2}})[ t]{_TIME}", s):
        when = datetime.strptime(f"{m.group(1)} {int(m.group(2)):02d}:{m.group(3)}", "%Y-%m-%d %H:%M")
        return when if when > after else None

    raise ValueError(
        "Unknown schedule. Use 'every 30m', 'every 2h', 'daily 08:00', 'weekdays 09:30', "
        "'weekly mon 18:00' or 'once 2026-11-02 14:00'."
    )


@dataclass
class Routine:
    id: str
    name: str
    schedule: str
    prompt: str
    enabled: bool = True
    next_run: str | None = None
    last_run: str | None = None
    last_status: str | None = None
    chat_id: str | None = None
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class RoutineStore:
    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, Routine] = {}
        if self.path.exists():
            try:
                for raw in json.loads(self.path.read_text(encoding="utf-8")):
                    r = Routine(**raw)
                    self.items[r.id] = r
            except (ValueError, TypeError):
                pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in self.items.values()], indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[Routine]:
        return sorted(self.items.values(), key=lambda r: r.created)

    def get(self, routine_id: str) -> Routine:
        return self.items[routine_id]

    def add(self, name: str, schedule: str, prompt: str) -> Routine:
        when = next_run(schedule, datetime.now())
        if when is None:
            raise ValueError("That time has already passed.")
        r = Routine(id=uuid.uuid4().hex[:8], name=name.strip() or "Routine", schedule=schedule.strip(),
                    prompt=prompt.strip(), next_run=when.isoformat(timespec="seconds"))
        self.items[r.id] = r
        self.save()
        return r

    def update(self, routine_id: str, **changes) -> Routine:
        r = self.items[routine_id]
        if changes.get("schedule"):
            next_run(changes["schedule"], datetime.now())  # raises ValueError on a bad schedule
        for key in ("name", "schedule", "prompt", "enabled"):
            if changes.get(key) is not None:
                setattr(r, key, changes[key])
        if changes.get("schedule") or changes.get("enabled"):
            when = next_run(r.schedule, datetime.now())
            r.next_run = when.isoformat(timespec="seconds") if when else None
            if when is None:
                r.enabled = False
        self.save()
        return r

    def remove(self, routine_id: str) -> bool:
        if self.items.pop(routine_id, None) is None:
            return False
        self.save()
        return True

    def due(self, now: datetime) -> list[Routine]:
        return [r for r in self.items.values()
                if r.enabled and r.next_run and datetime.fromisoformat(r.next_run) <= now]

    def mark_ran(self, routine_id: str, now: datetime, status: str) -> None:
        r = self.items.get(routine_id)
        if r is None:
            return
        r.last_run = now.isoformat(timespec="seconds")
        r.last_status = status
        when = next_run(r.schedule, now)
        r.next_run = when.isoformat(timespec="seconds") if when else None
        if when is None:
            r.enabled = False
        self.save()
