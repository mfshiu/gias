"""
Navigation Scenario：批次執行所有測試案例

順序執行 60 個案例（或子集），每案例的結果 JSON 寫到 `results/<run_id>/`。
結束時自動呼叫 `analyze.py` 彙整 CSV + Markdown 報告。

執行：
    python -m navigation_scenario.batch_runner
    python -m navigation_scenario.batch_runner --category single_target
    python -m navigation_scenario.batch_runner --pct 30
    python -m navigation_scenario.batch_runner --limit 5
    python -m navigation_scenario.batch_runner --live --category constrained   # 真實 GIAS 流程
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from src.log_helper import init_logging

from navigation_scenario.event_injector import MqttEventInjector
from navigation_scenario.kg_reset import restore_navigation_baseline
from navigation_scenario.runner import CaseResult, run_case
from navigation_scenario.test_cases import (
    TestCase,
    all_cases,
    filter_cases,
)

logger = init_logging()


RESULTS_ROOT = Path(__file__).resolve().parent.parent / "navigation_scenario_results"


# =============================================================================
# 工具
# =============================================================================
def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _new_run_id() -> str:
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")


def _write_case_result(out_dir: Path, result: CaseResult) -> Path:
    fpath = out_dir / f"{result.case.case_id}.json"
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    return fpath


def _maybe_reset_blackboard() -> None:
    """執行前可選擇 reset Blackboard baseline（這裡只做提示）。"""
    print(
        "提示：建議在執行前先重置 Blackboard（python -m navigation_scenario.seed_blackboard）"
    )


# =============================================================================
# 批次主流程
# =============================================================================
def run_batch(
    cases: list[TestCase],
    *,
    snapshot_wait_sec: float = 2.0,
    timeout_sec: float = 60.0,
    inter_case_pause_sec: float = 0.5,
    output_dir: Path | None = None,
    fail_fast: bool = False,
    reset_between_cases: bool = True,
    reset_before_batch: bool = True,
    replan_failure_probability: float = 0.0,
    live_session=None,
) -> Path:
    """執行一批 cases，回傳輸出目錄路徑。

    live_session（navigation_scenario.live.runner.LiveSession）不為 None 時，
    以真實 GIAS 流程執行每個案例；否則使用圖論模擬。
    """
    mode = "live" if live_session is not None else "simulation"
    _ensure_dir(RESULTS_ROOT)
    run_dir = output_dir or (RESULTS_ROOT / _new_run_id())
    _ensure_dir(run_dir)

    print(f"\n=== Navigation Scenario Batch Run ===")
    print(f"  輸出目錄  : {run_dir}")
    print(f"  模式      : {mode}")
    print(f"  案例數    : {len(cases)}")
    print(f"  每案還原 KG : {reset_between_cases}")
    if replan_failure_probability > 0:
        print(f"  Replan 不確定性 : {replan_failure_probability:.2f}")
    print()

    if reset_before_batch:
        try:
            stats = restore_navigation_baseline()
            print(
                f"  [KG] 批次前還原 baseline：zones={stats['zones_reset']} "
                f"booths={stats['booths_reset']} "
                f"blocked_edges剩={stats['blocked_edges_remaining']}"
            )
        except Exception as e:
            print(f"  [KG] 警告：批次前還原失敗（{e}），建議先執行 seed_blackboard")

    successes = 0
    failures: list[tuple[str, str]] = []

    with (contextlib.nullcontext() if live_session is not None else MqttEventInjector()) as injector:
        for i, case in enumerate(cases, start=1):
            if reset_between_cases and i > 1:
                try:
                    restore_navigation_baseline()
                except Exception as e:
                    print(f"\n  [KG] 警告：案例前還原失敗（{e}）", flush=True)
            t0 = time.monotonic()
            print(
                f"[{i:2d}/{len(cases)}] {case.case_id:<20} "
                f"{case.category:<14} event={case.event.kind} @ {case.event.injection_pct}%",
                end="",
                flush=True,
            )
            try:
                if live_session is not None:
                    result = live_session.run_case(case)
                else:
                    result = run_case(
                        case,
                        injector=injector,
                        snapshot_wait_sec=snapshot_wait_sec,
                        timeout_sec=timeout_sec,
                        auto_recover=True,
                        replan_failure_probability=replan_failure_probability,
                    )
                _write_case_result(run_dir, result)
                m = result.metrics
                tag = "OK " if m.task_success else "FAIL"
                successes += 1 if m.task_success else 0
                if not m.task_success:
                    failures.append((case.case_id, m.notes or "task failed"))
                dt = time.monotonic() - t0
                print(
                    f"  → {tag}  PE={m.path_efficiency:.2f} "
                    f"ISR={m.isr:.0f} RSR={m.rsr:.0f}  ({dt:.1f}s)"
                )
            except Exception as e:
                failures.append((case.case_id, str(e)))
                traceback.print_exc()
                print(f"  → ERROR ({e})")
                if fail_fast:
                    break

            time.sleep(inter_case_pause_sec)

    # 寫總結 manifest
    manifest = {
        "run_dir": str(run_dir),
        "mode": mode,
        "n_cases": len(cases),
        "n_success": successes,
        "n_fail": len(cases) - successes,
        "failures": failures,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(run_dir / "_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n=== 完成 ===")
    print(f"  成功 : {successes}/{len(cases)}")
    print(f"  失敗 : {len(cases) - successes}")
    print(f"  結果 : {run_dir}\n")
    return run_dir


# =============================================================================
# CLI
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="批次執行 Navigation Scenario 測試")
    parser.add_argument(
        "--category", choices=["single_target", "constrained", "multi_step"],
        help="只跑指定類別",
    )
    parser.add_argument("--pct", type=int, choices=[30, 60], help="只跑指定注入時機")
    parser.add_argument(
        "--event", choices=["crowd_congestion", "area_closure", "route_detour"],
        help="只跑指定事件種類",
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 個（除錯用）")
    parser.add_argument(
        "--case-id", action="append", default=[],
        help="只跑指定 case_id（可重複指定）",
    )
    parser.add_argument(
        "--snapshot-wait", type=float, default=2.0,
        help="注入後等多久再讀 KG（秒）",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--pause", type=float, default=0.5, help="案例間暫停（秒）")
    parser.add_argument("--output", type=str, default=None, help="自訂輸出目錄")
    parser.add_argument("--fail-fast", action="store_true", help="第一個錯誤就停")
    parser.add_argument(
        "--no-analyze", action="store_true",
        help="跑完不自動執行 analyze",
    )
    parser.add_argument(
        "--no-reset-between",
        action="store_true",
        help="不要在每個案例之間還原 KG（易累積封鎖邊，成功率會大幅下降）",
    )
    parser.add_argument(
        "--no-reset-before",
        action="store_true",
        help="不要在批次開始前還原 KG",
    )
    parser.add_argument(
        "--replan-failure-probability",
        type=float,
        default=0.0,
        help=(
            "模擬 LLM/通訊不可靠性的 replan 失敗機率（0-1）；"
            "純圖論模擬 Dijkstra 永遠找得到路徑，此參數讓 RSR 接近真實 GIAS agent；"
            "建議值 0.10 ~ 0.15 對齊簡報約 85 百分比"
        ),
    )
    parser.add_argument(
        "--log-level",
        choices=["VERBOSE", "DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="以真實 GIAS 流程執行（LLM 規劃 + 監測迴圈 + 機器人在 Blackboard 圖上移動）",
    )
    from navigation_scenario.live.runner import add_live_arguments, session_from_args
    add_live_arguments(parser)
    args = parser.parse_args()

    if args.log_level:
        os.environ["LOG_LEVEL"] = args.log_level

    # 篩選案例
    if args.case_id:
        from navigation_scenario.test_cases import case_by_id
        cases = [c for c in (case_by_id(cid) for cid in args.case_id) if c is not None]
    else:
        cases = filter_cases(
            category=args.category,  # type: ignore[arg-type]
            injection_pct=args.pct,
            event_kind=args.event,
        )

    if args.limit > 0:
        cases = cases[: args.limit]

    if not cases:
        print("沒有符合條件的案例。")
        return 1

    _maybe_reset_blackboard()

    out_dir = Path(args.output) if args.output else None
    if args.live and args.replan_failure_probability > 0:
        print("提示：--replan-failure-probability 只用於模擬模式，live 模式忽略此參數")
    with (session_from_args(args) if args.live else contextlib.nullcontext()) as live_session:
        run_dir = run_batch(
            cases,
            snapshot_wait_sec=args.snapshot_wait,
            timeout_sec=args.timeout,
            inter_case_pause_sec=args.pause,
            output_dir=out_dir,
            fail_fast=args.fail_fast,
            reset_between_cases=not args.no_reset_between,
            reset_before_batch=not args.no_reset_before,
            replan_failure_probability=0.0 if args.live else args.replan_failure_probability,
            live_session=live_session,
        )

    if not args.no_analyze:
        try:
            from navigation_scenario.analyze import analyze_run
            analyze_run(run_dir)
        except Exception as e:
            print(f"warn: analyze failed: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
