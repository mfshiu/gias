"""
GenExam Scenario 測試案例定義（60 案例，全於生成前固定限制條件）

對應評估二投影片：
  Single-Constraint Generation : 30 例（單一限制：difficulty 或 question_type）
  Multi-Constraint Generation  : 30 例（三限制全套：difficulty + bloom_level + question_type）

注意：本評估**不**做生成中途的動態事件注入；
      所有限制條件在 GenerationSession 建立時即固定，
      用以單純評估 GIAS（GVR + KG-RAG + IPM）的「可控生成」與「知識一致性」能力。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Literal


Category = Literal["single_constraint", "multi_constraint"]
ConstraintFocus = Literal["difficulty", "question_type", "all"]


# =============================================================================
# 中文化標籤
# =============================================================================
DIFFICULTY_LABELS_ZH: dict[str, str] = {
    "easy": "簡單",
    "medium": "中等難度",
    "hard": "困難",
}

QUESTION_TYPE_LABELS_ZH: dict[str, str] = {
    "mcq": "選擇題",
    "true_false": "是非題",
    "short_answer": "簡答題",
    "cloze": "填空題",
}

BLOOM_LABELS_ZH: dict[int, str] = {
    1: "記憶",
    2: "理解",
    3: "應用",
    4: "分析",
    5: "評鑑",
    6: "創造",
}

TOPIC_LABELS_ZH: dict[str, str] = {
    "air_pollution": "空氣污染",
    "waste_management": "廢棄物管理",
    "climate_change": "氣候變遷",
    "water_resources": "水資源",
    "biodiversity": "生物多樣性",
    "energy_conservation": "能源節約",
}


# =============================================================================
# 案例資料結構
# =============================================================================
@dataclass(frozen=True)
class GenExamTestCase:
    # 防止 pytest 把這個 dataclass 當成測試類別蒐集
    __test__ = False

    case_id: str
    category: Category
    request: str                      # 自然語言任務描述（餵給 CoordinatorBot）
    topic: str                        # 主題 id（對應 Blackboard 的 Topic.name）
    count: int                        # 要產生的題數
    constraint_focus: ConstraintFocus  # 本案例著重測試的限制維度
    difficulty: str | None = None      # easy / medium / hard；None = 不限定
    bloom_level: int | None = None     # 1–6；None = 不限定
    question_type: str = "mcq"         # 預設選擇題（投影片強調「選擇題」）
    expected_min_pass: int | None = None  # 至少需通過 Verifier 的題數（None = count）
    notes: str = ""

    def constraints_dict(self) -> dict[str, Any]:
        """轉成 {constraint_key: value} 的純字典，餵給 GVR pipeline。"""
        d: dict[str, Any] = {
            "topic": self.topic,
            "count": self.count,
            "question_type": self.question_type,
        }
        if self.difficulty is not None:
            d["difficulty"] = self.difficulty
        if self.bloom_level is not None:
            d["bloom_level"] = self.bloom_level
        return d

    @property
    def min_pass(self) -> int:
        return self.expected_min_pass if self.expected_min_pass is not None else self.count

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =============================================================================
# 文字組裝
# =============================================================================
def _build_request_text(
    topic_zh: str,
    count: int,
    difficulty: str | None,
    bloom_level: int | None,
    question_type: str,
) -> str:
    """組裝自然語言請求，例：
       「請生成 5 題關於空氣污染的中等難度選擇題」
       「請生成 5 題關於廢棄物管理的困難選擇題（Bloom Level 4：分析）」
    """
    qtype_zh = QUESTION_TYPE_LABELS_ZH[question_type]

    parts: list[str] = [f"請生成 {count} 題關於{topic_zh}的"]
    if difficulty:
        parts.append(DIFFICULTY_LABELS_ZH[difficulty])
    parts.append(qtype_zh)

    base = "".join(parts)
    if bloom_level is not None:
        bloom_zh = BLOOM_LABELS_ZH[bloom_level]
        base += f"（Bloom Level {bloom_level}：{bloom_zh}）"
    return base


# =============================================================================
# Single-Constraint Generation：30 案
# =============================================================================
#
# 設計策略：將 30 案均分為兩組「單一限制聚焦」：
#   - Group A (15 案)：聚焦於 difficulty 維度
#       5 個主題 × 3 個難度 = 15
#       此時 question_type 固定為 mcq；不指定 bloom_level
#   - Group B (15 案)：聚焦於 question_type 維度
#       5 個主題 × 3 種題型（每主題輪換 3 種題型）= 15
#       此時 difficulty / bloom_level 皆不指定（讓系統依主題自然選擇）
#
# 評估意義：分別檢驗系統在「難度可控」與「題型可控」兩個獨立維度上的表現。
# -----------------------------------------------------------------------------

# Group A：5 個主題 × 3 個難度
_SINGLE_A_TOPICS: list[str] = [
    "air_pollution",
    "waste_management",
    "climate_change",
    "water_resources",
    "biodiversity",
]

_SINGLE_A_DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

# Group B：5 個主題 × 3 種題型（每主題不同組合）
_SINGLE_B_PLAN: list[tuple[str, tuple[str, ...]]] = [
    ("air_pollution",      ("mcq", "true_false", "short_answer")),
    ("waste_management",   ("mcq", "true_false", "cloze")),
    ("climate_change",     ("mcq", "short_answer", "cloze")),
    ("water_resources",    ("true_false", "short_answer", "cloze")),
    ("energy_conservation", ("mcq", "true_false", "short_answer")),
]


def _build_single_constraint_cases() -> list[GenExamTestCase]:
    out: list[GenExamTestCase] = []
    count_per_case = 5

    # ----- Group A：difficulty constraint --------------------------------
    case_idx = 0
    for topic_id in _SINGLE_A_TOPICS:
        topic_zh = TOPIC_LABELS_ZH[topic_id]
        for diff in _SINGLE_A_DIFFICULTIES:
            case_idx += 1
            req = _build_request_text(
                topic_zh, count_per_case, diff, None, "mcq",
            )
            out.append(
                GenExamTestCase(
                    case_id=f"single_diff_{case_idx:02d}",
                    category="single_constraint",
                    request=req,
                    topic=topic_id,
                    count=count_per_case,
                    constraint_focus="difficulty",
                    difficulty=diff,
                    bloom_level=None,
                    question_type="mcq",
                    notes=f"Single-Constraint: difficulty={diff}, type=mcq (default)",
                )
            )

    # ----- Group B：question_type constraint -----------------------------
    case_idx = 0
    for topic_id, qtypes in _SINGLE_B_PLAN:
        topic_zh = TOPIC_LABELS_ZH[topic_id]
        for qt in qtypes:
            case_idx += 1
            req = _build_request_text(
                topic_zh, count_per_case, None, None, qt,
            )
            out.append(
                GenExamTestCase(
                    case_id=f"single_qtype_{case_idx:02d}",
                    category="single_constraint",
                    request=req,
                    topic=topic_id,
                    count=count_per_case,
                    constraint_focus="question_type",
                    difficulty=None,
                    bloom_level=None,
                    question_type=qt,
                    notes=f"Single-Constraint: question_type={qt}",
                )
            )

    return out


# =============================================================================
# Multi-Constraint Generation：30 案
# =============================================================================
#
# 設計策略：6 主題 × 5 案 = 30，每案完整指定 (difficulty, bloom_level, question_type)
#
# 5 案的限制組合（涵蓋從低階記憶到高階創造的認知光譜，並穿插不同題型）：
#   index 0: (easy,   bloom=1, mcq)          記憶 / 簡單
#   index 1: (medium, bloom=2, true_false)   理解 / 中等
#   index 2: (medium, bloom=3, short_answer) 應用 / 中等
#   index 3: (hard,   bloom=4, mcq)          分析 / 困難（投影片範例對應這格）
#   index 4: (hard,   bloom=5, cloze)        評鑑 / 困難
# -----------------------------------------------------------------------------
_MULTI_TOPICS: list[str] = [
    "air_pollution",
    "waste_management",
    "climate_change",
    "water_resources",
    "biodiversity",
    "energy_conservation",
]

_MULTI_COMBOS: list[tuple[str, int, str]] = [
    ("easy",   1, "mcq"),
    ("medium", 2, "true_false"),
    ("medium", 3, "short_answer"),
    ("hard",   4, "mcq"),
    ("hard",   5, "cloze"),
]


def _build_multi_constraint_cases() -> list[GenExamTestCase]:
    out: list[GenExamTestCase] = []
    count_per_case = 5
    case_idx = 0
    for topic_id in _MULTI_TOPICS:
        topic_zh = TOPIC_LABELS_ZH[topic_id]
        for diff, bloom, qt in _MULTI_COMBOS:
            case_idx += 1
            req = _build_request_text(topic_zh, count_per_case, diff, bloom, qt)
            out.append(
                GenExamTestCase(
                    case_id=f"multi_{case_idx:02d}",
                    category="multi_constraint",
                    request=req,
                    topic=topic_id,
                    count=count_per_case,
                    constraint_focus="all",
                    difficulty=diff,
                    bloom_level=bloom,
                    question_type=qt,
                    notes=(
                        f"Multi-Constraint: difficulty={diff}, bloom={bloom}, "
                        f"type={qt}"
                    ),
                )
            )
    return out


# =============================================================================
# 公開 API
# =============================================================================
def all_cases() -> list[GenExamTestCase]:
    """回傳所有 60 個測試案例。"""
    return _build_single_constraint_cases() + _build_multi_constraint_cases()


def cases_by_category(category: Category) -> list[GenExamTestCase]:
    return [c for c in all_cases() if c.category == category]


def case_by_id(case_id: str) -> GenExamTestCase | None:
    for c in all_cases():
        if c.case_id == case_id:
            return c
    return None


def filter_cases(
    *,
    category: Category | None = None,
    topic: str | None = None,
    difficulty: str | None = None,
    bloom_level: int | None = None,
    question_type: str | None = None,
    constraint_focus: ConstraintFocus | None = None,
) -> list[GenExamTestCase]:
    out = all_cases()
    if category:
        out = [c for c in out if c.category == category]
    if topic:
        out = [c for c in out if c.topic == topic]
    if difficulty:
        out = [c for c in out if c.difficulty == difficulty]
    if bloom_level is not None:
        out = [c for c in out if c.bloom_level == bloom_level]
    if question_type:
        out = [c for c in out if c.question_type == question_type]
    if constraint_focus:
        out = [c for c in out if c.constraint_focus == constraint_focus]
    return out


def dump_cases_json(path: str) -> None:
    data = [c.to_dict() for c in all_cases()]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def summary() -> dict[str, int]:
    cs = all_cases()
    return {
        "total": len(cs),
        "single_constraint": sum(1 for c in cs if c.category == "single_constraint"),
        "single_diff_focus": sum(
            1 for c in cs
            if c.category == "single_constraint" and c.constraint_focus == "difficulty"
        ),
        "single_qtype_focus": sum(
            1 for c in cs
            if c.category == "single_constraint"
            and c.constraint_focus == "question_type"
        ),
        "multi_constraint": sum(1 for c in cs if c.category == "multi_constraint"),
        "by_difficulty_easy":   sum(1 for c in cs if c.difficulty == "easy"),
        "by_difficulty_medium": sum(1 for c in cs if c.difficulty == "medium"),
        "by_difficulty_hard":   sum(1 for c in cs if c.difficulty == "hard"),
        "by_qtype_mcq":         sum(1 for c in cs if c.question_type == "mcq"),
        "by_qtype_true_false":  sum(1 for c in cs if c.question_type == "true_false"),
        "by_qtype_short_answer": sum(1 for c in cs if c.question_type == "short_answer"),
        "by_qtype_cloze":       sum(1 for c in cs if c.question_type == "cloze"),
    }


# =============================================================================
# CLI
# =============================================================================
if __name__ == "__main__":
    import sys

    s = summary()
    print("=== GenExam Scenario 測試案例摘要 ===")
    for k, v in s.items():
        print(f"  {k:24s} : {v}")

    if "--list" in sys.argv:
        print("\n--- 全部 60 案例 ---")
        for c in all_cases():
            tags = [c.category, c.topic]
            if c.difficulty:    tags.append(f"diff={c.difficulty}")
            if c.bloom_level:   tags.append(f"bloom={c.bloom_level}")
            tags.append(f"type={c.question_type}")
            print(f"  [{c.case_id}] {c.request}")
            print(f"      ({', '.join(tags)})")

    if "--dump" in sys.argv:
        out = "genexam_scenario_cases.json"
        dump_cases_json(out)
        print(f"\n已輸出 {out}")
