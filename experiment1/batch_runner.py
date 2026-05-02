"""
批次執行 + 結果寫入 JSON

由 run_single_goal / run_constraint / run_multi_step 共用。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter
from experiment1.runner import RunnerOptions, run_case
from experiment1.test_cases import build_test_cases, filter_cases
from experiment1.metrics import CaseResult


DEFAULT_RESULTS_DIR = Path("experiment1/results")


def _build_kg() -> Neo4jBoltAdapter:
    cfg = get_agent_config()
    kg_cfg = cfg.get("kg", {})
    base = kg_cfg.get("neo4j") or {}
    bb = kg_cfg.get("neo4j_blackboard") or {}
    if not base or not bb:
        raise RuntimeError("Missing [kg.neo4j] / [kg.neo4j_blackboard] in gias.toml")
    merged = {**base, **bb}
    return Neo4jBoltAdapter.from_config(merged, logger=None)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ratios",
        type=str,
        default="0.3,0.6",
        help="事件注入比例（逗號分隔）。預設 0.3,0.6",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="每個 ratio 最多執行 N 個 case（0 = 全部）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="事件注入 RNG 種子",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="輸出 JSON 路徑（預設 experiment1/results/<group>_<timestamp>.json）",
    )
    parser.add_argument(
        "--no-midway",
        action="store_true",
        help="不注入中途事件（停用 replan 觸發）",
    )


def parse_ratios(s: str) -> list[float]:
    out: list[float] = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            raise SystemExit(f"--ratios 參數錯誤：{tok!r}")
    return out


def run_group(group: str, args: argparse.Namespace) -> int:
    ratios = parse_ratios(args.ratios)
    cases = [c for c in build_test_cases() if c.group == group]
    if not cases:
        print(f"找不到 group={group} 的 cases。", file=sys.stderr)
        return 2

    if args.limit and args.limit > 0:
        cases = filter_cases(cases, limit=args.limit)

    print(f"\n=== experiment1.run_{group} ===")
    print(f"  cases  : {len(cases)} 個")
    print(f"  ratios : {ratios}")
    print(f"  midway : {'OFF' if args.no_midway else 'ON'}")

    agent_config = get_agent_config()
    kg = _build_kg()

    results: list[CaseResult] = []
    started = time.time()
    try:
        total_n = len(cases) * len(ratios)
        idx = 0
        for ratio in ratios:
            for case in cases:
                idx += 1
                t0 = time.time()
                opts = RunnerOptions(
                    ratio=ratio,
                    seed=args.seed + idx,
                    midway_inject=not args.no_midway,
                )
                print(
                    f"  [{idx}/{total_n}] {case.id} ratio={ratio} "
                    f"intent={case.intention[:40]}",
                    flush=True,
                )
                try:
                    res = run_case(
                        case=case,
                        agent_config=agent_config,
                        kg=kg,
                        options=opts,
                    )
                except Exception as e:
                    res = CaseResult(
                        case_id=case.id,
                        group=group,
                        intention=case.intention,
                        ratio=ratio,
                        error=f"{type(e).__name__}: {e}",
                    )
                elapsed = time.time() - t0
                print(
                    f"    → success={res.success} pe={res.plan_executable} "
                    f"isr={res.intention_satisfied} replan={res.replan_triggered}/{res.replan_success} "
                    f"path={res.path_length_m:.1f}m time={elapsed:.1f}s",
                    flush=True,
                )
                results.append(res)
    finally:
        try:
            kg.close()
        except Exception:
            pass

    total_elapsed = time.time() - started

    # 寫檔
    out_path = Path(args.out) if args.out else (
        DEFAULT_RESULTS_DIR / f"{group}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "group": group,
        "ratios": ratios,
        "n_cases": len(cases),
        "midway_inject": not args.no_midway,
        "seed": args.seed,
        "elapsed_sec": round(total_elapsed, 2),
        "results": [r.to_dict() for r in results],
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n寫入結果：{out_path}（共 {len(results)} 筆，耗時 {total_elapsed:.1f}s）")
    return 0
