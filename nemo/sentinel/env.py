"""Environment scrubbing for every subprocess a tool starts."""

from __future__ import annotations

import os
from fnmatch import fnmatchcase

SCRUB_PATTERNS = [
    "*KEY*", "*TOKEN*", "*SECRET*", "*PASSW*", "*CREDENTIAL*", "*AUTH*", "*COOKIE*", "*SESSION*",
    "NEMO_*", "AWS_*", "AZURE_*", "GOOGLE_*", "GITHUB_*", "SSH_AUTH_SOCK",
]
# never scrubbed: processes need these, and none of them holds a secret
KEEP = {"PATH", "PATHEXT", "HOME", "USERPROFILE", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "LANG", "TERM"}


def is_secret_name(name: str) -> bool:
    upper = name.upper()
    return upper not in KEEP and any(fnmatchcase(upper, p) for p in SCRUB_PATTERNS)


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not is_secret_name(k)}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env
