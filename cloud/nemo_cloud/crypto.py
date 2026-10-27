"""Identifier hashing (HMAC-SHA256), identifier encryption (AES-GCM), scrypt passwords, bearer tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


class Crypto:
    def __init__(self, secret: str):
        master = hashlib.sha256(secret.encode()).digest()
        self._hmac_key = hmac.new(master, b"identifier-hmac", hashlib.sha256).digest()
        self._aes = AESGCM(hmac.new(master, b"identifier-aes-gcm", hashlib.sha256).digest())
        self._code_key = hmac.new(master, b"otp", hashlib.sha256).digest()

    def id_hash(self, identifier: str) -> str:
        return hmac.new(self._hmac_key, identifier.encode(), hashlib.sha256).hexdigest()

    def code_hash(self, id_hash: str, code: str) -> str:
        return hmac.new(self._code_key, f"{id_hash}:{code}".encode(), hashlib.sha256).hexdigest()

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self._aes.encrypt(nonce, plaintext.encode(), None)).decode()

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token)
        return self._aes.decrypt(raw[:12], raw[12:], None).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return hmac.compare_digest(candidate.hex(), digest)


def new_token() -> tuple[str, str, str]:
    """(token, sha256 hash, display prefix). Only the hash is stored."""
    token = "nm_" + secrets.token_urlsafe(32)
    return token, token_hash(token), token[:10]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"
