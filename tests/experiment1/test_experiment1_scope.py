"""
experiment1 的驗證範圍標示（V-03）

experiment1 只驗證規劃，執行與重規劃是 Dijkstra 模擬；結果 JSON、執行時的輸出與 summary
都必須標示這個範圍，並指向 navigation_scenario 的 live 模式。
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from experiment1 import analyze, batch_runner
from experiment1.metrics import CaseResult
from experiment1.scope import METRIC_SOURCES, MODE, NOTICE_LINES


class _FakeKG:
    closed = False

    def close(self):
        self.closed = True


def _run_group(tmp_path, monkeypatch):
    kg = _FakeKG()
    calls = []

    def fake_run_case(*, case, agent_config, kg, options):
        calls.append(case.id)
        res = CaseResult(case_id=case.id, group=case.group, intention=case.intention, ratio=options.ratio)
        res.plan_executable = True
        res.success = True
        return res

    monkeypatch.setattr(batch_runner, "get_agent_config", lambda: {})
    monkeypatch.setattr(batch_runner, "_build_kg", lambda: kg)
    monkeypatch.setattr(batch_runner, "run_case", fake_run_case)
    out = tmp_path / "single_goal.json"
    args = argparse.Namespace(ratios="0.3", limit=2, seed=1, out=str(out), no_midway=False)
    assert batch_runner.run_group("single_goal", args) == 0
    assert len(calls) == 2 and kg.closed
    return json.loads(out.read_text(encoding="utf-8"))


def test_result_json_records_planning_only_scope(tmp_path, monkeypatch):
    payload = _run_group(tmp_path, monkeypatch)

    assert payload["mode"] == "planning_only" == MODE
    assert payload["validation_scope"] == {
        "planning": "gias",
        "execution": "simulation",
        "replanning": "simulation",
    }
    assert payload["superseded_by"]["scenario"] == "navigation_scenario"
    assert "--live" in payload["superseded_by"]["command"]
    # 原有欄位不變
    assert payload["group"] == "single_goal" and len(payload["results"]) == 2


def test_run_prints_scope_notice(tmp_path, monkeypatch, capsys):
    _run_group(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    for line in NOTICE_LINES:
        assert line in out


def _legacy_result_file(tmp_path):
    """2026-10 之前的結果檔：沒有 mode / validation_scope 欄位。"""
    path = tmp_path / "single_goal_20260427_000000.json"
    path.write_text(json.dumps({
        "group": "single_goal",
        "ratios": [0.3],
        "results": [
            {"case_id": "SG-01", "group": "single_goal", "intention": "x", "ratio": 0.3,
             "success": True, "plan_executable": True, "intention_satisfied": True,
             "path_length_m": 60.0, "completion_time_sec": 60.0},
            {"case_id": "SG-02", "group": "single_goal", "intention": "y", "ratio": 0.3,
             "success": False, "plan_executable": False},
        ],
    }), encoding="utf-8")
    return path


def _run_analyze(tmp_path, monkeypatch):
    src = _legacy_result_file(tmp_path)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["analyze", str(src), "--out-dir", str(out_dir)])
    assert analyze.main() == 0
    return out_dir


def test_summary_marks_scope_and_metric_sources(tmp_path, monkeypatch, capsys):
    out_dir = _run_analyze(tmp_path, monkeypatch)
    md = (out_dir / "summary.md").read_text(encoding="utf-8")

    assert "**Mode**：`planning_only`" in md
    for line in NOTICE_LINES:
        assert line in md
    assert "- PE : Plan Executability（來源：GIAS 計畫）" in md
    for name in ("TSR", "ISR", "RSR", "constraint_rate", "avg_path_m", "avg_time_s"):
        note = next(l for l in md.splitlines() if l.startswith(f"- {name} : "))
        assert note.endswith("（來源：Dijkstra 模擬）"), note

    terminal = capsys.readouterr().out
    assert "Mode: planning_only" in terminal
    for line in NOTICE_LINES:
        assert line in terminal


def test_summary_metrics_unchanged(tmp_path, monkeypatch):
    out_dir = _run_analyze(tmp_path, monkeypatch)
    csv_text = (out_dir / "summary.csv").read_text(encoding="utf-8").splitlines()
    assert csv_text[0] == ",".join(analyze.HEADER)
    assert csv_text[1].startswith("single_goal,0.3,2,0.5,0.5,0.5,N/A,")


def test_every_metric_column_has_a_source():
    metrics = [h for h in analyze.HEADER if h not in ("group", "ratio", "n")]
    assert sorted(metrics) == sorted(METRIC_SOURCES)
    assert sorted(metrics) == sorted(analyze.METRIC_NOTES)
    # 只有 PE 由 GIAS 的計畫決定
    assert [m for m, src in METRIC_SOURCES.items() if src == "gias"] == ["PE"]


@pytest.mark.parametrize("phrase", ["只驗證規劃", "navigation_scenario.batch_runner --live", "scope.py"])
def test_readme_states_scope(phrase):
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[2] / "experiment1" / "README.md").read_text(encoding="utf-8")
    assert phrase in readme
