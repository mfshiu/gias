"""ActionMatcher 門檻單元測試（C-06）：查不到語意相關的 action 時要回傳空串列。"""

from __future__ import annotations

import logging

from src.core.intent.action_matcher import ActionMatcher
from src.core.intent.domain_profile import DomainProfile


class _Store:
    def __init__(self, rows, params=None):
        self.rows = rows
        self.params = params or {}
        self.searches = []

    def ensure_action_desc_index(self, dimensions):
        pass

    def search_actions_by_vector(self, *, vector, top_k, min_score):
        self.searches.append(min_score)
        return [r for r in self.rows if r["score"] >= min_score][:top_k]

    def get_action_params(self, name):
        return self.params.get(name, [])


class _Embedder:
    def __init__(self):
        self.calls = 0

    def embed_text(self, text):
        self.calls += 1
        return [0.1, 0.2, 0.3]


def _matcher(rows, params=None, **kw):
    store, emb = _Store(rows, params), _Embedder()
    m = ActionMatcher(action_store=store, embedder=emb, domain=DomainProfile(), logger=logging.getLogger("t"), **kw)
    return m, store, emb


def _row(name, score):
    return {"name": name, "description": name, "score": score, "id": name}


def test_primary_threshold_matches():
    m, store, _ = _matcher([_row("LocateExhibit", 0.82), _row("AnswerFAQ", 0.7)])
    assert [x.action.name for x in m.match_actions("帶我去 A12")] == ["LocateExhibit"]
    assert store.searches == [0.0]          # 只查一次，門檻在本機過濾


def test_fallback_floor_accepts_weakly_related():
    m, _, _ = _matcher([_row("LocateExhibit", 0.68), _row("AnswerFAQ", 0.55)])
    assert [x.action.name for x in m.match_actions("帶我去那邊")] == ["LocateExhibit"]


def test_unrelated_intent_returns_no_match():
    """舊行為：門檻降為 0 重查，任何意圖都會配到 action。"""
    m, _, _ = _matcher([_row("AnswerFAQ", 0.58), _row("LocateExhibit", 0.52)])
    assert m.match_actions("幫我修理 iPhone") == []


def test_thresholds_are_configurable_and_overridable():
    rows = [_row("AnswerFAQ", 0.58)]
    m, _, _ = _matcher(rows, fallback_min_score=0.5)
    assert len(m.match_actions("x")) == 1
    m, _, _ = _matcher(rows)
    assert len(m.match_actions("x", min_score=0.0)) == 1
    assert m.match_actions("x", allow_fallback=False) == []


def test_param_gate_bypass_is_marked_and_does_not_re_embed():
    params = {"LocateExhibit": [{"key": "target_name", "type": "string", "required": True}]}
    m, store, emb = _matcher([_row("LocateExhibit", 0.8)], params)
    matches = m.match_actions("帶我去展區", slots={"destination_hint": "x"})
    assert [x.action.name for x in matches] == ["LocateExhibit"]
    assert matches[0].evidence["param_gate_bypassed"] is True
    assert matches[0].fillable is False
    assert emb.calls == 1 and store.searches == [0.0]


def test_param_gate_passes_when_fillable():
    params = {"LocateExhibit": [{"key": "target_name", "type": "string", "required": True}]}
    m, _, _ = _matcher([_row("LocateExhibit", 0.8)], params)
    matches = m.match_actions("帶我去 A12", slots={"target_name": "A12"})
    assert matches[0].evidence["param_gate_bypassed"] is False
    assert matches[0].bindings == {"target_name": "A12"}
