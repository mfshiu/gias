"""
GenExam Scenario：單一案例執行器（GVR 閉環，真實 LLM 呼叫）

執行流程：

  1. Plan       ：建立 GenerationSession / Intention / Constraint 節點
  2. Retrieve   ：從 Blackboard 抓出 topic 相關的 Concept + Fact（KG-RAG 素材）
  3. Generate   ：對每題呼叫 LLM 產生試題（含選項 / 答案 / knowledge_refs）
  4. Verify     ：
       a. schema 一致性檢核（本地）
       b. knowledge_refs 是否真實存在 Blackboard（本地）
       c. （選用）LLM-judge：獨立評估 difficulty / bloom / question_type
  5. Refine     ：對未通過的題目呼叫 LLM 改寫，最多 N 次（N 預設 2）
  6. Finalize   ：把通過的題目寫回 Blackboard，session 標 Finalized
  7. Evaluate   ：呼叫 metrics.evaluate_case() 算 KC / CSR / SC / Success / GenTime

CLI：
    python -m genexam_scenario.runner <case_id>
    python -m genexam_scenario.runner single_diff_02 --llm-judge --max-refine 2
    python -m genexam_scenario.runner multi_07 --dry-run    # 不呼叫 LLM，用 mock 題

注意：本 runner 預期 actions / blackboard database 已 seed。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from src.app_helper import get_agent_config
from src.kg.adapter_neo4j import Neo4jBoltAdapter
from src.llm.client import LLMClient
from src.log_helper import init_logging

from genexam_scenario.config import BLOOM_LEVELS, BLOOM_RUBRIC, DIFFICULTY_RUBRIC
from genexam_scenario.metrics import (
    CaseMetrics,
    GeneratedQuestion,
    evaluate_case,
)
from genexam_scenario.test_cases import (
    BLOOM_LABELS_ZH,
    DIFFICULTY_LABELS_ZH,
    QUESTION_TYPE_LABELS_ZH,
    TOPIC_LABELS_ZH,
    GenExamTestCase,
    case_by_id,
)


logger = init_logging()


# =============================================================================
# 資料結構
# =============================================================================
@dataclass
class KnowledgeItem:
    concept_id: str
    concept_name: str
    concept_name_zh: str
    definition: str
    facts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class QuestionAttempt:
    attempt_no: int
    accepted: bool
    raw: dict[str, Any]
    sc_issues: list[str] = field(default_factory=list)
    csr_issues: list[str] = field(default_factory=list)
    grounding_issues: list[str] = field(default_factory=list)
    judge_difficulty: str | None = None
    judge_bloom: int | None = None
    judge_qtype: str | None = None
    judge_rationale: str | None = None        # judge 為何判此分級
    refine_directives: list[str] = field(default_factory=list)  # 此次傳給 refiner 的具體指令


@dataclass
class GenerationLog:
    case_id: str
    session_id: str
    started_at: float = 0.0
    finished_at: float = 0.0
    n_topic_concepts: int = 0
    knowledge_items_used: list[str] = field(default_factory=list)
    accepted_questions: list[dict[str, Any]] = field(default_factory=list)
    per_question_attempts: list[list[QuestionAttempt]] = field(default_factory=list)
    refine_total: int = 0
    fatal_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_sec": (self.finished_at - self.started_at)
                            if self.finished_at else 0.0,
            "n_topic_concepts": self.n_topic_concepts,
            "knowledge_items_used": self.knowledge_items_used,
            "accepted_questions": self.accepted_questions,
            "refine_total": self.refine_total,
            "per_question_attempts": [
                [asdict(a) for a in attempts]
                for attempts in self.per_question_attempts
            ],
            "fatal_error": self.fatal_error,
        }


@dataclass
class CaseResult:
    case: GenExamTestCase
    metrics: CaseMetrics
    log: GenerationLog

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "metrics": self.metrics.to_dict(),
            "log": self.log.to_dict(),
        }


# =============================================================================
# 連線
# =============================================================================
def build_blackboard_kg() -> Neo4jBoltAdapter:
    cfg = get_agent_config()
    base = cfg.get("kg", {}).get("neo4j") or {}
    overrides = cfg.get("kg", {}).get("neo4j_blackboard") or {}
    if not base or not overrides:
        raise RuntimeError("Missing [kg.neo4j] / [kg.neo4j_blackboard] in gias.toml")
    return Neo4jBoltAdapter.from_config({**base, **overrides}, logger=None)


def build_llm() -> LLMClient:
    return LLMClient.from_config(get_agent_config())


# =============================================================================
# Phase 1：Plan — 建立 GenerationSession / Intention / Constraint 節點
# =============================================================================
def plan_generation(kg: Neo4jBoltAdapter, case: GenExamTestCase) -> str:
    session_id = f"qgen_{case.case_id}_{uuid.uuid4().hex[:8]}"
    kg.write(
        """
        MERGE (s:GenerationSession {session_id:$sid})
        SET s.request_text = $req,
            s.topic = $topic,
            s.count = $count,
            s.status = 'Planning',
            s.started_at = datetime(),
            s.case_id = $cid
        """,
        {
            "sid": session_id,
            "req": case.request,
            "topic": case.topic,
            "count": case.count,
            "cid": case.case_id,
        },
    )
    kg.write(
        """
        MERGE (s:GenerationSession {session_id:$sid})
        MERGE (i:Intention {session_id:$sid})
        SET i.goal_text = $goal
        MERGE (s)-[:HAS_INTENTION]->(i)
        """,
        {"sid": session_id, "goal": case.request},
    )

    required: dict[str, Any] = {
        "topic": case.topic, "count": case.count,
        "question_type": case.question_type,
    }
    if case.difficulty is not None:
        required["difficulty"] = case.difficulty
    if case.bloom_level is not None:
        required["bloom_level"] = case.bloom_level

    for k, v in required.items():
        kg.write(
            """
            MATCH (s:GenerationSession {session_id:$sid})
            MERGE (c:Constraint {session_id:$sid, key:$k})
            SET c.value = $v,
                c.source = 'initial',
                c.phase = 'initial',
                c.locked = true
            MERGE (s)-[:HAS_CONSTRAINT]->(c)
            """,
            {"sid": session_id, "k": k, "v": str(v)},
        )

    return session_id


# =============================================================================
# Phase 2：Retrieve — KG-RAG 從 Blackboard 抓 Concept + Fact
# =============================================================================
def retrieve_knowledge(
    kg: Neo4jBoltAdapter,
    topic: str,
    *,
    top_k: int = 6,
) -> list[KnowledgeItem]:
    rows = kg.read(
        """
        MATCH (t:Topic {name:$topic})-[:HAS_SUBTOPIC]->(:Subtopic)
              -[:HAS_CONCEPT]->(c:Concept)
        OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(f:Fact)
        WITH c, collect(DISTINCT {id:f.id, statement:f.statement, source_id:f.source_id}) AS facts
        RETURN c.id AS concept_id, c.name AS concept_name,
               c.name_zh AS concept_name_zh, c.definition AS definition,
               facts
        ORDER BY c.id
        """,
        {"topic": topic},
    )
    items: list[KnowledgeItem] = []
    for r in rows:
        items.append(
            KnowledgeItem(
                concept_id=r["concept_id"],
                concept_name=r.get("concept_name") or "",
                concept_name_zh=r.get("concept_name_zh") or "",
                definition=r.get("definition") or "",
                facts=[
                    f for f in (r.get("facts") or [])
                    if isinstance(f, dict) and f.get("id")
                ],
            )
        )
    if top_k <= 0:
        return items
    return items[:top_k]


def get_topic_concept_ids(kg: Neo4jBoltAdapter, topic: str) -> set[str]:
    rows = kg.read(
        """
        MATCH (t:Topic {name:$topic})-[:HAS_SUBTOPIC]->(:Subtopic)
              -[:HAS_CONCEPT]->(c:Concept)
        RETURN c.id AS id
        """,
        {"topic": topic},
    )
    return {r["id"] for r in rows}


# =============================================================================
# Phase 3：Generate — 呼叫 LLM 產出單一題目
# =============================================================================
def _build_rubric_block() -> str:
    """供 generator / refiner / judge 三方共用的 rubric 區塊。"""
    diff_lines = "\n".join(
        f"  - {k}：{v}" for k, v in DIFFICULTY_RUBRIC.items()
    )
    bloom_lines = "\n".join(
        f"  - level {k}：{v}" for k, v in BLOOM_RUBRIC.items()
    )
    return (
        "【難度判準（Difficulty Rubric）】\n"
        + diff_lines
        + "\n\n【Bloom 認知層級判準（Bloom Rubric）】\n"
        + bloom_lines
    )


_GENERATE_SYSTEM_PROMPT = (
    """你是一個資深的環境永續教育出題專家。請依使用者指定的「主題」、「難度」、「Bloom 認知層級」、「題型」要求出題。

嚴格遵守以下原則：
1. 題幹與選項只能取材自使用者提供的「知識素材（Concept / Fact）」，不得幻覺。
2. 必須回傳合法 JSON（無前後文字、無 ```markdown 圍欄），符合給定 schema。
3. difficulty / bloom_level / question_type 必須與使用者要求一致（除非未指定）；
   出題時請對照下方判準，確保題目「實質上」符合該等級，而非只在欄位上標註。
4. 對於 mcq：提供 4 個選項、僅 1 個正確答案、答案字串需完全等於選項中的某一個。
5. 對於 true_false：answer 為布林（true 或 false）。
6. 對於 short_answer：answer 為非空字串（≤ 60 字）。
7. 對於 cloze：題幹中需用「____」標示空格，answer 為填入的字串。
8. knowledge_refs 必須包含至少 1 個你實際引用到的 Concept.id 或 Fact.id。
9. 若難度為 hard，knowledge_refs 應同時引用 ≥ 2 個不同 Concept（跨概念因果鏈），
   題幹必須提供具體情境資料（數值／條件／限制），干擾選項中至少 2 個是「概念相近但細節不同」的陷阱。

"""
    + _build_rubric_block()
)


def _build_generate_user_prompt(
    case: GenExamTestCase,
    knowledge_pool: list[KnowledgeItem],
    used_concept_ids: set[str],
) -> str:
    topic_zh = TOPIC_LABELS_ZH.get(case.topic, case.topic)

    # 優先用「尚未引用過」的 concept，分散題目對知識的覆蓋（提高 KC）
    candidates = [k for k in knowledge_pool if k.concept_id not in used_concept_ids]
    if not candidates:
        candidates = list(knowledge_pool)

    # 隨機取 3 個 candidate 作為素材
    sample = random.sample(candidates, k=min(3, len(candidates)))

    knowledge_lines: list[str] = []
    for k in sample:
        knowledge_lines.append(
            f"- Concept[{k.concept_id}] {k.concept_name_zh}（{k.concept_name}）：{k.definition}"
        )
        for f in k.facts[:2]:
            knowledge_lines.append(
                f"    Fact[{f['id']}] {f.get('statement','')}"
            )

    parts: list[str] = []
    parts.append(f"主題：{topic_zh}（topic_id={case.topic}）")
    parts.append(f"題型：{case.question_type}（{QUESTION_TYPE_LABELS_ZH.get(case.question_type)}）")
    if case.difficulty is not None:
        parts.append(
            f"難度：{case.difficulty}（{DIFFICULTY_LABELS_ZH.get(case.difficulty)}）"
        )
    if case.bloom_level is not None:
        parts.append(
            f"Bloom Level：{case.bloom_level}（{BLOOM_LABELS_ZH.get(case.bloom_level)}）"
        )
    parts.append("")
    parts.append("可用的知識素材（請挑選並引用）：")
    parts.extend(knowledge_lines)
    parts.append("")
    parts.append("請回傳 JSON，schema：")
    parts.append("""{
  "stem": "題幹（必填）",
  "question_type": "mcq | true_false | short_answer | cloze",
  "difficulty": "easy | medium | hard",
  "bloom_level": 1,
  "choices": ["A", "B", "C", "D"],
  "answer": "A | true | 字串",
  "rationale": "解答原理 / 為何此答案符合 Bloom 等級",
  "knowledge_refs": ["C_XXX", "F_XXX_1"]
}""")
    return "\n".join(parts)


def generate_question(
    llm: LLMClient,
    case: GenExamTestCase,
    knowledge_pool: list[KnowledgeItem],
    used_concept_ids: set[str],
    *,
    attempt_no: int = 1,
) -> dict[str, Any]:
    user_prompt = _build_generate_user_prompt(case, knowledge_pool, used_concept_ids)
    messages = [
        {"role": "system", "content": _GENERATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    obj = llm.json(
        messages,
        schema=None,
        temperature=0.6,
        response_format={"type": "json_object"},
    )
    if not isinstance(obj, dict):
        raise RuntimeError(f"Generator returned non-dict: {obj!r}")
    return obj


# =============================================================================
# Phase 4：Verify — 本地 schema + 知識 + 限制檢核；可選 LLM-judge
# =============================================================================
_JUDGE_SYSTEM_PROMPT = (
    """你是一個嚴格的試題評審。任務：依據下方「判準」獨立判定給定試題的
difficulty / bloom_level / question_type，並指出具體理由。

僅回傳 JSON，無其他文字：
{
  "difficulty": "easy | medium | hard",
  "bloom_level": 1,
  "question_type": "mcq | true_false | short_answer | cloze",
  "judge_rationale": "用 1-2 句中文具體說明本題為何屬於此 difficulty 與 bloom（例：『僅需識別單一定義，缺乏跨概念推理或情境資料，故為 medium』）。"
}

評審時注意：
- 「自己宣稱 hard」不代表它就是 hard，請依「實質特徵」判斷。
- 若題目能僅憑單一 Concept / Fact 直接得解，必為 easy 或 medium。

"""
    + _build_rubric_block()
)


def _validate_knowledge_refs(
    refs: list[str],
    topic_concepts: set[str],
    all_fact_ids: set[str],
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if not refs:
        issues.append("missing_knowledge_refs")
        return False, issues
    valid_count = 0
    for r in refs:
        if r in topic_concepts or r in all_fact_ids:
            valid_count += 1
        else:
            issues.append(f"unknown_ref:{r}")
    if valid_count == 0:
        issues.append("no_valid_knowledge_ref")
        return False, issues
    return True, issues


def _to_generated_question(raw: dict[str, Any]) -> GeneratedQuestion:
    return GeneratedQuestion(
        question_id=str(raw.get("question_id") or uuid.uuid4().hex[:8]),
        stem=str(raw.get("stem") or ""),
        question_type=str(raw.get("question_type") or "mcq"),
        difficulty=raw.get("difficulty"),
        bloom_level=int(raw["bloom_level"]) if raw.get("bloom_level") is not None else None,
        choices=list(raw.get("choices") or []),
        answer=raw.get("answer"),
        rationale=str(raw.get("rationale") or ""),
        knowledge_refs=list(raw.get("knowledge_refs") or []),
    )


def llm_judge(
    llm: LLMClient,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """獨立 LLM 對 difficulty / bloom / qtype 重新判定。"""
    payload = {
        "stem": raw.get("stem"),
        "choices": raw.get("choices"),
        "answer": raw.get("answer"),
        "rationale": raw.get("rationale"),
    }
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user",
         "content": f"請判定下列試題：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"},
    ]
    obj = llm.json(messages, schema=None, temperature=0.0,
                   response_format={"type": "json_object"})
    if not isinstance(obj, dict):
        return {}
    return obj


def verify_question(
    raw: dict[str, Any],
    case: GenExamTestCase,
    *,
    topic_concepts: set[str],
    all_fact_ids: set[str],
    llm: LLMClient | None = None,
    use_llm_judge: bool = False,
) -> QuestionAttempt:
    from genexam_scenario.metrics import _is_schema_consistent, _check_constraints

    q = _to_generated_question(raw)

    # 1) schema 一致性
    sc_ok, sc_issues = _is_schema_consistent(q)

    # 2) 限制條件（用 LLM judge 或自宣告）
    judge_diff = judge_bloom = judge_qt = None
    judge_rationale: str | None = None
    if use_llm_judge and llm is not None:
        j = llm_judge(llm, raw)
        if isinstance(j, dict):
            judge_diff = j.get("difficulty")
            judge_bloom = int(j["bloom_level"]) if j.get("bloom_level") is not None else None
            judge_qt = j.get("question_type")
            judge_rationale = j.get("judge_rationale") or j.get("rationale")
            q.difficulty = judge_diff or q.difficulty
            q.bloom_level = judge_bloom if judge_bloom is not None else q.bloom_level
            q.question_type = judge_qt or q.question_type

    required: dict[str, Any] = {"question_type": case.question_type}
    if case.difficulty is not None:
        required["difficulty"] = case.difficulty
    if case.bloom_level is not None:
        required["bloom_level"] = case.bloom_level
    csr_ok, csr_issues = _check_constraints(q, required)

    # 3) 知識依據
    ground_ok, ground_issues = _validate_knowledge_refs(
        q.knowledge_refs, topic_concepts, all_fact_ids,
    )

    accepted = sc_ok and csr_ok and ground_ok
    return QuestionAttempt(
        attempt_no=int(raw.get("attempt_no", 1)),
        accepted=accepted,
        raw={**raw, "question_id": q.question_id},
        sc_issues=sc_issues,
        csr_issues=csr_issues,
        grounding_issues=ground_issues,
        judge_difficulty=judge_diff,
        judge_bloom=judge_bloom,
        judge_qtype=judge_qt,
        judge_rationale=judge_rationale,
    )


# =============================================================================
# Phase 5：Refine — 對未通過的題目改寫
# =============================================================================
_REFINE_SYSTEM_PROMPT = (
    """你是一個資深的試題改寫者。請依下方「現有試題」、「驗證者列出的問題」與「具體改寫指令」，
產生一道**修正後**的試題（同一題位、同樣題型 / 難度 / Bloom，不可放寬）。

原則：
1. 必須**逐條**處理「具體改寫指令」中的每一項；不可避重就輕。
2. 題型 / 難度 / Bloom 等限制不可放寬。寧可重寫整題，也不要只改欄位標籤敷衍。
3. 必須保留並合理引用至少 1 個 Concept.id 或 Fact.id 作為 knowledge_refs；
   若難度為 hard，請改用「跨 ≥ 2 個 Concept」的因果鏈出題。
4. 僅回傳 JSON，無其他文字，schema 同 generator。

"""
    + _build_rubric_block()
)


def _build_refine_directives(
    *,
    case: GenExamTestCase,
    prev_attempt: "QuestionAttempt",
) -> list[str]:
    """將 verifier / judge 的駁回資訊轉成「給 refiner 的具體改寫指令」。

    對 difficulty / bloom 不符的情境給出**可執行**的指引，而非只回覆 mismatch 字串。
    """
    directives: list[str] = []
    csr_issues = prev_attempt.csr_issues or []
    judge_diff = prev_attempt.judge_difficulty
    judge_bloom = prev_attempt.judge_bloom
    judge_qt = prev_attempt.judge_qtype
    judge_rationale = prev_attempt.judge_rationale

    diff_mismatch = any("difficulty mismatch" in s for s in csr_issues)
    bloom_mismatch = any("bloom mismatch" in s for s in csr_issues)
    qtype_mismatch = any("qtype mismatch" in s for s in csr_issues)

    if diff_mismatch and case.difficulty == "hard":
        directives.append(
            "【難度強化指令】Judge 判定本題為 "
            + str(judge_diff or "easier-than-hard")
            + "，需要升級為 hard："
            + (f"（judge 理由：{judge_rationale}）" if judge_rationale else "")
            + " 請執行下列其中 ≥ 2 項："
            "(a) 題幹加入具體情境資料（數值、時間尺度、限制條件、地區）；"
            "(b) 將「直接回憶或單一定義」改為「跨 ≥ 2 個 Concept 的因果推理」"
            "（例：A 概念導致 B 現象，B 現象再影響 C 對策，問哪個對策最有效）；"
            "(c) 設計 ≥ 2 個「概念相近但細節不同」的干擾選項"
            "（例：機制相同但作用對象不同／因果方向相反／時間尺度不同）；"
            "(d) 移除題幹中直接洩漏答案的關鍵字（如「定義」、「主要原因」等明示語）。"
            " **不可只修改 difficulty 欄位敷衍**。"
        )
    elif diff_mismatch:
        directives.append(
            f"【難度修正指令】本題實際為 {judge_diff or 'unknown'}，"
            f"使用者要求 {case.difficulty}。"
            + (f"（judge 理由：{judge_rationale}）" if judge_rationale else "")
            + "請依判準調整題幹深度與選項陷阱層次。"
        )

    if bloom_mismatch:
        directives.append(
            f"【Bloom 修正指令】本題實際處於 Bloom={judge_bloom or '?'}，"
            f"使用者要求 Bloom={case.bloom_level}。"
            + (f"（judge 理由：{judge_rationale}）" if judge_rationale else "")
            + " 請依 Bloom 判準改寫，例如："
            "Bloom 4 (Analyze) 需「拆解情境並推論因果」；"
            "Bloom 5 (Evaluate) 需「依準則對方案做價值判斷」；"
            "Bloom 6 (Create) 需「整合多個概念提出新方案」。"
        )

    if qtype_mismatch:
        directives.append(
            f"【題型修正指令】本題實際被判為 {judge_qt or 'unknown'}，"
            f"使用者要求 {case.question_type}。"
            "請嚴格遵照題型 schema："
            f"{'mcq=4 選項僅 1 正解；' if case.question_type=='mcq' else ''}"
            f"{'cloze=題幹必含 ____ 填空標記，answer 為填入字串；' if case.question_type=='cloze' else ''}"
            f"{'true_false=answer 為布林；' if case.question_type=='true_false' else ''}"
            f"{'short_answer=answer 為單一非空字串；' if case.question_type=='short_answer' else ''}"
        )

    if prev_attempt.sc_issues:
        directives.append(
            "【Schema 修正】請修正下列結構問題："
            + "; ".join(prev_attempt.sc_issues)
        )

    if prev_attempt.grounding_issues:
        directives.append(
            "【知識依據修正】"
            + "; ".join(prev_attempt.grounding_issues)
            + "。knowledge_refs 必須只放實際存在的 Concept.id 或 Fact.id。"
        )

    if not directives:
        directives.append("請依使用者要求重新產生一題。")
    return directives


def refine_question(
    llm: LLMClient,
    case: GenExamTestCase,
    prev_attempt: QuestionAttempt,
    knowledge_pool: list[KnowledgeItem],
    used_concept_ids: set[str],
    *,
    attempt_no: int,
) -> tuple[dict[str, Any], list[str]]:
    """改寫一道題；同時回傳這次傳給 refiner 的「具體改寫指令」供 log。

    與 v1 的差異：
    1. 每次 refine 重新抽 knowledge sample（避免繞同一個 Concept）。
    2. 把 judge 的 rationale + 具體改寫指令一併餵入。
    """
    directives = _build_refine_directives(case=case, prev_attempt=prev_attempt)

    user_prompt_base = _build_generate_user_prompt(case, knowledge_pool, used_concept_ids)
    extra_parts: list[str] = [
        "",
        "=== 現有試題（待修正）===",
        json.dumps(prev_attempt.raw, ensure_ascii=False, indent=2),
        "",
        "=== Judge 的獨立判定 ===",
        json.dumps(
            {
                "judge_difficulty": prev_attempt.judge_difficulty,
                "judge_bloom": prev_attempt.judge_bloom,
                "judge_qtype": prev_attempt.judge_qtype,
                "judge_rationale": prev_attempt.judge_rationale,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "=== 具體改寫指令（必須逐條處理）===",
    ]
    extra_parts.extend(f"- {d}" for d in directives)
    extra_parts.append("")
    extra_parts.append(f"（本次為第 {attempt_no} 次改寫嘗試）")
    extra = "\n".join(extra_parts)

    messages = [
        {"role": "system", "content": _REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt_base + "\n" + extra},
    ]
    obj = llm.json(messages, schema=None, temperature=0.4,
                   response_format={"type": "json_object"})
    if not isinstance(obj, dict):
        raise RuntimeError(f"Refiner returned non-dict: {obj!r}")
    obj["attempt_no"] = attempt_no
    return obj, directives


# =============================================================================
# Phase 6：Finalize — 寫回 Blackboard
# =============================================================================
def persist_question(
    kg: Neo4jBoltAdapter,
    session_id: str,
    raw: dict[str, Any],
) -> str:
    q = _to_generated_question(raw)
    kg.write(
        """
        MATCH (s:GenerationSession {session_id:$sid})
        MERGE (q:Question {question_id:$qid})
        SET q.session_id = $sid,
            q.stem = $stem,
            q.type = $qt,
            q.difficulty = $diff,
            q.bloom_level = $bloom,
            q.answer_text = $ans_text,
            q.rationale = $rat,
            q.status = 'verified',
            q.knowledge_refs = $refs
        MERGE (s)-[:HAS_QUESTION]->(q)
        """,
        {
            "sid": session_id, "qid": q.question_id,
            "stem": q.stem, "qt": q.question_type,
            "diff": q.difficulty, "bloom": q.bloom_level,
            "ans_text": q.answer if isinstance(q.answer, str) else json.dumps(q.answer, ensure_ascii=False),
            "rat": q.rationale, "refs": list(q.knowledge_refs),
        },
    )
    # MCQ 的選項
    if q.question_type == "mcq":
        for i, ch in enumerate(q.choices):
            kg.write(
                """
                MATCH (q:Question {question_id:$qid})
                MERGE (c:Choice {choice_id:$cid})
                SET c.text = $txt,
                    c.is_correct = $ok
                MERGE (q)-[:HAS_CHOICE {order:$ord}]->(c)
                """,
                {
                    "qid": q.question_id, "cid": f"{q.question_id}_{i}",
                    "txt": str(ch), "ok": (ch == q.answer), "ord": i,
                },
            )
    # 知識依據
    for ref in q.knowledge_refs:
        kg.write(
            """
            MATCH (q:Question {question_id:$qid})
            OPTIONAL MATCH (c:Concept {id:$ref})
            OPTIONAL MATCH (f:Fact {id:$ref})
            WITH q, c, f
            FOREACH (_ IN CASE WHEN c IS NOT NULL THEN [1] ELSE [] END |
                MERGE (q)-[:GROUNDED_ON]->(c)
            )
            FOREACH (_ IN CASE WHEN f IS NOT NULL THEN [1] ELSE [] END |
                MERGE (q)-[:GROUNDED_ON]->(f)
            )
            """,
            {"qid": q.question_id, "ref": ref},
        )
    # 分類學連線（Difficulty / BloomLevel / QuestionType）
    kg.write(
        """
        MATCH (q:Question {question_id:$qid})
        OPTIONAL MATCH (d:Difficulty {name:$diff})
        OPTIONAL MATCH (qt:QuestionType {name:$qt})
        OPTIONAL MATCH (bl:BloomLevel {level:$bloom})
        FOREACH (_ IN CASE WHEN d IS NOT NULL THEN [1] ELSE [] END |
            MERGE (q)-[:HAS_DIFFICULTY]->(d))
        FOREACH (_ IN CASE WHEN qt IS NOT NULL THEN [1] ELSE [] END |
            MERGE (q)-[:OF_TYPE]->(qt))
        FOREACH (_ IN CASE WHEN bl IS NOT NULL THEN [1] ELSE [] END |
            MERGE (q)-[:HAS_BLOOM_LEVEL]->(bl))
        """,
        {"qid": q.question_id, "diff": q.difficulty,
         "qt": q.question_type, "bloom": q.bloom_level},
    )
    return q.question_id


def finalize_session(kg: Neo4jBoltAdapter, session_id: str) -> None:
    kg.write(
        """
        MATCH (s:GenerationSession {session_id:$sid})
        SET s.status = 'Finalized', s.finished_at = datetime()
        """,
        {"sid": session_id},
    )


# =============================================================================
# Dry-run：產生 mock 題目（不呼叫 LLM）
# =============================================================================
def _mock_question(
    case: GenExamTestCase,
    knowledge_pool: list[KnowledgeItem],
    used: set[str],
) -> dict[str, Any]:
    pool = [k for k in knowledge_pool if k.concept_id not in used] or list(knowledge_pool)
    k = random.choice(pool)
    qt = case.question_type
    diff = case.difficulty or "medium"
    bloom = case.bloom_level or 2

    base = {
        "stem": f"關於「{k.concept_name_zh}」，下列敘述何者正確？",
        "question_type": qt,
        "difficulty": diff,
        "bloom_level": bloom,
        "rationale": f"依據 KG 中 {k.concept_id} 的定義：{k.definition}",
        "knowledge_refs": [k.concept_id] + [f["id"] for f in k.facts[:1]],
    }
    if qt == "mcq":
        base["choices"] = [
            f"{k.concept_name_zh}的核心定義是「{k.definition[:20]}…」",
            "與該主題無關的錯誤敘述 A",
            "與該主題無關的錯誤敘述 B",
            "與該主題無關的錯誤敘述 C",
        ]
        base["answer"] = base["choices"][0]
    elif qt == "true_false":
        base["stem"] = f"「{k.concept_name_zh}」的定義是：{k.definition}。此敘述正確嗎？"
        base["answer"] = True
    elif qt == "short_answer":
        base["stem"] = f"請簡述「{k.concept_name_zh}」的定義。"
        base["answer"] = k.definition[:60]
    elif qt == "cloze":
        base["stem"] = f"____ 是指：{k.definition}（填入正確的概念名稱）"
        base["answer"] = k.concept_name_zh
    return base


# =============================================================================
# 核心：run_case
# =============================================================================
def run_case(
    case: GenExamTestCase,
    *,
    kg: Neo4jBoltAdapter,
    llm: LLMClient | None,
    max_refine_attempts: int = 2,
    use_llm_judge: bool = False,
    dry_run: bool = False,
    verbose: bool = True,
    seed: int | None = None,
) -> CaseResult:
    if seed is not None:
        random.seed(seed)

    log = GenerationLog(case_id=case.case_id, session_id="")
    log.started_at = time.monotonic()

    try:
        # 1) Plan
        session_id = plan_generation(kg, case)
        log.session_id = session_id
        if verbose:
            print(f"  [plan] session_id={session_id}")

        # 2) Retrieve
        topic_concepts = get_topic_concept_ids(kg, case.topic)
        log.n_topic_concepts = len(topic_concepts)
        knowledge_pool = retrieve_knowledge(kg, case.topic, top_k=0)
        all_fact_ids = {f["id"] for k in knowledge_pool for f in k.facts}
        if verbose:
            print(f"  [retrieve] {len(knowledge_pool)} concepts / {len(all_fact_ids)} facts")

        # 3-5) Generate → Verify → Refine 迴圈
        accepted_raws: list[dict[str, Any]] = []
        used: set[str] = set()

        for i in range(case.count):
            attempts: list[QuestionAttempt] = []
            current_raw: dict[str, Any] | None = None

            for attempt_no in range(1, max_refine_attempts + 2):
                # generate or refine
                if attempt_no == 1:
                    if dry_run or llm is None:
                        current_raw = _mock_question(case, knowledge_pool, used)
                    else:
                        current_raw = generate_question(
                            llm, case, knowledge_pool, used,
                            attempt_no=attempt_no,
                        )
                else:
                    prev_attempt = attempts[-1]
                    directives_for_log: list[str] = []
                    if dry_run or llm is None:
                        current_raw = _mock_question(case, knowledge_pool, used)
                    else:
                        current_raw, directives_for_log = refine_question(
                            llm, case, prev_attempt,
                            knowledge_pool, used,
                            attempt_no=attempt_no,
                        )
                    log.refine_total += 1

                current_raw["attempt_no"] = attempt_no
                att = verify_question(
                    current_raw, case,
                    topic_concepts=topic_concepts,
                    all_fact_ids=all_fact_ids,
                    llm=llm if use_llm_judge else None,
                    use_llm_judge=use_llm_judge,
                )
                if attempt_no > 1:
                    att.refine_directives = directives_for_log
                attempts.append(att)

                if verbose:
                    badge = "OK" if att.accepted else "FAIL"
                    print(
                        f"  [q{i+1}/{case.count}] attempt={attempt_no} [{badge}] "
                        f"sc_issues={len(att.sc_issues)} "
                        f"csr_issues={len(att.csr_issues)} "
                        f"ground_issues={len(att.grounding_issues)}"
                    )

                if att.accepted:
                    break

            # 最後一輪結果：accepted or fallback（取最後一次）
            final_attempt = next((a for a in attempts if a.accepted), attempts[-1])
            log.per_question_attempts.append(attempts)
            if final_attempt.accepted:
                accepted_raws.append(final_attempt.raw)
                # 記下已用 concept，下一題避開
                for ref in final_attempt.raw.get("knowledge_refs", []) or []:
                    if ref in topic_concepts:
                        used.add(ref)

        # 6) Finalize：寫回 Blackboard
        for raw in accepted_raws:
            persist_question(kg, session_id, raw)
            log.accepted_questions.append(raw)
            for ref in raw.get("knowledge_refs", []) or []:
                if ref in topic_concepts and ref not in log.knowledge_items_used:
                    log.knowledge_items_used.append(ref)
        finalize_session(kg, session_id)

    except Exception as e:
        log.fatal_error = f"{type(e).__name__}: {e}"
        if verbose:
            print(f"  [fatal] {log.fatal_error}", file=sys.stderr)

    log.finished_at = time.monotonic()
    elapsed = log.finished_at - log.started_at

    # 7) Evaluate：把通過 verifier 的題目轉成 GeneratedQuestion 餵給 metrics
    accepted_questions = [_to_generated_question(r) for r in log.accepted_questions]
    metrics = evaluate_case(
        case,
        accepted_questions,
        topic_concept_ids=topic_concepts,
        gen_time_sec=elapsed,
    )

    if verbose:
        print(
            f"  [eval] KC={metrics.kc:.2f} CSR={metrics.csr:.2f} "
            f"SC={metrics.sc:.2f} success={metrics.task_success} "
            f"elapsed={elapsed:.2f}s refines={log.refine_total}"
        )

    return CaseResult(case=case, metrics=metrics, log=log)


# =============================================================================
# CLI
# =============================================================================
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="GenExam Scenario：執行單一試題生成案例（GVR 閉環）",
    )
    p.add_argument("case_id", help="例：single_diff_02 / multi_07")
    p.add_argument("--dry-run", action="store_true",
                   help="不呼叫 LLM，使用 mock 題目（測試 pipeline）")
    p.add_argument("--llm-judge", action="store_true",
                   help="Verifier 啟用獨立 LLM 重新判定 difficulty/bloom/qtype")
    p.add_argument("--max-refine", type=int, default=2,
                   help="每題最多 refine 次數（預設 2）")
    p.add_argument("--out", default=None,
                   help="把結果輸出為 JSON 檔（不指定則僅印到 stdout）")
    p.add_argument("--seed", type=int, default=None,
                   help="random seed（影響 mock 與 knowledge 抽樣）")
    args = p.parse_args(argv)

    case = case_by_id(args.case_id)
    if case is None:
        print(f"找不到 case_id={args.case_id}", file=sys.stderr)
        return 1

    print(f"\n=== run_case: {case.case_id} ===")
    print(f"  request : {case.request}")
    print(
        f"  topic={case.topic}, count={case.count}, "
        f"difficulty={case.difficulty}, bloom={case.bloom_level}, "
        f"type={case.question_type}"
    )
    print(
        f"  mode    : "
        f"{'dry-run (mock)' if args.dry_run else 'live (LLM)'}"
        f"  llm_judge={args.llm_judge}"
        f"  max_refine={args.max_refine}"
    )

    kg = build_blackboard_kg()
    llm = None if args.dry_run else build_llm()

    try:
        result = run_case(
            case,
            kg=kg, llm=llm,
            max_refine_attempts=args.max_refine,
            use_llm_judge=args.llm_judge,
            dry_run=args.dry_run,
            verbose=True,
            seed=args.seed,
        )
    finally:
        try:
            kg.close()
        except Exception:
            pass

    print("\n--- summary ---")
    print(
        f"  case_id  = {result.case.case_id}"
        f"  category = {result.case.category}"
    )
    print(
        f"  KC={result.metrics.kc:.3f}  CSR={result.metrics.csr:.3f}  "
        f"SC={result.metrics.sc:.3f}  success={result.metrics.task_success}  "
        f"gen_time={result.metrics.gen_time_sec:.2f}s  "
        f"refines={result.log.refine_total}"
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"  寫出：{args.out}")

    return 0 if (result.log.fatal_error is None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
