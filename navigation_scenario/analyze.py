"""
Navigation Scenario：彙整單次批次的結果為 CSV / Markdown 報告

讀入 batch_runner.py 產出的 `results/<run_id>/*.json`，產出：
  - summary.csv         ：每個案例一列
  - aggregate.csv       ：依 (category, injection_pct) 彙整
  - report.md           ：與簡報相同格式的表格報告

執行：
    python -m navigation_scenario.analyze <run_dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from navigation_scenario.metrics import (
    AggregateMetrics,
    CaseMetrics,
    aggregate,
    format_summary_table,
    group_by_category_and_pct,
)


CATEGORY_LABEL = {
    "single_target": "Single-Goal Navigation",
    "constrained": "Constraint-Based Navigation",
    "multi_step": "Multi-Step Navigation",
}


def _load_case_metrics(run_dir: Path) -> list[CaseMetrics]:
    cases: list[CaseMetrics] = []
    for f in sorted(run_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        m = data.get("metrics") or {}
        try:
            cases.append(
                CaseMetrics(
                    case_id=m["case_id"],
                    category=m["category"],
                    injection_pct=int(m["injection_pct"]),
                    event_kind=m["event_kind"],
                    task_success=bool(m["task_success"]),
                    targets_reached=int(m["targets_reached"]),
                    targets_total=int(m["targets_total"]),
                    intention_kept=bool(m["intention_kept"]),
                    baseline_distance=float(m["baseline_distance"]),
                    actual_distance=float(m["actual_distance"]),
                    replan_required=bool(m["replan_required"]),
                    replan_attempted=bool(m["replan_attempted"]),
                    replan_success=bool(m["replan_success"]),
                    completion_time_sec=float(m["completion_time_sec"]),
                    timed_out=bool(m.get("timed_out", False)),
                    notes=m.get("notes", ""),
                    extra=m.get("extra") or {},
                )
            )
        except Exception as e:
            print(f"warn: skip {f.name}: {e}")
    return cases


# =============================================================================
# CSV
# =============================================================================
def _write_per_case_csv(cases: list[CaseMetrics], out: Path) -> None:
    headers = [
        "case_id", "category", "injection_pct", "event_kind",
        "task_success", "targets_reached", "targets_total",
        "intention_kept", "baseline_distance", "actual_distance",
        "PE", "TSR", "ISR", "RSR",
        "replan_required", "replan_attempted", "replan_success",
        "completion_time_sec", "timed_out", "notes",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for c in cases:
            w.writerow([
                c.case_id, c.category, c.injection_pct, c.event_kind,
                int(c.task_success), c.targets_reached, c.targets_total,
                int(c.intention_kept), f"{c.baseline_distance:.2f}",
                f"{c.actual_distance:.2f}",
                f"{c.path_efficiency:.3f}", f"{c.tsr:.1f}",
                f"{c.isr:.1f}", f"{c.rsr:.1f}",
                int(c.replan_required), int(c.replan_attempted),
                int(c.replan_success), f"{c.completion_time_sec:.2f}",
                int(c.timed_out), c.notes,
            ])


def _write_aggregate_csv(
    grouped: dict[tuple[str, int], AggregateMetrics],
    out: Path,
) -> None:
    headers = [
        "category", "injection_pct", "n_cases",
        "TSR", "ISR", "RSR", "PE",
        "avg_completion_time_sec",
        "replan_required_cases", "replan_success_cases",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for (cat, pct), agg in sorted(grouped.items()):
            w.writerow([
                cat, pct, agg.n_cases,
                f"{agg.tsr:.3f}", f"{agg.isr:.3f}", f"{agg.rsr:.3f}",
                f"{agg.path_efficiency:.3f}",
                f"{agg.avg_completion_time_sec:.2f}",
                agg.replan_required_cases, agg.replan_success_cases,
            ])


# =============================================================================
# Markdown
# =============================================================================
def _md_table(grouped: dict[tuple[str, int], AggregateMetrics]) -> str:
    """產出與評估投影片相同視覺結構的表格。

    結構（與簡報一致）：
        Scenario | Intention Stability (ISR) | Plan Executability | Dynamic Adaptation
                 |   30% Events | 60% Events |  30% | 60% Events |  30% | 60% Events |
    """
    rows = [
        "| Scenario | Intention Stability (ISR) ||  Plan Executability || Dynamic Adaptation (Replanning Success) ||",
        "| --- | :---: | :---: | :---: | :---: | :---: | :---: |",
        "| | **30% Events** | **60% Events** | **30% Events** | **60% Events** | **30% Events** | **60% Events** |",
    ]
    for cat in ("single_target", "constrained", "multi_step"):
        cells = []
        # 順序：ISR(30), ISR(60), PE(30), PE(60), RSR(30), RSR(60)
        for metric in ("isr", "path_efficiency", "rsr"):
            for pct in (30, 60):
                agg = grouped.get((cat, pct))
                if not agg:
                    cells.append("n/a")
                else:
                    cells.append(f"**{getattr(agg, metric) * 100:.0f}%**")
        rows.append(f"| {CATEGORY_LABEL[cat]} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _md_breakdown_by_event(cases: list[CaseMetrics]) -> str:
    """按事件種類拆分 PE、ISR、RSR。"""
    by_event: dict[str, list[CaseMetrics]] = {}
    for c in cases:
        by_event.setdefault(c.event_kind, []).append(c)

    rows = ["| Event Kind | N | TSR | ISR | RSR | PE | Avg Time (s) |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for kind in ("crowd_congestion", "area_closure", "route_detour"):
        cs = by_event.get(kind, [])
        if not cs:
            rows.append(f"| {kind} | 0 | – | – | – | – | – |")
            continue
        agg = aggregate(cs)
        rows.append(
            f"| {kind} | {agg.n_cases} | {agg.tsr*100:.1f}% | "
            f"{agg.isr*100:.1f}% | {agg.rsr*100:.1f}% | "
            f"{agg.path_efficiency*100:.1f}% | "
            f"{agg.avg_completion_time_sec:.2f} |"
        )
    return "\n".join(rows)


def _md_failures(cases: list[CaseMetrics]) -> str:
    fails = [c for c in cases if not c.task_success]
    if not fails:
        return "_(no failures)_"
    lines = ["| case_id | category | event | notes |", "|---|---|---|---|"]
    for c in fails:
        lines.append(
            f"| `{c.case_id}` | {c.category} | "
            f"{c.event_kind} @ {c.injection_pct}% | {c.notes} |"
        )
    return "\n".join(lines)


def _write_markdown(
    cases: list[CaseMetrics],
    grouped: dict[tuple[str, int], AggregateMetrics],
    run_dir: Path,
    out: Path,
) -> None:
    overall = aggregate(cases)
    live = any((c.extra or {}).get("mode") == "live" for c in cases)
    mode = (
        "live（真實 GIAS 流程：LLM 規劃 → 監測迴圈 → 機器人實際移動）；完成時間為真實經過時間（含 LLM）"
        if live else "simulation（圖論模擬，不呼叫 IntentionalAgent）"
    )
    md = f"""# Navigation Scenario 評估報告

**Run directory**：`{run_dir}`
**Mode**：{mode}
**Total cases**：{overall.n_cases}

## 1. 整體指標

| Metric | Value |
|---|---:|
| TSR (Task Success Rate) | {overall.tsr*100:.1f}% |
| ISR (Intention Stability) | {overall.isr*100:.1f}% |
| RSR (Replanning Success) | {overall.rsr*100:.1f}% |
| PE (Path Efficiency) | {overall.path_efficiency*100:.1f}% |
| Avg completion time | {overall.avg_completion_time_sec:.2f} s |
| Replan required | {overall.replan_required_cases} cases |
| Replan succeeded | {overall.replan_success_cases} cases |

## 2. 依「類別 × 注入時機」分組

{_md_table(grouped)}

## 3. 依「事件種類」分組

{_md_breakdown_by_event(cases)}

## 4. 失敗案例

{_md_failures(cases)}

---
本報告由 `navigation_scenario.analyze` 產生。對應原始資料：
- `summary.csv`：所有案例每案一列
- `aggregate.csv`：依 (category, injection_pct) 彙整
"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)


# =============================================================================
# 對外 API
# =============================================================================
def analyze_run(run_dir: Path) -> dict[str, Path]:
    """讀取 run_dir 下所有 *.json 並產出 CSV / Markdown。"""
    run_dir = Path(run_dir)
    cases = _load_case_metrics(run_dir)
    if not cases:
        raise RuntimeError(f"在 {run_dir} 找不到任何案例結果 JSON")

    grouped = group_by_category_and_pct(cases)

    summary_csv = run_dir / "summary.csv"
    aggregate_csv = run_dir / "aggregate.csv"
    report_md = run_dir / "report.md"

    _write_per_case_csv(cases, summary_csv)
    _write_aggregate_csv(grouped, aggregate_csv)
    _write_markdown(cases, grouped, run_dir, report_md)

    print(f"\n=== 分析完成 ===")
    print(f"  {summary_csv}")
    print(f"  {aggregate_csv}")
    print(f"  {report_md}")
    print()
    print(format_summary_table(grouped))
    print()

    return {
        "summary_csv": summary_csv,
        "aggregate_csv": aggregate_csv,
        "report_md": report_md,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="分析 Navigation Scenario 批次結果")
    parser.add_argument("run_dir", type=str, help="batch_runner 輸出的目錄")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"找不到目錄：{run_dir}")
        return 1

    analyze_run(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
