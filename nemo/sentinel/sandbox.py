"""Bubblewrap (bwrap) namespace sandbox on Linux: read-only root, private /tmp, fresh /proc and /dev, no network."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def bwrap_argv(argv: list[str], workspace: Path, *, network: bool = False, extra_rw: list[Path] | None = None) -> list[str]:
    ws = str(workspace.resolve())
    args = [
        shutil.which("bwrap") or "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", ws, ws,
    ]
    for path in extra_rw or []:
        p = str(path.resolve())
        args += ["--bind", p, p]
    args += ["--unshare-pid", "--unshare-ipc", "--unshare-uts"]
    if not network:
        args.append("--unshare-net")
    args += ["--die-with-parent", "--new-session", "--chdir", ws, "--"]
    return args + argv


def wrap(argv: list[str], workspace: Path, *, enabled: bool = True, network: bool = False,
         extra_rw: list[Path] | None = None) -> tuple[list[str], bool]:
    """argv, possibly inside bwrap, and whether it is sandboxed."""
    if enabled and available():
        return bwrap_argv(argv, workspace, network=network, extra_rw=extra_rw), True
    return argv, False
