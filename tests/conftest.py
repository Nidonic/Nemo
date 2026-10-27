"""Offline fixtures: every test gets its own data dir and a MockLLM; nothing touches the network."""

from __future__ import annotations

import pytest

from nemo.app import App
from nemo.config import Config
from nemo.llm.mock import MockLLM, reply  # noqa: F401  (re-exported for tests)
from nemo.sentinel import ApprovalDecision


@pytest.fixture(autouse=True)
def nemo_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("NEMO_HOME", str(home))
    monkeypatch.delenv("NEMO_VAULT_KEY", raising=False)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return home


class Approver:
    """Answers Sentinel questions from a script and records them."""

    def __init__(self, *answers: ApprovalDecision):
        self.answers = list(answers)
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return self.answers.pop(0) if self.answers else ApprovalDecision(allow=False)


@pytest.fixture
def make_app(tmp_path):
    apps: list[App] = []

    def build(*script, approver: Approver | None = None, **config) -> App:
        cfg = Config(workspace=str(tmp_path / "ws"), **config)
        cfg.llm.kind, cfg.llm.provider = "mock", "mock"
        cfg.workspace_path()
        app = App(cfg, llm=MockLLM(*script))
        if approver is not None:
            app.sentinel.approver = approver
        apps.append(app)
        return app

    yield build
    for app in apps:
        app.memory.close()
