"""Settings (pydantic v2), stored as JSON at <data_dir>/config.json. NEMO_HOME moves the data dir."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def nemo_home() -> Path:
    home = Path(os.environ.get("NEMO_HOME") or Path.home() / ".nemo")
    home.mkdir(parents=True, exist_ok=True)
    return home


# kind is the wire protocol; "openai" means any OpenAI-compatible /chat/completions endpoint
PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5", "env": "ANTHROPIC_API_KEY"},
    "openai": {"kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "env": "OPENAI_API_KEY"},
    "deepseek": {"kind": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "env": "DEEPSEEK_API_KEY"},
    "bailian": {"kind": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "env": "DASHSCOPE_API_KEY"},
    "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "openrouter/auto", "env": "OPENROUTER_API_KEY"},
    "gemini": {"kind": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash", "env": "GEMINI_API_KEY"},
    "groq": {"kind": "openai", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "env": "GROQ_API_KEY"},
    "moonshot": {"kind": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2-turbo-preview", "env": "MOONSHOT_API_KEY"},
    "ollama": {"kind": "openai", "base_url": "http://localhost:11434/v1", "model": "llama3.1", "env": ""},
    "relay": {"kind": "openai", "base_url": "", "model": "default", "env": ""},
    "mock": {"kind": "mock", "base_url": "", "model": "mock", "env": ""},
    "custom": {"kind": "openai", "base_url": "", "model": "", "env": ""},
}


class LLMSettings(BaseModel):
    provider: str = "anthropic"
    kind: Literal["openai", "anthropic", "mock"] = "anthropic"
    base_url: str = PROVIDERS["anthropic"]["base_url"]
    api_key: str = ""  # may be a {{vault:NAME}} placeholder
    model: str = PROVIDERS["anthropic"]["model"]
    max_tokens: int = 8192
    vision: bool = True
    # optional OpenAI-compatible embeddings endpoint for semantic memory
    embeddings_base_url: str = ""
    embeddings_api_key: str = ""
    embeddings_model: str = ""
    # optional OpenAI-compatible /images/generations model for the avatar studio (same base URL and key)
    image_model: str = ""


class Rule(BaseModel):
    """[[sentinel.rules]]: the first rule whose globs match the tool and arguments decides."""

    tool: str = "*"
    args: dict[str, str] = Field(default_factory=dict)
    action: Literal["allow", "ask", "deny"] = "ask"
    reason: str = ""


class SentinelSettings(BaseModel):
    mode: Literal["ask", "strict", "auto"] = "ask"
    deny_tools: list[str] = Field(default_factory=list)
    always_allow_tools: list[str] = Field(default_factory=list)
    always_ask_tools: list[str] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    egress_allowlist: list[str] = Field(default_factory=lambda: [
        "api.github.com", "github.com", "raw.githubusercontent.com", "pypi.org", "duckduckgo.com",
        "en.wikipedia.org", "zh.wikipedia.org",
    ])
    sandbox: bool = True  # bubblewrap on Linux when available


class AgentSettings(BaseModel):
    max_steps: int = 40
    max_context_messages: int = 50
    max_context_images: int = 3
    stuck_window: int = 3


class HubSettings(BaseModel):
    url: str = ""  # e.g. https://relay.example.com ; empty = not signed in
    token: str = ""
    device_id: str = ""
    device_name: str = ""
    sync: bool = True


class Config(BaseModel):
    home: str = ""  # data directory; empty = NEMO_HOME or ~/.nemo
    name: str = "Nemo"
    language: str = "auto"
    workspace: str = ""
    llm: LLMSettings = Field(default_factory=LLMSettings)
    sentinel: SentinelSettings = Field(default_factory=SentinelSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    hub: HubSettings = Field(default_factory=HubSettings)
    # name -> {"command": str, "args": [str], "env": {str: str}}; env values may use {{vault:NAME}}
    mcp_servers: dict[str, dict] = Field(default_factory=dict)
    # host -> extra request headers for web_fetch; values may use {{vault:NAME}} (resolved by the Sentinel)
    http_auth: dict[str, dict[str, str]] = Field(default_factory=dict)

    def data_dir(self) -> Path:
        if self.home:
            path = Path(self.home).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            return path
        return nemo_home()

    def workspace_path(self) -> Path:
        path = Path(self.workspace).expanduser() if self.workspace else self.data_dir() / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def api_key_source(self) -> str:
        if self.llm.api_key:
            return self.llm.api_key
        env = PROVIDERS.get(self.llm.provider, {}).get("env")
        return os.environ.get(env, "") if env else ""

    def is_ready(self) -> bool:
        if self.llm.kind == "mock":
            return True
        if self.llm.provider == "relay":
            return bool(self.hub.url and self.hub.token)
        keyless = self.llm.provider in ("ollama", "custom")
        return bool(self.llm.model and self.llm.base_url and (self.api_key_source() or keyless))

    def public(self) -> dict:
        data = self.model_dump()
        key = self.api_key_source()
        data["llm"]["api_key"] = ""
        data["llm"]["api_key_set"] = bool(key)
        data["llm"]["api_key_hint"] = key if key.startswith("{{vault:") else (f"…{key[-4:]}" if len(key) > 8 else "")
        data["llm"]["embeddings_api_key"] = ""
        data["hub"]["token"] = ""
        data["hub"]["signed_in"] = bool(self.hub.url and self.hub.token)
        data["workspace"] = str(self.workspace_path())
        data["ready"] = self.is_ready()
        return data


def config_path() -> Path:
    return nemo_home() / "config.json"


def load_config() -> Config:
    path = config_path()
    if path.exists():
        try:
            return Config.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return Config()


def save_config(config: Config) -> None:
    path = config.data_dir() / "config.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
