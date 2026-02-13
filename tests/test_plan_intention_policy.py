# tests/test_plan_intention_policy.py
# 執行（專案根目錄下）：
#   python -m pytest -q -k test_plan_intention_policy

import pytest

from src.core.intentional_agent import IntentionalAgent
from src.core.intent.sub_intent import SubIntent


class _DummyLLM:
    """避免真的呼叫 LLM。"""
    pass


@pytest.fixture()
def minimal_agent_config():
    # llm: 讓 load_llm_runtime_config 不炸
    # kg: 讓 IntentionalAgent.kg 的 type 檢查通過，實際連線由 stub 取代
    return {
        "llm": {},
        "kg": {"type": "neo4j", "neo4j": {}},
    }


@pytest.fixture()
def agent(monkeypatch, minimal_agent_config):
    # ✅ 讓 IntentionalAgent 初始化時，不會真的去初始化 LLM
    monkeypatch.setattr(
        "src.core.intentional_agent.LLMClient.from_config",
        lambda cfg: _DummyLLM(),
        raising=True,
    )

    # KG：不真的連 Neo4j，stub Neo4jBoltAdapter.from_config 回傳假物件
    _dummy_kg = object()
    monkeypatch.setattr(
        "src.core.intentional_agent.Neo4jBoltAdapter.from_config",
        lambda *args, **kwargs: _dummy_kg,
        raising=True,
    )

    # ActionStore 建構時只會用 self.kg，上面已給假 kg，必要時可再 stub ActionStore
    try:
        monkeypatch.setattr(
            "src.core.intentional_agent.ActionStore",
            lambda *args, **kwargs: object(),
            raising=False,
        )
    except Exception:
        pass

    return IntentionalAgent(agent_config=minimal_agent_config, intention="測試意圖")


def test_plan_intention_fail_if_any_sub_intent_unmatched(monkeypatch, agent):
    # 模擬拆解：兩個子意圖，其中一個無法配到 action（break_down 回傳 SubIntent 串列）
    subs = [
        SubIntent(intent="可以處理的子意圖"),
        SubIntent(intent="無法處理的子意圖"),
    ]
    monkeypatch.setattr(agent, "break_down_intention", lambda _: subs)

    def fake_match_actions(intent_text: str, slots=None):
        return ["SomeAction"] if intent_text == "可以處理的子意圖" else []

    monkeypatch.setattr(agent, "match_actions", fake_match_actions)

    result = agent.plan_intention("測試意圖")

    # 有任何子意圖無法配到 action => 回傳 leaf_unresolved，並列出 unmatched_sub_intentions
    assert isinstance(result, dict)
    assert result.get("type") == "leaf_unresolved"
    assert "無法處理的子意圖" in result.get("unmatched_sub_intentions", [])


def test_plan_intention_success_when_all_sub_intents_matched(monkeypatch, agent):
    subs = [SubIntent(intent="子意圖A"), SubIntent(intent="子意圖B")]
    monkeypatch.setattr(agent, "break_down_intention", lambda _: subs)

    # match_actions(intent_text, slots=...) 回傳 list[ActionMatch]，用簡單物件具 .name 即可
    class _FakeMatch:
        def __init__(self, name): self.name = name
    def fake_match_actions(intent_text: str, slots=None):
        return [_FakeMatch(f"ActionFor({intent_text})")]

    monkeypatch.setattr(agent, "match_actions", fake_match_actions)
    # 跳過 selector（會用到 embedder）：直接回傳允許的 action 描述，供後續 planner 使用
    monkeypatch.setattr(agent.selector, "select_actions", lambda sub_intents: {"ActionFor(子意圖A)": "desc", "ActionFor(子意圖B)": "desc"})
    # 避免 planner 真的呼叫 LLM：直接回傳一個「成功」的 plan
    def fake_plan(norm, chosen_actions):
        return {"id": "root", "intent": norm, "type": "composite", "sub_plans": [], "execution_logic": []}
    monkeypatch.setattr(agent.planner, "plan", fake_plan)

    result = agent.plan_intention("測試意圖")

    assert isinstance(result, dict)
    assert result.get("type") != "leaf_unresolved"
