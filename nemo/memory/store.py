"""Atomic memories in SQLite, ranked by rare-word IDF (optionally fused with embeddings), with an undo changelog."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from nemo.memory.tokenize import document_frequency, score, token_set
from nemo.schema import new_id

CATEGORIES = ("preference", "identity", "work", "habit", "general")
SOURCES = ("user", "chat", "inference", "imported")
RRF_K = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    category   TEXT NOT NULL,
    source     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    importance REAL NOT NULL DEFAULT 0.5
);
CREATE TABLE IF NOT EXISTS memory_changes (
    id         TEXT PRIMARY KEY,
    at         TEXT NOT NULL,
    action     TEXT NOT NULL,
    before     TEXT NOT NULL DEFAULT '[]',
    after      TEXT NOT NULL DEFAULT '{}',
    reason     TEXT NOT NULL DEFAULT '',
    restored   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memory_vectors (
    id     TEXT PRIMARY KEY,
    model  TEXT NOT NULL,
    vector TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MemoryItem:
    id: str
    content: str
    category: str = "general"
    source: str = "chat"
    created_at: str = ""
    updated_at: str = ""
    importance: float = 0.5

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Change:
    id: str
    at: str
    action: str
    before: list[dict]
    after: dict
    reason: str
    restored: bool


Embedder = Callable[[list[str]], "list[list[float]]"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class MemoryStore:
    def __init__(self, path: Path, embedder: Embedder | None = None, embed_model: str = ""):
        self.path = path
        self.embedder = embedder
        self.embed_model = embed_model
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # rows ---------------------------------------------------------------------

    @staticmethod
    def _item(row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(**{k: row[k] for k in row.keys()})

    def get(self, memory_id: str) -> MemoryItem | None:
        row = self.db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return self._item(row) if row else None

    def all(self, category: str | None = None) -> list[MemoryItem]:
        if category:
            rows = self.db.execute("SELECT * FROM memories WHERE category=? ORDER BY created_at DESC", (category,))
        else:
            rows = self.db.execute("SELECT * FROM memories ORDER BY created_at DESC")
        return [self._item(r) for r in rows]

    def _insert(self, item: MemoryItem) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO memories (id, content, category, source, created_at, updated_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item.id, item.content, item.category, item.source, item.created_at, item.updated_at, item.importance),
        )

    def _delete(self, memory_id: str) -> None:
        self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.db.execute("DELETE FROM memory_vectors WHERE id=?", (memory_id,))

    def _log(self, action: str, before: list[MemoryItem], after: MemoryItem | None, reason: str) -> str:
        change_id = new_id("chg_")
        self.db.execute(
            "INSERT INTO memory_changes (id, at, action, before, after, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (change_id, now_iso(), action, json.dumps([b.to_dict() for b in before], ensure_ascii=False),
             json.dumps(after.to_dict() if after else {}, ensure_ascii=False), reason),
        )
        return change_id

    # writes (every one is logged, so every one can be undone) -----------------------

    def add(self, content: str, category: str = "general", source: str = "chat", importance: float = 0.5,
            reason: str = "") -> MemoryItem:
        content = content.strip()
        if not content:
            raise ValueError("A memory needs some text.")
        with self._lock:
            for existing in self.all():
                if existing.content.strip().lower() == content.lower():
                    return existing
            item = MemoryItem(id=new_id("mem_"), content=content,
                              category=category if category in CATEGORIES else "general",
                              source=source if source in SOURCES else "chat",
                              created_at=now_iso(), importance=max(0.0, min(1.0, importance)))
            self._insert(item)
            self._log("create", [], item, reason)
            self.db.commit()
        self._embed([item])
        return item

    def update(self, memory_id: str, content: str | None = None, category: str | None = None,
               importance: float | None = None, reason: str = "edited") -> MemoryItem:
        with self._lock:
            old = self.get(memory_id)
            if old is None:
                raise KeyError(memory_id)
            new = MemoryItem(**old.to_dict())
            if content is not None and content.strip():
                new.content = content.strip()
            if category in CATEGORIES:
                new.category = category
            if importance is not None:
                new.importance = max(0.0, min(1.0, float(importance)))
            new.updated_at = now_iso()
            self._insert(new)
            self._log("rewrite", [old], new, reason)
            self.db.commit()
        if new.content != old.content:
            self._embed([new])
        return new

    def forget(self, memory_id: str, reason: str = "forgotten") -> bool:
        with self._lock:
            old = self.get(memory_id)
            if old is None:
                return False
            self._delete(memory_id)
            self._log("drop", [old], None, reason)
            self.db.commit()
        return True

    def merge(self, ids: list[str], content: str, reason: str = "merged") -> MemoryItem:
        with self._lock:
            before = [m for m in (self.get(i) for i in ids) if m]
            if len(before) < 2:
                raise ValueError("merge needs at least two existing memories")
            keep = before[0]
            item = MemoryItem(id=new_id("mem_"), content=content.strip(), category=keep.category, source="inference",
                              created_at=min(b.created_at for b in before), updated_at=now_iso(),
                              importance=max(b.importance for b in before))
            for b in before:
                self._delete(b.id)
            self._insert(item)
            self._log("merge", before, item, reason)
            self.db.commit()
        self._embed([item])
        return item

    def changes(self, limit: int = 100) -> list[Change]:
        rows = self.db.execute("SELECT * FROM memory_changes ORDER BY at DESC, rowid DESC LIMIT ?", (limit,))
        return [Change(id=r["id"], at=r["at"], action=r["action"], before=json.loads(r["before"]),
                       after=json.loads(r["after"]), reason=r["reason"], restored=bool(r["restored"])) for r in rows]

    def undo(self, change_id: str) -> bool:
        """Put things back the way they were before a change."""
        with self._lock:
            row = self.db.execute("SELECT * FROM memory_changes WHERE id=?", (change_id,)).fetchone()
            if row is None or row["restored"]:
                return False
            after = json.loads(row["after"])
            if after.get("id"):
                self._delete(after["id"])
            restored = [MemoryItem(**b) for b in json.loads(row["before"])]
            for item in restored:
                self._insert(item)
            self.db.execute("UPDATE memory_changes SET restored=1 WHERE id=?", (change_id,))
            self.db.commit()
        self._embed(restored)
        return True

    # retrieval -------------------------------------------------------------------

    def lexical(self, query: str, pool: list[MemoryItem] | None = None) -> list[tuple[MemoryItem, float]]:
        pool = pool if pool is not None else self.all()
        docs = [token_set(m.content) for m in pool]
        df, n = document_frequency(docs)
        q = token_set(query)
        scored = [(m, score(q, d, df, n)) for m, d in zip(pool, docs)]
        return sorted([s for s in scored if s[1] > 0], key=lambda s: (-s[1], -s[0].importance))

    def search(self, query: str, limit: int = 8, category: str | None = None) -> list[MemoryItem]:
        pool = self.all(category)
        lexical = self.lexical(query, pool)
        semantic = self._semantic(query, pool)
        if not semantic:
            return [m for m, _ in lexical[:limit]]
        # reciprocal rank fusion: Σ 1 / (k + rank)
        fused: dict[str, float] = {}
        by_id = {m.id: m for m in pool}
        for ranking in ([m for m, _ in lexical], semantic):
            for rank, m in enumerate(ranking, 1):
                fused[m.id] = fused.get(m.id, 0.0) + 1 / (RRF_K + rank)
        order = sorted(fused, key=lambda i: -fused[i])
        return [by_id[i] for i in order[:limit]]

    # embeddings (optional) ---------------------------------------------------------

    def _embed(self, items: list[MemoryItem]) -> None:
        if not self.embedder or not items:
            return
        try:
            vectors = self.embedder([m.content for m in items])
        except Exception:  # an embeddings outage must not break remembering
            return
        with self._lock:
            for m, v in zip(items, vectors):
                self.db.execute("INSERT OR REPLACE INTO memory_vectors (id, model, vector) VALUES (?, ?, ?)",
                                (m.id, self.embed_model, json.dumps(v)))
            self.db.commit()

    def _semantic(self, query: str, pool: list[MemoryItem], min_sim: float = 0.3) -> list[MemoryItem]:
        if not self.embedder or not pool:
            return []
        try:
            q = self.embedder([query])[0]
        except Exception:
            return []
        rows = {r["id"]: json.loads(r["vector"]) for r in self.db.execute(
            "SELECT id, vector FROM memory_vectors WHERE model=?", (self.embed_model,))}
        scored = [(m, cosine(q, rows[m.id])) for m in pool if m.id in rows]
        return [m for m, s in sorted(scored, key=lambda s: -s[1]) if s >= min_sim]

    def close(self) -> None:
        self.db.close()
