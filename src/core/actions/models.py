# src\core\actions\models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping



@dataclass(frozen=True, slots=True)
class ActionDef:
    """
    定義一個可被執行/規劃的 Action（工具/能力）
    """
    name: str
    description: str
    meta: dict[str, Any] | None = None
    # 建議在 meta 放：
    # - "kg_id": Neo4j node id
    # - "tool_name": 實際工具名稱（若不同於 name）
    # - "params_schema": list[dict]  (若你不從 KG 取 params，而是從程式端 registry)
    # - "domains"/"capabilities" 等



@dataclass(frozen=True, slots=True)
class ActionMatch:
    """
    Action 候選匹配結果：除了文字相似度(score/base_score)，
    還要包含「參數可填性」與「參數相似度」，並形成 final_score 供 selector 排序/門檻判定。

    - score：保留欄位以相容舊程式（建議等於 final_score）
    - base_score：原本的文字/embedding 相似度
    - param_score：slots 與 action params 的一致性分數（0~1）
    - fillable：required params 是否能填出值（否則應直接 reject）
    - bindings：param_key -> 值（之後 planner 可用來組 action string）
    - reject_reason：若被 gate 擋下，記錄原因（方便 debug）
    """
    action: ActionDef

    # ✅ 相容舊欄位：建議存 final_score
    score: float

    # ✅ 新增：分數拆解
    base_score: float = 0.0
    param_score: float = 0.0
    final_score: float = 0.0

    # ✅ 新增：參數檢查結果
    fillable: bool = True
    bindings: Mapping[str, Any] = field(default_factory=dict)

    # ✅ 新增：門檻與拒絕原因（可選）
    thresholds: Mapping[str, float] = field(default_factory=dict)  # e.g. {"param":0.35,"final":0.55}
    reject_reason: str = ""

    # ✅ 證據與除錯資訊
    evidence: dict[str, Any] | None = None


    @staticmethod
    def from_base(
        action: ActionDef,
        base_score: float,
        *,
        param_score: float = 0.0,
        fillable: bool = True,
        bindings: Mapping[str, Any] | None = None,
        w_base: float = 0.4,
        w_param: float = 0.6,
        thresholds: Mapping[str, float] | None = None,
        reject_reason: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> "ActionMatch":
        """
        由 base_score + param_score 計算 final_score，並把 score 設為 final_score（維持相容）。
        """
        final = (w_base * float(base_score)) + (w_param * float(param_score))
        th = dict(thresholds or {})
        b = bindings or {}
        return ActionMatch(
            action=action,
            score=final,
            base_score=float(base_score),
            param_score=float(param_score),
            final_score=float(final),
            fillable=bool(fillable),
            bindings=b,
            thresholds=th,
            reject_reason=reject_reason,
            evidence=evidence,
        )


    def is_acceptable(self) -> bool:
        """
        依 thresholds 判斷是否可用（selector 可直接用這個 gate）。
        預設：只要 fillable=True 且 final_score > 0。
        """
        if not self.fillable:
            return False
        t_param = float(self.thresholds.get("param", 0.0)) if self.thresholds else 0.0
        t_final = float(self.thresholds.get("final", 0.0)) if self.thresholds else 0.0
        return (self.param_score >= t_param) and (self.final_score >= t_final) and (self.final_score > 0.0)
