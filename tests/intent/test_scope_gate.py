"""ScopeGate 單元測試（C-03）：失敗或格式不符時必須拋錯，由呼叫端決定 strict 行為。"""

from __future__ import annotations

import logging

import pytest

from src.core.intent.scope_gate import ScopeGate, ScopeGateError


class _FakeLLM:
    def __init__(self, reply=None, exc=None):
        self.reply = reply
        self.exc = exc

    def json(self, messages, schema=None):
        if self.exc is not None:
            raise self.exc
        return self.reply


def _decide(llm):
    gate = ScopeGate(llm=llm, logger=logging.getLogger("test"))
    return gate.decide(user_intent="帶我去 A12", available_actions=[{"name": "LocateExhibit", "description": "導航"}])


def test_valid_reply_is_parsed():
    d = _decide(_FakeLLM({"can_execute": True, "reason": "ok"}))
    assert d.can_execute is True
    assert d.reason == "ok"


@pytest.mark.parametrize("value, expected", [("false", False), ("True", True), (0, False), (1, True)])
def test_can_execute_is_parsed_strictly(value, expected):
    # 舊行為：bool("false") 為 True，會把拒絕誤判成放行
    assert _decide(_FakeLLM({"can_execute": value, "reason": "r"})).can_execute is expected


def test_llm_failure_raises_instead_of_allowing():
    # 舊行為：吞掉例外並回傳 can_execute=True，使 scope_gate_strict 失效
    with pytest.raises(ScopeGateError):
        _decide(_FakeLLM(exc=RuntimeError("timeout")))


@pytest.mark.parametrize("reply", [["not", "a", "dict"], {"reason": "missing flag"}, {"can_execute": "maybe"}])
def test_malformed_reply_raises(reply):
    with pytest.raises(ScopeGateError):
        _decide(_FakeLLM(reply))
