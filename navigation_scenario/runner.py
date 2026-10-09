"""
Navigation Scenario：單一案例執行器

執行流程（simulation-based）：

  1. 抓 Blackboard KG 快照 → 算 baseline 最短路徑（含 constraint）
  2. 沿著 baseline 路徑「逐步前進」（每節點視為一次 step）
  3. 走到 injection_pct% 進度時 → MQTT 發送 inject_event → 短暫等待，
     再抓一份新快照
  4. 從目前節點到剩餘目標，用新快照重算路徑
     - 若仍可達：採用新路徑繼續走（replan 成功）
     - 若不可達：拿掉該案例的軟限制（avoid_crowded）再試一次（保守 fallback）
     - 若仍不可達：任務失敗
  5. 收集 CaseMetrics 與 step trace，回傳結果
  6. 結束時自動發送對應的「復原事件」（route_clear / area_reopen / crowd_clear）
     以避免污染下一個案例

注意：本模組以「圖論模擬」為主，不真的呼叫 IntentionalAgent 規劃；
這是因為要產出可重現、可量化、可在無 LLM 環境下執行的結果。
以真實 GIAS 流程（LLM 規劃 + 監測迴圈 + 機器人實際移動）執行請用
`navigation_scenario.live.runner`，或 `batch_runner --live`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from src.log_helper import init_logging

from navigation_scenario.config import (
    EVENT_KIND_CROWD,
    EVENT_KIND_CLOSURE,
    EVENT_KIND_DETOUR,
    REPLAN_OVERHEAD_SEC,
    WALK_SPEED_MPS,
)
from navigation_scenario.event_injector import MqttEventInjector
from navigation_scenario.metrics import CaseMetrics
from navigation_scenario.pathing import (
    GraphSnapshot,
    MultiSegmentPath,
    PathResult,
    load_snapshot,
    multi_segment_path,
    resolve_target_node,
    walk_time_seconds,
)
from navigation_scenario.test_cases import TestCase

logger = init_logging()


# 注入事件 → 對應的「清除事件」kind（測試結束時還原 KG）
_RECOVERY_KIND: dict[str, str] = {
    EVENT_KIND_CROWD: "crowd_clear",
    EVENT_KIND_CLOSURE: "area_reopen",
    EVENT_KIND_DETOUR: "route_clear",
}


@dataclass
class StepRecord:
    step: int
    from_node: str
    to_node: str
    distance: float
    progress_pct: float
    event: dict[str, Any] | None = None
    replanned: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseResult:
    case: TestCase
    metrics: CaseMetrics
    steps: list[StepRecord] = field(default_factory=list)
    baseline_path: list[str] = field(default_factory=list)
    final_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "metrics": self.metrics.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "baseline_path": self.baseline_path,
            "final_path": self.final_path,
        }


# =============================================================================
# 核心執行邏輯
# =============================================================================
def _resolve_targets(snap: GraphSnapshot, case: TestCase) -> list[str]:
    return [
        resolve_target_node(snap, t.id, t.kind, start=case.start)
        for t in case.targets
    ]


def _compute_progress_pct(step_index: int, total_steps: int) -> float:
    if total_steps <= 0:
        return 100.0
    return min(100.0, 100.0 * step_index / total_steps)


def _remaining_targets(targets: list[str], reached_idx: int) -> list[str]:
    return targets[reached_idx:]


def _inject_via_mqtt(
    injector: MqttEventInjector,
    case: TestCase,
) -> dict[str, Any]:
    """根據案例事件型別呼叫對應的注入方法。"""
    ev = case.event
    if ev.kind == EVENT_KIND_CROWD:
        return injector.inject_crowd_congestion(ev.target, ttl_samples=ev.ttl_samples)
    if ev.kind == EVENT_KIND_CLOSURE:
        return injector.inject_area_closure(ev.target, ttl_samples=ev.ttl_samples)
    if ev.kind == EVENT_KIND_DETOUR:
        t = ev.target
        if isinstance(t, dict):
            return injector.inject_route_detour(
                t.get("from"),
                t.get("to"),
                obstacle_type=(ev.payload or {}).get("obstacle_type", "barrier"),
                ttl_samples=ev.ttl_samples,
            )
    return injector.inject_event(
        ev.kind, ev.target, ttl_samples=ev.ttl_samples, payload=ev.payload
    )


def _recovery_inject(injector: MqttEventInjector, case: TestCase) -> None:
    """測試結束後送出對應的清除事件，避免污染下一個案例。"""
    ev = case.event
    rec_kind = _RECOVERY_KIND.get(ev.kind)
    if not rec_kind:
        return
    try:
        if ev.kind == EVENT_KIND_DETOUR and isinstance(ev.target, dict):
            injector.inject_route_clear(
                ev.target.get("from"), ev.target.get("to"), ttl_samples=2
            )
        else:
            injector.inject_event(rec_kind, ev.target, ttl_samples=2)
    except Exception as e:
        logger.warning("recovery inject failed: %s", e)


def _attempt_replan(
    snap: GraphSnapshot,
    cursor: str,
    remaining_targets: list[str],
    case: TestCase,
) -> tuple[MultiSegmentPath, list[str]]:
    """
    嘗試重新規劃：先用原本 constraints；若不可達就漸進放鬆軟限制。
    回傳 (路徑, 實際使用的 constraints)。
    """
    base_constraints = list(case.constraints)
    plan = multi_segment_path(snap, cursor, remaining_targets, constraints=base_constraints)
    if plan.reachable:
        return plan, base_constraints

    # 放鬆 avoid_crowded（軟限制）
    relaxed = [c for c in base_constraints if c != "avoid_crowded"]
    if relaxed != base_constraints:
        plan2 = multi_segment_path(snap, cursor, remaining_targets, constraints=relaxed)
        if plan2.reachable:
            return plan2, relaxed

    # 再放鬆 avoid_blocked → 不行就算了
    relaxed2 = [c for c in relaxed if c != "avoid_blocked"]
    if relaxed2 != relaxed:
        plan3 = multi_segment_path(snap, cursor, remaining_targets, constraints=relaxed2)
        if plan3.reachable:
            return plan3, relaxed2

    return plan, base_constraints  # 不可達


def run_case(
    case: TestCase,
    *,
    injector: Optional[MqttEventInjector] = None,
    snapshot_wait_sec: float = 2.0,
    timeout_sec: float = 60.0,
    walk_speed_mps: float = WALK_SPEED_MPS,
    auto_recover: bool = True,
    replan_failure_probability: float = 0.0,
    seed: int | None = None,
) -> CaseResult:
    """執行單一案例並回傳結果。

    Args:
        injector: 已連線的 `MqttEventInjector`；若 None 會自動建立並關閉。
        snapshot_wait_sec: 注入後等多久再讀 KG，讓感測器把新狀態寫進去。
        timeout_sec: 總體超時。
        walk_speed_mps: 模擬步行速度（m/s）。
        auto_recover: 結束時是否自動送 recovery event 清除影響。
        replan_failure_probability: 模擬 LLM/通訊不可靠性的 replan 失敗機率（0-1）。
            設定 >0 時，即使 Dijkstra 找到可達路徑，也以此機率視為 replan 失敗。
            用於對齊「真實 GIAS agent」的 RSR 表現。
        seed: 隨機種子（控制 replan_failure_probability 的可重現性）；
            若 None 則用 case_id hash 自動派生。
    """
    own_injector = injector is None
    if injector is None:
        injector = MqttEventInjector()
        injector.connect()

    if seed is None:
        seed = int(hashlib.md5(case.case_id.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)

    try:
        return _run_case_impl(
            case,
            injector=injector,
            snapshot_wait_sec=snapshot_wait_sec,
            timeout_sec=timeout_sec,
            walk_speed_mps=walk_speed_mps,
            auto_recover=auto_recover,
            replan_failure_probability=replan_failure_probability,
            rng=rng,
        )
    finally:
        if own_injector:
            try:
                injector.disconnect()
            except Exception:
                pass


def _run_case_impl(
    case: TestCase,
    *,
    injector: MqttEventInjector,
    snapshot_wait_sec: float,
    timeout_sec: float,
    walk_speed_mps: float,
    auto_recover: bool,
    replan_failure_probability: float = 0.0,
    rng: random.Random | None = None,
) -> CaseResult:
    if rng is None:
        rng = random.Random()
    t_start = time.monotonic()
    steps: list[StepRecord] = []
    notes: list[str] = []

    # ----- 1. baseline 路徑 -----
    snap = load_snapshot()
    target_nodes = _resolve_targets(snap, case)
    cursor = case.start
    baseline_plan = multi_segment_path(
        snap, cursor, target_nodes, constraints=list(case.constraints)
    )
    baseline_nodes = baseline_plan.flat_nodes()
    baseline_distance = (
        baseline_plan.total_distance if baseline_plan.reachable else float("inf")
    )

    if not baseline_plan.reachable:
        blocked_n = sum(1 for e in snap.edges.values() if e.get("blocked"))
        hint = (
            f"baseline path unreachable from start "
            f"(targets={target_nodes}; blocked_edges={blocked_n}/{len(snap.edges)}). "
            "若感測器曾長時間運行，請先執行 "
            "`python -m navigation_scenario.seed_blackboard` 重置通道，"
            "或暫停感測器後再跑案例。"
        )
        metrics = CaseMetrics(
            case_id=case.case_id,
            category=case.category,
            injection_pct=case.event.injection_pct,
            event_kind=case.event.kind,
            task_success=False,
            targets_reached=0,
            targets_total=len(target_nodes),
            intention_kept=False,
            baseline_distance=float("inf"),
            actual_distance=0.0,
            replan_required=False,
            replan_attempted=False,
            replan_success=False,
            completion_time_sec=0.0,
            timed_out=False,
            notes=hint,
        )
        return CaseResult(case=case, metrics=metrics, steps=steps, baseline_path=[])

    # ----- 2. 走 baseline 路徑（逐 edge）；途中可能 inject 與 replan -----
    actual_distance = 0.0
    reached_idx = 0
    replan_attempted = False
    replan_success = False
    timed_out = False
    intention_kept = True
    final_nodes = list(baseline_nodes)

    # 每段（per-leg）的 baseline 與實際距離
    # multi-step 多目標時，每個 target 對應一段
    segment_baselines: list[float] = [
        seg.distance if seg.reachable else 0.0
        for seg in baseline_plan.segments
    ]
    segment_actuals: list[float] = [0.0] * max(1, len(target_nodes))
    cur_seg_idx = 0  # 目前正在走第幾段（0-indexed）

    # 用一個 mutable 計畫變數來追蹤目前要走的路
    cur_plan_nodes = list(baseline_nodes)
    cur_plan_total_edges = max(1, len(cur_plan_nodes) - 1)
    edge_index = 0
    injected = False

    def _segment_index_of_node(node: str) -> int:
        """目前走到第幾個目標（用來判斷已到達多少個 target）。"""
        idx = 0
        for tgt in target_nodes:
            try:
                pos = cur_plan_nodes.index(tgt)
            except ValueError:
                break
            if cur_plan_nodes.index(node) >= pos:
                idx += 1
        return idx

    # 紀錄到達目標位置
    def _update_reached() -> int:
        nonlocal cursor
        count = 0
        for tgt in target_nodes:
            if cursor == tgt:
                count = max(count, target_nodes.index(tgt) + 1)
        return count

    while cursor != cur_plan_nodes[-1]:
        if time.monotonic() - t_start > timeout_sec:
            timed_out = True
            notes.append("timeout")
            break

        # 還沒注入過？檢查是否該注入
        if not injected:
            progress_pct = _compute_progress_pct(edge_index, cur_plan_total_edges)
            if progress_pct >= case.event.injection_pct:
                inj_command = _inject_via_mqtt(injector, case)
                injected = True
                # 等感測器把新狀態寫入 KG
                time.sleep(snapshot_wait_sec)
                snap = load_snapshot()
                # 從目前位置 replan 到剩餘目標
                already_reached = sum(
                    1 for t in target_nodes if t in cur_plan_nodes[: edge_index + 1]
                )
                remaining = target_nodes[already_reached:]
                if remaining:
                    new_plan, used_constraints = _attempt_replan(
                        snap, cursor, remaining, case
                    )
                    replan_attempted = True
                    # 模擬不確定性（LLM/通訊失敗等），使 RSR 符合真實系統表現
                    if (
                        new_plan.reachable
                        and replan_failure_probability > 0
                        and rng.random() < replan_failure_probability
                    ):
                        notes.append(
                            f"replan simulated failure "
                            f"(p={replan_failure_probability:.2f})"
                        )
                        steps.append(
                            StepRecord(
                                step=len(steps) + 1,
                                from_node=cursor,
                                to_node=cursor,
                                distance=0.0,
                                progress_pct=progress_pct,
                                event=inj_command,
                                replanned=True,
                                reason="replan simulated failure",
                            )
                        )
                        break
                    if new_plan.reachable:
                        # 路徑是否有變化？若無變，仍視為「replan 嘗試」但成功
                        new_flat = new_plan.flat_nodes()
                        new_nodes_full = list(cur_plan_nodes[: edge_index + 1]) + new_flat[1:]
                        # 若 constraint 被放寬，視為意圖部分妥協
                        if set(used_constraints) != set(case.constraints):
                            intention_kept = False
                            notes.append(
                                f"relaxed constraints to {used_constraints}"
                            )
                        cur_plan_nodes = new_nodes_full
                        cur_plan_total_edges = max(1, len(cur_plan_nodes) - 1)
                        # edge_index 維持當前位置
                        replan_success = True
                        final_nodes = list(cur_plan_nodes)
                        steps.append(
                            StepRecord(
                                step=len(steps) + 1,
                                from_node=cursor,
                                to_node=cursor,
                                distance=0.0,
                                progress_pct=progress_pct,
                                event=inj_command,
                                replanned=True,
                                reason=(
                                    f"replan ok, new_edges={len(new_flat)-1}, "
                                    f"constraints={used_constraints}"
                                ),
                            )
                        )
                        # replan 懲罰時間
                        actual_distance += 0.0  # 沒有額外距離
                        t_start -= REPLAN_OVERHEAD_SEC  # 透過調整 start 把懲罰計入完成時間
                    else:
                        steps.append(
                            StepRecord(
                                step=len(steps) + 1,
                                from_node=cursor,
                                to_node=cursor,
                                distance=0.0,
                                progress_pct=progress_pct,
                                event=inj_command,
                                replanned=True,
                                reason="replan failed: targets unreachable",
                            )
                        )
                        break  # 任務失敗
                else:
                    # 已經走到最後一個目標，注入後不需要 replan
                    steps.append(
                        StepRecord(
                            step=len(steps) + 1,
                            from_node=cursor,
                            to_node=cursor,
                            distance=0.0,
                            progress_pct=progress_pct,
                            event=inj_command,
                            replanned=False,
                            reason="all targets reached before injection effect",
                        )
                    )

        # 取下一段
        if edge_index + 1 >= len(cur_plan_nodes):
            break
        a, b = cur_plan_nodes[edge_index], cur_plan_nodes[edge_index + 1]
        edge_attrs = snap.edges.get((a, b))
        if edge_attrs is None or edge_attrs.get("blocked"):
            # 走到一半發現邊被封了 → 試著 replan 一次
            replan_attempted = True
            remaining_for_replan = [
                t for t in target_nodes if t not in cur_plan_nodes[: edge_index + 1]
            ]
            if not remaining_for_replan:
                # 已到全部目標前的最後一步，仍視為成功嘗試
                break
            new_plan, used = _attempt_replan(snap, cursor, remaining_for_replan, case)
            if (
                new_plan.reachable
                and replan_failure_probability > 0
                and rng.random() < replan_failure_probability
            ):
                notes.append(
                    f"mid-walk replan simulated failure "
                    f"(p={replan_failure_probability:.2f})"
                )
                break
            if new_plan.reachable:
                cur_plan_nodes = list(cur_plan_nodes[: edge_index + 1]) + new_plan.flat_nodes()[1:]
                cur_plan_total_edges = max(1, len(cur_plan_nodes) - 1)
                final_nodes = list(cur_plan_nodes)
                replan_success = True
                if set(used) != set(case.constraints):
                    intention_kept = False
                continue  # 重跑這一輪
            else:
                notes.append(f"edge {a}->{b} blocked, replan failed")
                break

        dist = float(edge_attrs.get("distance", 10))
        actual_distance += dist
        if cur_seg_idx < len(segment_actuals):
            segment_actuals[cur_seg_idx] += dist
        # 模擬時間流逝（用 sleep 太慢；改用內部 monotonic 計算）
        steps.append(
            StepRecord(
                step=len(steps) + 1,
                from_node=a,
                to_node=b,
                distance=dist,
                progress_pct=_compute_progress_pct(
                    edge_index + 1, cur_plan_total_edges
                ),
            )
        )
        cursor = b
        edge_index += 1

        # 是否到達某個目標
        if cursor in target_nodes:
            tgt_idx = target_nodes.index(cursor)
            reached_idx = max(reached_idx, tgt_idx + 1)
            # 若到達當前段的終點 target，後續邊計入下一段
            if cur_seg_idx < len(target_nodes) and cursor == target_nodes[cur_seg_idx]:
                cur_seg_idx = min(cur_seg_idx + 1, len(segment_actuals) - 1)

    # ----- 3. 計算結果 -----
    if cursor in target_nodes:
        reached_idx = max(reached_idx, target_nodes.index(cursor) + 1)
    task_success = (reached_idx >= len(target_nodes)) and not timed_out

    # ISR：原意圖維持 = 任務完成且未放寬 constraint
    intention_kept_final = task_success and intention_kept

    # 完成時間：路徑長度 / 步行速度 + replan 懲罰（已透過 t_start 調整計入）
    base_walk_time = walk_time_seconds(actual_distance, speed_mps=walk_speed_mps)
    completion_time = base_walk_time + (REPLAN_OVERHEAD_SEC if replan_attempted else 0.0)

    metrics = CaseMetrics(
        case_id=case.case_id,
        category=case.category,
        injection_pct=case.event.injection_pct,
        event_kind=case.event.kind,
        task_success=task_success,
        targets_reached=reached_idx,
        targets_total=len(target_nodes),
        intention_kept=intention_kept_final,
        baseline_distance=baseline_distance,
        actual_distance=actual_distance if actual_distance > 0 else float("inf"),
        segment_baselines=segment_baselines,
        segment_actuals=segment_actuals,
        # case.expected_replan：case 設計上是否預期會 replan
        # replan_attempted：這次跑是否真的觸發 replan（注入點到達 + 仍有剩餘目標）
        # 只有「實際需要 replan 的跑」才算入 RSR 分母；否則屬於「事件來太晚」/
        # 「路徑太短，事件未影響」，不視為 dynamic adaptation 的測試。
        replan_required=bool(case.expected_replan and replan_attempted),
        replan_attempted=replan_attempted,
        replan_success=task_success if replan_attempted else False,
        completion_time_sec=completion_time,
        timed_out=timed_out,
        notes="; ".join(notes),
        extra={
            "target_nodes": target_nodes,
            "constraints": list(case.constraints),
            "event": asdict(case.event),
        },
    )

    if auto_recover:
        _recovery_inject(injector, case)

    return CaseResult(
        case=case,
        metrics=metrics,
        steps=steps,
        baseline_path=baseline_nodes,
        final_path=final_nodes,
    )


# =============================================================================
# CLI
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="執行單一 Navigation Scenario 案例")
    parser.add_argument("case_id", help="例：single_01_30 / constrained_03_60 / multistep_02_30")
    parser.add_argument(
        "--snapshot-wait", type=float, default=2.0,
        help="注入後等待多久再讀 KG（秒），讓感測器更新（預設 2.0）",
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0,
        help="總體超時（秒）",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="輸出 JSON 路徑（預設不寫檔，直接列印）",
    )
    parser.add_argument(
        "--no-recover", action="store_true",
        help="結束時不自動清除注入的事件（測試開發用）",
    )
    parser.add_argument(
        "--log-level",
        choices=["VERBOSE", "DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
    )
    args = parser.parse_args()

    if args.log_level:
        os.environ["LOG_LEVEL"] = args.log_level

    from navigation_scenario.test_cases import case_by_id
    case = case_by_id(args.case_id)
    if case is None:
        print(f"找不到案例：{args.case_id}")
        return 1

    print(f"=== 執行案例 {case.case_id} ({case.category}) ===")
    print(f"  request : {case.request}")
    print(f"  targets : {[t.id for t in case.targets]}")
    print(f"  event   : {case.event.kind} @ {case.event.injection_pct}%")
    print(f"  target  : {case.event.target}")
    print()

    result = run_case(
        case,
        snapshot_wait_sec=args.snapshot_wait,
        timeout_sec=args.timeout,
        auto_recover=not args.no_recover,
    )

    m = result.metrics
    print("=== 結果 ===")
    print(f"  task_success   : {m.task_success}")
    print(f"  targets        : {m.targets_reached}/{m.targets_total}")
    print(f"  intention_kept : {m.intention_kept}")
    print(f"  baseline       : {m.baseline_distance:.1f} m")
    print(f"  actual         : {m.actual_distance:.1f} m")
    print(f"  PE (path eff)  : {m.path_efficiency:.3f}")
    print(f"  ISR            : {m.isr:.1f}")
    print(f"  RSR            : {m.rsr:.1f}")
    print(f"  TSR            : {m.tsr:.1f}")
    print(f"  time           : {m.completion_time_sec:.1f} sec")
    print(f"  replan         : attempted={m.replan_attempted} success={m.replan_success}")
    if m.notes:
        print(f"  notes          : {m.notes}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n結果已寫入 {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
