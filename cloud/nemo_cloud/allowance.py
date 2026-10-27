"""The free allowance, in micro-yuan (uy; 1 CNY = 1,000,000 uy): a lifetime pool per account and a daily cap."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime

from nemo_cloud.db import DB, now
from nemo_cloud.settings import Settings


class OutOfAllowance(Exception):
    pass


def price_for(settings: Settings, model: str) -> tuple[int, int]:
    p = settings.prices.get(model) or settings.prices.get("*") or [0, 0]
    return int(p[0]), int(p[1])


def cost_uy(settings: Settings, model: str, prompt_tokens: int, completion_tokens: int) -> int:
    """cost = ⌈(prompt × price_in + completion × price_out) / 1,000,000⌉, prices in uy per million tokens."""
    pin, pout = price_for(settings, model)
    return math.ceil((prompt_tokens * pin + completion_tokens * pout) / 1_000_000)


def day_start(ts: int | None = None) -> int:
    d = datetime.fromtimestamp(ts or time.time())
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


class Allowance:
    def __init__(self, db: DB, settings: Settings):
        self.db, self.settings = db, settings

    def spent_today(self, account_id: str) -> int:
        row = self.db.one("SELECT COALESCE(SUM(charged), 0) AS s FROM ledger WHERE account_id=? AND ts>=?", account_id, day_start())
        return int(row["s"])

    def status(self, account_id: str) -> dict:
        a = self.db.one("SELECT grant_uy, used, unlimited, granted FROM accounts WHERE id=?", account_id)
        cap = a["granted"] or self.settings.daily_cap_uy  # granted > 0 overrides the daily cap for this account
        return {"pool_uy": a["grant_uy"], "used_uy": a["used"], "balance_uy": max(0, a["grant_uy"] - a["used"]),
                "today_uy": self.spent_today(account_id), "daily_cap_uy": cap, "unlimited": bool(a["unlimited"])}

    def check(self, account_id: str) -> None:
        s = self.status(account_id)
        if s["unlimited"]:
            return
        if s["balance_uy"] <= 0:
            self.db.event(account_id, "refusal", "allowance used up")
            raise OutOfAllowance("The free allowance is used up. Add your own model key in Settings to keep going.")
        if s["today_uy"] >= s["daily_cap_uy"]:
            self.db.event(account_id, "refusal", "daily cap")
            raise OutOfAllowance("Today's free allowance is used up; it resets at midnight. Or add your own model key.")

    def charge(self, account_id: str, kind: str, model: str, prompt_tokens: int, completion_tokens: int,
               request_id: str = "", byok: bool = False, extra: dict | None = None) -> int:
        cost = cost_uy(self.settings, model, prompt_tokens, completion_tokens)
        charged = 0 if byok else cost
        with self.db.tx() as c:
            c.execute("INSERT INTO ledger (account_id, ts, kind, model, prompt_tokens, completion_tokens, charged, request_id, cost_uy, extra) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (account_id, now(), kind, model, prompt_tokens, completion_tokens, charged, request_id, cost,
                       json.dumps({"byok": byok, **(extra or {})})))
            if charged:
                c.execute("UPDATE accounts SET used = used + ? WHERE id=?", (charged, account_id))
        return charged

    def adjust(self, account_id: str, delta_uy: int, reason: str) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE accounts SET grant_uy = grant_uy + ? WHERE id=?", (delta_uy, account_id))
            c.execute("INSERT INTO ledger (account_id, ts, kind, charged, cost_uy, extra) VALUES (?, ?, 'adjust', 0, ?, ?)",
                      (account_id, now(), -delta_uy, reason))
        self.db.event(account_id, "allowance_adjust", {"delta_uy": delta_uy, "reason": reason})

    def history(self, account_id: str, limit: int = 100) -> list[dict]:
        rows = self.db.all("SELECT ts, kind, model, prompt_tokens, completion_tokens, charged, cost_uy FROM ledger "
                           "WHERE account_id=? ORDER BY id DESC LIMIT ?", account_id, limit)
        return [dict(r) for r in rows]
