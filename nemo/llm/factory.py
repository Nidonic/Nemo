"""Build the configured model client."""

from __future__ import annotations

from nemo.config import Config
from nemo.llm.anthropic import AnthropicChat
from nemo.llm.base import BaseLLM, LLMError
from nemo.llm.mock import MockLLM
from nemo.llm.openai_chat import OpenAIChat
from nemo.vault import Vault, VaultError


def make_llm(config: Config, vault: Vault | None = None) -> BaseLLM:
    s = config.llm
    if s.kind == "mock":
        return MockLLM()
    if not config.is_ready():
        raise LLMError("No model is set up yet. Open Settings and choose a provider, a model and an API key.")
    key = config.api_key_source()
    base_url = s.base_url
    if s.provider == "relay" and config.hub.url:
        base_url = config.hub.url.rstrip("/") + "/v1"
        key = key or config.hub.token
    if vault is not None:
        try:
            key = vault.resolve(key)
        except VaultError as exc:
            raise LLMError(str(exc)) from exc
    if s.kind == "anthropic":
        return AnthropicChat(base_url, key, s.model, s.max_tokens, s.vision)
    return OpenAIChat(base_url, key, s.model, s.max_tokens, s.vision)
