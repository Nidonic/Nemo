"""nemo: serve (default), chat, run, doctor, setup, vault, memory, login."""

from __future__ import annotations

import asyncio
import json
import sys
import webbrowser
from typing import Optional

import typer
from rich.console import Console

from nemo import __version__
from nemo.config import PROVIDERS, config_path, load_config, nemo_home, save_config

# Windows consoles default to a legacy code page that cannot print ✓, ⚠ or Chinese text
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

cli = typer.Typer(add_completion=False, no_args_is_help=False, help="Nemo, a personal agent for every device you own.")
vault_cli = typer.Typer(help="Encrypted secrets, referenced in settings as {{vault:NAME}}.")
memory_cli = typer.Typer(help="Remembered facts.")
cli.add_typer(vault_cli, name="vault")
cli.add_typer(memory_cli, name="memory")
out = Console()


@cli.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context, version: bool = typer.Option(False, "--version", help="Show the version.")) -> None:
    if version:
        out.print(f"nemo {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        serve()


@cli.command()
def serve(host: str = typer.Option("127.0.0.1", help="Interface to listen on."),
          port: int = typer.Option(7860), no_browser: bool = typer.Option(False, "--no-browser")) -> None:
    """Start the local daemon and the web console."""
    import uvicorn

    from nemo.server import create_app

    api = create_app(host=host)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    if api.state.token:
        url += f"?token={api.state.token}"
    out.print(f"[bold]Nemo {__version__}[/] · data {nemo_home()}")
    out.print(f"Open {url}")
    if not no_browser:
        webbrowser.open(url)
    uvicorn.run(api, host=host, port=port, log_level="warning")


class TerminalUI:
    def __init__(self) -> None:
        self.thinking = False

    async def on_text_delta(self, delta: str) -> None:
        if self.thinking:
            out.print()
            self.thinking = False
        out.print(delta, end="", soft_wrap=True, highlight=False, markup=False)

    async def on_reasoning_delta(self, delta: str) -> None:
        self.thinking = True
        out.print(f"[dim]{delta}[/]", end="", soft_wrap=True, highlight=False)

    async def on_step_end(self, message) -> None:
        if message.content:
            out.print()

    async def on_tool_start(self, call, summary: str) -> None:
        out.print(f"[cyan]  ⚙ {call.name}[/] [dim]{summary}[/]", highlight=False)

    async def on_tool_end(self, call, result) -> None:
        if not result.ok:
            first = result.content.splitlines()[0] if result.content else ""
            out.print(f"[red]    ✗ {first[:200]}[/]", highlight=False)

    async def on_inbox(self, message) -> None:
        pass


async def _terminal_approver(request):
    from nemo.sentinel import ApprovalDecision

    out.print(f"\n[yellow]  ⚠ {request.reason}[/]\n    [bold]{request.tool}[/]: {request.preview or json.dumps(request.args)[:300]}")
    choices = "[y]es once" + (" / [c]onversation / [s]ession / [d]ay / [a]lways" if len(request.scopes) > 1 else "") + " / [N]o"
    answer = (await asyncio.to_thread(input, f"    Allow? {choices}: ")).strip().lower()
    scope = {"y": "once", "yes": "once", "c": "conversation", "s": "session", "d": "24h", "a": "always"}.get(answer)
    if scope and scope not in request.scopes:
        scope = "once"
    return ApprovalDecision(allow=bool(scope), scope=scope or "once")


async def _chat(once: str | None = None) -> None:
    from nemo.agent.core import StuckError
    from nemo.app import App
    from nemo.llm import LLMError

    app = App()
    if not app.config.is_ready():
        out.print("[red]No model set up yet. Run `nemo setup` first.[/]")
        raise typer.Exit(1)
    app.sentinel.approver = _terminal_approver
    await app.start()
    thread = app.threads.create(title=(once or "Terminal chat")[:60])
    agent = app.agent_for(thread)
    ui = TerminalUI()
    try:
        if once:
            await agent.run(once, ui)
            out.print()
            return
        out.print(f"[bold]{app.config.name}[/] is here ({app.config.llm.model}). /exit to leave.\n")
        while True:
            try:
                text = (await asyncio.to_thread(input, "you › ")).strip()
            except EOFError:
                break
            if text in ("/exit", "/quit"):
                break
            if not text:
                continue
            try:
                await agent.run(text, ui)
            except (LLMError, StuckError) as exc:
                out.print(f"\n[red]{exc}[/]")
            out.print("\n")
    finally:
        await app.shutdown()


@cli.command()
def chat() -> None:
    """Talk to Nemo in this terminal."""
    try:
        asyncio.run(_chat())
    except KeyboardInterrupt:
        out.print()


@cli.command()
def run(text: str) -> None:
    """Do one task and exit."""
    asyncio.run(_chat(text))


@cli.command()
def setup() -> None:
    """Choose the model provider, model and API key."""
    from nemo.vault import Vault

    config = load_config()
    names = [n for n in PROVIDERS if n not in ("relay", "mock")]
    out.print("Providers: " + ", ".join(f"{i + 1}) {n}" for i, n in enumerate(names)))
    choice = input(f"Provider [{config.llm.provider}]: ").strip()
    if choice:
        provider = names[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(names) else choice
        if provider not in PROVIDERS:
            out.print(f"[red]Unknown provider {provider!r}.[/]")
            raise typer.Exit(1)
        preset = PROVIDERS[provider]
        config.llm.provider, config.llm.kind = provider, preset["kind"]  # type: ignore[assignment]
        config.llm.base_url, config.llm.model = preset["base_url"], preset["model"]
    config.llm.base_url = input(f"Base URL [{config.llm.base_url}]: ").strip() or config.llm.base_url
    config.llm.model = input(f"Model [{config.llm.model}]: ").strip() or config.llm.model
    import getpass

    key = getpass.getpass("API key (stored encrypted; Enter keeps the current one): ").strip()
    if key:
        Vault(nemo_home()).set("LLM_API_KEY", key)
        config.llm.api_key = "{{vault:LLM_API_KEY}}"
    config.name = input(f"Agent name [{config.name}]: ").strip() or config.name
    save_config(config)
    out.print(f"Saved to {config_path()}")


@cli.command()
def doctor() -> None:
    """Check the setup: model, vault, sandbox, hands, browser, coding agents."""
    import shutil

    from nemo.coding import CodingManager
    from nemo.sentinel import sandbox
    from nemo.vault import Vault, VaultError

    config = load_config()
    rows: list[tuple[str, bool, str]] = []
    rows.append(("model", config.is_ready(), f"{config.llm.provider} · {config.llm.model}"))
    try:
        names = Vault(nemo_home()).list()
        rows.append(("vault", True, f"{len(names)} secret(s)"))
    except VaultError as exc:
        rows.append(("vault", False, str(exc)))
    rows.append(("sandbox (bubblewrap)", sandbox.available(), "Linux only" if not sys.platform.startswith("linux") else ""))
    from importlib.util import find_spec

    for mod, label, extra in (("mss", "computer hands: capture", "hands"), ("pyautogui", "computer hands: input", "hands"),
                              ("playwright", "browser", "browser"), ("mcp", "MCP", "mcp")):
        found = find_spec(mod) is not None
        rows.append((label, found, "" if found else f'pip install "nemo-agent[{extra}]"'))
    rows.append(("phone hands (adb)", bool(shutil.which("adb")), ""))
    for a in CodingManager().agents():
        rows.append((f"coding: {a['label']}", a["installed"], a["sessions_dir"] if a["has_sessions"] else ""))
    rows.append(("relay", bool(config.hub.url and config.hub.token), config.hub.url or "not signed in"))
    for name, ok, note in rows:
        out.print(f"  {'[green]✓[/]' if ok else '[yellow]·[/]'} {name:<26} [dim]{note}[/]", highlight=False)


@cli.command()
def login(url: str = typer.Argument(..., help="Relay URL, e.g. https://relay.example.com"),
          identifier: str = typer.Argument(..., help="E-mail, or a +86 phone number")) -> None:
    """Sign this device in to a relay with a one-time code."""
    import uuid

    import httpx

    from nemo.vault import Vault

    config = load_config()
    url = url.rstrip("/")
    r = httpx.post(f"{url}/v1/auth/code", json={"identifier": identifier}, timeout=30)
    if r.status_code >= 400:
        out.print(f"[red]{r.text}[/]")
        raise typer.Exit(1)
    code = input("Code: ").strip()
    config.hub.device_id = config.hub.device_id or uuid.uuid4().hex
    r = httpx.post(f"{url}/v1/auth/verify", timeout=30, json={
        "identifier": identifier, "code": code, "device": {"id": config.hub.device_id, "name": config.hub.device_name, "kind": "computer"}})
    if r.status_code >= 400:
        out.print(f"[red]{r.text}[/]")
        raise typer.Exit(1)
    Vault(nemo_home()).set("HUB_TOKEN", r.json()["token"])
    config.hub.url, config.hub.token = url, "{{vault:HUB_TOKEN}}"
    save_config(config)
    out.print("Signed in. Run `nemo` to come online.")


@vault_cli.command("set")
def vault_set(name: str, value: Optional[str] = typer.Argument(None)) -> None:
    """Store a secret (prompted when no value is given)."""
    import getpass

    from nemo.vault import Vault

    Vault(nemo_home()).set(name, value if value is not None else getpass.getpass(f"{name}: "))
    out.print(f"Stored. Use it in settings as {{{{vault:{name}}}}}.")


@vault_cli.command("get")
def vault_get(name: str) -> None:
    from nemo.vault import Vault

    value = Vault(nemo_home()).get(name)
    if value is None:
        raise typer.Exit(1)
    print(value)


@vault_cli.command("list")
def vault_list() -> None:
    from nemo.vault import Vault

    for name in Vault(nemo_home()).list():
        print(name)


@vault_cli.command("delete")
def vault_delete(name: str) -> None:
    from nemo.vault import Vault

    out.print("Deleted." if Vault(nemo_home()).delete(name) else "No such secret.")


@memory_cli.command("list")
def memory_list(query: str = typer.Argument("")) -> None:
    from nemo.memory import MemoryStore

    store = MemoryStore(nemo_home() / "memory.db")
    for m in (store.search(query, 50) if query else store.all()):
        out.print(f"[dim]{m.id}[/] ({m.category}) {m.content}", highlight=False)


@memory_cli.command("dream")
def memory_dream() -> None:
    """Run a consolidation pass now."""
    from nemo.app import App

    async def go() -> list[dict]:
        app = App()
        try:
            return await app.dream_now()
        finally:
            app.memory.close()

    for change in asyncio.run(go()):
        out.print(f"{change['action']}: {change.get('content') or change['reason']} [dim](undo: {change['change_id']})[/]")


@memory_cli.command("undo")
def memory_undo(change_id: str) -> None:
    from nemo.memory import MemoryStore

    out.print("Restored." if MemoryStore(nemo_home() / "memory.db").undo(change_id) else "Nothing to undo.")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
