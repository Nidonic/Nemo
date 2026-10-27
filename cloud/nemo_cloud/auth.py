"""Sign-in: one-time codes to a +86 phone or an e-mail address, optional scrypt passwords, bearer API keys."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from nemo_cloud.crypto import Crypto, hash_password, new_code, new_token, token_hash, verify_password
from nemo_cloud.db import DB, now
from nemo_cloud.settings import Settings

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
CN_PHONE = re.compile(r"^(?:\+?86)?(1[3-9]\d{9})$")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Identity:
    channel: str  # phone | email
    value: str  # normalised: +8613812341234 or lower-case e-mail

    @property
    def hint(self) -> str:
        if self.channel == "phone":
            digits = self.value[-11:]
            return f"{digits[:3]}****{digits[-4:]}"
        name, _, domain = self.value.partition("@")
        return f"{name[0]}***@{domain}"


def normalise(raw: str) -> Identity:
    raw = (raw or "").strip()
    phone = CN_PHONE.match(re.sub(r"[\s-]", "", raw))
    if phone:
        return Identity("phone", "+86" + phone.group(1))
    if EMAIL.match(raw):
        return Identity("email", raw.lower())
    raise AuthError("Use an e-mail address or a mainland China (+86) mobile number.")


@dataclass
class Account:
    id: str
    channel: str
    hint: str
    grant_uy: int
    used: int
    unlimited: bool
    has_password: bool
    invite_code: str
    created_at: int

    @property
    def balance_uy(self) -> int:
        return max(0, self.grant_uy - self.used)


def to_account(row) -> Account:
    return Account(id=row["id"], channel=row["channel"], hint=row["hint"], grant_uy=row["grant_uy"], used=row["used"],
                   unlimited=bool(row["unlimited"]), has_password=bool(row["password_hash"]),
                   invite_code=row["invite_code"], created_at=row["created_at"])


class Auth:
    def __init__(self, db: DB, crypto: Crypto, settings: Settings):
        self.db, self.crypto, self.settings = db, crypto, settings

    # codes ---------------------------------------------------------------------------

    def issue_code(self, identity: Identity, ip: str = "") -> str:
        """Create a code, enforcing one per identifier per interval and a per-IP hourly cap."""
        h = self.crypto.id_hash(identity.value)
        t = now()
        last = self.db.one("SELECT created_at FROM codes WHERE id_hash=? ORDER BY created_at DESC LIMIT 1", h)
        if last and t - last["created_at"] < self.settings.code_interval:
            raise AuthError(f"Please wait {self.settings.code_interval - (t - last['created_at'])}s before asking for another code.", 429)
        if ip:
            n = self.db.one("SELECT COUNT(*) AS n FROM codes WHERE ip=? AND created_at>?", ip, t - 3600)["n"]
            if n >= self.settings.code_per_ip_hour:
                raise AuthError("Too many codes from this network. Try again later.", 429)
        code = new_code()
        self.db.run("INSERT INTO codes (id_hash, code_hash, ip, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                    h, self.crypto.code_hash(h, code), ip, t, t + self.settings.code_ttl)
        return code

    def check_code(self, identity: Identity, code: str) -> None:
        h = self.crypto.id_hash(identity.value)
        row = self.db.one("SELECT * FROM codes WHERE id_hash=? AND used=0 ORDER BY created_at DESC LIMIT 1", h)
        if row is None or row["expires_at"] < now():
            raise AuthError("That code has expired. Ask for a new one.", 401)
        if row["attempts"] >= self.settings.code_attempts:
            raise AuthError("Too many wrong codes. Ask for a new one.", 429)
        if not secrets.compare_digest(row["code_hash"], self.crypto.code_hash(h, (code or "").strip())):
            self.db.run("UPDATE codes SET attempts = attempts + 1 WHERE id=?", row["id"])
            raise AuthError("Wrong code.", 401)
        self.db.run("UPDATE codes SET used=1 WHERE id=?", row["id"])

    # accounts --------------------------------------------------------------------------

    def find(self, identity: Identity):
        return self.db.one("SELECT * FROM accounts WHERE id_hash=?", self.crypto.id_hash(identity.value))

    def get(self, account_id: str) -> Account | None:
        row = self.db.one("SELECT * FROM accounts WHERE id=?", account_id)
        return to_account(row) if row else None

    def find_or_create(self, identity: Identity, invite: str = ""):
        row = self.find(identity)
        if row:
            return row, False
        account_id = "acc_" + secrets.token_hex(8)
        t = now()
        inviter = self.db.one("SELECT id FROM accounts WHERE invite_code=?", invite.strip().upper()) if invite else None
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO accounts (id, id_hash, channel, hint, created_at, identifier_enc, invite_code, invited_by, grant_uy) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, self.crypto.id_hash(identity.value), identity.channel, identity.hint, t,
                 self.crypto.encrypt(identity.value), secrets.token_hex(4).upper(), inviter["id"] if inviter else "",
                 self.settings.signup_grant_uy))
            c.execute("INSERT INTO ledger (account_id, ts, kind, charged, cost_uy, extra) VALUES (?, ?, 'grant', 0, ?, 'signup')",
                      (account_id, t, -self.settings.signup_grant_uy))
            if inviter:
                c.execute("UPDATE accounts SET invites = invites + 1, grant_uy = grant_uy + ? WHERE id=?",
                          (self.settings.invite_bonus_uy, inviter["id"]))
                c.execute("INSERT INTO ledger (account_id, ts, kind, charged, cost_uy, extra) VALUES (?, ?, 'grant', 0, ?, ?)",
                          (inviter["id"], t, -self.settings.invite_bonus_uy, f"invite {account_id}"))
        self.db.event(account_id, "signup", {"channel": identity.channel})
        return self.db.one("SELECT * FROM accounts WHERE id=?", account_id), True

    def check_password(self, identity: Identity, password: str):
        row = self.find(identity)
        if row is None or not row["password_hash"]:
            raise AuthError("No password is set for this account; sign in with a code.", 401)
        t = now()
        if row["locked_until"] and row["locked_until"] > t:
            raise AuthError(f"Too many wrong passwords. Try again in {(row['locked_until'] - t) // 60 + 1} min, or use a code.", 429)
        if not verify_password(password, row["password_hash"]):
            failed = row["failed_logins"] + 1
            locked = t + self.settings.lockout_seconds if failed >= self.settings.max_failed_logins else None
            self.db.run("UPDATE accounts SET failed_logins=?, locked_until=? WHERE id=?",
                        0 if locked else failed, locked, row["id"])
            self.db.event(row["id"], "password_failed", {"locked": bool(locked)})
            raise AuthError("Wrong password.", 401)
        self.db.run("UPDATE accounts SET failed_logins=0, locked_until=NULL WHERE id=?", row["id"])
        return row

    def set_password(self, account_id: str, password: str) -> None:
        if len(password) < 8:
            raise AuthError("Use at least 8 characters.")
        self.db.run("UPDATE accounts SET password_hash=?, password_set_at=?, failed_logins=0, locked_until=NULL WHERE id=?",
                    hash_password(password), now(), account_id)
        self.db.event(account_id, "password_set")

    # keys ------------------------------------------------------------------------------------

    def issue_key(self, account_id: str, device: dict, via: str) -> str:
        token, h, prefix = new_token()
        kind = device.get("kind") if device.get("kind") in ("phone", "computer", "web") else "computer"
        self.db.run("INSERT INTO api_keys (key_hash, prefix, account_id, device, hub_device_id, hub_device_kind, created_at, via) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    h, prefix, account_id, str(device.get("name", ""))[:80], str(device.get("id", ""))[:64], kind, now(), via)
        self.db.event(account_id, "signin", {"via": via, "device": device.get("name", ""), "prefix": prefix})
        return token

    def authenticate(self, token: str):
        """The api_keys row for a bearer token, or None."""
        if not token or not token.startswith("nm_"):
            return None
        row = self.db.one("SELECT k.*, a.disabled FROM api_keys k JOIN accounts a ON a.id = k.account_id "
                          "WHERE k.key_hash=? AND k.revoked_at IS NULL", token_hash(token))
        if row is None or row["disabled"]:
            return None
        self.db.run("UPDATE api_keys SET last_used_at=? WHERE key_hash=?", now(), row["key_hash"])
        return row

    def revoke(self, key_hash: str, account_id: str) -> bool:
        cur = self.db.run("UPDATE api_keys SET revoked_at=? WHERE key_hash=? AND account_id=? AND revoked_at IS NULL",
                          now(), key_hash, account_id)
        if cur.rowcount:
            self.db.event(account_id, "signout")
        return bool(cur.rowcount)

    def keys(self, account_id: str) -> list[dict]:
        rows = self.db.all("SELECT prefix, device, hub_device_id, hub_device_kind, created_at, last_used_at, via "
                           "FROM api_keys WHERE account_id=? AND revoked_at IS NULL ORDER BY created_at DESC", account_id)
        return [dict(r) for r in rows]
