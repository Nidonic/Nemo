"""The dream cycle: find near-duplicate memories (Jaccard > 0.6) and propose merge / rewrite / drop.

Proposals keep every key entity (numbers, names, CJK terms) of what they replace, or they are rejected.
Applied proposals go through MemoryStore, so each one lands in memory_changes and can be undone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from nemo.memory.store import MemoryItem, MemoryStore
from nemo.memory.tokenize import CJK_RUN, STOPWORDS, jaccard, token_set

THRESHOLD = 0.6
_CAPITALISED = re.compile(r"\b[A-Z][\w-]+")
_NUMBER = re.compile(r"\d[\d.,:/-]*")

# given (prompt) returns model text; used to word merges and rewrites
Writer = Callable[[str], Awaitable[str]]


@dataclass
class Proposal:
    action: str  # merge | rewrite | drop
    ids: list[str]
    content: str = ""
    reason: str = ""
    similarity: float = 0.0
    before: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def entities(text: str) -> set[str]:
    """Things a rewrite must not lose: numbers, capitalised names mid-sentence, CJK terms."""
    found = {n.strip(".,:/-") for n in _NUMBER.findall(text)}
    for m in _CAPITALISED.finditer(text):
        before = text[:m.start()].rstrip()
        if before and before[-1] not in ".!?:":  # a capital that only starts a sentence is not a name
            if m.group().lower() not in STOPWORDS:
                found.add(m.group().lower())
    for run in CJK_RUN.findall(text):
        found |= {run[i:i + 2] for i in range(len(run) - 1)} if len(run) > 1 else {run}
    return {e for e in found if e}


def keeps_entities(before: list[str], after: str, *, superseded: str = "") -> bool:
    """True when `after` still mentions every key entity of `before` (minus those of a superseded fact)."""
    needed = set().union(*(entities(b) for b in before)) - entities(superseded)
    have = entities(after) | token_set(after)
    return needed <= have


def find_pairs(items: list[MemoryItem], threshold: float = THRESHOLD) -> list[tuple[MemoryItem, MemoryItem, float]]:
    sets = [token_set(m.content) for m in items]
    pairs = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            sim = jaccard(sets[i], sets[j])
            if sim > threshold:
                pairs.append((items[i], items[j], sim))
    return sorted(pairs, key=lambda p: -p[2])


async def propose(store: MemoryStore, writer: Writer | None = None, threshold: float = THRESHOLD) -> list[Proposal]:
    items = store.all()
    used: set[str] = set()
    proposals: list[Proposal] = []
    for a, b, sim in find_pairs(items, threshold):
        if a.id in used or b.id in used:
            continue
        ta, tb = token_set(a.content), token_set(b.content)
        older, newer = (a, b) if (a.updated_at or a.created_at) <= (b.updated_at or b.created_at) else (b, a)

        if ta <= tb or tb <= ta:
            small, big = (a, b) if len(ta) <= len(tb) else (b, a)
            proposals.append(Proposal("drop", [small.id], reason=f"Already covered by: {big.content}", similarity=sim,
                                      before=[small.to_dict()]))
        elif writer is not None:
            text = await writer(
                "Two saved facts about the user overlap. Reply with JSON only: "
                '{"action": "merge" | "rewrite", "content": "<one fact>"}. '
                "Use merge when both are true and complementary (combine them into one sentence that keeps every "
                "name, number and detail). Use rewrite when the newer fact replaces the older one.\n"
                f"Older: {older.content}\nNewer: {newer.content}"
            )
            try:
                data = json.loads(text[text.index("{"): text.rindex("}") + 1])
                action, content = data["action"], str(data["content"]).strip()
            except (ValueError, KeyError):
                continue
            if action == "rewrite":
                ok = keeps_entities([newer.content], content)
            else:
                action, ok = "merge", keeps_entities([a.content, b.content], content)
            if not ok or not content:
                continue  # the safety guard: a proposal that drops entities is rejected
            ids = [older.id, newer.id]
            proposals.append(Proposal(action, ids, content=content, similarity=sim,
                                      reason="Newer fact replaces an older one." if action == "rewrite" else "Complementary facts combined.",
                                      before=[older.to_dict(), newer.to_dict()]))
        else:
            continue
        used.update(proposals[-1].ids)
    return proposals


def apply(store: MemoryStore, proposal: Proposal) -> str | None:
    """Apply one proposal; returns the change id, or None when the memories changed meanwhile."""
    current = [store.get(i) for i in proposal.ids]
    if any(m is None for m in current):
        return None
    if proposal.action == "drop":
        store.forget(proposal.ids[0], reason=f"dream: {proposal.reason}")
    elif proposal.action in ("merge", "rewrite"):
        store.merge(proposal.ids, proposal.content, reason=f"dream {proposal.action}: {proposal.reason}")
    else:
        return None
    latest = store.changes(1)
    return latest[0].id if latest else None


async def dream(store: MemoryStore, writer: Writer | None = None) -> list[dict]:
    """One consolidation pass: propose, apply, and report what changed (each entry carries its undo id)."""
    report = []
    for p in await propose(store, writer):
        change_id = apply(store, p)
        if change_id:
            report.append({**p.to_dict(), "change_id": change_id})
    return report
