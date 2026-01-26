# src/llm/tasks/intent_tasks.py

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..client import LLMClient, LLMSchemaValidationError, LLMInvalidJSONError
from ..prompts.registry import PromptRegistry, PromptMeta
from ..schemas.intent import IntentParseResult


DEFAULT_TEMPLATE = "intent_parse_v1"


# parse_intent()
# - 任務層（Tasks）：所有代理都只呼叫這裡，不直接碰 prompt / provider / retry 細節
# - 負責：
#   1) 準備 prompt 變數
#   2) 取 PromptRegistry 模板並渲染成 messages
#   3) 呼叫 LLMClient.json() 並以 Pydantic schema 驗證
#   4) 若 schema 失敗：加上修復提示再試一次（可控次數）
def parse_intent(
    llm: LLMClient,
    user_text: str,
    *,
    template_name: str = DEFAULT_TEMPLATE,
    registry: Optional[PromptRegistry] = None,
    variables: Optional[Dict[str, Any]] = None,
    max_fix_retries: int = 1,
    llm_kwargs: Optional[Dict[str, Any]] = None,
) -> Tuple[IntentParseResult, PromptMeta]:
    """
    解析自然語言 -> IntentParseResult（含 candidates）

    回傳：(result, prompt_meta)
    """
    registry = registry or PromptRegistry.from_default()
    variables = dict(variables or {})
    llm_kwargs = dict(llm_kwargs or {})

    # 渲染 prompt -> messages
    messages, meta = registry.render(
        template_name,
        user_text=user_text,
        variables=variables,
    )

    # 第一次嘗試：嚴格 schema 驗證
    try:
        result = llm.json(messages, schema=IntentParseResult, **llm_kwargs)
        return result, meta
    except (LLMSchemaValidationError, LLMInvalidJSONError) as e:
        last_err = str(e)

    # 修復重試：加上一段「只輸出 JSON、不得多欄位」的提示
    for _ in range(max_fix_retries):
        fix_messages = list(messages)
        fix_messages.append(
            {
                "role": "user",
                "content": (
                    "你的上一個輸出未通過 JSON/schema 驗證。"
                    "請僅輸出單一 JSON 物件，且必須符合輸出格式："
                    '{ "candidates": [ { "intent_id": "...", "name": "...", "description": "...", "slots": { } } ] }'
                    "不得包含任何其他文字、不得使用 Markdown。"
                    f"\n驗證錯誤摘要：{last_err}"
                ),
            }
        )

        try:
            result = llm.json(fix_messages, schema=IntentParseResult, **llm_kwargs)
            return result, meta
        except (LLMSchemaValidationError, LLMInvalidJSONError) as e:
            last_err = str(e)

    # 仍失敗：把錯誤往上丟，讓上層決定怎麼處理（例如降級模型或改走人工）
    raise LLMSchemaValidationError(f"parse_intent failed after fix retries: {last_err}")


def main():
    test_input = "幫我查一下台北今天的天氣"
    test_input = "下午三點移動至 301 會議室，開啟空調。然後準備投影設備，並通知所有參與者。"
    test_input = "一邊播放輕音樂，一邊把燈光調暗。"
    test_input = "準備 301 會議室，下午兩點要跟客戶進行視訊提案。"
    test_input = "先去 A1 倉庫領取測試樣品並送往 301 會議室，接著在下午兩點準時為 VIP 客戶進行產品演示，演示包含投影控制與樣品解說，完成後引導客戶前往 1 樓出口並發送滿意度調查。"

    # 1) 建立 LLMClient（依你的 LLMClient 實作調整參數）
    #    常見：LLMClient.from_env() / LLMClient() / LLMClient(provider=...)
    llm = LLMClient.from_env()

    # 2) 正確呼叫：先傳 llm，再傳 user_text
    result, meta = parse_intent(llm, test_input)

    # 3) candidates 才是意圖清單
    intents = result.candidates

    print("=== parse_intent 測試結果 ===")
    print(f"template={getattr(meta, 'template_name', 'N/A')} version={getattr(meta, 'version', 'N/A')}")

    for i, intent in enumerate(intents, start=1):
        print(f"\nIntent {i}")
        print(intent.model_dump())  # Pydantic v2


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    main()