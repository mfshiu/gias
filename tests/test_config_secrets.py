"""設定檔中的機密（S-03）：${VAR} 環境變數參照、明文機密提醒、OPENAI_API_KEY。"""

from __future__ import annotations

import logging
import pathlib

import toml

from src.app_helper import expand_env, get_agent_config, literal_secrets
from src.llm.config import load_llm_runtime_config

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_expand_env_replaces_references(monkeypatch):
    monkeypatch.setenv("GIAS_TEST_PW", "pw-under-test")
    monkeypatch.delenv("GIAS_TEST_MISSING", raising=False)
    missing: set[str] = set()
    cfg = {"a": "${GIAS_TEST_PW}", "b": ["x-${GIAS_TEST_PW}-y", 3], "c": {"d": "${GIAS_TEST_MISSING}"},
           "e": "plain", "f": 1.5}
    assert expand_env(cfg, missing) == {"a": "pw-under-test", "b": ["x-pw-under-test-y", 3], "c": {"d": ""},
                                        "e": "plain", "f": 1.5}
    assert missing == {"GIAS_TEST_MISSING"}


def test_literal_secrets_lists_plain_values_only():
    cfg = {"broker": {"mqtt01": {"username": "gias", "password": "hunter2"}},
           "llm": {"openai": {"api_key": "${OPENAI_API_KEY}", "embed_model": "m"}},
           "kg": {"neo4j": {"password": ""}},
           "blackboard": {"access": {"token": "abc"}}, "tls": {"keyfile_password": "kp"}}
    assert literal_secrets(cfg) == ["broker.mqtt01.password", "blackboard.access.token", "tls.keyfile_password"]


def _write(tmp_path, text):
    path = tmp_path / "gias.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_get_agent_config_expands_and_warns_once_without_values(tmp_path, monkeypatch, caplog):
    path = _write(tmp_path, '[llm.openai]\napi_key = "sk-plain-under-test"\n'
                            '[kg.neo4j]\npassword = "${GIAS_TEST_NEO4J}"\n[broker.m]\npassword = "${GIAS_TEST_UNSET}"\n')
    monkeypatch.setenv("GIAS_CONFIG_PATH", str(path))
    monkeypatch.setenv("GIAS_TEST_NEO4J", "neo4j-under-test")
    monkeypatch.delenv("GIAS_TEST_UNSET", raising=False)
    with caplog.at_level(logging.WARNING, logger="src.app_helper"):
        cfg = get_agent_config()
        get_agent_config()
    assert cfg["kg"]["neo4j"]["password"] == "neo4j-under-test"
    assert cfg["broker"]["m"]["password"] == ""
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2                                        # 每個設定檔只提醒一次
    assert "stores secrets in plain text: llm.openai.api_key" in messages[0]
    assert "references unset environment variables: GIAS_TEST_UNSET" in messages[1]
    assert not any("under-test" in m for m in messages)             # 不輸出機密的值


def test_openai_key_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-under-test")
    assert load_llm_runtime_config({"llm": {"provider": "openai"}}).openai_api_key == "env-key-under-test"
    assert load_llm_runtime_config({"llm": {"openai": {"api_key": ""}}}).openai_api_key == "env-key-under-test"
    assert load_llm_runtime_config({"llm": {"openai": {"api_key": "cfg-key"}}}).openai_api_key == "cfg-key"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert load_llm_runtime_config({"llm": {"provider": "openai"}}).openai_api_key is None


def test_example_config_has_no_secrets():
    example = toml.load(REPO / "gias.example.toml")
    assert literal_secrets(example) == []
    assert example["llm"]["openai"]["api_key"] == "${OPENAI_API_KEY}"
