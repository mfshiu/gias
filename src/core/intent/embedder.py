from src.llm.client import LLMClient

class LLMEmbedder:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def embed_text(self, text: str) -> list[float]:
        for fn_name in ("embed_text", "embed", "embedding", "embeddings"):
            fn = getattr(self.llm, fn_name, None)
            if callable(fn):
                v = fn(text)
                if isinstance(v, dict) and "embedding" in v:
                    return v["embedding"]
                if isinstance(v, list):
                    return v
        raise AttributeError("LLMClient 沒有 embedding 方法（embed/embed_text/embeddings）。")
