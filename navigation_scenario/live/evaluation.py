"""
live 模式的指標計算：從機器人實際走過的 trajectory 與 IntentionalAgent 的執行結果算出 CaseMetrics。

與模擬模式（runner.py）使用相同的 CaseMetrics，差別在資料來源：
- 到達目標、距離、路徑效率：來自 GuideAgent 實際走過的邊
- 意圖維持（ISR）：任務成功，且途中沒有違反案例的限制（例：避開擁擠卻走進 Crowded 區）
- 重規劃：事件注入後，IntentionalAgent 做了 retry / repair / replan，或 GuideAgent 繞行
- 完成時間：真實經過的時間（含 LLM 規劃）；不同 seconds_per_meter 之間不可直接比較
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from navigation_scenario.metrics import CaseMetrics
from navigation_scenario.pathing import MultiSegmentPath
from navigation_scenario.test_cases import Target, TestCase

from .guide_agent import WalkStep, avoid_states_for


def _matches(target: Target, node: str, zone: str | None) -> bool:
    if target.kind == "zone":
        return zone == target.id
    return node == target.id


def reached_targets(case: TestCase, start_zone: str | None, trajectory: list[WalkStep]) -> list[int]:
    """依序比對目標，回傳每個目標在 trajectory 中被到達的位置（-1 表示起點即是目標）。"""
    visits = [(case.start, start_zone)] + [(s.to_node, s.zone) for s in trajectory]
    hits: list[int] = []
    pos = 0
    for target in case.targets:
        found = next((i for i in range(pos, len(visits)) if _matches(target, *visits[i])), None)
        if found is None:
            break
        hits.append(found - 1)
        pos = found
    return hits


def constraint_violations(case: TestCase, trajectory: list[WalkStep]) -> list[dict[str, Any]]:
    """經過了案例要求避開的區域（目標所在區域除外）。"""
    avoid = avoid_states_for(case.constraints)
    if not avoid:
        return []
    target_zones = {t.id for t in case.targets if t.kind == "zone"}
    target_nodes = {t.id for t in case.targets if t.kind != "zone"}
    return [
        {"to_node": s.to_node, "zone": s.zone, "zone_state": s.zone_state}
        for s in trajectory
        if s.zone_state in avoid and s.zone not in target_zones and s.to_node not in target_nodes
    ]


def ia_adaptations(summary: dict[str, Any]) -> dict[str, int]:
    """IntentionalAgent 層的調適次數：子樹 / 根層重規劃，以及節點重派（retry、repair）。"""
    replans = len(summary.get("replan_log") or [])
    redispatches = sum(max(0, int(r.get("attempts") or 0) - 1) for r in summary.get("results") or [])
    return {"replans": replans, "redispatches": redispatches}


def plan_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """把 plan 樹攤平成 atomic 步驟摘要（id、task、params）。"""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "atomic" or node.get("is_atomic") is True:
            out.append({"id": node.get("id"), "task": node.get("task"), "params": node.get("params") or {}})
            return
        for child in node.get("sub_plans") or []:
            walk(child)

    walk(plan)
    return out


def evaluate_live_case(
    case: TestCase,
    *,
    target_nodes: list[str],
    baseline_plan: MultiSegmentPath,
    start_zone: str | None,
    trajectory: list[WalkStep],
    outcome: dict[str, Any],
    injection: dict[str, Any] | None,
    timed_out: bool,
    wall_sec: float,
    seconds_per_meter: float,
) -> CaseMetrics:
    hits = reached_targets(case, start_zone, trajectory)
    task_success = len(hits) == len(case.targets) and not timed_out
    violations = constraint_violations(case, trajectory)

    # 每段實際距離：以到達各目標的位置切分；最後一個目標之後多走的距離算進最後一段
    seg_actuals = [0.0] * max(1, len(case.targets))
    last = len(seg_actuals) - 1
    seg = 0
    while seg < min(len(hits), last) and hits[seg] == -1:   # 起點就是目標
        seg += 1
    for i, step in enumerate(trajectory):
        seg_actuals[seg] += step.distance
        while seg < min(len(hits), last) and hits[seg] == i:
            seg += 1
    seg_baselines = [s.distance if s.reachable else 0.0 for s in baseline_plan.segments]
    actual = sum(s.distance for s in trajectory)

    summary = outcome.get("summary") or {}
    adapt = ia_adaptations(summary)
    injected = injection is not None
    inject_at = injection.get("at") if injected else None
    reroutes_after = sum(1 for s in trajectory if s.rerouted and inject_at is not None and s.at >= inject_at)
    remaining_at_injection = len(case.targets) - int(injection.get("targets_reached", 0)) if injected else 0
    replan_attempted = injected and (adapt["replans"] + adapt["redispatches"] + reroutes_after) > 0

    plan = outcome.get("plan") or {}
    notes: list[str] = []
    if plan.get("type") == "leaf_unresolved":
        notes.append(f"plan unresolved: {plan.get('reason')}")
    if outcome.get("error"):
        notes.append(f"agent error: {outcome['error']}")
    if timed_out:
        notes.append("timeout")
    if violations:
        notes.append(f"constraint violated at {[v['to_node'] for v in violations]}")
    failed_steps = [r for r in summary.get("results") or [] if r.get("state") == "failed"]
    for r in failed_steps:
        notes.append(f"step {r.get('id')} {r.get('task')} failed: {r.get('error')}")
    if not task_success and not notes:
        notes.append(f"targets reached {len(hits)}/{len(case.targets)}")

    return CaseMetrics(
        case_id=case.case_id,
        category=case.category,
        injection_pct=case.event.injection_pct,
        event_kind=case.event.kind,
        task_success=task_success,
        targets_reached=len(hits),
        targets_total=len(case.targets),
        intention_kept=task_success and not violations,
        baseline_distance=baseline_plan.total_distance,
        actual_distance=actual if actual > 0 else float("inf"),
        segment_baselines=seg_baselines,
        segment_actuals=seg_actuals,
        replan_required=bool(case.expected_replan and injected and remaining_at_injection > 0),
        replan_attempted=replan_attempted,
        replan_success=task_success if replan_attempted else False,
        completion_time_sec=wall_sec,
        timed_out=timed_out,
        notes="; ".join(notes),
        extra={
            "mode": "live",
            "target_nodes": target_nodes,
            "constraints": list(case.constraints),
            "event": asdict(case.event),
            "injection": injection,
            "plan_steps": plan_steps(plan),
            "plan_type": plan.get("type"),
            "planning_sec": outcome.get("planning_sec"),
            "execution_sec": outcome.get("execution_sec"),
            "ia_ok": bool(summary.get("ok")),
            "ia_message": summary.get("message"),
            "ia_state_counts": summary.get("state_counts"),
            "ia_replans": adapt["replans"],
            "ia_redispatches": adapt["redispatches"],
            "replan_log": [
                {k: v for k, v in entry.items() if k != "at"} for entry in summary.get("replan_log") or []
            ],
            "executor_reroutes_after_injection": reroutes_after,
            "constraint_violations": violations,
            "walk_time_sec": round(actual * seconds_per_meter, 3),
        },
    )
