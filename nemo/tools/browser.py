"""browser (rung 3): a headless Chromium driven page by page through Playwright.

Optional: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import base64
from typing import Any

from nemo.computer.grid import sensitive_hit
from nemo.schema import Attachment, RiskLevel, ToolResult
from nemo.tools.base import BaseTool, CallAssessment, ToolContext, clip, obj, url_host
from nemo.tools.web import html_to_text

ACTIONS = ("open", "read", "click", "fill", "press", "screenshot", "back", "close")


class BrowserTool(BaseTool):
    name = "browser"
    description = (
        "A headless web browser for pages that need JavaScript, clicks or forms. Actions: open(url), read (page text), "
        "click(selector or text), fill(selector, text), press(key), screenshot, back, close. "
        "Selectors are CSS or text=… (e.g. text=Sign in)."
    )
    parameters = obj({
        "action": {"type": "string", "enum": list(ACTIONS)},
        "url": {"type": "string"},
        "selector": {"type": "string"},
        "text": {"type": "string"},
        "key": {"type": "string"},
    }, ["action"])
    risk = RiskLevel.MODERATE

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None

    def target(self, args: dict[str, Any]) -> str:
        if args.get("action") == "open":
            return url_host(str(args.get("url", "")))
        return str(args.get("action", ""))

    def assess(self, args: dict[str, Any], ctx: ToolContext) -> CallAssessment:
        action = args.get("action")
        a = CallAssessment(risk=self.risk, target=self.target(args), summary=f"{action} {args.get('url') or args.get('selector') or ''}".strip(),
                           network=True)
        if action == "open":
            a.egress = url_host(str(args.get("url", ""))) or "*"
        elif action in ("fill", "click", "press"):
            a.egress = url_host(self._page.url) if self._page else "*"  # a form submit sends to the current site
        word = sensitive_hit(args.get("selector"), args.get("text") if action == "click" else None)
        if word and action in ("click", "press"):
            a.warn(f"The browser is about to press something that looks like “{word}”.")
        return a

    async def _ensure(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("The browser needs: pip install playwright && playwright install chromium") from exc
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        return self._page

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._pw = self._browser = self._page = None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> "str | ToolResult":
        action = args.get("action")
        if action == "close":
            await self.close()
            return "Browser closed."
        try:
            page = await self._ensure()
        except RuntimeError as exc:
            return f"Error: {exc}"
        if action == "open":
            await page.goto(str(args["url"]), wait_until="domcontentloaded", timeout=45000)
        elif action == "click":
            await page.click(str(args.get("selector") or f"text={args.get('text', '')}"), timeout=15000)
            await page.wait_for_load_state("domcontentloaded")
        elif action == "fill":
            await page.fill(str(args["selector"]), str(args.get("text", "")), timeout=15000)
        elif action == "press":
            await page.keyboard.press(str(args.get("key") or "Enter"))
            await page.wait_for_load_state("domcontentloaded")
        elif action == "back":
            await page.go_back()
        elif action == "screenshot":
            png = await page.screenshot(type="jpeg", quality=70)
            size = page.viewport_size or {"width": 0, "height": 0}
            return ToolResult(call_id="", name="", content=f"Screenshot of {page.url}",
                              attachments=[Attachment(mime="image/jpeg", data=base64.b64encode(png).decode(),
                                                      width=size["width"], height=size["height"])])
        elif action != "read":
            return f"Error: unknown action {action}."
        title = await page.title()
        text = html_to_text(await page.content())
        return f"{title}\n{page.url}\n\n{clip(text, 12000)}"
