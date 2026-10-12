"""
GenExam Scenario 評估指標

對應投影片所列的五項指標：
  1) KC  : Knowledge Coverage         知識覆蓋率
  2) CSR : Constraint Satisfaction Rate 限制滿足率
  3) SC  : Semantic Consistency       語意一致性
  4) Task Success Rate                  案例層級成功率
  5) Gen Time                           生成時間（秒）

設計重點：
  - KC、SC 只看題目本身與 case 的限制條件即可計算；CSR 需要評審判定（由 runner 提供，見下）。
  - 對於語意一致性的「深層」語意核對（題幹 ↔ 答案 ↔ 知識依據）可選擇外掛 LLM-judge；
    此模組僅實作「**結構一致性**」（schema-level），其餘留下 hook 由 runner 注入。
  - CSR、SC 以要求的題數為分母，每個題位取最後一次的題目（通過驗證的那次，否則最後一次）；
    未通過驗證、未產出的題位都算不符合。只看通過驗證的題目會讓 CSR、SC 必為 100%（V-07）。

公式：

    KC  = |{distinct concept/fact ids 被 generated 問題引用 且屬於該 topic}|
          ─────────────────────────────────────────────────────────────
          |{該 topic 在 Blackboard 中的所有 Concept ids}|

    CSR = |{評審判定符合所有指定限制 (difficulty / bloom / question_type) 的題位}|
          ──────────────────────────────────────────────────────────────────
          |{要求的題數}|
          判定來自評審（csr_source）：生成結束後另外呼叫的 LLM 評審，不比對 LLM 自己標示的值；
          使用 --llm-judge 時沿用驗證時的 judge（不是獨立判斷）；無法評審時（dry-run）CSR 為 None（N/A）

    SC  = |{schema 一致的題位（依題型檢核）}|
          ───────────────────────────────────
          |{要求的題數}|

    Task Success = 1  if  通過驗證的題數 ≥ count
                       AND  csr ≥ csr_threshold（預設 1.0；CSR 為 N/A 時不檢查）
                       AND  sc  ≥ sc_threshold （預設 1.0）
                 = 0  otherwise

    Gen Time     = wall-clock seconds from request acceptance to finalize（不含 CSR 評審）
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict, field, replace
from typing import Any, Iterable

from genexam_scenario.test_cases import GenExamTestCase


# =============================================================================
# 通過判定閾值（runner / batch_runner 可覆寫）
# =============================================================================
DEFAULT_CSR_THRESHOLD = 1.0    # 預設要求所有題目都符合限制
DEFAULT_SC_THRESHOLD = 1.0     # 預設要求所有題目 schema 一致

# CSR 的判定來源
CSR_SOURCE_EVAL_JUDGE = "eval_judge"            # 生成結束後另外呼叫的 LLM 評審
CSR_SOURCE_VERIFIER_JUDGE = "verifier_judge"    # --llm-judge：沿用驗證時的 judge，不是獨立判斷
CSR_SOURCE_UNAVAILABLE = "unavailable"          # dry-run 或停用評審：CSR 為 N/A
CSR_SOURCE_LEGACY = "legacy"                    # 舊版結果檔：只看通過驗證的題目並比對自我標示，必為 100%

CSR_SOURCE_LABELS = {
    CSR_SOURCE_EVAL_JUDGE: "LLM 評審（與生成、驗證分開）",
    CSR_SOURCE_VERIFIER_JUDGE: "沿用驗證的 judge（不是獨立判斷）",
    CSR_SOURCE_UNAVAILABLE: "無法評審（N/A）",
    CSR_SOURCE_LEGACY: "舊版計算（只看通過驗證的題目、比對自我標示，必為 100%）",
}


def format_rate(x: float | None, *, pct: bool = False, digits: int = 2) -> str:
    """比例的文字；None（無法評估）為 N/A。"""
    if x is None:
        return "N/A"
    return f"{x * 100:.{digits}f}%" if pct else f"{x:.{digits}f}"


# =============================================================================
# 資料結構
# =============================================================================
@dataclass
class GeneratedQuestion:
    """GVR pipeline 寫入 Blackboard 後，runner 讀回的單一題目表示。"""
    question_id: str
    stem: str
    question_type: str                       # mcq / true_false / short_answer / cloze
    difficulty: str | None = None             # easy / medium / hard
    bloom_level: int | None = None            # 1–6
    choices: list[str] = field(default_factory=list)
    answer: Any = None                        # str / bool / list[str]
    rationale: str = ""
    knowledge_refs: list[str] = field(default_factory=list)  # Concept / Fact ids


@dataclass
class CaseMetrics:
    """單一 case 的所有指標。"""
    case_id: str
    category: str
    topic: str
    requested_count: int
    questions_generated: int
    kc: float
    csr: float | None                         # None：無法評審（N/A）
    sc: float
    task_success: bool
    gen_time_sec: float
    constraints_required: dict[str, Any]
    per_question: list[dict[str, Any]] = field(default_factory=list)
    csr_source: str = CSR_SOURCE_UNAVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =============================================================================
# 1) Knowledge Coverage (KC)
# =============================================================================
def compute_kc(
    questions: Iterable[GeneratedQuestion],
    topic_concept_ids: set[str],
) -> float:
    """
    KC = 該 topic 被 generated 問題引用過的 distinct concept 數 / 該 topic 全部 concept 數

    - `topic_concept_ids` 由 runner 預先從 Blackboard 撈出（屬於本 case topic 的 Concept.id 集合）
    - knowledge_refs 中混合 Concept 與 Fact id 都可，這裡僅以「是否屬於該 topic 的 concept」計算
    """
    if not topic_concept_ids:
        return 0.0
    referenced: set[str] = set()
    for q in questions:
        for ref in q.knowledge_refs or []:
            if ref in topic_concept_ids:
                referenced.add(ref)
    return len(referenced) / len(topic_concept_ids)


# =============================================================================
# 2) Constraint Satisfaction Rate (CSR)
# =============================================================================
def _check_constraints(
    q: GeneratedQuestion,
    required: dict[str, Any],
) -> tuple[bool, list[str]]:
    """回傳 (是否滿足所有限制, 違反項目列表)。"""
    issues: list[str] = []
    if "difficulty" in required:
        if q.difficulty != required["difficulty"]:
            issues.append(
                f"difficulty mismatch: expected={required['difficulty']}, got={q.difficulty}"
            )
    if "bloom_level" in required:
        if int(q.bloom_level or 0) != int(required["bloom_level"]):
            issues.append(
                f"bloom mismatch: expected={required['bloom_level']}, got={q.bloom_level}"
            )
    if "question_type" in required:
        if q.question_type != required["question_type"]:
            issues.append(
                f"qtype mismatch: expected={required['question_type']}, got={q.question_type}"
            )
    return (len(issues) == 0, issues)


def judged_question(q: GeneratedQuestion, labels: dict[str, Any]) -> GeneratedQuestion:
    """以評審判定的難度／Bloom／題型取代題目自己標示的值。"""
    return replace(
        q,
        difficulty=labels.get("difficulty"),
        bloom_level=labels.get("bloom_level"),
        question_type=labels.get("question_type"),
    )


def _denominator(questions: list[GeneratedQuestion], requested_count: int | None) -> int:
    return max(len(questions), requested_count if requested_count is not None else len(questions))


def compute_csr(
    questions: list[GeneratedQuestion],
    required: dict[str, Any],
    *,
    labels: list[dict[str, Any] | None] | None,
    requested_count: int | None = None,
) -> tuple[float | None, list[list[str]]]:
    """
    回傳 (csr, per_question_issues_list)；per_question_issues_list 長度 = len(questions)

    labels：評審對每題的判定（{"difficulty", "bloom_level", "question_type"}，該題判定失敗為 None）；
            整體為 None 表示無法評審，csr 為 None。限制一律比對評審的判定，不用題目自己標示的值。
    requested_count：分母（要求的題數），未產出的題位算不符合；預設為 len(questions)。
    """
    if labels is None:
        return None, [[] for _ in questions]
    total = _denominator(questions, requested_count)
    if not total:
        return 0.0, []
    passed = 0
    issues_all: list[list[str]] = []
    for i, q in enumerate(questions):
        judged = labels[i] if i < len(labels) else None
        if judged is None:
            issues_all.append(["not_judged"])
            continue
        ok, issues = _check_constraints(judged_question(q, judged), required)
        issues_all.append(issues)
        if ok:
            passed += 1
    return passed / total, issues_all


# =============================================================================
# 3) Semantic Consistency (SC)
# =============================================================================
def _is_schema_consistent(q: GeneratedQuestion) -> tuple[bool, list[str]]:
    """依題型檢核 schema 一致性（無需 LLM judge 的快速一致性檢查）。"""
    issues: list[str] = []
    stem = (q.stem or "").strip()
    if len(stem) < 5:
        issues.append("stem_too_short")

    qt = q.question_type
    if qt == "mcq":
        choices = [c for c in (q.choices or []) if isinstance(c, str)]
        if not (2 <= len(choices) <= 6):
            issues.append("mcq_choice_count_invalid")
        ans = q.answer
        # answer 可以是「索引 (int) / 文字 / list[str]（多選）」
        if isinstance(ans, int):
            if not (0 <= ans < len(choices)):
                issues.append("mcq_answer_index_out_of_range")
        elif isinstance(ans, str):
            if ans not in choices:
                issues.append("mcq_answer_not_in_choices")
        else:
            issues.append("mcq_answer_invalid_type")
        if len(set(choices)) != len(choices):
            issues.append("mcq_duplicate_choices")
    elif qt == "true_false":
        ans = q.answer
        if not (isinstance(ans, bool) or (isinstance(ans, str)
                                          and ans.lower() in {"true", "false", "是", "否"})):
            issues.append("true_false_answer_invalid")
    elif qt == "short_answer":
        if not isinstance(q.answer, str) or len(q.answer.strip()) < 1:
            issues.append("short_answer_empty")
    elif qt == "cloze":
        if "____" not in stem and "___" not in stem and "（）" not in stem and "()" not in stem:
            issues.append("cloze_no_blank_in_stem")
        if not isinstance(q.answer, (str, list)):
            issues.append("cloze_answer_invalid_type")
        elif isinstance(q.answer, str) and not q.answer.strip():
            issues.append("cloze_answer_empty")
    else:
        issues.append(f"unknown_question_type:{qt}")

    if not q.knowledge_refs:
        issues.append("no_knowledge_ref")

    return (len(issues) == 0, issues)


def compute_sc(
    questions: list[GeneratedQuestion],
    *,
    requested_count: int | None = None,
) -> tuple[float, list[list[str]]]:
    """回傳 (sc, per_question_issues)；分母為要求的題數（未產出的題位算不一致），預設為 len(questions)。"""
    total = _denominator(questions, requested_count)
    if not total:
        return 0.0, []
    consistent = 0
    issues_all: list[list[str]] = []
    for q in questions:
        ok, issues = _is_schema_consistent(q)
        issues_all.append(issues)
        if ok:
            consistent += 1
    return consistent / total, issues_all


# =============================================================================
# 4) Task Success Rate（單案級判定）
# =============================================================================
def compute_task_success(
    *,
    questions_generated: int,
    requested_count: int,
    csr: float | None,
    sc: float,
    csr_threshold: float = DEFAULT_CSR_THRESHOLD,
    sc_threshold: float = DEFAULT_SC_THRESHOLD,
) -> bool:
    """CSR 為 None（無法評審）時不檢查 CSR。"""
    if questions_generated < requested_count:
        return False
    if csr is not None and csr < csr_threshold:
        return False
    if sc < sc_threshold:
        return False
    return True


# =============================================================================
# 整合：單一 case 指標計算
# =============================================================================
def evaluate_case(
    case: GenExamTestCase,
    questions: list[GeneratedQuestion],
    *,
    final_questions: list[GeneratedQuestion],
    topic_concept_ids: set[str],
    gen_time_sec: float,
    judged_labels: list[dict[str, Any] | None] | None = None,
    csr_source: str = CSR_SOURCE_UNAVAILABLE,
    csr_threshold: float = DEFAULT_CSR_THRESHOLD,
    sc_threshold: float = DEFAULT_SC_THRESHOLD,
) -> CaseMetrics:
    """計算單一 case 的所有指標，回傳 `CaseMetrics`。

    questions：通過驗證的題目（KC 與題數）；final_questions：每個題位最後一次的題目（CSR、SC）；
    judged_labels：評審對 final_questions 的判定，None 表示無法評審（CSR 為 N/A）。
    """
    # 依 case 整理出 runner 須驗的「實際限制條件」（不含 topic / count）
    required: dict[str, Any] = {"question_type": case.question_type}
    if case.difficulty is not None:
        required["difficulty"] = case.difficulty
    if case.bloom_level is not None:
        required["bloom_level"] = case.bloom_level

    kc = compute_kc(questions, topic_concept_ids)
    csr, csr_issues = compute_csr(final_questions, required, labels=judged_labels, requested_count=case.count)
    sc, sc_issues = compute_sc(final_questions, requested_count=case.count)
    if judged_labels is None:
        csr_source = CSR_SOURCE_UNAVAILABLE
    success = compute_task_success(
        questions_generated=len(questions),
        requested_count=case.count,
        csr=csr,
        sc=sc,
        csr_threshold=csr_threshold,
        sc_threshold=sc_threshold,
    )

    accepted_ids = {q.question_id for q in questions}
    per_q: list[dict[str, Any]] = []
    for i, q in enumerate(final_questions):
        per_q.append(
            {
                "question_id": q.question_id,
                "accepted": q.question_id in accepted_ids,
                "question_type": q.question_type,
                "difficulty": q.difficulty,
                "bloom_level": q.bloom_level,
                "judged": judged_labels[i] if judged_labels is not None and i < len(judged_labels) else None,
                "csr_issues": csr_issues[i] if i < len(csr_issues) else [],
                "sc_issues": sc_issues[i] if i < len(sc_issues) else [],
                "knowledge_refs": list(q.knowledge_refs),
            }
        )

    return CaseMetrics(
        case_id=case.case_id,
        category=case.category,
        topic=case.topic,
        requested_count=case.count,
        questions_generated=len(questions),
        kc=kc,
        csr=csr,
        sc=sc,
        task_success=success,
        gen_time_sec=gen_time_sec,
        constraints_required=required,
        per_question=per_q,
        csr_source=csr_source,
    )


# =============================================================================
# 5) 跨案彙整（對應投影片 SIMULATED RESULTS 那張表）
# =============================================================================
def aggregate_metrics(case_metrics: list[CaseMetrics]) -> dict[str, Any]:
    """
    回傳形如：
      {
        "overall": {...},
        "single_constraint": {...},
        "multi_constraint": {...},
      }
    """
    def _bucket(buckets: list[CaseMetrics]) -> dict[str, Any]:
        if not buckets:
            return {
                "n_cases": 0, "kc": 0.0, "csr": 0.0, "sc": 0.0,
                "task_success_rate": 0.0, "avg_gen_time_sec": 0.0,
            }
        kc = statistics.mean(m.kc for m in buckets)
        csr_values = [m.csr for m in buckets if m.csr is not None]      # N/A 的案例不列入
        csr = statistics.mean(csr_values) if csr_values else None
        sc = statistics.mean(m.sc for m in buckets)
        success = statistics.mean(1.0 if m.task_success else 0.0 for m in buckets)
        gt = statistics.mean(m.gen_time_sec for m in buckets)
        return {
            "n_cases": len(buckets),
            "kc": round(kc, 4),
            "csr": round(csr, 4) if csr is not None else None,
            "sc": round(sc, 4),
            "task_success_rate": round(success, 4),
            "avg_gen_time_sec": round(gt, 4),
        }

    single = [m for m in case_metrics if m.category == "single_constraint"]
    multi = [m for m in case_metrics if m.category == "multi_constraint"]
    return {
        "overall": _bucket(case_metrics),
        "single_constraint": _bucket(single),
        "multi_constraint": _bucket(multi),
    }


def format_results_table(agg: dict[str, Any]) -> str:
    """格式化成投影片風格的表格字串。"""
    rows = [
        ("KC (Knowledge Coverage)", "kc", "%"),
        ("CSR (Constraint Satisfaction Rate)", "csr", "%"),
        ("SC (Semantic Consistency)", "sc", "%"),
        ("Task Success Rate", "task_success_rate", "%"),
        ("Gen Time (sec/case)", "avg_gen_time_sec", "s"),
    ]
    s = []
    header = f"{'Metric':<38} | {'Single':>9} | {'Multi':>9} | {'Overall':>9}"
    sep = "-" * len(header)
    s.append(header)
    s.append(sep)
    for label, key, unit in rows:
        single = agg["single_constraint"].get(key, 0.0)
        multi = agg["multi_constraint"].get(key, 0.0)
        over = agg["overall"].get(key, 0.0)
        if unit == "%":
            cells = [format_rate(v, pct=True, digits=1) for v in (single, multi, over)]
            s.append(f"{label:<38} | {cells[0]:>9} | {cells[1]:>9} | {cells[2]:>9}")
        else:
            s.append(
                f"{label:<38} | {single:>8.2f}s | {multi:>8.2f}s | {over:>8.2f}s"
            )
    s.append(sep)
    s.append(
        f"{'n_cases':<38} | {agg['single_constraint']['n_cases']:>9} | "
        f"{agg['multi_constraint']['n_cases']:>9} | {agg['overall']['n_cases']:>9}"
    )
    return "\n".join(s)


# =============================================================================
# 自我測試（單元測試輕量版）
# =============================================================================
def _self_test() -> int:
    """模擬幾題正確 / 違規的問題，驗證所有指標公式合理。"""
    from genexam_scenario.test_cases import all_cases

    cases = all_cases()
    sample = cases[3]    # 任取一個 single-difficulty=hard 之類的案例

    # 模擬 topic 的 concept 池
    topic_concepts = {f"C_TEST_{i}" for i in range(10)}

    # 5 題：4 題完美，1 題違規（bloom/型別 + schema）
    perfect: list[GeneratedQuestion] = []
    for i in range(4):
        perfect.append(
            GeneratedQuestion(
                question_id=f"q{i}",
                stem="這是一道測試題目，請問下列何者為正確答案？",
                question_type=sample.question_type,
                difficulty=sample.difficulty,
                bloom_level=sample.bloom_level,
                choices=["A 選項描述", "B 選項描述", "C 選項描述", "D 選項描述"],
                answer="A 選項描述",
                knowledge_refs=[f"C_TEST_{i}"],
            )
        )
    bad = GeneratedQuestion(
        question_id="q_bad",
        stem="?",
        question_type="mcq",
        difficulty="easy" if sample.difficulty != "easy" else "hard",
        bloom_level=6,
        choices=["X"],
        answer="not_in_choices",
        knowledge_refs=[],
    )
    questions = perfect + [bad]

    # 4 題完美的通過驗證；評審的判定與題目標示相同
    labels = [
        {"difficulty": q.difficulty, "bloom_level": q.bloom_level, "question_type": q.question_type}
        for q in questions
    ]
    cm = evaluate_case(
        sample,
        perfect,
        final_questions=questions,
        judged_labels=labels,
        csr_source=CSR_SOURCE_EVAL_JUDGE,
        topic_concept_ids=topic_concepts,
        gen_time_sec=1.5,
    )
    print(f"sample case          : {sample.case_id}  ({sample.request})")
    print(f"questions_generated  : {cm.questions_generated} / {cm.requested_count}")
    print(f"KC  (4 distinct refs / 10 concepts = 0.4)   = {cm.kc:.2f}")
    print(f"CSR (4 of {cm.requested_count} judged as satisfying)          = {format_rate(cm.csr)}")
    print(f"SC  (4 of {cm.requested_count} pass schema)                   = {cm.sc:.2f}")
    print(f"task_success                                = {cm.task_success}")

    agg = aggregate_metrics([cm, cm, cm])
    print("\n--- aggregated（將 3 份相同 metrics 視為跨案彙整） ---")
    print(format_results_table(agg))
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
