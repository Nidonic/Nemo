"""Profile memory: Markdown files the user can read and edit (SOUL.md, USER.md, GLOBAL.md, plus any others)."""

from __future__ import annotations

from pathlib import Path

DEFAULTS: dict[str, str] = {
    "SOUL.md": """# Soul

I am {name}, a personal agent who lives on the user's own devices.

- I do things rather than only explaining how. When a task needs a command, a file, a web page or another device, I use my tools.
- I am brief and warm. I say what I did and what I found.
- I stop and ask before anything that cannot be undone: deleting, overwriting, sending, paying, publishing.
- Passwords, one-time codes, logins and CAPTCHAs are the user's; I hand those steps over with a takeover.
""",
    "USER.md": """# About the user

(Nothing yet. {name} adds lasting facts here; atomic facts go to the memory store.)
""",
    "GLOBAL.md": """# Standing instructions

(Rules that apply to every conversation, e.g. "answer in Chinese", "use metric units".)
""",
}


class ProfileMemory:
    def __init__(self, root: Path, name: str = "Nemo"):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        for filename, text in DEFAULTS.items():
            path = root / filename
            if not path.exists():
                path.write_text(text.format(name=name), encoding="utf-8")

    def _path(self, name: str) -> Path:
        name = Path(name).name
        if not name.lower().endswith(".md"):
            name += ".md"
        return self.root / name

    def list(self) -> list[str]:
        order = list(DEFAULTS)
        names = sorted(p.name for p in self.root.glob("*.md"))
        return [n for n in order if n in names] + [n for n in names if n not in order]

    def read(self, name: str) -> str:
        path = self._path(name)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write(self, name: str, content: str) -> str:
        path = self._path(name)
        path.write_text(content, encoding="utf-8")
        return path.name

    def append(self, name: str, text: str) -> str:
        current = self.read(name).rstrip("\n")
        return self.write(name, f"{current}\n{text.strip()}\n" if current else f"{text.strip()}\n")

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if path.name in DEFAULTS or not path.exists():
            return False
        path.unlink()
        return True
