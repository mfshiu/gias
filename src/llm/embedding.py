# src/llm/embedding.py
from __future__ import annotations

from typing import Any, List


def normalize_embedding(raw: Any) -> List[float]:
    if isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
        return [float(x) for x in raw]

    if isinstance(raw, dict) and "embedding" in raw and isinstance(raw["embedding"], list):
        return [float(x) for x in raw["embedding"]]

    # OpenAI-like response: resp.data[0].embedding
    try:
        data = getattr(raw, "data", None)
        if data and len(data) > 0:
            emb = getattr(data[0], "embedding", None)
            if isinstance(emb, list):
                return [float(x) for x in emb]
    except Exception:
        pass

    raise RuntimeError("Failed to normalize embedding response.")
