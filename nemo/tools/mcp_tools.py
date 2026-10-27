"""Tools from MCP servers in the config, over stdio. Env values may use {{vault:NAME}}; the subprocess env is scrubbed."""

from __future__ import annotations

import logging
import re
from contextlib import AsyncExitStack
from typing import Any

from nemo.schema import RiskLevel
from nemo.sentinel.env import scrubbed_env
from nemo.tools.base import BaseTool, ToolCollection, ToolContext, clip
from nemo.vault import Vault

log = logging.getLogger("nemo.mcp")

READ_ONLY = re.compile(r"^(get|list|search|read|fetch|query|find|describe|show|lookup|view|count)", re.IGNORECASE)


def mcp_name(server: str, tool: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{tool}")[:64]


class MCPTool(BaseTool):
    def __init__(self, server: str, session: Any, spec: Any):
        self.name = mcp_name(server, spec.name)
        self.description = f"[{server}] {spec.description or spec.name}"
        self.parameters = spec.inputSchema or {"type": "object", "properties": {}}
        self.risk = RiskLevel.SAFE if READ_ONLY.match(spec.name) else RiskLevel.SENSITIVE
        self.server, self.session, self.remote = server, session, spec.name

    def target(self, args: dict[str, Any]) -> str:
        return self.server

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> str:
        result = await self.session.call_tool(self.remote, args)
        parts = [getattr(item, "text", None) or f"[{getattr(item, 'type', 'content')}]" for item in result.content]
        body = "\n".join(parts) or "(no content)"
        return clip(f"Error: {body}" if getattr(result, "isError", False) else body)


class MCPManager:
    def __init__(self, tools: ToolCollection, vault: Vault):
        self.tools = tools
        self.vault = vault
        self.stack: AsyncExitStack | None = None
        self.status: dict[str, str] = {}

    async def start(self, servers: dict[str, dict]) -> None:
        await self.stop()
        if not servers:
            return
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            self.status = {name: "the 'mcp' package is not installed (pip install mcp)" for name in servers}
            return
        self.stack = AsyncExitStack()
        for server, spec in servers.items():
            try:
                env = scrubbed_env(self.vault.resolve(dict(spec.get("env") or {})))
                params = StdioServerParameters(command=spec["command"], args=list(spec.get("args", [])), env=env)
                read, write = await self.stack.enter_async_context(stdio_client(params))
                session = await self.stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = (await session.list_tools()).tools
                self.tools.add(*(MCPTool(server, session, t) for t in listed))
                self.status[server] = f"connected, {len(listed)} tools"
            except Exception as exc:  # one broken server must not stop the others or the app
                log.warning("MCP server %s failed: %s", server, exc)
                self.status[server] = f"failed: {exc}"

    async def stop(self) -> None:
        self.tools.remove_prefix("mcp__")
        self.status = {}
        if self.stack is not None:
            try:
                await self.stack.aclose()
            except Exception as exc:  # closing a dead server's pipes can raise anything
                log.debug("closing MCP servers: %s", exc)
            self.stack = None
