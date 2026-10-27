"""The system prompt, rebuilt before every model call so memory, language, devices and time are current."""

from __future__ import annotations

import functools
import platform
import sys
from datetime import datetime
from typing import TYPE_CHECKING

from nemo.agent.threads import Thread
from nemo.schema import Role

if TYPE_CHECKING:
    from nemo.app import App


@functools.cache
def os_name() -> str:
    # slow the first time on Windows (a WMI query); App.start warms it off the event loop
    return f"{platform.system()} {platform.release()}"


LADDER = """# The ladder of action
Climb only as high as you must:
1. API, CLI or MCP server (shell, python_execute, mcp__… tools): fastest and most reliable.
2. Direct HTTP (web_fetch), signed in automatically for hosts the user has configured.
3. The browser tool, page by page.
4. Screen hands (computer_act / phone_act) on a 0..999 grid: last resort, for apps with no other way in.
On a screen: look (…_screen), act once, look again. Hand logins, passwords, payment codes and CAPTCHAs to the
user with a takeover action; finish when the screen task is done."""

RULES = """# How you work
- Act with your tools instead of explaining how. Look things up rather than guessing.
- The Sentinel checks every call. When it asks the user and they decline, accept it and offer another way;
  never retry a refused call.
- Never put {{vault:…}} placeholders or secrets in tool arguments; configured credentials are applied for you.
- When you learn a lasting fact about the user, use remember. For long multi-step work, create a goal;
  for anything recurring, create a routine.
- `@Device …` at the start of a message means the user wants that device to do it (device_call with task).
- Reply in Markdown. Be brief: what you did, what you found, what needs the user."""


def language_rule(app: "App", thread: Thread) -> str:
    lang = app.config.language
    if lang and lang != "auto":
        return f"- Always reply in {lang}."
    last = next((m.content for m in reversed(thread.messages) if m.role is Role.USER and m.content), "")
    if any("㐀" <= ch <= "鿿" for ch in last):
        return "- The user is writing in Chinese: reply in Chinese (简体中文) unless asked otherwise."
    return "- Reply in the language of the user's latest message."


def build_system_prompt(app: "App", thread: Thread) -> str:
    cfg = app.config
    now = datetime.now().astimezone()
    profile = "\n\n".join(f'<file name="{n}">\n{app.profile.read(n).strip()}\n</file>' for n in app.profile.list())

    last_user = next((m.content for m in reversed(thread.messages) if m.role is Role.USER and m.content), "")
    recalled = app.memory.search(last_user, limit=6) if last_user else []
    memories = "\n".join(f"- ({m.category}) {m.content}" for m in recalled) or "(nothing relevant)"

    devices = "Not signed in to a relay."
    if app.hub is not None:
        online = [d for d in app.hub.devices if d.get("online") and d.get("id") != cfg.hub.device_id]
        devices = ", ".join(f"{d.get('name')} ({d.get('kind')})" for d in online) or "no other device online"

    goals = "\n".join(f"- {g.id}: {g.title} ({round(g.progress * 100)}%)" for g in app.goals.list("active")[:8]) or "(none)"

    context = ""
    if thread.routine_id:
        context = "\nThis is a scheduled routine running in the background; the user is not watching. Do the task, then end with a short report.\n"
    elif thread.goal_id:
        context = f"\nThis is a scheduled check-in on goal {thread.goal_id}. Make progress on the next step, then record it with goal_update.\n"
    elif thread.origin.startswith("hub:"):
        context = "\nThis task was sent by another of the user's devices; approvals go to the user there.\n"

    shell = "PowerShell" if sys.platform == "win32" else "bash"
    return f"""You are {cfg.name}, a personal agent that lives on the user's own devices and does things for them.

{profile}

# What you remember that may matter now
{memories}

{RULES}
{language_rule(app, thread)}

{LADDER}
{context}
# Now
- Time: {now:%A %Y-%m-%d %H:%M %Z}
- This device: {os_name()}; the shell tool runs {shell}; workspace {cfg.workspace_path()}
- Other devices: {devices}
- Active goals:
{goals}
"""
