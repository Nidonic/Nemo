"""Relay database: SQLite in WAL mode, 13 tables."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id             TEXT PRIMARY KEY,
    id_hash        TEXT NOT NULL UNIQUE,
    channel        TEXT NOT NULL,
    hint           TEXT NOT NULL,
    created_at     INTEGER NOT NULL,
    granted        INTEGER NOT NULL DEFAULT 0,
    used           INTEGER NOT NULL DEFAULT 0,
    disabled       INTEGER NOT NULL DEFAULT 0,
    identifier_enc TEXT NOT NULL DEFAULT '',
    unlimited      INTEGER NOT NULL DEFAULT 0,
    password_hash  TEXT NOT NULL DEFAULT '',
    password_set_at INTEGER,
    failed_logins  INTEGER NOT NULL DEFAULT 0,
    locked_until   INTEGER,
    invite_code    TEXT NOT NULL DEFAULT '',
    invited_by     TEXT NOT NULL DEFAULT '',
    invites        INTEGER NOT NULL DEFAULT 0,
    grant_uy       INTEGER NOT NULL DEFAULT 0,
    contribute_bonus_at INTEGER,
    invite_settled_at   INTEGER
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash        TEXT PRIMARY KEY,
    prefix          TEXT NOT NULL,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    device          TEXT NOT NULL DEFAULT '',
    hub_device_id   TEXT NOT NULL DEFAULT '',
    hub_device_kind TEXT NOT NULL DEFAULT '',
    created_at      INTEGER NOT NULL,
    last_used_at    INTEGER,
    revoked_at      INTEGER,
    via             TEXT NOT NULL DEFAULT 'code'
);
CREATE INDEX IF NOT EXISTS api_keys_account ON api_keys(account_id);
CREATE TABLE IF NOT EXISTS codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    id_hash    TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    used       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS codes_id_hash ON codes(id_hash, created_at);
CREATE TABLE IF NOT EXISTS ledger (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id        TEXT NOT NULL REFERENCES accounts(id),
    ts                INTEGER NOT NULL,
    kind              TEXT NOT NULL,
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    charged           INTEGER NOT NULL,
    request_id        TEXT NOT NULL DEFAULT '',
    cost_uy           INTEGER NOT NULL DEFAULT 0,
    extra             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ledger_account_ts ON ledger(account_id, ts);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT '',
    ts         INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_account_ts ON events(account_id, ts);
CREATE TABLE IF NOT EXISTS devices (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    id         TEXT NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    os         TEXT NOT NULL DEFAULT '',
    version    TEXT NOT NULL DEFAULT '',
    actions    TEXT NOT NULL DEFAULT '[]',
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    PRIMARY KEY (account_id, id)
);
CREATE TABLE IF NOT EXISTS video_tasks (
    id          TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    model       TEXT NOT NULL DEFAULT '',
    prompt      TEXT NOT NULL DEFAULT '',
    upstream_id TEXT NOT NULL DEFAULT '',
    result_url  TEXT NOT NULL DEFAULT '',
    charged     INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS video_tasks_account ON video_tasks(account_id, created_at);
CREATE TABLE IF NOT EXISTS samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    ts         INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '',
    consent    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS profiles (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id),
    rev        INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    device     TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '{}',
    face       TEXT NOT NULL DEFAULT '',
    connectors TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS model_probes (
    model        TEXT PRIMARY KEY,
    ts           INTEGER NOT NULL,
    ok           INTEGER NOT NULL DEFAULT 0,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    capabilities TEXT NOT NULL DEFAULT '{}',
    error        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sync_conversations (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    cid        TEXT NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    device     TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    deleted_at INTEGER,
    seq        INTEGER NOT NULL,
    PRIMARY KEY (account_id, cid)
);
CREATE INDEX IF NOT EXISTS sync_conversations_seq ON sync_conversations(account_id, seq);
CREATE TABLE IF NOT EXISTS sync_messages (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    mid        TEXT NOT NULL,
    cid        TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    device     TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    deleted_at INTEGER,
    PRIMARY KEY (account_id, mid)
);
CREATE INDEX IF NOT EXISTS sync_messages_cid_seq ON sync_messages(account_id, cid, seq);
"""

TABLES = ("accounts", "api_keys", "codes", "ledger", "events", "devices", "video_tasks", "samples", "profiles",
          "model_probes", "settings", "sync_conversations", "sync_messages")


def now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def run(self, sql: str, *args: Any) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    def event(self, account_id: str, kind: str, detail: Any = "") -> None:
        self.run("INSERT INTO events (account_id, ts, kind, detail) VALUES (?, ?, ?, ?)", account_id, now(), kind,
                 detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))

    def setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key=?", key)
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, value)

    def tables(self) -> list[str]:
        return [r["name"] for r in self.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]

    def close(self) -> None:
        self.conn.close()
