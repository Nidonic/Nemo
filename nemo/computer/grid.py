"""The normalised 0..999 coordinate grid and the action dialect shared by phone and computer hands.

X_screen = floor(x / 999 × W),  Y_screen = floor(y / 999 × H)
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Any

GRID = 999
ACTIONS = ("tap", "double_tap", "long_press", "swipe", "type", "key", "takeover", "finish")
KEYS = ("Back", "Home", "Enter", "Tab", "Escape", "Backspace", "Delete", "Up", "Down", "Left", "Right",
        "PageUp", "PageDown", "Recents", "Space")

SENSITIVE_WORDS = ["确认支付", "立即支付", "转账", "提交订单", "发送", "删除", "购买", "付款",
                   "pay", "transfer", "checkout", "delete", "send", "purchase", "place order", "buy now", "confirm payment"]


def to_screen(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    x = max(0, min(GRID, int(x)))
    y = max(0, min(GRID, int(y)))
    sx = math.floor(x / GRID * width)
    sy = math.floor(y / GRID * height)
    return min(sx, width - 1), min(sy, height - 1)


def to_grid(sx: int, sy: int, width: int, height: int) -> tuple[int, int]:
    return round(sx / max(1, width - 1) * GRID), round(sy / max(1, height - 1) * GRID)


def sensitive_hit(*texts: Any) -> str | None:
    for text in texts:
        if not text:
            continue
        low = str(text).lower()
        for word in SENSITIVE_WORDS:
            if word in low:
                return word
    return None


@dataclass
class Shot:
    image: bytes
    mime: str
    width: int  # physical screen size
    height: int


def compress(png: bytes, width: int, height: int, max_side: int = 1280, quality: int = 70) -> Shot:
    """Shrink a screenshot for the model; coordinates stay in the grid, so scaling does not matter."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    scale = min(1.0, max_side / max(img.size))
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return Shot(out.getvalue(), "image/jpeg", width, height)


def validate(args: dict[str, Any]) -> str | None:
    action = args.get("action")
    if action not in ACTIONS:
        return f"action must be one of {', '.join(ACTIONS)}"
    need = {"tap": ("x", "y"), "double_tap": ("x", "y"), "long_press": ("x", "y"),
            "swipe": ("x", "y", "x2", "y2"), "type": ("text",), "key": ("key",)}.get(action, ())
    missing = [k for k in need if args.get(k) is None]
    if missing:
        return f"{action} needs {', '.join(missing)}"
    for k in ("x", "y", "x2", "y2"):
        if args.get(k) is not None and not (0 <= int(args[k]) <= GRID):
            return f"{k} must be between 0 and {GRID}"
    return None
