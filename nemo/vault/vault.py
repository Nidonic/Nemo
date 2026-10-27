"""Credential vault: secrets encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256) in <data_dir>/vault.enc.

Trusted configuration (the model API key, MCP server env, per-host HTTP headers) refers to a secret as
{{vault:NAME}}. The Sentinel resolves those placeholders just before a call runs. A placeholder written by
the model itself is refused, and secret values are redacted from anything returned to the model.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

PLACEHOLDER = re.compile(r"\{\{\s*vault:([A-Za-z0-9_.-]+)\s*\}\}")
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class VaultError(Exception):
    pass


def has_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(PLACEHOLDER.search(value))
    if isinstance(value, dict):
        return any(has_placeholder(v) or has_placeholder(k) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(has_placeholder(v) for v in value)
    return False


class Vault:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "vault.enc"
        self.key_path = data_dir / "vault.key"
        self._fernet = Fernet(self._key())

    def _key(self) -> bytes:
        env = os.environ.get("NEMO_VAULT_KEY")
        if env:
            return env.encode()
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except InvalidToken as exc:
            raise VaultError("The vault cannot be decrypted with this key.") from exc

    def _save(self, data: dict[str, str]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
        tmp.replace(self.path)

    def set(self, name: str, value: str) -> None:
        if not _NAME.match(name):
            raise VaultError("Secret names use letters, digits, '_', '.' and '-' (max 64).")
        data = self._load()
        data[name] = value
        self._save(data)

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def list(self) -> list[str]:
        return sorted(self._load())

    def delete(self, name: str) -> bool:
        data = self._load()
        if data.pop(name, None) is None:
            return False
        self._save(data)
        return True

    def resolve(self, value: Any) -> Any:
        """Replace every {{vault:NAME}} in strings, dicts and lists. Unknown names raise VaultError."""
        if isinstance(value, str):
            if not PLACEHOLDER.search(value):
                return value
            data = self._load()

            def sub(match: re.Match) -> str:
                name = match.group(1)
                if name not in data:
                    raise VaultError(f"No secret named {name} in the vault.")
                return data[name]

            return PLACEHOLDER.sub(sub, value)
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value

    def redact(self, text: str) -> str:
        """Hide any secret value that shows up in text bound for the model."""
        try:
            secrets = self._load()
        except VaultError:
            return text
        for name, secret in secrets.items():
            if secret and len(secret) >= 4 and secret in text:
                text = text.replace(secret, f"[vault:{name}]")
        return text
