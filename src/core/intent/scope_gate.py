from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True, slots=True)
class ScopeDecision:
    can_execute: bool
    reason: str

class ScopeGate:
    def __init__(self, llm, logger):
        self.llm = llm
        self.logger = logger

    def decide(self, *, user_intent: str, available_actions: list[dict[str, Any]]) -> ScopeDecision:
        # 只提供「能力列表」：name/desc，不給它改寫意圖的空間
        tools = [{"name": a.get("name", ""), "description": a.get("description", "")} for a in available_actions]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a capability checker.\n"
                    "Decide whether the user's intent can be completed using ONLY the available actions.\n"
                    "Do not rewrite the intent. Do not propose alternative tasks.\n"
                    "Return a single JSON object with fields: can_execute (boolean), reason (string).\n"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User intent:\n{user_intent}\n\n"
                    f"Available actions:\n{tools}\n\n"
                    "Return JSON:"
                ),
            },
        ]

        # 用你的 LLMClient.json 走 schema（若你不想加 schema，至少用 strict json）
        try:
            obj = self.llm.json(messages, schema=None)  # 若你的 llm.json 一定要 schema，就改成小 pydantic
            can_execute = bool(obj.get("can_execute", False))
            reason = str(obj.get("reason", "")).strip() or "No reason provided."
            return ScopeDecision(can_execute=can_execute, reason=reason)
        except Exception as e:
            # 保守策略：若 gate 自身失敗，為避免誤殺，可選擇放行或拒絕
            # 建議：測試/嚴格模式下拒絕；正式服務模式下放行並記 log
            self.logger.warning("ScopeGate failed: %s", e)
            return ScopeDecision(can_execute=True, reason="ScopeGate failed; allow by default.")
