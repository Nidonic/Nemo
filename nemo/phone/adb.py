"""An Android phone's screen and input over adb (USB or wireless debugging)."""

from __future__ import annotations

import re
import shutil
import subprocess

from nemo.computer.backend import HandsUnavailable, png_size
from nemo.computer.grid import Shot, compress, to_screen

KEYCODES = {"Back": 4, "Home": 3, "Enter": 66, "Tab": 61, "Escape": 111, "Backspace": 67, "Delete": 112,
            "Up": 19, "Down": 20, "Left": 21, "Right": 22, "Recents": 187, "Space": 62, "PageUp": 92, "PageDown": 93}


class PhoneBackend:
    def __init__(self, serial: str = ""):
        self.adb = shutil.which("adb")
        if not self.adb:
            raise HandsUnavailable("Phone hands need adb (Android platform-tools) and USB or wireless debugging.")
        self.serial = serial
        devices = [l.split()[0] for l in self._run("devices").splitlines()[1:] if l.strip().endswith("device")]
        if not devices:
            raise HandsUnavailable("No Android device is connected over adb.")
        if not self.serial:
            self.serial = devices[0]
        self._size: tuple[int, int] | None = None

    def _cmd(self, *args: str) -> list[str]:
        base = [self.adb]
        if getattr(self, "serial", ""):
            base += ["-s", self.serial]
        return base + list(args)

    def _run(self, *args: str, binary: bool = False, timeout: int = 20):
        out = subprocess.run(self._cmd(*args), capture_output=True, timeout=timeout)
        if out.returncode != 0:
            raise HandsUnavailable(out.stderr.decode("utf-8", "replace").strip() or "adb failed")
        return out.stdout if binary else out.stdout.decode("utf-8", "replace")

    def size(self) -> tuple[int, int]:
        if self._size is None:
            m = re.search(r"(\d+)x(\d+)", self._run("shell", "wm", "size").splitlines()[-1])
            self._size = (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)
        return self._size

    def screenshot(self) -> Shot:
        png = self._run("exec-out", "screencap", "-p", binary=True)
        w, h = png_size(png)
        self._size = (w, h)
        return compress(png, w, h)

    def _xy(self, x: int, y: int) -> tuple[int, int]:
        return to_screen(x, y, *self.size())

    def tap(self, x: int, y: int) -> None:
        sx, sy = self._xy(x, y)
        self._run("shell", "input", "tap", str(sx), str(sy))

    def double_tap(self, x: int, y: int) -> None:
        self.tap(x, y)
        self.tap(x, y)

    def long_press(self, x: int, y: int, ms: int = 800) -> None:
        sx, sy = self._xy(x, y)
        self._run("shell", "input", "swipe", str(sx), str(sy), str(sx), str(sy), str(ms))

    def swipe(self, x: int, y: int, x2: int, y2: int, ms: int = 400) -> None:
        sx, sy = self._xy(x, y)
        ex, ey = self._xy(x2, y2)
        self._run("shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), str(ms))

    def type(self, text: str, clear_first: bool = False) -> None:
        if clear_first:
            self._run("shell", "input", "keycombination", "113", "29")  # Ctrl+A
            self._run("shell", "input", "keyevent", "67")
        if not text.isascii():
            # adb's input text is ASCII only; the ADB Keyboard IME accepts a broadcast with any text
            self._run("shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text)
            return
        escaped = re.sub(r"([\\\"'`$&|;<>()*?#~ ])", lambda m: "%s" if m.group(1) == " " else "\\" + m.group(1), text)
        self._run("shell", "input", "text", escaped)

    def key(self, name: str) -> None:
        code = KEYCODES.get(name)
        self._run("shell", "input", "keyevent", str(code if code is not None else name))
