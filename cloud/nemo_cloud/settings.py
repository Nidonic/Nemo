"""Relay settings, from environment variables (NEMO_CLOUD_*)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"NEMO_CLOUD_{name}", default)


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: _env("DB", "nemo_cloud.db"))
    # master secret: keys the identifier HMAC, the AES-GCM identifier encryption and OTP hashing
    secret: str = field(default_factory=lambda: _env("SECRET", ""))
    admin_token: str = field(default_factory=lambda: _env("ADMIN_TOKEN", ""))
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL", "http://127.0.0.1:8787"))

    # allowance, in micro-yuan (1 CNY = 1,000,000 uy)
    signup_grant_uy: int = field(default_factory=lambda: int(_env("SIGNUP_GRANT_UY", "5000000")))
    daily_cap_uy: int = field(default_factory=lambda: int(_env("DAILY_CAP_UY", "1000000")))
    invite_bonus_uy: int = field(default_factory=lambda: int(_env("INVITE_BONUS_UY", "2000000")))

    # upstream model used for the free allowance (OpenAI-compatible)
    upstream_base_url: str = field(default_factory=lambda: _env("UPSTREAM_BASE_URL", ""))
    upstream_api_key: str = field(default_factory=lambda: _env("UPSTREAM_API_KEY", ""))
    # alias → upstream model id, e.g. {"default": "deepseek-chat"}
    model_aliases: dict[str, str] = field(default_factory=lambda: json.loads(_env("MODEL_ALIASES", '{"default": "deepseek-chat"}')))
    # model → [uy per 1M prompt tokens, uy per 1M completion tokens]
    prices: dict[str, list[int]] = field(default_factory=lambda: json.loads(_env(
        "PRICES", '{"deepseek-chat": [2000000, 8000000], "*": [4000000, 16000000]}')))

    # one-time codes
    code_ttl: int = 600
    code_attempts: int = 5
    code_interval: int = 60  # seconds between codes for one identifier
    code_per_ip_hour: int = 10
    # "console" prints codes to the log (development); "smtp" e-mails them; SMS goes to a webhook
    email_sender: str = field(default_factory=lambda: _env("EMAIL_SENDER", "console"))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "465")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: _env("SMTP_FROM", ""))
    sms_webhook: str = field(default_factory=lambda: _env("SMS_WEBHOOK", ""))

    # passwords
    max_failed_logins: int = 5
    lockout_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.secret:
            # development only: a fresh secret each start makes every stored identifier unreadable after restart
            self.secret = os.urandom(32).hex()
            self.ephemeral_secret = True
        else:
            self.ephemeral_secret = False
