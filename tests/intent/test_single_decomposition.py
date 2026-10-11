"""意圖只拆解一次的單元測試（A-04）。

意圖解析拆出的子意圖用來比對動作，也直接成為計畫的第一層；planner 不再自行重拆。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.intent.action_selector import ActionSelector
from src.core.intent.planner import RecursivePlanner
from src.core.intent.prompt_builder import PromptBuilder
from src.core.intent.scope_gate import ScopeDecision
from src.core.intent.sub_intent import SubIntent
from src.core.intentional_agent import IntentionalAgent

GIVEN = [
    {"id": "1", "intent": "推薦 AI 展品", "slots": {"interests": ["AI"]}, "actions": ["RecommendExhibits(Interests)"]},
    {"id": "2", "intent": "帶我過去", "slots": {}, "actions": ["LocateExhibit(TargetName)"]},
]
ACTIONS = {"RecommendExhibits(Interests)": "推薦展品", "LocateExhibit(TargetName)": "引導前往"}


def _sub(sid, intent, action="", is_atomic=True):
    return {"id": sid, "intent": intent, "is_atomic": is_atomic, "atomic_source": "pre_defined" if action else None,
            "action": action}


ROOT_OK = {"sub_intents": [_sub("1", "推薦 AI 展品", 'RecommendExhibits(Interests=["AI"])'),
                           _sub("2", "帶我過去", 'LocateExhibit(TargetName="B_AI1")')],
           "relationships": [{"type": "Sequence", "from_id": "1", "to_id": "2"}]}
ROOT_REWRITTEN = {"sub_intents": [_sub("1", "推薦並帶我去 AI 展品", 'RecommendExhibits(Interests=["AI"])'),
                                  _sub("3", "查人潮", "CrowdStatus()")],
                  "relationships": [{"type": "Sequence", "from_id": "1", "to_id": "3"}]}


class _Decomposer:
    def __init__(self, *replies, child=None):
        self.replies = list(replies)
        self.child = child
        self.calls = []

    def decompose(self, intent, available_actions, context=None):
        self.calls.append((intent, context))
        if self.replies:
            return self.replies.pop(0)
        return self.child


def _planner(dec):
    return RecursivePlanner(decomposer=dec, logger=logging.getLogger("t"))


# ----------------------------------------------------------------------
# Planner：照給定的子意圖綁定，不一致時重問一次，仍不一致就以給定為準
# ----------------------------------------------------------------------
def test_root_uses_the_given_sub_intents_in_one_call():
    dec = _Decomposer(ROOT_OK)
    plan = _planner(dec).plan("推薦 AI 展品並帶我過去", ACTIONS, given_sub_intents=GIVEN)
    assert len(dec.calls) == 1
    assert dec.calls[0][1] == {"given_sub_intents": GIVEN}
    assert [n["id"] for n in plan["sub_plans"]] == ["1", "2"]
    assert "debug" not in plan


def test_rewritten_decomposition_is_asked_again():
    dec = _Decomposer(ROOT_REWRITTEN, ROOT_OK)
    plan = _planner(dec).plan("x", ACTIONS, given_sub_intents=GIVEN)
    assert dec.calls[1][1]["given_sub_intents_feedback"] == {"missing": ["2"], "extra": ["3"]}
    assert [n["intent"] for n in plan["sub_plans"]] == ["推薦 AI 展品", "帶我過去"]


def test_still_rewritten_decomposition_is_aligned_to_the_given_sub_intents():
    child = {"sub_intents": [_sub("1", "引導", 'LocateExhibit(TargetName="B_AI1")')], "relationships": []}
    dec = _Decomposer(ROOT_REWRITTEN, ROOT_REWRITTEN, child=child)
    plan = _planner(dec).plan("x", ACTIONS, given_sub_intents=GIVEN)
    first, restored = plan["sub_plans"]
    assert first["intent"] == "推薦並帶我去 AI 展品"                 # 保留對應到的項目
    assert (restored["id"], restored["intent"], restored["type"]) == ("2", "帶我過去", "composite")
    assert restored["sub_plans"][0]["action"] == 'LocateExhibit(TargetName="B_AI1")'   # 補回的子意圖再往下拆
    assert plan["execution_logic"] == []                              # 指向被移除項目的關係一併移除
    assert plan["debug"] == {"sub_intent_alignment": {"restored": ["2"], "dropped": ["3"]}}
    # 下一層不再受給定子意圖限制
    assert dec.calls[2] == ("帶我過去", None)


def test_prompt_lists_given_sub_intents_and_feedback():
    ctx = {"given_sub_intents": GIVEN, "given_sub_intents_feedback": {"missing": ["2"], "extra": ["3"]}}
    prompt = PromptBuilder().build_prompt("x", ACTIONS, ctx)
    assert '- "1": 推薦 AI 展品 | entities {"interests": ["AI"]} | matched: RecommendExhibits(Interests)' in prompt
    assert "Use exactly these as your `sub_intents`" in prompt
    assert "missing ids ['2'], unexpected ids ['3']" in prompt
    assert "Replanning" not in prompt


# ----------------------------------------------------------------------
# 比對結果只算一次
# ----------------------------------------------------------------------
def test_selector_reuses_given_matches():
    matcher = MagicMock()
    kg = MagicMock()
    kg.read.return_value = []
    sel = ActionSelector(kg=kg, matcher=matcher, logger=MagicMock())
    match = SimpleNamespace(action=SimpleNamespace(name="RecommendExhibits", description="推薦展品"), score=0.9)
    out = sel.select_actions([SubIntent(intent="推薦")], matches=[[match]])
    assert out == {"RecommendExhibits()": "推薦展品"}
    matcher.match_actions.assert_not_called()


# ----------------------------------------------------------------------
# plan_intention
# ----------------------------------------------------------------------
def _agent():
    cfg = {"llm": {"provider": "mock"}, "broker": {"broker_name": "mqtt01"},
           "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}, "neo4j_actions": {}}}
    with patch("src.core.intentional_agent.Neo4jBoltAdapter") as adapter_cls, \
         patch("src.core.intentional_agent.LLMClient.from_config", return_value=MagicMock()):
        adapter_cls.from_config.return_value = MagicMock()
        return IntentionalAgent(agent_config=cfg, intention="test")


def _match(name):
    return SimpleNamespace(action=SimpleNamespace(name=name), score=0.9)


def _plan_with(subs, matches):
    agent = _agent()
    agent.break_down_intention = lambda text: subs
    agent.match_actions = lambda text, slots=None: matches[text]
    agent.selector = MagicMock()
    agent.selector.select_actions.return_value = ACTIONS
    agent.scope_gate = MagicMock()
    agent.scope_gate.decide.return_value = ScopeDecision(can_execute=True, reason="ok")
    agent.planner = MagicMock()
    agent.planner.plan.return_value = {
        "id": "root", "type": "composite", "intent": "x", "execution_logic": [],
        "sub_plans": [{"id": "1", "type": "atomic", "is_atomic": True, "task": "RecommendExhibits",
                       "topic": "info.request", "action": "RecommendExhibits()", "sub_plans": []}],
    }
    agent.plan_intention("推薦 AI 展品並帶我過去")
    return agent


def test_plan_intention_passes_parsed_sub_intents_and_their_matches():
    subs = [SubIntent(intent="推薦 AI 展品", slots={"interests": ["AI"], "_source_text": "x", "limit": ""}),
            SubIntent(intent="帶我過去")]
    agent = _plan_with(subs, {"推薦 AI 展品": [_match("RecommendExhibits")], "帶我過去": [_match("LocateExhibit")]})
    assert agent.planner.plan.call_args.kwargs["given_sub_intents"] == GIVEN
    # 比對結果交給 selector，不再比對一次
    assert agent.selector.select_actions.call_args.kwargs["matches"] == [
        [_match("RecommendExhibits")], [_match("LocateExhibit")]]


def test_single_or_fallback_sub_intent_is_not_imposed():
    single = _plan_with([SubIntent(intent="推薦 AI 展品")], {"推薦 AI 展品": [_match("RecommendExhibits")]})
    assert "given_sub_intents" not in single.planner.plan.call_args.kwargs
    fallback = [SubIntent(intent="推薦 AI 展品", raw={"fallback": True}), SubIntent(intent="帶我過去")]
    agent = _plan_with(fallback, {"推薦 AI 展品": [_match("RecommendExhibits")], "帶我過去": [_match("LocateExhibit")]})
    assert "given_sub_intents" not in agent.planner.plan.call_args.kwargs
