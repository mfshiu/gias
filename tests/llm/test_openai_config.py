"""OpenAI 聊天模型與端點由設定決定（E-05）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.llm.config import load_llm_runtime_config
from src.llm.providers.factory import build_provider_client
from src.llm.providers.openai_provider import OpenAIProvider


def _cfg(**openai):
    return {"llm": {"provider": "openai", "openai": {"api_key": "sk-test", **openai}}}


def test_defaults_keep_the_previous_model():
    cfg = load_llm_runtime_config(_cfg())
    assert (cfg.openai_model, cfg.openai_base_url, cfg.openai_use_responses) == ("gpt-4.1-mini", None, None)


@pytest.mark.parametrize("raw, expected", [(True, True), ("false", False), ("yes", True), (None, None)])
def test_model_endpoint_and_api_come_from_config(raw, expected):
    cfg = load_llm_runtime_config(_cfg(model="gpt-4o", base_url="https://llm.example/v1", use_responses=raw))
    assert (cfg.openai_model, cfg.openai_base_url, cfg.openai_use_responses) == (
        "gpt-4o", "https://llm.example/v1", expected)


def test_factory_passes_the_settings_to_the_provider(monkeypatch):
    monkeypatch.delenv("GIAS_OPENAI_USE_RESPONSES", raising=False)
    client, key, _ = build_provider_client(load_llm_runtime_config(
        _cfg(model="gpt-4o", base_url="https://llm.example/v1", use_responses=True)))
    assert isinstance(client, OpenAIProvider) and key == "sk-test"
    assert (client.default_model, client.base_url, client.use_responses) == ("gpt-4o", "https://llm.example/v1", True)


def test_chat_uses_the_configured_model_unless_overridden(monkeypatch):
    monkeypatch.delenv("GIAS_OPENAI_USE_RESPONSES", raising=False)
    client, _, _ = build_provider_client(load_llm_runtime_config(_cfg(model="gpt-4o")))
    sent = []

    def create(**payload):
        sent.append(payload["model"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None)

    monkeypatch.setattr(client.client.chat.completions, "create", create)
    client.chat([{"role": "user", "content": "hi"}])
    client.chat([{"role": "user", "content": "hi"}], model="gpt-4.1")
    assert sent == ["gpt-4o", "gpt-4.1"]


def test_example_config_documents_the_model():
    import pathlib

    import toml

    example = toml.load(pathlib.Path(__file__).resolve().parents[2] / "gias.example.toml")
    assert example["llm"]["openai"]["model"] == "gpt-4.1-mini"
