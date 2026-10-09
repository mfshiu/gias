"""live 模式封閉式端到端測試（V-01 / V-02）。

使用真實的 IntentionalAgent 監測迴圈（trigger / repair / cancel / 重規劃脈絡）、
真實的 GuideAgent 與 evaluation；只把外部服務換成記憶體替身：
- MQTT：dispatcher 直接呼叫 GuideAgent._handle，結果直接送進 ExecutionMonitor
- Neo4j Blackboard：記憶體中的 GraphSnapshot；事件注入時改寫快照，並以 BlackboardWatcher 的格式通知訂閱者
- LLM 規劃：plan_intention 以預先準備的計畫取代（記錄重規劃時收到的 context）
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.app_helper import get_agent_config
from src.core.intentional_agent import IntentionalAgent
from navigation_scenario.config import NAVIGATION_PROFILE
from navigation_scenario.live.guide_agent import GuideAgent
from navigation_scenario.live.runner import live_agent_config, run_case_live, run_intention
from navigation_scenario.pathing import snapshot_from_config
from navigation_scenario.test_cases import case_by_id

_EMPTY_BROKER = {"broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}}}


class _Parcel:
    def __init__(self, content):
        self.content = content


def _base_config() -> dict:
    try:
        cfg = get_agent_config()
        if cfg.get("llm") and cfg.get("broker"):
            return cfg
    except Exception:
        pass
    return {
        "llm": {"provider": "mock"},
        "broker": {"broker_name": "mqtt01", "mqtt01": {"broker_type": "empty"}},
        "kg": {"type": "neo4j", "neo4j": {"uri": "bolt://localhost:7687"}, "neo4j_actions": {}},
    }


def _plan(*steps) -> dict:
    nodes = [
        {"id": str(i), "type": "atomic", "is_atomic": True, "intent": task, "task": task,
         "topic": "navigation.request", "action": f"{task}()", "params": params, "sub_plans": []}
        for i, (task, params) in enumerate(steps, start=1)
    ]
    seq = [{"type": "Sequence", "from_id": str(i), "to_id": str(i + 1)} for i in range(1, len(nodes))]
    return {"id": "root", "type": "composite", "intent": "navigation request", "sub_plans": nodes,
            "execution_logic": seq}


class _World:
    """記憶體中的 Blackboard + GuideAgent + 會「規劃」出預備計畫的 IntentionalAgent。"""

    def __init__(self, plans: list[dict], *, seconds_per_meter: float = 0.02):
        self.snap = snapshot_from_config()
        self.guide = GuideAgent(_EMPTY_BROKER, snapshot_loader=lambda: self.snap,
                                seconds_per_meter=seconds_per_meter)
        self.guide.publish = self._guide_publish
        self.plans = list(plans)
        self.contexts: list[dict | None] = []
        self.handlers: dict[str, object] = {}
        self.ia: IntentionalAgent | None = None
        self._patches = [
            patch("src.core.intentional_agent.subscribe_blackboard", lambda *a, **k: "sub-1"),
            patch("src.core.intentional_agent.unsubscribe_blackboard", lambda *a, **k: True),
            patch("src.core.intentional_agent.try_query_blackboard", self._query),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()

    # ---- Blackboard 替身 ----
    def _query(self, agent, cypher, params=None, **kw):
        if "CURRENT_STATE" in cypher:
            return [{"zone": z, "state": s} for z, s in self.snap.zone_states.items() if s in ("Crowded", "Closed")]
        if "CONNECTED_TO" in cypher:
            return [{"from": a, "to": b} for (a, b), e in self.snap.edges.items() if e.get("blocked")]
        if "CURRENT_POSITION" in cypher:
            return [{"node": self.guide.position}]
        return []

    def apply_event(self, case) -> dict:
        ev = case.event
        if ev.kind in ("crowd_congestion", "area_closure"):
            state = "Crowded" if ev.kind == "crowd_congestion" else "Closed"
            self.snap.zone_states[ev.target] = state
            # BlackboardWatcher 對 CURRENT_STATE 關係改變送出的事件
            self._emit({
                "topic": "Zone/CURRENT_STATE/State", "action": "create",
                "new_value": {"source_id": ev.target, "target_id": state},
                "metadata": {"source_label": "Zone", "source_id": ev.target, "rel_type": "CURRENT_STATE",
                             "target_label": "State", "target_id": state},
            })
        else:   # route_detour：watcher 不監看關係屬性，因此不會有事件
            for a, b in ((ev.target["from"], ev.target["to"]), (ev.target["to"], ev.target["from"])):
                self.snap.edges[(a, b)]["blocked"] = True
        return {"method": "memory", "kind": ev.kind, "target": ev.target}

    def _emit(self, event: dict) -> None:
        for topic, handler in list(self.handlers.items()):
            if topic.startswith("blackboard.subscriber."):
                handler(topic, event)

    # ---- MQTT 替身 ----
    def _guide_publish(self, topic, data):
        if topic.endswith(".result") and self.ia is not None and self.ia._monitor is not None:
            self.ia._monitor.on_action_result(topic, data)

    def _dispatch(self, node, payload):
        if node.get("_cancel"):
            self.guide._handle_cancel("navigation.cancel", _Parcel(payload))
            return
        threading.Thread(target=self.guide._handle, args=("navigation.request", _Parcel(payload)),
                         daemon=True).start()

    # ---- IntentionalAgent ----
    def _plan_intention(self, intention, *, context=None):
        self.contexts.append(context)
        return self.plans.pop(0) if self.plans else {"type": "leaf_unresolved", "reason": "no more plans"}

    def launch(self, case):
        cfg = live_agent_config(_base_config(), enable_llm_assist=False, node_timeout_sec=10, deadline_sec=20)
        cfg["intent"]["monitoring"].update({"max_replans": 2, "max_retries_per_node": 1})
        with patch("src.core.intentional_agent.Neo4jBoltAdapter") as adapter_cls:
            adapter_cls.from_config.return_value = MagicMock()
            ia = IntentionalAgent(cfg, case.request, domain_profile=NAVIGATION_PROFILE)
        ia.subscribe = lambda topic, data_type="str", topic_handler=None: self.handlers.__setitem__(topic, topic_handler)
        ia.plan_intention = self._plan_intention
        self.ia = ia
        return _ThreadHandle(ia, lambda: run_intention(ia, case.request, dispatcher=self._dispatch))

    def run(self, case_id: str, *, timeout_sec: float = 20.0):
        return run_case_live(case_by_id(case_id), guide=self.guide, launch=self.launch,
                             apply_event=self.apply_event, snapshot_loader=lambda: self.snap,
                             timeout_sec=timeout_sec)


class _ThreadHandle:
    def __init__(self, ia, target):
        self.ia = ia
        self.done = threading.Event()
        self._outcome: dict = {}

        def run():
            try:
                self._outcome = target()
            finally:
                self.done.set()

        threading.Thread(target=run, daemon=True).start()

    @property
    def outcome(self):
        return self._outcome

    def stop(self):
        self.ia.request_stop()
        self.done.wait(5)


# ----------------------------------------------------------------------
# 情境
# ----------------------------------------------------------------------
def test_crowd_event_reaches_intentional_agent_and_triggers_replan():
    """限制式案例：前往 AI 展區途中該區變擁擠 → 事件經監測迴圈觸發重規劃，機器人從目前位置繼續。"""
    plans = [
        _plan(("LocateExhibit", {"target_name": "AI_Tech_Area", "target_type": "exhibit_zone"})),
        _plan(("SuggestRoute", {"destination": "AI_Tech_Area", "avoid_crowded": True})),
    ]
    with _World(plans) as world:
        result = world.run("constrained_01_30")
    m = result.metrics

    assert m.task_success is True
    assert m.intention_kept is True
    assert m.extra["ia_replans"] == 1
    assert m.replan_required is True and m.replan_attempted is True and m.replan_success is True
    assert m.extra["injection"]["progress_pct"] == 33.3
    # 重規劃時 LLM 收到的脈絡：環境事實與重規劃原因
    ctx = world.contexts[1]
    assert {"zone": "AI_Tech_Area", "state": "Crowded"} in ctx["env_facts"]["zone_states"]
    assert ctx["replan"]["kind"] == "replan_subtree"
    assert ctx["replan"]["affected_steps"][0]["task"] == "LocateExhibit"
    # 機器人沒有回到起點重走
    assert result.final_path.count("P_Entrance") == 1
    assert result.final_path[-1] == "B_AI1"
    assert [s["task"] for s in m.extra["plan_steps"]] == ["LocateExhibit"]


def test_blocked_passage_is_handled_by_executor_reroute():
    """改道案例：通道封鎖不會產生黑板事件，由 GuideAgent 就地繞行。"""
    with _World([_plan(("LocateExhibit", {"target_name": "B_GM1", "target_type": "booth"}))]) as world:
        result = world.run("constrained_06_30")
    m = result.metrics

    assert m.task_success is True
    assert m.extra["ia_replans"] == 0
    assert m.extra["executor_reroutes_after_injection"] >= 1
    assert result.final_path == ["P_Entrance", "P_Info", "B_SU1", "P_South_Hub", "B_GM1"]
    assert round(m.path_efficiency, 3) == round(48 / 68, 3)


def test_executor_failure_escalates_to_retry_then_replan():
    """LLM 給錯目的地：GuideAgent 回報失敗 → IA 重派一次 → 仍失敗 → 帶著錯誤訊息重規劃 → 成功。"""
    plans = [
        _plan(("LocateExhibit", {"target_name": "不存在的攤位", "target_type": "booth"})),
        _plan(("LocateExhibit", {"target_name": "TechCorp AI", "target_type": "booth"})),
    ]
    with _World(plans) as world:
        result = world.run("single_07_30")
    m = result.metrics

    assert m.task_success is True
    assert m.extra["ia_redispatches"] >= 1
    assert m.extra["ia_replans"] == 1
    assert "cannot resolve destination" in world.contexts[1]["replan"]["affected_steps"][0]["error"]
    assert result.final_path[-1] == "B_AI1"


def test_case_timeout_stops_intentional_agent_and_robot():
    """逾時時要求 IntentionalAgent 停止：取消執行中的導航，機器人停下，不影響下一個案例。"""
    # 全程 48 m × 0.05 s/m ≈ 2.4 秒；0.3 秒就逾時
    with _World([_plan(("LocateExhibit", {"target_name": "B_GM1"}))], seconds_per_meter=0.05) as world:
        t0 = time.monotonic()
        result = world.run("constrained_06_30", timeout_sec=0.3)
        assert time.monotonic() - t0 < 2.0          # 停止要求很快生效
        time.sleep(2.5)                              # 若沒停下，這段時間足以走到 B_GM1
        assert world.guide.position != "B_GM1"
        assert len(world.guide.snapshot_state()[2]) <= 1
    m = result.metrics

    assert m.timed_out is True
    assert m.task_success is False
    assert "timeout" in m.notes


def test_batch_runner_live_mode_uses_session_and_labels_report(tmp_path):
    from navigation_scenario.analyze import analyze_run
    from navigation_scenario.batch_runner import run_batch

    class _Session:
        def __init__(self):
            self.cases = []

        def run_case(self, case):
            self.cases.append(case.case_id)
            with _World([_plan(("LocateExhibit", {"target_name": "B_GM1"}))], seconds_per_meter=0.0) as world:
                return world.run(case.case_id)

    session = _Session()
    with patch("navigation_scenario.batch_runner.MqttEventInjector",
               side_effect=AssertionError("live mode must not use the MQTT injector")):
        run_dir = run_batch([case_by_id("constrained_06_30")], output_dir=tmp_path, live_session=session,
                            reset_before_batch=False, reset_between_cases=False, inter_case_pause_sec=0.0)
    assert session.cases == ["constrained_06_30"]
    import json
    assert json.loads((run_dir / "_manifest.json").read_text(encoding="utf-8"))["mode"] == "live"
    report = analyze_run(run_dir)["report_md"].read_text(encoding="utf-8")
    assert "**Mode**：live" in report
