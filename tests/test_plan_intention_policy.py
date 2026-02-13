# tests/test_plan_intention_policy.py
# 執行：
#   set PYTHONPATH=src && python -m pytest -q -k test_plan_intention_policy

import pytest

from core.intentional_agent import IntentionalAgent


class _DummyLLM:
    """避免真的呼叫 LLM。"""
    pass


@pytest.fixture()
def minimal_agent_config():
    # 只要讓 load_llm_runtime_config(agent_config).get("llm") 不會炸即可
    return {"llm": {}}


@pytest.fixture()
def agent(monkeypatch, minimal_agent_config):
    # ✅ 讓 IntentionalAgent 初始化時，不會真的去初始化 LLM
    monkeypatch.setattr(
        "llm.client.LLMClient.from_config",
        lambda cfg: _DummyLLM(),
        raising=True,
    )

    # 如果你的 __init__ 內還會碰到 KG/ActionStore，保險起見也先 stub（有就用、沒有也不會錯）
    try:
        monkeypatch.setattr(
            "kg.action_store.ActionStore",
            lambda *args, **kwargs: object(),
            raising=False,
        )
    except Exception:
        pass

    try:
        monkeypatch.setattr(
            "kg.adapter_neo4j.Neo4jBoltAdapter",
            lambda *args, **kwargs: object(),
            raising=False,
        )
    except Exception:
        pass

    return IntentionalAgent(agent_config=minimal_agent_config, intention="測試意圖")


def test_plan_intention_fail_if_any_sub_intent_unmatched(monkeypatch, agent):
    # 模擬拆解：兩個子意圖，其中一個無法配到 action
    monkeypatch.setattr(agent, "break_down_intention", lambda _: ["可以處理的子意圖", "無法處理的子意圖"])

    def fake_match_actions(sub_intent: str):
        return ["SomeAction"] if sub_intent == "可以處理的子意圖" else []

    monkeypatch.setattr(agent, "match_actions", fake_match_actions)

    result = agent.plan_intention("測試意圖")

    # ✅ 這裡依你要的政策來驗證：
    # - 有任何子意圖無法配到 action => 整體不執行
    # - 並要標示出失敗點
    assert isinstance(result, dict)
    assert result.get("status") in ("failed", "abort", "error")
    assert result.get("failed_sub_intent") == "無法處理的子意圖"


def test_plan_intention_success_when_all_sub_intents_matched(monkeypatch, agent):
    monkeypatch.setattr(agent, "break_down_intention", lambda _: ["子意圖A", "子意圖B"])
    monkeypatch.setattr(agent, "match_actions", lambda s: [f"ActionFor({s})"])

    result = agent.plan_intention("測試意圖")

    assert isinstance(result, dict)
    assert result.get("status") in ("ok", "success")
    assert result.get("failed_sub_intent") in (None, "")
