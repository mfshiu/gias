"""
GenExam 的 CSR／SC 計算（V-07）

- 分母為要求的題數；每個題位取最後一次的題目，未通過驗證、未產出都算不符合
- CSR 比對評審的判定，不比對 LLM 自己標示的值；--llm-judge 時沿用驗證的 judge；無法評審時為 N/A
- Gen Time 不含評審
"""

from __future__ import annotations

import csv
import json
import time
from types import SimpleNamespace

import pytest

from genexam_scenario import analyze, batch_runner, runner
from genexam_scenario.metrics import (
    CSR_SOURCE_EVAL_JUDGE,
    CSR_SOURCE_LABELS,
    CSR_SOURCE_LEGACY,
    CSR_SOURCE_UNAVAILABLE,
    CSR_SOURCE_VERIFIER_JUDGE,
    GeneratedQuestion,
    aggregate_metrics,
    compute_csr,
    compute_sc,
    compute_task_success,
    evaluate_case,
    format_results_table,
)
from genexam_scenario.test_cases import case_by_id

CASE = case_by_id("single_diff_02")          # 5 題、medium、mcq
REQUIRED = {"question_type": "mcq", "difficulty": "medium"}
AGREE = {"difficulty": "medium", "bloom_level": 2, "question_type": "mcq"}
DISAGREE = {"difficulty": "hard", "bloom_level": 4, "question_type": "mcq"}


def _q(i, **kw):
    base = dict(
        question_id=f"q{i}", stem="關於細懸浮微粒，下列敘述何者正確？", question_type="mcq",
        difficulty="medium", bloom_level=2, choices=["甲", "乙", "丙", "丁"], answer="甲",
        knowledge_refs=["c_pm25"],
    )
    base.update(kw)
    return GeneratedQuestion(**base)


# ----------------------------------------------------------------------
# 指標公式
# ----------------------------------------------------------------------
def test_csr_compares_judged_labels_not_self_declared():
    q = _q(0)                                         # 自己標示 medium，符合要求
    assert compute_csr([q], REQUIRED, labels=[DISAGREE])[0] == 0.0
    csr, issues = compute_csr([q], REQUIRED, labels=[AGREE])
    assert csr == 1.0 and issues == [[]]
    # 反過來：自己標錯，但評審判定符合
    assert compute_csr([_q(0, difficulty="easy")], REQUIRED, labels=[AGREE])[0] == 1.0


def test_csr_is_none_without_judgement():
    csr, issues = compute_csr([_q(0), _q(1)], REQUIRED, labels=None)
    assert csr is None and issues == [[], []]


def test_unjudged_question_counts_as_unsatisfied():
    csr, issues = compute_csr([_q(0), _q(1)], REQUIRED, labels=[None, AGREE])
    assert csr == 0.5 and issues[0] == ["not_judged"]


def test_denominator_is_the_requested_count():
    assert compute_sc([_q(0), _q(1)], requested_count=5)[0] == 0.4
    assert compute_csr([_q(0), _q(1)], REQUIRED, labels=[AGREE, AGREE], requested_count=5)[0] == 0.4
    assert compute_sc([], requested_count=0)[0] == 0.0


def test_rejected_final_questions_are_counted():
    good, bad = _q(0), _q(1, choices=["甲"], answer="戊")      # bad：schema 不一致，沒通過驗證
    m = evaluate_case(
        CASE, [good], final_questions=[good, bad], judged_labels=[AGREE, AGREE],
        csr_source=CSR_SOURCE_EVAL_JUDGE, topic_concept_ids={"c_pm25"}, gen_time_sec=1.0,
    )
    assert m.questions_generated == 1
    assert m.sc == pytest.approx(1 / 5) and m.csr == pytest.approx(2 / 5)
    assert [p["accepted"] for p in m.per_question] == [True, False]
    assert m.per_question[0]["judged"] == AGREE
    assert m.csr_source == CSR_SOURCE_EVAL_JUDGE and m.task_success is False


def test_csr_source_is_unavailable_without_labels():
    m = evaluate_case(CASE, [_q(0)], final_questions=[_q(0)], judged_labels=None,
                      csr_source=CSR_SOURCE_EVAL_JUDGE, topic_concept_ids={"c_pm25"}, gen_time_sec=0.0)
    assert m.csr is None and m.csr_source == CSR_SOURCE_UNAVAILABLE


def test_task_success_skips_csr_only_when_unavailable():
    kw = dict(questions_generated=5, requested_count=5, sc=1.0)
    assert compute_task_success(csr=None, **kw) is True
    assert compute_task_success(csr=0.8, **kw) is False
    assert compute_task_success(csr=1.0, **kw) is True


def test_aggregate_skips_cases_without_csr():
    def metrics(csr):
        q = _q(0)
        labels = None if csr is None else [AGREE if csr else DISAGREE]
        return evaluate_case(CASE, [q], final_questions=[q], judged_labels=labels,
                             topic_concept_ids={"c_pm25"}, gen_time_sec=1.0)

    with_csr, without = metrics(1), metrics(None)
    assert aggregate_metrics([with_csr, without])["overall"]["csr"] == pytest.approx(0.2)   # 1/5
    agg = aggregate_metrics([without])
    assert agg["overall"]["csr"] is None
    assert "N/A" in format_results_table(agg)


# ----------------------------------------------------------------------
# run_case：評審在生成結束後、與驗證分開
# ----------------------------------------------------------------------
def _pool():
    return [runner.KnowledgeItem(concept_id="c_pm25", concept_name="PM2.5", concept_name_zh="細懸浮微粒",
                                 definition="粒徑小於等於 2.5 微米的懸浮微粒", facts=[{"id": "f_who"}])]


@pytest.fixture
def pipeline(monkeypatch):
    """不連 Neo4j、不呼叫 LLM：生成用 mock 題目，評審回覆由 state["judge"] 決定。"""
    state = {"calls": [], "judge": AGREE, "fail_generate_at": None}

    def generate(llm, case, pool, used, attempt_no=1):
        n = sum(1 for c in state["calls"] if c == "generate")
        state["calls"].append("generate")
        if state["fail_generate_at"] == n:
            raise RuntimeError("LLM down")
        return runner._mock_question(case, pool, used)

    def judge(llm, raw):
        state.setdefault("first_judge_at", time.monotonic())
        state["calls"].append("judge")
        reply = state["judge"]
        if isinstance(reply, Exception):
            state["judge"] = AGREE                      # 只失敗一次
            raise reply
        return dict(reply)

    monkeypatch.setattr(runner, "plan_generation", lambda kg, case: "s1")
    monkeypatch.setattr(runner, "get_topic_concept_ids", lambda kg, topic: {"c_pm25"})
    monkeypatch.setattr(runner, "retrieve_knowledge", lambda kg, topic, top_k=0: _pool())
    monkeypatch.setattr(runner, "persist_question", lambda kg, sid, raw: None)
    monkeypatch.setattr(runner, "finalize_session", lambda kg, sid: state["calls"].append("finalize"))
    monkeypatch.setattr(runner, "generate_question", generate)
    monkeypatch.setattr(runner, "refine_question",
                        lambda llm, case, prev, pool, used, attempt_no=2: (runner._mock_question(case, pool, used), []))
    monkeypatch.setattr(runner, "llm_judge", judge)
    return state


def _run(**kw):
    kw.setdefault("llm", SimpleNamespace())
    return runner.run_case(CASE, kg=None, verbose=False, seed=1, **kw).metrics


def test_eval_judge_overrides_self_declared_labels(pipeline):
    pipeline["judge"] = DISAGREE
    m = _run()
    assert m.questions_generated == 5                 # 驗證只看自己標示的值，全部通過
    assert m.csr == 0.0 and m.sc == 1.0               # 評審認為都不是 medium
    assert m.csr_source == CSR_SOURCE_EVAL_JUDGE and m.task_success is False
    calls = pipeline["calls"]
    assert calls.count("judge") == 5
    assert calls.index("finalize") < calls.index("judge")      # 評審在生成結束後，不計入 Gen Time


def test_gen_time_excludes_the_judge(pipeline):
    result = runner.run_case(CASE, kg=None, llm=SimpleNamespace(), verbose=False, seed=1)
    assert result.log.finished_at <= pipeline["first_judge_at"]
    assert result.metrics.gen_time_sec == pytest.approx(result.log.finished_at - result.log.started_at)


def test_eval_judge_normalizes_labels(pipeline):
    pipeline["judge"] = {"difficulty": " Medium ", "bloom_level": "2", "question_type": "MCQ"}
    m = _run()
    assert m.csr == 1.0 and m.task_success is True


def test_verifier_judge_is_reused_without_extra_calls(pipeline):
    pipeline["judge"] = DISAGREE
    m = _run(use_llm_judge=True, max_refine_attempts=1)
    assert m.questions_generated == 0                 # 驗證用 judge，全部被擋下
    assert m.csr == 0.0 and m.csr_source == CSR_SOURCE_VERIFIER_JUDGE
    assert pipeline["calls"].count("judge") == 10     # 5 題位 × 2 次嘗試，沒有另外評審


@pytest.mark.parametrize("kw", [{"dry_run": True}, {"eval_judge": False}, {"llm": None}])
def test_no_judgement_gives_na(pipeline, kw):
    m = _run(**kw)
    assert m.csr is None and m.csr_source == CSR_SOURCE_UNAVAILABLE
    assert "judge" not in pipeline["calls"]
    assert m.task_success is True                     # 題數與 SC 達標；CSR 不檢查


def test_failed_judgement_counts_as_unsatisfied(pipeline):
    pipeline["judge"] = RuntimeError("judge timeout")
    m = _run()
    assert m.csr == pytest.approx(4 / 5)
    assert m.per_question[0]["csr_issues"] == ["not_judged"]


def test_missing_slots_count_against_csr_and_sc(pipeline):
    pipeline["fail_generate_at"] = 2                  # 第 3 題生成時失敗
    result = runner.run_case(CASE, kg=None, llm=SimpleNamespace(), verbose=False, seed=1)
    m = result.metrics
    assert result.log.fatal_error
    assert m.questions_generated == 0                 # 中途失敗時不寫回題目（既有行為）
    # 已產出的 2 個題位仍計入，未產出的 3 個算不符合
    assert m.csr == pytest.approx(2 / 5) and m.sc == pytest.approx(2 / 5)
    assert len(m.per_question) == 2


def test_failure_before_retrieval_still_reports(monkeypatch, pipeline):
    def boom(kg, case):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(runner, "plan_generation", boom)
    result = runner.run_case(CASE, kg=None, llm=SimpleNamespace(), verbose=False)
    assert "neo4j down" in result.log.fatal_error
    assert result.metrics.csr == 0.0 and result.metrics.task_success is False


# ----------------------------------------------------------------------
# CLI、manifest 與報告
# ----------------------------------------------------------------------
class _FakeKG:
    def close(self):
        pass


def test_runner_cli_no_eval_judge(pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "build_blackboard_kg", _FakeKG)
    monkeypatch.setattr(runner, "build_llm", SimpleNamespace)
    out = tmp_path / "r.json"
    assert runner.main(["single_diff_02", "--no-eval-judge", "--out", str(out)]) == 0
    metrics = json.loads(out.read_text(encoding="utf-8"))["metrics"]
    assert metrics["csr"] is None and metrics["csr_source"] == CSR_SOURCE_UNAVAILABLE
    assert "judge" not in pipeline["calls"]


def test_batch_records_eval_judge_and_report_shows_sources(pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(batch_runner, "build_blackboard_kg", _FakeKG)
    monkeypatch.setattr(batch_runner, "build_llm", SimpleNamespace)
    run_dir = tmp_path / "run"
    assert batch_runner.main(["--case-id", "single_diff_02", "--case-id", "single_diff_01",
                              "--no-eval-judge", "--no-analyze", "--pause", "0", "--output", str(run_dir)]) == 0
    assert json.loads((run_dir / "_manifest.json").read_text(encoding="utf-8"))["eval_judge"] is False

    # 一案改成舊版結果檔（沒有 csr_source，CSR 為舊算法的 100%）
    legacy = run_dir / "single_diff_01.json"
    data = json.loads(legacy.read_text(encoding="utf-8"))
    data["metrics"].pop("csr_source")
    data["metrics"]["csr"] = 1.0
    legacy.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    analyze.analyze_run(run_dir)
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert f"{CSR_SOURCE_LABELS[CSR_SOURCE_UNAVAILABLE]} 1 案" in md
    assert f"{CSR_SOURCE_LABELS[CSR_SOURCE_LEGACY]} 1 案" in md

    with open(run_dir / "summary.csv", encoding="utf-8") as f:
        rows = {r["case_id"]: r for r in csv.DictReader(f)}
    assert rows["single_diff_02"]["CSR"] == "N/A" and rows["single_diff_02"]["csr_source"] == CSR_SOURCE_UNAVAILABLE
    assert rows["single_diff_01"]["CSR"] == "1.0000" and rows["single_diff_01"]["csr_source"] == CSR_SOURCE_LEGACY


def test_report_main_table_shows_na(pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(batch_runner, "build_blackboard_kg", _FakeKG)
    run_dir = batch_runner.run_batch([CASE], dry_run=True, inter_case_pause_sec=0, output_dir=tmp_path / "run")
    analyze.analyze_run(run_dir)
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    csr_row = next(l for l in md.splitlines() if "**CSR**" in l)
    single, multi, overall = [c.strip() for c in csr_row.strip("|").split("|")[1:4]]
    assert single == overall == "**N/A**"                 # Multi 沒有案例，沿用既有的 0.0%
