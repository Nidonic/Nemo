"""Nemo's look: a name, an emoji and colour, and five mood stills (WebP) the UI swaps between."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

MOODS = ("neutral", "happy", "thinking", "sleepy", "surprised")


class AvatarStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.meta_path = root / "profile.json"

    def meta(self) -> dict:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {"emoji": "🐚", "color": "#0f8f86", "style": "nautilus", "rev": 0}

    def set_meta(self, **changes) -> dict:
        meta = self.meta()
        meta.update({k: v for k, v in changes.items() if k in ("emoji", "color", "style", "description") and v is not None})
        meta["rev"] = int(meta.get("rev", 0)) + 1
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return meta

    def faces(self) -> dict[str, str]:
        """mood → data URL, for the moods that have a still."""
        out = {}
        for mood in MOODS:
            path = self.root / f"{mood}.webp"
            if path.exists():
                out[mood] = "data:image/webp;base64," + base64.b64encode(path.read_bytes()).decode()
        return out

    def set_face(self, mood: str, image: bytes, size: int = 384) -> None:
        """Store any image as a square WebP still."""
        if mood not in MOODS:
            raise ValueError(f"mood must be one of {MOODS}")
        from PIL import Image

        img = Image.open(io.BytesIO(image)).convert("RGBA")
        side = min(img.size)
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((size, size))
        out = io.BytesIO()
        img.save(out, "WEBP", quality=85)
        (self.root / f"{mood}.webp").write_bytes(out.getvalue())
        self.set_meta()

    def clear_face(self, mood: str) -> bool:
        path = self.root / f"{mood}.webp"
        if not path.exists():
            return False
        path.unlink()
        self.set_meta()
        return True
