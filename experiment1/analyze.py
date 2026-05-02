"""
Experiment 1 結果分析

讀入由 run_single_goal / run_constraint / run_multi_step 產出的
results JSON，計算 (group, ratio) 桶聚合 → 產出：
  - 終端表格
  - <out_dir>/summary.csv
  - <out_dir>/summary.md

執行：
    python -m experiment1.analyze
        # 自動讀 experiment1/results 下最新一份各 group 的 JSON
    python -m experiment1.analyze a.json b.json c.json
        # 顯式指定多個結果檔
    python -m experiment1.analyze --pattern "experiment1/results/*.json"
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from pathlib import Path
from typing import Iterable

from experiment1.metrics import CaseResult, aggregate


DEFAULT_RESULTS_DIR = Path("experiment1/results")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _load_one(path: str | os.PathLike) -> list[CaseResult]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out: list[CaseResult] = []
    for r in data.get("results", []):
        cr = CaseResult(
            case_id=r.get("case_id", ""),
            group=r.get("group", ""),
            intention=r.get("intention", ""),
            ratio=float(r.get("ratio", 0.0)),
        )
        cr.success = bool(r.get("success", False))
        cr.plan_executable = bool(r.get("plan_executable", False))
        cr.intention_satisfied = bool(r.get("intention_satisfied", False))
        cr.replan_triggered = int(r.get("replan_triggered", 0))
        cr.replan_success = int(r.get("replan_success", 0))
        cr.constraint_satisfied = bool(r.get("constraint_satisfied", True))
        cr.completion_time_sec = float(r.get("completion_time_sec", 0.0))
        cr.path_length_m = float(r.get("path_length_m", 0.0))
        cr.visited_zones = list(r.get("visited_zones") or [])
        cr.plan_atomic_actions = list(r.get("plan_atomic_actions") or [])
        cr.initial_events = list(r.get("initial_events") or [])
        cr.midway_events = list(r.get("midway_events") or [])
        cr.plan_unresolved_reason = r.get("plan_unresolved_reason", "")
        cr.error = r.get("error", "")
        out.append(cr)
    return out


def _resolve_inputs(args: argparse.Namespace) -> list[str]:
    if args.files:
        return list(args.files)
    if args.pattern:
        return sorted(glob.glob(args.pattern))
    # 預設：每個 group 取最新一份
    files: list[str] = []
    if not DEFAULT_RESULTS_DIR.exists():
        return files
    for grp in ("single_goal", "constraint_based", "multi_step"):
        cands = sorted(DEFAULT_RESULTS_DIR.glob(f"{grp}_*.json"))
        if cands:
            files.append(str(cands[-1]))
    return files


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
HEADER = (
    "group", "ratio", "n", "TSR", "PE", "ISR", "RSR",
    "constraint_rate", "avg_path_m", "avg_time_s",
)


def _render_terminal(rows: list[dict]) -> str:
    widths = [max(len(str(r.get(h, ""))) for r in rows + [{h: h for h in HEADER}]) for h in HEADER]
    sep = "  "
    out_lines = []
    out_lines.append(sep.join(h.ljust(w) for h, w in zip(HEADER, widths)))
    out_lines.append(sep.join("-" * w for w in widths))
    for r in rows:
        line = sep.join(str(r.get(h, "")).ljust(w) for h, w in zip(HEADER, widths))
        out_lines.append(line)
    return "\n".join(out_lines)


def _render_markdown(rows: list[dict]) -> str:
    out = []
    out.append("| " + " | ".join(HEADER) + " |")
    out.append("|" + "|".join("---" for _ in HEADER) + "|")
    for r in rows:
        out.append("| " + " | ".join(str(r.get(h, "")) for h in HEADER) + " |")
    return "\n".join(out)


def _to_rows(results: Iterable[CaseResult]) -> list[dict]:
    aggs = aggregate(results)
    rows: list[dict] = []
    for a in aggs:
        d = a.to_dict()
        rows.append({
            "group": d["group"],
            "ratio": d["ratio"],
            "n": d["n"],
            "TSR": d["TSR"],
            "PE": d["PE"],
            "ISR": d["ISR"],
            "RSR": ("N/A" if d["RSR"] is None else d["RSR"]),
            "constraint_rate": d["constraint_rate"],
            "avg_path_m": d["avg_path_length_m"],
            "avg_time_s": d["avg_completion_time_sec"],
        })
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Experiment1 result analyzer")
    parser.add_argument("files", nargs="*", help="個別結果檔（JSON）")
    parser.add_argument("--pattern", type=str, default=None, help="glob 模式（如 'experiment1/results/*.json'）")
    parser.add_argument("--out-dir", type=str, default=str(DEFAULT_RESULTS_DIR), help="輸出 summary.csv / summary.md 的目錄")
    args = parser.parse_args()

    files = _resolve_inputs(args)
    if not files:
        print("找不到任何結果檔。請先執行 run_single_goal / run_constraint / run_multi_step。")
        return 1

    print(f"=== experiment1.analyze ===")
    print("Sources:")
    for f in files:
        print(f"  - {f}")

    results: list[CaseResult] = []
    for f in files:
        results.extend(_load_one(f))
    print(f"Loaded {len(results)} case results.\n")

    rows = _to_rows(results)
    if not rows:
        print("無可分析資料。")
        return 1

    txt = _render_terminal(rows)
    print(txt)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "summary.csv"
    md_path = out_dir / "summary.md"

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Experiment 1 Summary\n\n")
        f.write(f"Sources: {len(files)} file(s), {len(results)} case(s) in total.\n\n")
        f.write("## Aggregated Metrics by (group, ratio)\n\n")
        f.write(_render_markdown(rows))
        f.write("\n\n## Notes\n\n")
        f.write("- TSR  : Task Success Rate\n")
        f.write("- PE   : Plan Executability\n")
        f.write("- ISR  : Intention Stability Rate\n")
        f.write("- RSR  : Replanning Success Rate (N/A if no replan triggered)\n")
        f.write("- constraint_rate : 受限案例中限制條件被滿足的比例\n")

    print(f"\n寫入：")
    print(f"  - {csv_path}")
    print(f"  - {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
