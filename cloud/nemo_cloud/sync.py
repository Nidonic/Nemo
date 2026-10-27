"""Conversation sync: every change gets the account's next sequence number; devices pull everything after
the last number they saw. Deletes are tombstones, so they sync too."""

from __future__ import annotations

from nemo_cloud.db import DB, now

ROLES = ("user", "assistant")
MAX_CONTENT = 200_000


class Sync:
    def __init__(self, db: DB):
        self.db = db

    def _next_seq(self, c, account_id: str) -> int:
        key = f"seq:{account_id}"
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        seq = int(row["value"]) + 1 if row else 1
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(seq)))
        return seq

    def push(self, account_id: str, device: str, conversations: list[dict], messages: list[dict]) -> int:
        t = now()
        with self.db.tx() as c:
            seq = 0
            for conv in conversations[:500]:
                cid = str(conv.get("cid", ""))[:64]
                if not cid:
                    continue
                seq = self._next_seq(c, account_id)
                if conv.get("deleted"):
                    c.execute("UPDATE sync_conversations SET deleted=1, deleted_at=?, seq=?, updated_at=? WHERE account_id=? AND cid=?",
                              (t, seq, t, account_id, cid))
                    c.execute("UPDATE sync_messages SET deleted=1, deleted_at=? WHERE account_id=? AND cid=?", (t, account_id, cid))
                    continue
                c.execute(
                    "INSERT INTO sync_conversations (account_id, cid, kind, title, device, created_at, updated_at, seq) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, cid) DO UPDATE SET "
                    "title=excluded.title, kind=excluded.kind, updated_at=excluded.updated_at, seq=excluded.seq, deleted=0",
                    (account_id, cid, "side" if conv.get("kind") == "side" else "main", str(conv.get("title", ""))[:200],
                     device, int(conv.get("created_at") or t), int(conv.get("updated_at") or t), seq))
            for msg in messages[:2000]:
                mid, cid = str(msg.get("mid", ""))[:64], str(msg.get("cid", ""))[:64]
                if not mid or not cid:
                    continue
                seq = self._next_seq(c, account_id)
                if msg.get("deleted"):
                    c.execute("UPDATE sync_messages SET deleted=1, deleted_at=?, seq=? WHERE account_id=? AND mid=?",
                              (t, seq, account_id, mid))
                    continue
                role = msg.get("role") if msg.get("role") in ROLES else "user"
                c.execute(
                    "INSERT INTO sync_messages (account_id, mid, cid, seq, role, content, device, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(account_id, mid) DO UPDATE SET content=excluded.content, seq=excluded.seq",
                    (account_id, mid, cid, seq, role, str(msg.get("content", ""))[:MAX_CONTENT], device,
                     int(msg.get("created_at") or t)))
        return self.current(account_id)

    def current(self, account_id: str) -> int:
        return int(self.db.setting(f"seq:{account_id}", "0"))

    def pull(self, account_id: str, since: int, limit: int = 2000) -> dict:
        convs = self.db.all("SELECT cid, kind, title, device, created_at, updated_at, deleted, seq FROM sync_conversations "
                            "WHERE account_id=? AND seq>? ORDER BY seq LIMIT ?", account_id, since, limit)
        msgs = self.db.all("SELECT mid, cid, role, content, device, created_at, deleted, seq FROM sync_messages "
                           "WHERE account_id=? AND seq>? ORDER BY seq LIMIT ?", account_id, since, limit)
        # a truncated list caps the cursor, so nothing between pages is skipped
        top = self.current(account_id)
        if len(convs) == limit:
            top = min(top, convs[-1]["seq"])
        if len(msgs) == limit:
            top = min(top, msgs[-1]["seq"])
        return {"conversations": [dict(r) for r in convs if r["seq"] <= top],
                "messages": [dict(r) for r in msgs if r["seq"] <= top],
                "seq": top, "more": top < self.current(account_id)}
