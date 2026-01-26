# src/llm/schemas/intent.py
#
# Pydantic/TypedDict：IntentCandidate, SubIntent...
# - 這裡先提供「最小可用」版本，配合 intent_parse_v1.md 的輸出格式
# - 同時提供：
#   1) TypedDict（靜態型別檢查友善）
#   2) Pydantic Model（可做 runtime 驗證）
#
# 注意：若你不想強依賴 pydantic，也可以只用 TypedDict。
# 但目前你在 LLMClient.json() 已支援 Pydantic model_validate()，建議保留。

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic import ConfigDict
from typing import Any, Dict, List, TypedDict


# -----------------------
# TypedDict (static typing)
# -----------------------

class IntentCandidateTD(TypedDict):
    intent_id: str
    name: str
    description: str
    slots: Dict[str, Any]


class IntentParseResultTD(TypedDict):
    candidates: List[IntentCandidateTD]


# -----------------------
# Pydantic (runtime validation)
# -----------------------

class IntentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 不允許多餘欄位，避免模型亂塞
    intent_id: str = Field(default="", description='例如 "I001"')
    name: str = Field(default="", description="意圖簡短名稱")
    description: str = Field(default="", description="一句話簡述")
    slots: Dict[str, Any] = Field(default_factory=dict, description="可用來查詢的參數鍵值")


class IntentParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: List[IntentCandidate] = Field(default_factory=list)


# -----------------------
# Optional: SubIntent / Hierarchy (for next stage)
# -----------------------

class SubIntent(BaseModel):
    """
    若你下一步要做意圖拆解（sub-intents），可用此結構當輸出 schema。
    目前先提供簡化版，不強制使用。
    """
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(default="")
    name: str = Field(default="")
    description: str = Field(default="")
    slots: Dict[str, Any] = Field(default_factory=dict)

    # 拆解樹：可再包含子意圖
    children: List["SubIntent"] = Field(default_factory=list)

    # 估計是否可直接執行（原子意圖）
    is_atomic: bool = False


# 讓 SubIntent 支援遞迴
try:  # pragma: no cover
    SubIntent.model_rebuild()
except Exception:
    pass
