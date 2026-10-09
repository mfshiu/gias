"""重規劃脈絡進入拆解 prompt 的單元測試（L-02）。"""

from __future__ import annotations

import logging

from src.core.intent.planner import RecursivePlanner
from src.core.intent.prompt_builder import PromptBuilder

ACTIONS = {"LocateExhibit(TargetName)": "引導訪客前往指定展區", "SuggestRoute(AvoidCrowd)": "建議路線"}

CONTEXT = {
    "replan": {
        "kind": "replan_subtree",
        "reason": "env change affects node a via ['Zone/CURRENT_STATE/State']",
        "affected_steps": [
            {"id": "a", "task": "LocateExhibit", "params": {"target_name": "AI_Tech_Area"},
             "state": "obsolete", "error": "area closed"},
        ],
    },
    "env_facts": {"zone_states": [{"zone": "AI_Tech_Area", "state": "Closed"}]},
}


def test_prompt_without_context_is_unchanged():
    prompt = PromptBuilder().build_prompt("帶我去 AI 區", ACTIONS)
    assert "Replanning" not in prompt
    assert 'set `scheduled_start` to "".\n\n### Output Format' in prompt
    assert prompt == PromptBuilder().build_prompt("帶我去 AI 區", ACTIONS, None)


def test_prompt_with_context_includes_reason_failed_step_and_env_facts():
    prompt = PromptBuilder().build_prompt("帶我去 AI 區", ACTIONS, CONTEXT)
    ctx_start = prompt.index("### Replanning Context")
    assert ctx_start < prompt.index("### Output Format")
    assert "env change affects node a" in prompt
    assert 'LocateExhibit {"target_name": "AI_Tech_Area"} (state=obsolete, error=area closed)' in prompt
    assert '"zone": "AI_Tech_Area", "state": "Closed"' in prompt
    assert "8. **Respect the Environment**" in prompt
    assert "9. **Do Not Repeat Failures**" in prompt


def test_large_env_facts_are_truncated():
    big = {"zone_states": [{"zone": f"Z{i}", "state": "Crowded"} for i in range(2000)]}
    prompt = PromptBuilder().build_prompt("x", ACTIONS, {"env_facts": big})
    assert "...(truncated)" in prompt
    assert len(prompt) < 10000


class _RecordingDecomposer:
    def __init__(self):
        self.contexts = []

    def decompose(self, intent, available_actions, context=None):
        self.contexts.append(context)
        if intent == "root":
            return {"sub_intents": [{"id": "1", "intent": "child", "is_atomic": False, "action": ""}],
                    "relationships": []}
        return {"sub_intents": [{"id": "1", "intent": "leaf", "is_atomic": True,
                                 "atomic_source": "pre_defined", "action": "SuggestRoute(AvoidCrowd=true)"}],
                "relationships": []}


class _TwoArgDecomposer:
    """舊介面：decompose 只收兩個參數。一般規劃（無 context）必須仍可使用。"""

    def decompose(self, intent, available_actions):
        return {"sub_intents": [{"id": "1", "intent": "leaf", "is_atomic": True,
                                 "atomic_source": "pre_defined", "action": "SuggestRoute()"}],
                "relationships": []}


def test_planner_passes_context_to_every_level():
    dec = _RecordingDecomposer()
    RecursivePlanner(decomposer=dec, logger=logging.getLogger("t")).plan("root", ACTIONS, context=CONTEXT)
    assert dec.contexts == [CONTEXT, CONTEXT]


def test_planner_without_context_keeps_two_argument_decompose():
    plan = RecursivePlanner(decomposer=_TwoArgDecomposer(), logger=logging.getLogger("t")).plan("root", ACTIONS)
    assert plan["sub_plans"][0]["action"] == "SuggestRoute()"
