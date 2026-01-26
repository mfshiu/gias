# src/llm/providers/ollama_provider.py
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from ..client import ProviderClient


@dataclass
class OllamaProviderResponse:
    """
    讓 LLMClient._normalize_response() 能吃的形狀：
    - content: str
    - usage: dict-like（可選）
    - raw: 任意原始回應
    """
    content: str
    usage: Dict[str, Any] | None = None
    raw: Any = None


class OllamaProvider(ProviderClient):
    """
    Ollama Provider（對應 Ollama HTTP API）
    - 預設呼叫 POST {base_url}/api/chat
    - stream 預設關閉（False），回傳一次性 JSON
    - 會把 Ollama 的 eval_count / prompt_eval_count 轉成 usage 欄位

    環境變數建議：
      OLLAMA_BASE_URL=http://localhost:11434
      OLLAMA_MODEL=llama3
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
        default_options: Optional[Dict[str, Any]] = None,
        keep_alive: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.default_options = default_options or {}
        self.keep_alive = keep_alive  # 例如 "5m" / "0"（不保留）

    def chat(self, messages: Sequence[Dict[str, Any]], **kwargs) -> Any:
        """
        必須符合 ProviderClient 介面：chat(messages, **kwargs) -> response(any)

        kwargs 支援（常用）：
        - model: 覆寫模型
        - options: dict（Ollama options，如 temperature, num_predict...）
        - timeout: float seconds
        - format: "json" 或 dict（Ollama chat 的 format 參數）
        - keep_alive: 覆寫 keep_alive
        """
        model = kwargs.get("model") or self.model
        timeout = kwargs.get("timeout", None)  # 由 LLMClient 傳入
        options = dict(self.default_options)
        options.update(kwargs.get("options") or {})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": False,
        }

        # 可選：要求 Ollama 以 JSON 格式輸出（但仍可能出現非 JSON content，需靠 LLMClient.parse_json）
        if "format" in kwargs and kwargs["format"] is not None:
            payload["format"] = kwargs["format"]

        if options:
            payload["options"] = options

        keep_alive = kwargs.get("keep_alive", self.keep_alive)
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive

        url = f"{self.base_url}/api/chat"
        raw = self._post_json(url, payload, timeout=timeout)

        # Ollama /api/chat 常見回傳形狀：
        # {
        #   "model": "...",
        #   "created_at": "...",
        #   "message": {"role":"assistant","content":"..."},
        #   "done": true,
        #   "prompt_eval_count": 12,
        #   "eval_count": 34,
        #   ...
        # }
        content = ""
        if isinstance(raw, dict):
            msg = raw.get("message") or {}
            content = (msg.get("content") or "") if isinstance(msg, dict) else ""
        else:
            content = str(raw or "")

        usage = None
        if isinstance(raw, dict):
            prompt_tokens = raw.get("prompt_eval_count")
            completion_tokens = raw.get("eval_count")
            total_tokens = None
            if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                total_tokens = prompt_tokens + completion_tokens

            usage = {
                "prompt_tokens": prompt_tokens if isinstance(prompt_tokens, int) else None,
                "completion_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
                "total_tokens": total_tokens,
                "cost": None,  # Ollama 本地通常不估 cost
            }

        return OllamaProviderResponse(content=content, usage=usage, raw=raw)

    # -------------------------
    # internal helpers
    # -------------------------

    def _post_json(self, url: str, payload: Dict[str, Any], *, timeout: Optional[float]) -> Any:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body)
                except Exception:
                    # 仍回傳原文字，交給上層處理
                    return {"content": body}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
            raise RuntimeError(f"Ollama HTTPError {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama URLError: {e}") from e
