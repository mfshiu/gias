"""
GenExam Scenario：批次執行所有 60 個試題生成案例

依序執行 case，每案結果 JSON 寫到 `genexam_scenario_results/<run_id>/`。
跑完自動呼叫 `analyze.py` 產生 CSV + Markdown 報告（除非 --no-analyze）。

執行：
    python -m genexam_scenario.batch_runner
    python -m genexam_scenario.batch_runner --category single_constraint
    python -m genexam_scenario.batch_runner --topic air_pollution
    python -m genexam_scenario.batch_runner --constraint-focus difficulty
    python -m genexam_scenario.batch_runner --limit 3 --dry-run     # 快速 smoke test
    python -m genexam_scenario.batch_runner --llm-judge             # 嚴格 LLM 驗證
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from src.log_helper import init_logging

from genexam_scenario.runner import (
    CaseResult,
    build_blackboard_kg,
    build_llm,
    run_case,
)
from genexam_scenario.test_cases import (
    GenExamTestCase,
    all_cases,
    case_by_id,
    filter_cases,
)


logger = init_logging()


RESULTS_ROOT = Path(__file__).resolve().parent.parent / "genexam_scenario_results"


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


# =============================================================================
# 批次主流程
# =============================================================================
def run_batch(
    cases: list[GenExamTestCase],
    *,
    dry_run: bool = False,
    use_llm_judge: bool = False,
    max_refine: int = 2,
    inter_case_pause_sec: float = 0.3,
    output_dir: Path | None = None,
    fail_fast: bool = False,
    verbose_per_case: bool = False,
    seed: int | None = None,
) -> Path:
    """執行一批 cases，回傳輸出目錄路徑。"""
    _ensure_dir(RESULTS_ROOT)
    run_dir = output_dir or (RESULTS_ROOT / _new_run_id())
    _ensure_dir(run_dir)

    print("\n=== GenExam Scenario Batch Run ===")
    print(f"  輸出目錄    : {run_dir}")
    print(f"  案例數      : {len(cases)}")
    print(f"  模式        : {'dry-run (mock)' if dry_run else 'live (LLM)'}")
    print(f"  LLM-judge   : {use_llm_judge}")
    print(f"  max_refine  : {max_refine}")
    print()

    kg = build_blackboard_kg()
    llm = None if dry_run else build_llm()

    successes = 0
    failures: list[tuple[str, str]] = []
    batch_t0 = time.monotonic()

    try:
        for i, case in enumerate(cases, start=1):
            t0 = time.monotonic()
            print(
                f"[{i:2d}/{len(cases)}] {case.case_id:<22} "
                f"{case.category:<18} focus={case.constraint_focus:<14}",
                end="",
                flush=True,
            )
            try:
                result = run_case(
                    case,
                    kg=kg,
                    llm=llm,
                    max_refine_attempts=max_refine,
                    use_llm_judge=use_llm_judge,
                    dry_run=dry_run,
                    verbose=verbose_per_case,
                    seed=seed,
                )
                _write_case_result(run_dir, result)
                m = result.metrics
                tag = "OK " if m.task_success else "FAIL"
                if m.task_success:
                    successes += 1
                else:
                    failures.append((
                        case.case_id,
                        f"csr={m.csr:.2f} sc={m.sc:.2f} gen={m.questions_generated}/{m.requested_count}",
                    ))
                dt = time.monotonic() - t0
                print(
                    f" → {tag}  KC={m.kc:.2f} CSR={m.csr:.2f} "
                    f"SC={m.sc:.2f}  ({dt:5.1f}s)"
                )
            except Exception as e:
                failures.append((case.case_id, str(e)))
                if verbose_per_case:
                    traceback.print_exc()
                print(f"  → ERROR ({e})")
                if fail_fast:
                    break

            if inter_case_pause_sec > 0 and i < len(cases):
                time.sleep(inter_case_pause_sec)
    finally:
        try:
            kg.close()
        except Exception:
            pass

    elapsed = time.monotonic() - batch_t0

    # 寫總結 manifest
    manifest = {
        "run_dir": str(run_dir),
        "n_cases": len(cases),
        "n_success": successes,
        "n_fail": len(cases) - successes,
        "failures": failures,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(elapsed, 2),
        "mode": "dry-run" if dry_run else "live",
        "use_llm_judge": use_llm_judge,
        "max_refine": max_refine,
        "filters": {
            "case_ids": [c.case_id for c in cases],
        },
    }
    with open(run_dir / "_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\n=== 批次完成 ===")
    print(f"  成功 : {successes}/{len(cases)}")
    print(f"  失敗 : {len(cases) - successes}")
    print(f"  耗時 : {elapsed:.1f} 秒（平均 {elapsed/max(1,len(cases)):.1f} s/case）")
    print(f"  結果 : {run_dir}\n")
    return run_dir


# =============================================================================
# CLI
# =============================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="批次執行 GenExam Scenario 測試（60 案）",
    )
    parser.add_argument(
        "--category",
        choices=["single_constraint", "multi_constraint"],
        help="只跑指定類別",
    )
    parser.add_argument(
        "--topic",
        choices=[
            "air_pollution", "waste_management", "climate_change",
            "water_resources", "biodiversity", "energy_conservation",
        ],
        help="只跑指定主題",
    )
    parser.add_argument(
        "--constraint-focus",
        choices=["difficulty", "question_type", "all"],
        help="只跑指定 constraint_focus",
    )
    parser.add_argument(
        "--difficulty",
        choices=["easy", "medium", "hard"],
        help="只跑指定難度",
    )
    parser.add_argument(
        "--question-type",
        choices=["mcq", "true_false", "short_answer", "cloze"],
        help="只跑指定題型",
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 個")
    parser.add_argument(
        "--case-id", action="append", default=[],
        help="只跑指定 case_id（可重複）",
    )

    parser.add_argument(
        "--dry-run", action="store_true",
        help="不呼叫 LLM，用 mock 題目快速驗證 pipeline",
    )
    parser.add_argument(
        "--llm-judge", action="store_true",
        help="Verifier 啟用獨立 LLM 重新判定 difficulty/bloom/qtype（較慢但嚴格）",
    )
    parser.add_argument(
        "--max-refine", type=int, default=2,
        help="每題最多 refine 次數（預設 2）",
    )
    parser.add_argument(
        "--pause", type=float, default=0.3,
        help="案例間暫停秒數（預設 0.3）",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="自訂輸出目錄；不指定則自動產生 run_<ts>/",
    )
    parser.add_argument("--fail-fast", action="store_true", help="第一個錯誤就停")
    parser.add_argument(
        "--verbose", action="store_true",
        help="印出每案的逐步 [plan]/[retrieve]/[q] log",
    )
    parser.add_argument(
        "--no-analyze", action="store_true",
        help="跑完不自動 analyze（只寫 *.json）",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="random seed（影響 mock 與 knowledge 抽樣）",
    )
    args = parser.parse_args(argv)

    # 篩選案例
    if args.case_id:
        cases = [c for c in (case_by_id(cid) for cid in args.case_id) if c is not None]
    else:
        cases = filter_cases(
            category=args.category,
            topic=args.topic,
            difficulty=args.difficulty,
            question_type=args.question_type,
            constraint_focus=args.constraint_focus,
        )

    if args.limit > 0:
        cases = cases[: args.limit]

    if not cases:
        print("沒有符合條件的案例。")
        return 1

    out_dir = Path(args.output) if args.output else None
    run_dir = run_batch(
        cases,
        dry_run=args.dry_run,
        use_llm_judge=args.llm_judge,
        max_refine=args.max_refine,
        inter_case_pause_sec=args.pause,
        output_dir=out_dir,
        fail_fast=args.fail_fast,
        verbose_per_case=args.verbose,
        seed=args.seed,
    )

    if not args.no_analyze:
        try:
            from genexam_scenario.analyze import analyze_run
            analyze_run(run_dir)
        except Exception as e:
            print(f"warn: analyze failed: {e}")
            if args.verbose:
                traceback.print_exc()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
