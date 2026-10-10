"""Action 參數解析與驗證單元測試（C-07）。"""

from __future__ import annotations

import logging

import pytest

from src.core.intent.action_params import normalize_params, parse_action_call, to_snake
from src.core.intent.planner import RecursivePlanner


def test_parse_typed_values():
    raw = parse_action_call(
        'SuggestRoute(CurrentLocation="入口, 北側", Interests=["AI", "VR"], AvoidCrowd=true, '
        'TimeBudgetMin=60, Ratio=0.5, Limit=null, Note=None, BoothNo=007, Target=A12)'
    )
    assert raw == {
        "CurrentLocation": "入口, 北側",   # 引號內的逗號不切
        "Interests": ["AI", "VR"],
        "AvoidCrowd": True,
        "TimeBudgetMin": 60,
        "Ratio": 0.5,
        "Limit": None,                      # 舊行為：字串 "null"
        "Note": None,
        "BoothNo": "007",                   # 前導零的編號保留為字串
        "Target": "A12",
    }


def test_parse_edge_cases():
    assert parse_action_call("Foo()") == {}
    assert parse_action_call("Foo") == {}
    assert parse_action_call("Foo('a', Key='it\\'s')") == {"Key": "it's"}     # 位置參數略過
    assert parse_action_call("Foo(A=1，B=[x, [y, z]])") == {"A": 1, "B": ["x", ["y", "z"]]}
    assert parse_action_call('Foo(Name: "B_GM1")') == {"Name": "B_GM1"}


@pytest.mark.parametrize("name, expected", [
    ("TargetType", "target_type"),
    ("BoothID", "booth_id"),              # 舊行為："booth_i_d"
    ("TimeBudgetMin", "time_budget_min"),
    ("target_name", "target_name"),
    ("IDNumber", "id_number"),
])
def test_to_snake(name, expected):
    assert to_snake(name) == expected


SCHEMA = [
    {"key": "interests", "type": "list[string]", "required": True},
    {"key": "booth_id", "type": "string", "required": False},
    {"key": "limit", "type": "int", "required": False},
    {"key": "avoid_crowd", "type": "bool", "required": False},
    {"key": "target_type", "type": "enum", "required": True, "enum": ["exhibit_zone", "booth", "exhibit"]},
    {"key": "ratio", "type": "number", "required": False},
]


def test_normalize_maps_keys_and_coerces_types():
    params, issues = normalize_params(
        {"BoothID": 12, "Limit": "5", "AvoidCrowd": "是", "TargetType": "攤位", "Interests": "AI、VR", "Ratio": "0.5"},
        SCHEMA,
        {"target_type": {"攤位": "booth"}},
    )
    assert params == {"booth_id": "12", "limit": 5, "avoid_crowd": True, "target_type": "booth",
                      "interests": ["AI", "VR"], "ratio": 0.5}
    assert issues == []


def test_normalize_reports_issues():
    params, issues = normalize_params(
        {"Interests": None, "Limit": "五個", "TargetType": "Booth", "AvoidCrowd": "maybe", "Color": "red"},
        SCHEMA,
    )
    assert params["target_type"] == "booth"           # 大小寫不同仍可對應
    assert "interests" not in params
    kinds = {(i["param"], i["issue"]) for i in issues}
    assert kinds == {("limit", "invalid_type"), ("avoid_crowd", "invalid_type"), ("color", "unknown_param"),
                     ("interests", "missing_required")}
    assert params["color"] == "red"                   # 不在 schema 內的參數仍保留給 executor


def test_normalize_invalid_enum_and_no_schema():
    _, issues = normalize_params({"TargetType": "landmark", "Interests": ["x"]}, SCHEMA)
    assert issues == [{"param": "target_type", "issue": "invalid_enum", "value": "landmark"}]
    params, issues = normalize_params({"TargetName": "A12", "Limit": None}, [])
    assert (params, issues) == ({"target_name": "A12"}, [])


# ----------------------------------------------------------------------
# RecursivePlanner 整合
# ----------------------------------------------------------------------
class _FakeKG:
    def query(self, cypher, params=None):
        return [{"action_id": "a1", "topic": "info.request", "task": "RecommendExhibits",
                 "display_name": "Recommend", "description": "推薦展區"}]

    def read(self, cypher, params=None):
        return []


class _FakeStore:
    def get_action_params(self, name):
        return SCHEMA


class _Decomposer:
    def decompose(self, intent, available_actions):
        return {"sub_intents": [{"id": "1", "intent": "推薦展區", "is_atomic": True, "atomic_source": "pre_defined",
                                 "action": 'RecommendExhibits(Interests=null, TargetType="攤位", Limit=3)'}],
                "relationships": []}


def test_planner_attaches_typed_params_and_issues():
    planner = RecursivePlanner(decomposer=_Decomposer(), logger=logging.getLogger("t"), kg=_FakeKG(),
                               action_store=_FakeStore(), enum_alias={"target_type": {"攤位": "booth"}})
    node = planner.plan("推薦幾個展區", {})["sub_plans"][0]
    assert node["params"] == {"target_type": "booth", "limit": 3}
    assert node["param_issues"] == [{"param": "interests", "issue": "missing_required"}]
