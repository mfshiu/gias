"""
GenExam Scenario：彙整單次批次的結果為 CSV / Markdown 報告

讀入 batch_runner.py 產出的 `genexam_scenario_results/<run_id>/*.json`，
產出：
  - summary.csv       ：每案一列，所有指標欄位
  - aggregate.csv     ：依 category 彙整（single / multi / overall）
  - by_topic.csv      ：依 topic 拆分
  - by_focus.csv      ：依 constraint_focus 拆分
  - report.md         ：與簡報相同格式的表格報告

執行：
    python -m genexam_scenario.analyze <run_dir>
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from genexam_scenario.metrics import (
    CaseMetrics,
    aggregate_metrics,
    format_results_table,
)


# =============================================================================
# Loaders
# =============================================================================
def _load_case_metrics(run_dir: Path) -> list[CaseMetrics]:
    """從 run_dir 下所有 *.json（除 _manifest）讀回 CaseMetrics。"""
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
                    topic=m["topic"],
                    requested_count=int(m["requested_count"]),
                    questions_generated=int(m["questions_generated"]),
                    kc=float(m["kc"]),
                    csr=float(m["csr"]),
                    sc=float(m["sc"]),
                    task_success=bool(m["task_success"]),
                    gen_time_sec=float(m["gen_time_sec"]),
                    constraints_required=m.get("constraints_required") or {},
                    per_question=m.get("per_question") or [],
                )
            )
        except Exception as e:
            print(f"warn: skip {f.name}: {e}")
    return cases


def _load_cases_full(run_dir: Path) -> list[dict[str, Any]]:
    """讀回完整 case JSON（含 log），給 markdown 失敗案例分析用。"""
    out: list[dict[str, Any]] = []
    for f in sorted(run_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


# =============================================================================
# 衍生欄位
# =============================================================================
def _constraint_focus(case_meta: dict[str, Any]) -> str:
    return (case_meta.get("constraint_focus") or "all")


def _required_str(m: CaseMetrics) -> str:
    keys = [k for k in ("difficulty", "bloom_level", "question_type")
            if k in m.constraints_required]
    return ", ".join(f"{k}={m.constraints_required[k]}" for k in keys)


# =============================================================================
# CSV 輸出
# =============================================================================
def _write_per_case_csv(
    cases: list[CaseMetrics],
    full: list[dict[str, Any]],
    out: Path,
) -> None:
    # 建 case_id → case_meta（為了取 constraint_focus / topic 等）
    meta_by_id: dict[str, dict[str, Any]] = {}
    for entry in full:
        c = entry.get("case") or {}
        meta_by_id[c.get("case_id", "")] = c

    headers = [
        "case_id", "category", "topic", "constraint_focus",
        "difficulty", "bloom_level", "question_type",
        "requested_count", "questions_generated",
        "KC", "CSR", "SC", "task_success",
        "gen_time_sec", "constraints_required",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for c in cases:
            meta = meta_by_id.get(c.case_id, {})
            w.writerow([
                c.case_id, c.category, c.topic,
                meta.get("constraint_focus", ""),
                meta.get("difficulty") or "",
                meta.get("bloom_level") if meta.get("bloom_level") is not None else "",
                meta.get("question_type", ""),
                c.requested_count, c.questions_generated,
                f"{c.kc:.4f}", f"{c.csr:.4f}", f"{c.sc:.4f}",
                int(c.task_success), f"{c.gen_time_sec:.2f}",
                _required_str(c),
            ])


def _write_aggregate_csv(agg: dict[str, Any], out: Path) -> None:
    headers = [
        "bucket", "n_cases", "KC", "CSR", "SC",
        "task_success_rate", "avg_gen_time_sec",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for bucket in ("single_constraint", "multi_constraint", "overall"):
            a = agg[bucket]
            w.writerow([
                bucket, a["n_cases"],
                f"{a['kc']:.4f}", f"{a['csr']:.4f}", f"{a['sc']:.4f}",
                f"{a['task_success_rate']:.4f}",
                f"{a['avg_gen_time_sec']:.4f}",
            ])


def _write_by_topic_csv(
    cases: list[CaseMetrics], out: Path,
) -> None:
    grouped: dict[str, list[CaseMetrics]] = defaultdict(list)
    for c in cases:
        grouped[c.topic].append(c)
    headers = [
        "topic", "n_cases", "KC", "CSR", "SC",
        "task_success_rate", "avg_gen_time_sec",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for topic in sorted(grouped.keys()):
            sub = grouped[topic]
            agg = aggregate_metrics(sub)["overall"]
            w.writerow([
                topic, agg["n_cases"],
                f"{agg['kc']:.4f}", f"{agg['csr']:.4f}", f"{agg['sc']:.4f}",
                f"{agg['task_success_rate']:.4f}",
                f"{agg['avg_gen_time_sec']:.4f}",
            ])


def _write_by_focus_csv(
    cases: list[CaseMetrics],
    full: list[dict[str, Any]],
    out: Path,
) -> None:
    focus_by_id: dict[str, str] = {}
    for entry in full:
        c = entry.get("case") or {}
        focus_by_id[c.get("case_id", "")] = c.get("constraint_focus", "all")

    grouped: dict[str, list[CaseMetrics]] = defaultdict(list)
    for c in cases:
        grouped[focus_by_id.get(c.case_id, "all")].append(c)

    headers = [
        "constraint_focus", "n_cases", "KC", "CSR", "SC",
        "task_success_rate", "avg_gen_time_sec",
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for focus in sorted(grouped.keys()):
            sub = grouped[focus]
            agg = aggregate_metrics(sub)["overall"]
            w.writerow([
                focus, agg["n_cases"],
                f"{agg['kc']:.4f}", f"{agg['csr']:.4f}", f"{agg['sc']:.4f}",
                f"{agg['task_success_rate']:.4f}",
                f"{agg['avg_gen_time_sec']:.4f}",
            ])


# =============================================================================
# Markdown 輸出（對應投影片 SIMULATED RESULTS 風格）
# =============================================================================
def _md_main_table(agg: dict[str, Any]) -> str:
    """投影片那張 5 列指標、Single / Multi 兩欄的表格。"""
    s = agg["single_constraint"]
    m = agg["multi_constraint"]
    o = agg["overall"]

    def pct(x: float) -> str:
        return f"**{x*100:.1f}%**"

    def sec(x: float) -> str:
        return f"**{x:.2f}s**"

    rows = [
        "| Metric | Single-Constraint | Multi-Constraint | Overall |",
        "|---|:---:|:---:|:---:|",
        f"| 📖 **KC** (Knowledge Coverage) | {pct(s['kc'])} | {pct(m['kc'])} | {pct(o['kc'])} |",
        f"| ✅ **CSR** (Constraint Satisfaction Rate) | {pct(s['csr'])} | {pct(m['csr'])} | {pct(o['csr'])} |",
        f"| 🔗 **SC** (Semantic Consistency) | {pct(s['sc'])} | {pct(m['sc'])} | {pct(o['sc'])} |",
        f"| 🚩 **Task Success Rate** | {pct(s['task_success_rate'])} | {pct(m['task_success_rate'])} | {pct(o['task_success_rate'])} |",
        f"| ⏱️ **Gen Time** (sec/case) | {sec(s['avg_gen_time_sec'])} | {sec(m['avg_gen_time_sec'])} | {sec(o['avg_gen_time_sec'])} |",
        f"| **n_cases** | {s['n_cases']} | {m['n_cases']} | {o['n_cases']} |",
    ]
    return "\n".join(rows)


def _md_by_topic(cases: list[CaseMetrics]) -> str:
    grouped: dict[str, list[CaseMetrics]] = defaultdict(list)
    for c in cases:
        grouped[c.topic].append(c)
    if not grouped:
        return "_(no cases)_"

    rows = [
        "| Topic | N | KC | CSR | SC | Success | Gen Time |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for topic in sorted(grouped.keys()):
        a = aggregate_metrics(grouped[topic])["overall"]
        rows.append(
            f"| `{topic}` | {a['n_cases']} | "
            f"{a['kc']*100:.1f}% | {a['csr']*100:.1f}% | {a['sc']*100:.1f}% | "
            f"{a['task_success_rate']*100:.1f}% | {a['avg_gen_time_sec']:.2f}s |"
        )
    return "\n".join(rows)


def _md_by_focus(
    cases: list[CaseMetrics], full: list[dict[str, Any]]
) -> str:
    focus_by_id: dict[str, str] = {}
    for entry in full:
        c = entry.get("case") or {}
        focus_by_id[c.get("case_id", "")] = c.get("constraint_focus", "all")

    grouped: dict[str, list[CaseMetrics]] = defaultdict(list)
    for c in cases:
        grouped[focus_by_id.get(c.case_id, "all")].append(c)
    if not grouped:
        return "_(no cases)_"

    rows = [
        "| Constraint Focus | N | KC | CSR | SC | Success | Gen Time |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for focus in sorted(grouped.keys()):
        a = aggregate_metrics(grouped[focus])["overall"]
        rows.append(
            f"| `{focus}` | {a['n_cases']} | "
            f"{a['kc']*100:.1f}% | {a['csr']*100:.1f}% | {a['sc']*100:.1f}% | "
            f"{a['task_success_rate']*100:.1f}% | {a['avg_gen_time_sec']:.2f}s |"
        )
    return "\n".join(rows)


def _md_failures(cases: list[CaseMetrics], full: list[dict[str, Any]]) -> str:
    fails = [c for c in cases if not c.task_success]
    if not fails:
        return "_(no failures — 全部 60 案皆通過)_"

    full_by_id: dict[str, dict[str, Any]] = {
        (entry.get("case") or {}).get("case_id", ""): entry for entry in full
    }

    lines = [
        "| case_id | category | topic | KC | CSR | SC | gen# / req# | fatal |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for c in fails:
        log = (full_by_id.get(c.case_id) or {}).get("log") or {}
        fatal = log.get("fatal_error") or ""
        lines.append(
            f"| `{c.case_id}` | {c.category} | {c.topic} | "
            f"{c.kc*100:.0f}% | {c.csr*100:.0f}% | {c.sc*100:.0f}% | "
            f"{c.questions_generated}/{c.requested_count} | {fatal[:60]} |"
        )
    return "\n".join(lines)


def _md_refine_summary(full: list[dict[str, Any]]) -> str:
    total_refines = 0
    cases_with_refine = 0
    for entry in full:
        log = entry.get("log") or {}
        r = int(log.get("refine_total", 0))
        if r > 0:
            cases_with_refine += 1
        total_refines += r
    return (
        f"- 觸發 refine 的案例數：**{cases_with_refine}** / {len(full)}\n"
        f"- 累計 refine 次數    ：**{total_refines}**\n"
        f"- 平均每案 refine     ：**{total_refines / max(1, len(full)):.2f}**"
    )


def _write_markdown(
    cases: list[CaseMetrics],
    full: list[dict[str, Any]],
    agg: dict[str, Any],
    run_dir: Path,
    out: Path,
    manifest: dict[str, Any] | None,
) -> None:
    mode = (manifest or {}).get("mode", "?")
    llm_judge = (manifest or {}).get("use_llm_judge", False)
    elapsed = (manifest or {}).get("elapsed_sec", "?")

    md_lines: list[str] = []
    md_lines.append("# GenExam Scenario 評估報告")
    md_lines.append("")
    md_lines.append(f"**Run directory**：`{run_dir}`")
    md_lines.append(f"**模式**：{mode}　**LLM-judge**：{llm_judge}　**總耗時**：{elapsed} 秒")
    md_lines.append(f"**Total cases**：{agg['overall']['n_cases']}")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")

    # 主表（投影片風格）
    md_lines.append("## 1. EVALUATION OBJECTIVES — 主要指標")
    md_lines.append("")
    md_lines.append("> 對應投影片 SIMULATED RESULTS 表格")
    md_lines.append("")
    md_lines.append(_md_main_table(agg))
    md_lines.append("")

    # 依 topic
    md_lines.append("## 2. 依「主題」拆分")
    md_lines.append("")
    md_lines.append(_md_by_topic(cases))
    md_lines.append("")

    # 依 constraint_focus
    md_lines.append("## 3. 依「constraint_focus」拆分")
    md_lines.append("")
    md_lines.append(_md_by_focus(cases, full))
    md_lines.append("")

    # Refine 統計
    md_lines.append("## 4. Refine 統計（GVR 閉環使用情況）")
    md_lines.append("")
    md_lines.append(_md_refine_summary(full))
    md_lines.append("")

    # 失敗案例
    md_lines.append("## 5. 失敗案例")
    md_lines.append("")
    md_lines.append(_md_failures(cases, full))
    md_lines.append("")

    # Footer
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("本報告由 `genexam_scenario.analyze` 產生。對應原始資料：")
    md_lines.append("- `summary.csv`：每案一列、所有欄位")
    md_lines.append("- `aggregate.csv`：依 category 彙整（single / multi / overall）")
    md_lines.append("- `by_topic.csv`：依主題拆分")
    md_lines.append("- `by_focus.csv`：依 constraint_focus 拆分")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))


# =============================================================================
# 對外 API
# =============================================================================
def analyze_run(run_dir: Path) -> dict[str, Path]:
    """讀取 run_dir 下所有 *.json 並產出 CSV / Markdown。"""
    run_dir = Path(run_dir)
    cases = _load_case_metrics(run_dir)
    full = _load_cases_full(run_dir)
    if not cases:
        raise RuntimeError(f"在 {run_dir} 找不到任何案例結果 JSON")

    agg = aggregate_metrics(cases)

    summary_csv = run_dir / "summary.csv"
    aggregate_csv = run_dir / "aggregate.csv"
    by_topic_csv = run_dir / "by_topic.csv"
    by_focus_csv = run_dir / "by_focus.csv"
    report_md = run_dir / "report.md"

    _write_per_case_csv(cases, full, summary_csv)
    _write_aggregate_csv(agg, aggregate_csv)
    _write_by_topic_csv(cases, by_topic_csv)
    _write_by_focus_csv(cases, full, by_focus_csv)

    manifest = None
    manifest_path = run_dir / "_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

    _write_markdown(cases, full, agg, run_dir, report_md, manifest)

    print("\n=== 分析完成 ===")
    print(f"  {summary_csv}")
    print(f"  {aggregate_csv}")
    print(f"  {by_topic_csv}")
    print(f"  {by_focus_csv}")
    print(f"  {report_md}")
    print()
    print(format_results_table(agg))
    print()

    return {
        "summary_csv": summary_csv,
        "aggregate_csv": aggregate_csv,
        "by_topic_csv": by_topic_csv,
        "by_focus_csv": by_focus_csv,
        "report_md": report_md,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="分析 GenExam Scenario 批次結果")
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
