"""日誌統一控制（E-04）：模組 logger 受 LOG_LEVEL 控制、執行期模組不直接 print。"""

from __future__ import annotations

import io
import logging

import pytest

from src.log_helper import APP_PACKAGES, init_logging

_NAMES = ("gias", *APP_PACKAGES)


@pytest.fixture
def restore_logging():
    saved = {n: (logging.getLogger(n).level, list(logging.getLogger(n).handlers), logging.getLogger(n).propagate,
                 list(logging.getLogger(n).filters)) for n in _NAMES}
    yield
    for n, (level, handlers, propagate, filters) in saved.items():
        lg = logging.getLogger(n)
        lg.setLevel(level)
        lg.handlers[:] = handlers
        lg.propagate = propagate
        lg.filters[:] = filters


def test_module_loggers_follow_log_level(monkeypatch, restore_logging):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    init_logging(pytest_mode=False)
    stream = io.StringIO()
    handler = logging.getLogger("gias").handlers[0]
    handler.setStream(stream)
    for name in ("src.kg.action_store", "navigation_scenario.live.runner"):
        module_logger = logging.getLogger(name)
        assert module_logger.getEffectiveLevel() == logging.INFO
        module_logger.debug("hidden-debug")
        module_logger.info("shown-info api_key=sk-under-test")
    out = stream.getvalue()
    assert "shown-info" in out and "hidden-debug" not in out
    assert "sk-under-test" not in out                                # 機密遮罩也套用到模組 logger
    assert all(logging.getLogger(n).handlers == [handler] for n in APP_PACKAGES)


def test_init_logging_is_idempotent(monkeypatch, restore_logging):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    init_logging(pytest_mode=False)
    init_logging(pytest_mode=False)
    assert [len(logging.getLogger(n).handlers) for n in _NAMES] == [1, 1, 1]
    assert logging.getLogger("src.anything").getEffectiveLevel() == logging.WARNING


def test_pytest_mode_hands_module_loggers_back_to_root(monkeypatch, restore_logging):
    """收集測試時模組以非 pytest 模式初始化過；進入 pytest 模式後要交回 root，caplog 才收得到。"""
    init_logging(pytest_mode=False)
    init_logging(pytest_mode=True)
    for name in APP_PACKAGES:
        lg = logging.getLogger(name)
        assert (lg.propagate, lg.handlers, lg.level) == (True, [], logging.NOTSET)


def test_executors_do_not_print(capsys, caplog):
    from src.agents import info_agent, navigation_agent

    with caplog.at_level(logging.INFO):
        assert info_agent._simulate_progress(["查詢資料", "整理結果"], delay=0.0)
        assert navigation_agent._simulate_progress(["定位起點"], delay=0.0)
    assert capsys.readouterr().out == ""
    assert "InfoAgent 2/2 整理結果" in caplog.text and "NavigationAgent 1/1 定位起點" in caplog.text


def test_planner_reports_the_decomposition_through_its_logger(capsys, caplog):
    from src.core.intent.planner import RecursivePlanner

    class Decomposer:
        def decompose(self, intent, available_actions, context=None):
            return {"sub_intents": [{"id": "1", "intent": "推薦展品", "is_atomic": True,
                                     "atomic_source": "pre_defined", "action": "RecommendExhibits()"}]}

    planner_logger = logging.getLogger("gias.test.planner")
    with caplog.at_level(logging.INFO, logger="gias.test.planner"):
        RecursivePlanner(decomposer=Decomposer(), logger=planner_logger).plan("帶我逛", {})
    assert capsys.readouterr().out == ""
    assert "處理意圖: 帶我逛" in caplog.text and "[EXEC] 推薦展品" in caplog.text
