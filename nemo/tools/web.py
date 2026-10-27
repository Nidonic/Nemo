"""web_search and web_fetch (rung 2: direct HTTP, with per-host auth headers from the vault)."""

from __future__ import annotations

import asyncio
from typing import Any

import html2text
import httpx

from nemo.schema import RiskLevel
from nemo.tools.base import BaseTool, CallAssessment, ToolContext, clip, obj, url_host

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 Nemo/0.2"


def html_to_text(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.ignore_images = True
    converter.body_width = 0
    return converter.handle(html)


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web. Returns titles, links and snippets."
    parameters = obj({"query": {"type": "string"}, "count": {"type": "integer", "description": "1-20, default 8"}}, ["query"])
    risk = RiskLevel.MODERATE

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        # the query itself leaves the machine
        return CallAssessment(risk=self.risk, target="", summary=str(args.get("query", "")), egress="duckduckgo.com",
                              network=True)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> str:
        query = str(args.get("query", "")).strip()
        count = max(1, min(int(args.get("count", 8)), 20))

        def run() -> list[dict]:
            from ddgs import DDGS

            return list(DDGS().text(query, max_results=count))

        try:
            results = await asyncio.to_thread(run)
        except Exception as exc:  # the search backend raises its own exception types
            return f"Error: search failed: {exc}"
        if not results:
            return "No results."
        return "\n\n".join(f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n   {r.get('body', '')}"
                           for i, r in enumerate(results, 1))


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = ("Fetch a URL and return it as readable text (HTML becomes Markdown). GET by default; method and "
                   "body send data. Hosts configured with credentials are signed in automatically.")
    parameters = obj({
        "url": {"type": "string"},
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "body": {"type": "string", "description": "Request body (JSON or text) for POST/PUT/PATCH."},
        "max_chars": {"type": "integer"},
    }, ["url"])
    risk = RiskLevel.MODERATE

    def target(self, args: dict[str, Any]) -> str:
        return url_host(str(args.get("url", "")))

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        host = self.target(args)
        method = str(args.get("method") or "GET").upper()
        a = CallAssessment(risk=self.risk, target=host, summary=f"{method} {args.get('url', '')}", egress=host or "*",
                           network=True)
        if method != "GET":
            a.escalate(RiskLevel.SENSITIVE, f"Sends a {method} request to {host}.")
        if host in ctx.app.config.http_auth:
            a.reads_private = True  # an authenticated fetch returns the user's own data
        return a

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> str:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return "Error: the URL must start with http:// or https://."
        method = str(args.get("method") or "GET").upper()
        headers = {"User-Agent": UA, **ctx.secrets.get("headers", {})}
        body = args.get("body")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers=headers) as client:
                response = await client.request(method, url, content=body.encode() if isinstance(body, str) else None)
        except httpx.HTTPError as exc:
            return f"Error fetching {url}: {exc}"
        kind = response.headers.get("content-type", "")
        limit = int(args.get("max_chars", 15_000))
        head = f"{response.status_code} {response.url}\n\n"
        if "html" in kind:
            return head + clip(html_to_text(response.text), limit)
        if kind.startswith("text/") or "json" in kind or "xml" in kind or not kind:
            return head + clip(response.text, limit)
        return head + f"(binary content: {kind}, {len(response.content)} bytes)"
