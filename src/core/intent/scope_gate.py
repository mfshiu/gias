from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True, slots=True)
class ScopeDecision:
    can_execute: bool
    reason: str


class ScopeGateError(RuntimeError):
    """ScopeGate 無法得出可信的判斷（LLM 呼叫失敗，或回覆格式不符）。"""


def _parse_bool(value: Any) -> bool | None:
    """嚴格解析布林值；無法判定時回傳 None（避免 bool("false") 變成 True）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "yes"):
            return True
        if s in ("false", "no"):
            return False
    return None


class ScopeGate:
    # 功能：能否用「目前可用的 actions」完成「使用者的意圖」
    # 輸入：使用者的意圖、目前可用的 actions
    # 輸出：是否能執行、原因
    # 流程：
    # 1. 把使用者的意圖和目前可用的 actions 轉成 prompt
    # 2. 用 LLM 判斷是否能執行
    # 3. 回傳是否能執行、原因
    # 4. LLM 失敗或回覆格式不符時拋出 ScopeGateError，
    #    由呼叫端依 scope_gate_strict 決定拒絕或放行

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

        try:
            obj = self.llm.json(messages, schema=None)
        except Exception as e:
            raise ScopeGateError(f"LLM call failed: {e}") from e

        if not isinstance(obj, dict):
            raise ScopeGateError(f"Malformed response: expected a JSON object, got {type(obj).__name__}")
        can_execute = _parse_bool(obj.get("can_execute"))
        if can_execute is None:
            raise ScopeGateError(f"Malformed response: can_execute={obj.get('can_execute')!r}")
        reason = str(obj.get("reason", "")).strip() or "No reason provided."
        return ScopeDecision(can_execute=can_execute, reason=reason)
