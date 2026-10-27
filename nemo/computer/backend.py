"""This computer's screen and input, through mss (capture) and pyautogui (mouse and keyboard)."""

from __future__ import annotations

import io
import time

from nemo.computer.grid import Shot, compress, to_screen

KEYMAP = {"Back": "browserback", "Home": "home", "Enter": "enter", "Tab": "tab", "Escape": "esc",
          "Backspace": "backspace", "Delete": "delete", "Up": "up", "Down": "down", "Left": "left", "Right": "right",
          "PageUp": "pageup", "PageDown": "pagedown", "Space": "space"}


class HandsUnavailable(Exception):
    pass


class ComputerBackend:
    def __init__(self) -> None:
        try:
            import mss  # noqa: F401
            import pyautogui
        except ImportError as exc:
            raise HandsUnavailable("Computer hands need: pip install mss pyautogui pillow") from exc
        except Exception as exc:  # pyautogui raises on headless Linux without a display
            raise HandsUnavailable(f"No screen to work with: {exc}") from exc
        pyautogui.FAILSAFE = True  # slamming the mouse into a corner stops everything
        pyautogui.PAUSE = 0.05
        self.gui = pyautogui

    def size(self) -> tuple[int, int]:
        w, h = self.gui.size()
        return int(w), int(h)

    def screenshot(self) -> Shot:
        import mss
        import mss.tools

        with mss.mss() as sct:
            mon = sct.monitors[1]
            raw = sct.grab(mon)
            png = mss.tools.to_png(raw.rgb, raw.size)
        w, h = self.size()  # logical size, the one mouse coordinates use
        return compress(png, w, h)

    def _xy(self, x: int, y: int) -> tuple[int, int]:
        return to_screen(x, y, *self.size())

    def tap(self, x: int, y: int) -> None:
        self.gui.click(*self._xy(x, y))

    def double_tap(self, x: int, y: int) -> None:
        self.gui.doubleClick(*self._xy(x, y))

    def long_press(self, x: int, y: int, ms: int = 800) -> None:
        sx, sy = self._xy(x, y)
        self.gui.mouseDown(sx, sy)
        time.sleep(ms / 1000)
        self.gui.mouseUp(sx, sy)

    def swipe(self, x: int, y: int, x2: int, y2: int, ms: int = 400) -> None:
        sx, sy = self._xy(x, y)
        ex, ey = self._xy(x2, y2)
        self.gui.moveTo(sx, sy)
        self.gui.dragTo(ex, ey, duration=ms / 1000, button="left")

    def scroll(self, x: int, y: int, amount: int) -> None:
        self.gui.scroll(amount, *self._xy(x, y))

    def type(self, text: str, clear_first: bool = False) -> None:
        if clear_first:
            self.gui.hotkey("ctrl", "a")
            self.gui.press("backspace")
        if text.isascii():
            self.gui.write(text, interval=0.01)
            return
        try:  # non-ASCII text goes through the clipboard
            import pyperclip

            pyperclip.copy(text)
            self.gui.hotkey("ctrl", "v")
        except ImportError as exc:
            raise HandsUnavailable("Typing non-ASCII text needs: pip install pyperclip") from exc

    def key(self, name: str) -> None:
        if "+" in name:
            self.gui.hotkey(*[KEYMAP.get(k, k.lower()) for k in name.split("+")])
        else:
            self.gui.press(KEYMAP.get(name, name.lower()))


def png_size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    return Image.open(io.BytesIO(png)).size
