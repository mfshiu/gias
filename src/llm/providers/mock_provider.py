# src/llm/providers/mock_provider.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Sequence

from ..client import LLMResponse, LLMUsage, ProviderClient


@dataclass
class MockProviderResponse:
    """
    讓 LLMClient._normalize_response() 能吃的形狀：
    - content: str
    - usage: Any (optional)
    """
    content: str
    usage: Dict[str, Any] | None = None
    raw: Any = None


class MockProvider(ProviderClient):
    """
    供本機/CI 測試用的 Mock Provider。

    特色：
    - 不需要任何金鑰
    - 永遠回傳「可被 parse_intent() schema 驗證通過」的 JSON
    - 可用 env 或 kwargs 覆寫回傳內容（方便測試錯誤情境）
    """

    def __init__(
        self,
        *,
        default_intent_id: str = "T001",
        default_name: str = "查詢天氣",
        default_description: str = "查詢指定地點的天氣狀況",
        default_location: str = "台北",
    ):
        self.default_intent_id = default_intent_id
        self.default_name = default_name
        self.default_description = default_description
        self.default_location = default_location

    def chat(self, messages: Sequence[Dict[str, Any]], **kwargs) -> Any:
        """
        模擬 provider.chat(messages) -> response

        kwargs 支援：
        - mock_mode: "ok" | "invalid_json" | "schema_fail"
        - mock_content: str 直接指定回傳文字（最高優先）
        """
        mock_content = kwargs.get("mock_content")
        if isinstance(mock_content, str):
            return self._wrap(mock_content, raw={"mode": "custom"})

        mode = str(kwargs.get("mock_mode", "ok")).lower()

        if mode == "invalid_json":
            # 讓 LLMClient._parse_json() 失敗
            return self._wrap("這不是 JSON", raw={"mode": mode})

        if mode == "schema_fail":
            # 是 JSON，但故意缺 required 欄位（例如 candidates）
            bad = {"foo": "bar"}
            return self._wrap(json.dumps(bad, ensure_ascii=False), raw={"mode": mode})

        # mode == "ok"
        # 從 user_text 猜地點（很粗略，僅供測試）
        user_text = self._extract_last_user_text(messages)
        location = self._guess_location(user_text) or self.default_location

        payload = {
            "candidates": [
                {
                    "intent_id": self.default_intent_id,
                    "name": self.default_name,
                    "description": self.default_description,
                    "slots": {"location": location},
                }
            ]
        }

        return self._wrap(json.dumps(payload, ensure_ascii=False), raw={"mode": mode, "user_text": user_text})

    # ---------- helpers ----------

    def _wrap(self, content: str, *, raw: Any = None) -> MockProviderResponse:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
        return MockProviderResponse(content=content, usage=usage, raw=raw)

    def _extract_last_user_text(self, messages: Sequence[Dict[str, Any]]) -> str:
        for m in reversed(messages):
            if (m or {}).get("role") == "user":
                return str((m or {}).get("content") or "")
        return ""

    def _guess_location(self, text: str) -> str | None:
        # 非 NLP，只做最常見幾個關鍵字
        candidates = ["台北", "新北", "桃園", "台中", "台南", "高雄", "基隆", "新竹"]
        for c in candidates:
            if c in text:
                return c
        return None
