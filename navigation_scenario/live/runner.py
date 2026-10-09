"""
Navigation Scenario live 模式：以真實 GIAS 流程執行案例（V-01 / V-02）。

與模擬模式（navigation_scenario.runner，純 Dijkstra）不同，每個案例都走完整條管線：

  1. IntentionalAgent.plan_intention(case.request)       ← 真實 LLM + Action KG
  2. execute_plan_with_monitoring(plan)                  ← 監測迴圈，經 MQTT 派工
  3. GuideAgent 在 Blackboard 圖上實際移動並回報結果
  4. 機器人走到注入時機（已走邊數 / baseline 邊數 ≥ injection_pct）時，
     把動態事件寫進 Blackboard；BlackboardAgent 偵測後通知 IntentionalAgent
  5. IntentionalAgent 依事件與結果 retry / repair / replan；GuideAgent 也會就地繞行
  6. 依機器人實際走過的路徑計算指標（evaluation.evaluate_live_case）

前置條件：MQTT broker、Neo4j（actions 已用 navigation_scenario.seed_actions 建立、
blackboard 已用 navigation_scenario.seed_blackboard 建立）、gias.toml 的 LLM 設定。
不需要 run_sensors（除非使用 --via-sensors）。BlackboardAgent、GuideAgent、InfoAgent
由本模組在同一個 process 內啟動；請勿同時執行其他 NavigationAgent，否則會重複接單。

執行：
    python -m navigation_scenario.live.runner single_01_30
    python -m navigation_scenario.live.runner constrained_01_30 --llm-assist --output out.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import threading
import time
from typing import Any, Callable, Protocol

from src.log_helper import init_logging

from navigation_scenario.config import NAVIGATION_PROFILE
from navigation_scenario.metrics import CaseMetrics
from navigation_scenario.pathing import GraphSnapshot, load_snapshot, multi_segment_path, resolve_target_node
from navigation_scenario.runner import CaseResult, StepRecord
from navigation_scenario.test_cases import TestCase

from .evaluation import evaluate_live_case, reached_targets
from .guide_agent import GuideAgent, WalkStep

logger = init_logging()

DEFAULT_SECONDS_PER_METER = 0.1
DEFAULT_CASE_TIMEOUT_SEC = 180.0


# =============================================================================
# IntentionalAgent 端
# =============================================================================
def run_intention(agent: Any, intention: str, *, dispatcher: Callable | None = None) -> dict[str, Any]:
    """規劃並以監測迴圈執行意圖，回傳 {plan, planning_sec, summary, execution_sec}。"""
    t0 = time.monotonic()
    plan = agent.plan_intention(intention)
    t1 = time.monotonic()
    outcome: dict[str, Any] = {"plan": plan, "planning_sec": round(t1 - t0, 3)}
    if plan.get("type") == "leaf_unresolved":
        outcome.update(summary={"ok": False, "message": plan.get("reason")}, execution_sec=0.0)
        return outcome
    summary = agent.execute_plan_with_monitoring(plan, dispatcher=dispatcher)
    outcome.update(summary=summary, execution_sec=round(time.monotonic() - t1, 3))
    return outcome


def live_agent_config(
    base: dict[str, Any],
    *,
    enable_llm_assist: bool,
    node_timeout_sec: float,
    deadline_sec: float,
) -> dict[str, Any]:
    """benchmark 用的 IntentionalAgent 設定：沿用 gias.toml，覆寫監測參數。"""
    cfg = copy.deepcopy(base)
    intent = cfg.setdefault("intent", {})
    monitoring = dict(intent.get("monitoring") or {})
    monitoring.update({
        "enabled": True,
        "poll_interval_sec": 0.1,
        "node_timeout_sec": node_timeout_sec,
        "deadline_sec": deadline_sec,
        "enable_llm_assist": enable_llm_assist,
    })
    intent["monitoring"] = monitoring
    return cfg


class IntentionHandle(Protocol):
    done: threading.Event

    @property
    def outcome(self) -> dict[str, Any]: ...

    def stop(self) -> None: ...


def _make_benchmark_agent_class():
    # 延後 import：IntentionalAgent 會載入 LLM / Neo4j 相依，單元測試不一定需要
    from src.core.intentional_agent import IntentionalAgent

    class BenchmarkIntentionalAgent(IntentionalAgent):
        """on_activate 時執行 run_intention，結果交給 benchmark。"""

        def __init__(self, agent_config, intention: str, *, domain_profile=None):
            self.done = threading.Event()
            self.outcome: dict[str, Any] = {}
            super().__init__(agent_config, intention, domain_profile=domain_profile)

        def on_activate(self):
            try:
                self.outcome = run_intention(self, self.intention)
            except Exception as e:
                logger.exception("Benchmark intention failed")
                self.outcome = {"error": str(e)}
            finally:
                self.done.set()
                self._terminate()

    return BenchmarkIntentionalAgent


class _AgentHandle:
    def __init__(self, agent: Any, *, stop_grace_sec: float):
        self.agent = agent
        self.done = agent.done
        self.stop_grace_sec = stop_grace_sec

    @property
    def outcome(self) -> dict[str, Any]:
        return self.agent.outcome

    def stop(self) -> None:
        # 先讓監測迴圈取消執行中的動作並結束，避免殘留的 agent 影響下一個案例
        self.agent.request_stop()
        if not self.done.wait(self.stop_grace_sec):
            logger.warning("IntentionalAgent did not stop within %.0fs", self.stop_grace_sec)
        try:
            self.agent.terminate()
        except Exception:
            pass


# =============================================================================
# 單一案例
# =============================================================================
def _unreachable_result(case: TestCase, target_nodes: list[str], snap: GraphSnapshot) -> CaseResult:
    blocked = sum(1 for e in snap.edges.values() if e.get("blocked"))
    metrics = CaseMetrics(
        case_id=case.case_id, category=case.category, injection_pct=case.event.injection_pct,
        event_kind=case.event.kind, task_success=False, targets_reached=0,
        targets_total=len(target_nodes), intention_kept=False, baseline_distance=float("inf"),
        actual_distance=0.0, replan_required=False, replan_attempted=False, replan_success=False,
        completion_time_sec=0.0,
        notes=(f"baseline path unreachable from start (targets={target_nodes}; "
               f"blocked_edges={blocked}/{len(snap.edges)}); reset the blackboard first"),
        extra={"mode": "live"},
    )
    return CaseResult(case=case, metrics=metrics)


def _step_records(trajectory: list[WalkStep], baseline_edges: int, injection: dict | None) -> list[StepRecord]:
    records: list[StepRecord] = []
    injected = False
    for i, s in enumerate(trajectory, start=1):
        if injection is not None and not injected and s.at >= injection["at"]:
            records.append(StepRecord(step=len(records) + 1, from_node=s.from_node, to_node=s.from_node,
                                      distance=0.0, progress_pct=injection["progress_pct"],
                                      event=injection, reason="event injected"))
            injected = True
        records.append(StepRecord(
            step=len(records) + 1, from_node=s.from_node, to_node=s.to_node, distance=s.distance,
            progress_pct=round(100.0 * i / max(1, baseline_edges), 1), replanned=s.rerouted,
            reason=f"{s.task} task_id={s.task_id}" + ("; rerouted" if s.rerouted else "")
                   + ("; relaxed avoid_crowded" if s.relaxed else ""),
        ))
    if injection is not None and not injected:
        records.append(StepRecord(step=len(records) + 1, from_node="", to_node="", distance=0.0,
                                  progress_pct=injection["progress_pct"], event=injection,
                                  reason="event injected"))
    return records


def run_case_live(
    case: TestCase,
    *,
    guide: GuideAgent,
    launch: Callable[[TestCase], IntentionHandle],
    apply_event: Callable[[TestCase], dict[str, Any]],
    snapshot_loader: Callable[[], GraphSnapshot],
    timeout_sec: float = DEFAULT_CASE_TIMEOUT_SEC,
) -> CaseResult:
    """以真實 GIAS 流程執行單一案例。

    launch(case) 啟動 IntentionalAgent 並回傳 handle；apply_event(case) 把事件寫進環境。
    兩者可替換（單元測試以記憶體中的替身取代 MQTT / Neo4j / LLM）。
    """
    snap = snapshot_loader()
    target_nodes = [resolve_target_node(snap, t.id, t.kind, start=case.start) for t in case.targets]
    baseline = multi_segment_path(snap, case.start, target_nodes, constraints=list(case.constraints))
    if not baseline.reachable:
        return _unreachable_result(case, target_nodes, snap)
    baseline_edges = max(1, len(baseline.flat_nodes()) - 1)
    start_zone = (snap.nodes.get(case.start) or {}).get("zone")

    state: dict[str, Any] = {"injection": None}
    lock = threading.Lock()

    def on_step(_step: WalkStep) -> None:
        # 在 GuideAgent 決定下一條邊之前注入，時機與模擬模式一致（以邊數計算進度）
        with lock:
            if state["injection"] is not None:
                return
            position, walked, trajectory = guide.snapshot_state()
            progress = 100.0 * len(trajectory) / baseline_edges
            if progress < case.event.injection_pct:
                return
            info = apply_event(case)
            state["injection"] = {
                **info,
                "at": time.monotonic(),
                "robot_at": position,
                "walked_m": walked,
                "progress_pct": round(progress, 1),
                "targets_reached": len(reached_targets(case, start_zone, trajectory)),
            }
        logger.info("Injected %s at %s (progress %.0f%%)", case.event.kind, position, progress)

    guide.reset(case.start)
    guide.step_listener = on_step
    t0 = time.monotonic()
    timed_out = False
    try:
        handle = launch(case)
        if not handle.done.wait(timeout_sec):
            timed_out = True
            handle.stop()
    finally:
        guide.step_listener = None
    wall_sec = round(time.monotonic() - t0, 3)

    _, _, trajectory = guide.snapshot_state()
    with lock:
        injection = state["injection"]
    metrics = evaluate_live_case(
        case,
        target_nodes=target_nodes,
        baseline_plan=baseline,
        start_zone=start_zone,
        trajectory=trajectory,
        outcome=handle.outcome or {},
        injection=injection,
        timed_out=timed_out,
        wall_sec=wall_sec,
        seconds_per_meter=guide.seconds_per_meter,
    )
    return CaseResult(
        case=case,
        metrics=metrics,
        steps=_step_records(trajectory, baseline_edges, injection),
        baseline_path=baseline.flat_nodes(),
        final_path=[case.start] + [s.to_node for s in trajectory],
    )


# =============================================================================
# 真實環境：啟動 BlackboardAgent / GuideAgent / InfoAgent
# =============================================================================
class LiveSession:
    """live 模式共用的執行環境（一個 batch 只啟動一次）。"""

    def __init__(
        self,
        *,
        seconds_per_meter: float = DEFAULT_SECONDS_PER_METER,
        case_timeout_sec: float = DEFAULT_CASE_TIMEOUT_SEC,
        node_timeout_sec: float = 60.0,
        enable_llm_assist: bool = False,
        via_sensors: bool = False,
        start_blackboard_agent: bool = True,
        start_info_agent: bool = True,
        blackboard_poll_sec: float = 0.5,
        connect_wait_sec: float = 3.0,
    ):
        self.seconds_per_meter = seconds_per_meter
        self.case_timeout_sec = case_timeout_sec
        self.node_timeout_sec = node_timeout_sec
        self.enable_llm_assist = enable_llm_assist
        self.via_sensors = via_sensors
        self.start_blackboard_agent = start_blackboard_agent
        self.start_info_agent = start_info_agent
        self.blackboard_poll_sec = blackboard_poll_sec
        self.connect_wait_sec = connect_wait_sec
        self._agents: list[Any] = []
        self._injector = None

    def __enter__(self) -> "LiveSession":
        from src.agents.info_agent import InfoAgent
        from src.app_helper import get_agent_config
        from src.blackboard.agent import BlackboardAgent
        from navigation_scenario.pathing import _bb_adapter

        from .environment import DirectEventApplier, KGPositionWriter, SensorEventApplier

        self.config = get_agent_config()
        self.bb_adapter = _bb_adapter()
        self.agent_class = _make_benchmark_agent_class()

        if self.start_blackboard_agent:
            bb = BlackboardAgent(self.config, poll_interval_sec=self.blackboard_poll_sec)
            bb.start_thread()
            self._agents.append(bb)
        self.guide = GuideAgent(
            self.config,
            snapshot_loader=self.snapshot,
            seconds_per_meter=self.seconds_per_meter,
            position_writer=KGPositionWriter(self.bb_adapter),
        )
        self.guide.start_thread()
        self._agents.append(self.guide)
        if self.start_info_agent:
            info = InfoAgent(self.config)
            info.start_thread()
            self._agents.append(info)
        time.sleep(self.connect_wait_sec)   # 等 broker 連線與 subscribe 完成

        if self.via_sensors:
            from navigation_scenario.event_injector import MqttEventInjector

            self._injector = MqttEventInjector()
            self._injector.connect()
            self.applier = SensorEventApplier(self._injector)
        else:
            self.applier = DirectEventApplier(self.bb_adapter)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._injector is not None:
            try:
                self._injector.disconnect()
            except Exception:
                pass
        for agent in reversed(self._agents):
            try:
                agent.terminate()
            except Exception:
                pass

    def snapshot(self) -> GraphSnapshot:
        return load_snapshot(self.bb_adapter)

    def _launch(self, case: TestCase) -> IntentionHandle:
        cfg = live_agent_config(
            self.config,
            enable_llm_assist=self.enable_llm_assist,
            node_timeout_sec=self.node_timeout_sec,
            deadline_sec=self.case_timeout_sec,
        )
        agent = self.agent_class(cfg, case.request, domain_profile=NAVIGATION_PROFILE)
        agent.start_thread()
        return _AgentHandle(agent, stop_grace_sec=90.0)

    def run_case(self, case: TestCase) -> CaseResult:
        return run_case_live(
            case,
            guide=self.guide,
            launch=self._launch,
            apply_event=self.applier.apply,
            snapshot_loader=self.snapshot,
            timeout_sec=self.case_timeout_sec,
        )


# =============================================================================
# CLI
# =============================================================================
def add_live_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seconds-per-meter", type=float, default=DEFAULT_SECONDS_PER_METER,
                        help="機器人每公尺耗時（秒），預設 0.1（10 公尺的邊走 1 秒）")
    parser.add_argument("--case-timeout", type=float, default=DEFAULT_CASE_TIMEOUT_SEC,
                        help="單一案例逾時（秒，含 LLM 規劃）")
    parser.add_argument("--node-timeout", type=float, default=60.0, help="單一動作逾時（秒）")
    parser.add_argument("--llm-assist", action="store_true", help="啟用 IntentionalAgent 的 LLM 輔助判斷")
    parser.add_argument("--via-sensors", action="store_true",
                        help="經 run_sensors 的感測器注入事件（預設直接寫入 Blackboard）")
    parser.add_argument("--no-blackboard-agent", action="store_true",
                        help="不在本 process 啟動 BlackboardAgent（已在別處執行時）")
    parser.add_argument("--no-info-agent", action="store_true",
                        help="不在本 process 啟動 InfoAgent（已在別處執行時）")


def session_from_args(args: argparse.Namespace) -> LiveSession:
    return LiveSession(
        seconds_per_meter=args.seconds_per_meter,
        case_timeout_sec=args.case_timeout,
        node_timeout_sec=args.node_timeout,
        enable_llm_assist=args.llm_assist,
        via_sensors=args.via_sensors,
        start_blackboard_agent=not args.no_blackboard_agent,
        start_info_agent=not args.no_info_agent,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="以真實 GIAS 流程執行單一 Navigation Scenario 案例")
    parser.add_argument("case_id", help="例：single_01_30 / constrained_03_60 / multistep_02_30")
    parser.add_argument("--output", type=str, default=None, help="輸出 JSON 路徑")
    parser.add_argument("--no-reset", action="store_true", help="執行前不還原 Blackboard baseline")
    parser.add_argument("--log-level", choices=["VERBOSE", "DEBUG", "INFO", "WARNING", "ERROR"], default=None)
    add_live_arguments(parser)
    args = parser.parse_args()
    if args.log_level:
        os.environ["LOG_LEVEL"] = args.log_level

    from navigation_scenario.kg_reset import restore_navigation_baseline
    from navigation_scenario.test_cases import case_by_id

    case = case_by_id(args.case_id)
    if case is None:
        print(f"找不到案例：{args.case_id}")
        return 1
    if not args.no_reset:
        restore_navigation_baseline()

    print(f"=== [live] {case.case_id} ({case.category}) ===")
    print(f"  request : {case.request}")
    print(f"  event   : {case.event.kind} @ {case.event.injection_pct}% target={case.event.target}")
    with session_from_args(args) as session:
        result = session.run_case(case)

    m = result.metrics
    print("=== 結果 ===")
    print(f"  task_success   : {m.task_success}  targets {m.targets_reached}/{m.targets_total}")
    print(f"  intention_kept : {m.intention_kept}")
    print(f"  PE             : {m.path_efficiency:.3f}  ({m.baseline_distance:.0f} m → {m.actual_distance:.0f} m)")
    print(f"  replan         : attempted={m.replan_attempted} success={m.replan_success} "
          f"(IA replans={m.extra.get('ia_replans')}, redispatches={m.extra.get('ia_redispatches')}, "
          f"executor reroutes={m.extra.get('executor_reroutes_after_injection')})")
    print(f"  plan           : {[s['task'] for s in m.extra.get('plan_steps', [])]}")
    print(f"  time           : {m.completion_time_sec:.1f} s (planning {m.extra.get('planning_sec')} s)")
    if m.notes:
        print(f"  notes          : {m.notes}")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        print(f"\n結果已寫入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
