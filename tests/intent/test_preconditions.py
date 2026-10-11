"""前置條件與效果的單元測試（A-02）。"""

from __future__ import annotations

import logging

import pytest

from src.core.intent import preconditions as pre
from src.core.intent.planner import RecursivePlanner
from src.kg.action_store import ActionStore, write_action_conditions

ZONE_OPEN = {"key": "zone_state", "arg": "target_name", "op": "not_in", "value": ["Closed"],
             "desc": "目的地所在區域沒有關閉"}


def _probe(state: dict, *, available: bool = True):
    def probe(key, arg):
        if not available or key == "undefined":
            return False, None
        return True, state.get((key, arg))
    return probe


def _node(nid, target, *, pre_specs=(ZONE_OPEN,), effects=(), task="LocateExhibit"):
    return {"id": nid, "type": "atomic", "is_atomic": True, "task": task, "params": {"target_name": target},
            "preconditions": list(pre_specs), "effects": list(effects), "sub_plans": []}


@pytest.mark.parametrize("op, value, actual, holds", [
    ("not_in", ["Closed"], "Normal", True),
    ("not_in", ["Closed"], "Closed", False),
    ("not_in", ["Closed"], None, True),        # 查無此狀態：視為 None
    ("in", ["Normal", "Sparse"], "Crowded", False),
    ("eq", "open", None, False),
    ("ne", "closed", "open", True),
    ("exists", None, None, False),
    ("not_exists", None, None, True),
])
def test_operators(op, value, actual, holds):
    spec = {"key": "k", "arg": "target_name", "op": op, "value": value}
    findings = pre.check_node(_node("1", "X", pre_specs=[spec]), _probe({("k", "X"): actual}))
    assert (findings == []) is holds


def test_violation_details_and_blocking():
    findings = pre.check_node(_node("1", "AI_Tech_Area"), _probe({("zone_state", "AI_Tech_Area"): "Closed"}))
    [f] = findings
    assert (f["status"], f["severity"], f["actual"], f["source"]) == ("violated", "block", "Closed", "blackboard")
    assert pre.blocking(findings) == findings
    assert "目的地所在區域沒有關閉 (zone_state[AI_Tech_Area]: expected not_in ['Closed'], actual Closed)" == pre.describe(f)


def test_warn_severity_is_not_blocking():
    spec = dict(ZONE_OPEN, severity="warn")
    findings = pre.check_node(_node("1", "Z", pre_specs=[spec]), _probe({("zone_state", "Z"): "Closed"}))
    assert findings[0]["status"] == "violated" and pre.blocking(findings) == []


def test_unverified_when_state_unavailable_or_param_missing():
    node = _node("1", "Z")
    assert pre.check_node(node, _probe({}, available=False))[0]["status"] == "unverified"
    node["params"] = {}
    assert pre.check_node(node, _probe({}))[0]["note"] == "parameter missing"
    assert pre.blocking(pre.check_node(node, _probe({}))) == []


def test_aliases_are_resolved_before_probing():
    resolve = {"AI 展區": "AI_Tech_Area"}.get
    findings = pre.check_node(_node("1", "AI 展區"), _probe({("zone_state", "AI_Tech_Area"): "Closed"}),
                              resolve=lambda v: resolve(v, v))
    assert findings[0]["arg_value"] == "AI_Tech_Area"


# ----------------------------------------------------------------------
# 效果：較早步驟的效果成為後續步驟的預期狀態
# ----------------------------------------------------------------------
AT_TARGET = {"key": "visitor_location", "op": "eq", "value": "B_AI1", "desc": "訪客在攤位旁"}
MOVES = {"key": "visitor_location", "value_arg": "target_name"}


def _plan(children, logic=()):
    return {"id": "root", "type": "composite", "sub_plans": list(children), "execution_logic": list(logic)}


def test_effect_of_earlier_step_satisfies_later_precondition():
    go = _node("1", "B_AI1", pre_specs=(), effects=[MOVES])
    explain = _node("2", "B_AI1", pre_specs=[AT_TARGET], task="ExplainExhibit")
    seq = [{"type": "Sequence", "from_id": "1", "to_id": "2"}]
    probe = _probe({("visitor_location", None): "P_Entrance"})
    assert pre.check_plan(_plan([go, explain], seq), probe) == []
    # 沒有先後關係（同一層平行執行）時看不到彼此的效果
    [f] = pre.check_plan(_plan([go, explain]), probe)
    assert (f["node_id"], f["actual"]) == ("2", "P_Entrance")


def test_effects_flow_out_of_nested_composites():
    inner = {"id": "g", "type": "composite", "execution_logic": [],
             "sub_plans": [_node("g.1", "B_AI1", pre_specs=(), effects=[MOVES])]}
    explain = _node("2", "B_AI1", pre_specs=[AT_TARGET], task="ExplainExhibit")
    plan = _plan([inner, explain], [{"type": "Sequence", "from_id": "g", "to_id": "2"}])
    assert pre.check_plan(plan, _probe({})) == []


def test_effect_with_literal_value_and_arg():
    node = _node("1", "B_GM1", pre_specs=(), effects=[{"key": "booth_status", "arg": "target_name", "value": "visited"}])
    overlay = {}
    pre.apply_effects(node, overlay)
    assert overlay == {("booth_status", "B_GM1"): "visited"}


# ----------------------------------------------------------------------
# Action KG：讀寫前置條件與效果；planner 把它們附到 atomic 節點
# ----------------------------------------------------------------------
class _KG:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.writes = []

    def query(self, cypher, params=None, **kw):
        return self.rows

    def read(self, cypher, params=None):
        return []

    def write(self, cypher, params=None):
        self.writes.append((cypher, params))


def test_action_store_reads_conditions():
    store = ActionStore(_KG([{"preconditions": [ZONE_OPEN], "effects": [MOVES]}]))
    assert store.get_action_conditions("LocateExhibit") == {"preconditions": [ZONE_OPEN], "effects": [MOVES]}
    assert ActionStore(_KG([])).get_action_conditions("X") == {"preconditions": [], "effects": []}


def test_write_action_conditions_creates_nodes():
    kg = _KG()
    write_action_conditions(kg.write, "a1", preconditions=[ZONE_OPEN], effects=[MOVES])
    (c1, p1), (c2, p2) = kg.writes
    assert ":REQUIRES]->(:Precondition" in c1 and p1["severity"] == "block" and p1["value"] == ["Closed"]
    assert ":PRODUCES]->(:Effect" in c2 and p2["value_arg"] == "target_name"


class _StoreWithConditions:
    def __init__(self, conditions=None, exc=None):
        self.conditions, self.exc = conditions, exc

    def get_action_params(self, name):
        return [{"key": "target_name", "type": "string", "required": True}]

    def get_action_conditions(self, name):
        if self.exc:
            raise self.exc
        return self.conditions


class _MetaKG(_KG):
    def query(self, cypher, params=None, **kw):
        return [{"action_id": "a1", "topic": "navigation.request", "task": "LocateExhibit"}]


class _Decomposer:
    def decompose(self, intent, available_actions):
        return {"sub_intents": [{"id": "1", "intent": "go", "is_atomic": True, "atomic_source": "pre_defined",
                                 "action": 'LocateExhibit(TargetName="B_AI1")'}], "relationships": []}


@pytest.mark.parametrize("store, expected", [
    (_StoreWithConditions({"preconditions": [ZONE_OPEN], "effects": []}), [ZONE_OPEN]),
    (_StoreWithConditions(exc=RuntimeError("no such label")), None),     # 取不到時不影響規劃
])
def test_planner_attaches_preconditions(store, expected):
    planner = RecursivePlanner(decomposer=_Decomposer(), logger=logging.getLogger("t"), kg=_MetaKG(), action_store=store)
    node = planner.plan("go", {})["sub_plans"][0]
    assert node["task"] == "LocateExhibit"
    assert node.get("preconditions") == expected
