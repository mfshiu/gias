"""
GenExam 的驗證範圍標示（V-04）

GenExam 是獨立的出題管線，沒有經過 IntentionalAgent；結果 JSON、manifest、執行時的輸出與
report.md 都必須標示這個範圍，並說明 CSR／SC 只看通過驗證的題目。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genexam_scenario import analyze, batch_runner, runner
from genexam_scenario.metrics import evaluate_case
from genexam_scenario.scope import GIAS_COMPONENTS, METRIC_NOTES, NOTICE_LINES, SCOPE
from genexam_scenario.test_cases import case_by_id

SCOPE_KEYS = ("scope", "validation_scope", "gias_components")


def _knowledge_pool():
    return [
        runner.KnowledgeItem(
            concept_id="c_pm25", concept_name="PM2.5", concept_name_zh="細懸浮微粒",
            definition="粒徑小於等於 2.5 微米的懸浮微粒", facts=[{"id": "f_who"}],
        )
    ]


def _accepted_raw(case):
    raw = runner._mock_question(case, _knowledge_pool(), set())
    att = runner.verify_question(raw, case, topic_concepts={"c_pm25"}, all_fact_ids={"f_who"})
    assert att.accepted
    return att.raw


def _case_result(case_id="single_diff_02"):
    case = case_by_id(case_id)
    raw = _accepted_raw(case)
    log = runner.GenerationLog(case_id=case.case_id, session_id="s1", accepted_questions=[raw])
    metrics = evaluate_case(
        case, [runner._to_generated_question(raw)], topic_concept_ids={"c_pm25"}, gen_time_sec=1.0,
    )
    return runner.CaseResult(case=case, metrics=metrics, log=log)


def test_case_result_json_records_scope():
    d = _case_result().to_dict()

    assert d["scope"] == "standalone_pipeline" == SCOPE
    assert d["validation_scope"] == {
        "planning": "fixed_pipeline",
        "execution": "fixed_pipeline",
        "replanning": "fixed_pipeline",
    }
    assert "IntentionalAgent planning" in d["gias_components"]["not_used"]
    assert "LLMClient" in d["gias_components"]["used"]
    # 原有欄位不變
    assert set(d) >= {"case", "metrics", "log"}


def test_scope_payload_is_a_copy():
    d = _case_result().to_dict()
    d["gias_components"]["used"].append("x")
    assert "x" not in GIAS_COMPONENTS["used"]


class _FakeKG:
    closed = False

    def close(self):
        self.closed = True


def _run_batch(tmp_path, monkeypatch):
    kg = _FakeKG()
    monkeypatch.setattr(batch_runner, "build_blackboard_kg", lambda: kg)
    monkeypatch.setattr(batch_runner, "run_case", lambda case, **kw: _case_result(case.case_id))
    cases = [case_by_id("single_diff_02"), case_by_id("single_diff_01")]
    run_dir = batch_runner.run_batch(cases, dry_run=True, inter_case_pause_sec=0, output_dir=tmp_path / "run")
    assert kg.closed
    return run_dir


def test_batch_manifest_records_scope_and_keeps_mode(tmp_path, monkeypatch, capsys):
    run_dir = _run_batch(tmp_path, monkeypatch)

    manifest = json.loads((run_dir / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == SCOPE
    assert manifest["validation_scope"]["planning"] == "fixed_pipeline"
    assert manifest["mode"] == "dry-run"          # 原有的 mode（是否呼叫 LLM）不變
    assert manifest["n_cases"] == 2

    case_json = json.loads((run_dir / "single_diff_02.json").read_text(encoding="utf-8"))
    assert case_json["scope"] == SCOPE

    out = capsys.readouterr().out
    for line in NOTICE_LINES:
        assert line in out


def test_report_marks_scope_and_metric_notes(tmp_path, monkeypatch, capsys):
    run_dir = _run_batch(tmp_path, monkeypatch)
    capsys.readouterr()
    analyze.analyze_run(run_dir)

    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert f"**驗證範圍**：`{SCOPE}`" in md
    for line in NOTICE_LINES:
        assert f"- {line}" in md
    for name, note in METRIC_NOTES.items():
        assert f"- **{name}**：{note}" in md
    # 範圍說明在主表之前，指標說明緊接在主表之後
    assert md.index("**驗證範圍**") < md.index("## 1.") < md.index("各指標實際量到的內容") < md.index("## 2.")

    out = capsys.readouterr().out
    for line in NOTICE_LINES:
        assert line in out


def test_report_labels_legacy_results(tmp_path, monkeypatch):
    """既有的結果檔沒有 scope 欄位，報告仍標示範圍。"""
    run_dir = _run_batch(tmp_path, monkeypatch)
    for f in run_dir.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for k in SCOPE_KEYS:
            data.pop(k, None)
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    analyze.analyze_run(run_dir)
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert f"**驗證範圍**：`{SCOPE}`" in md
    assert "**Total cases**：2" in md


def test_runner_cli_prints_scope(tmp_path, monkeypatch, capsys):
    kg = _FakeKG()
    monkeypatch.setattr(runner, "build_blackboard_kg", lambda: kg)
    monkeypatch.setattr(runner, "run_case", lambda case, **kw: _case_result(case.case_id))
    out_json = tmp_path / "result.json"

    assert runner.main(["single_diff_02", "--dry-run", "--out", str(out_json)]) == 0

    out = capsys.readouterr().out
    for line in NOTICE_LINES:
        assert line in out
    assert json.loads(out_json.read_text(encoding="utf-8"))["scope"] == SCOPE


def test_csr_and_sc_reuse_the_verifier_rules():
    """標示的前提：通過驗證的題目在指標中必定 CSR = SC = 1；驗證不通過的題目不進指標。

    若改成連同未通過的題目一起計算，這個測試會失敗，請一併更新 scope.py 與 README 的說明。
    """
    case = case_by_id("single_diff_02")
    raw = _accepted_raw(case)
    m = evaluate_case(case, [runner._to_generated_question(raw)], topic_concept_ids={"c_pm25"}, gen_time_sec=0.0)
    assert m.csr == 1.0 and m.sc == 1.0

    # 自己標示的難度不符時，驗證就擋下，不會進到 CSR
    wrong = {**raw, "difficulty": "hard" if case.difficulty != "hard" else "easy"}
    att = runner.verify_question(wrong, case, topic_concepts={"c_pm25"}, all_fact_ids={"f_who"})
    assert not att.accepted and att.csr_issues


@pytest.mark.parametrize("phrase", ["沒有經過 IntentionalAgent", "standalone_pipeline", "--llm-judge", "scope.py"])
def test_readme_states_scope(phrase):
    readme = (Path(__file__).resolve().parents[2] / "genexam_scenario" / "README.md").read_text(encoding="utf-8")
    assert phrase in readme
