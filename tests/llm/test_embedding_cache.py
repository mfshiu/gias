"""Embedding 快取（P-04）。"""

from __future__ import annotations

import threading

import pytest

from src.llm import cache as cache_mod
from src.llm.cache import EmbeddingCache
from src.llm.client import LLMClient
from src.llm.config import load_llm_runtime_config


class _Provider:
    """會計算呼叫次數的假 embedding provider。"""

    def __init__(self):
        self.calls = []
        self.fail = False

    def chat(self, messages, **kw):
        raise NotImplementedError

    def embed_text(self, text, **kw):
        self.calls.append((text, kw.get("model"), kw.get("dimensions")))
        if self.fail:
            raise RuntimeError("401 unauthorized")
        return [float(len(text)), float(kw.get("dimensions") or 0)]


def _client(cache=None, provider=None, model="text-embedding-3-small"):
    return LLMClient(
        provider or _Provider(), provider_name="openai", default_timeout=None, default_max_retries=0,
        default_retry_backoff=0.0, default_retry_jitter=0.0, strict_json=True, default_embed_model=model,
        openai_api_key=None, embedding_cache=cache,
    )


def test_same_text_is_embedded_once():
    client = _client(EmbeddingCache())
    first = client.embed_text("帶我去 AI 展區")
    first.append(99.0)                                        # 回傳的是副本
    assert client.embed_text("帶我去 AI 展區", timeout=5) == [9.0, 0.0]
    assert client.embed_text("推薦展品") == [4.0, 0.0]
    assert len(client.provider_client.calls) == 2
    assert (client.embedding_cache.hits, client.embedding_cache.misses) == (1, 2)


def test_model_and_options_are_part_of_the_key():
    client = _client(EmbeddingCache())
    client.embed_text("x")
    client.embed_text("x", model="text-embedding-3-large")
    client.embed_text("x", dimensions=256)
    client.embed_text("x", dimensions=256)
    assert len(client.provider_client.calls) == 3


def test_failures_are_not_cached_and_cache_can_be_disabled():
    provider = _Provider()
    provider.fail = True
    client = _client(EmbeddingCache(), provider)
    with pytest.raises(Exception):
        client.embed_text("x")
    provider.fail = False
    assert client.embed_text("x") == [1.0, 0.0]

    uncached = _client(None)
    uncached.embed_text("x")
    uncached.embed_text("x")
    assert len(uncached.provider_client.calls) == 2


def test_lru_evicts_least_recently_used():
    cache = EmbeddingCache(max_items=2)
    client = _client(cache)
    client.embed_text("a")
    client.embed_text("b")
    client.embed_text("a")                                  # a 最近使用
    client.embed_text("c")                                  # 淘汰 b
    client.embed_text("a")
    client.embed_text("b")
    assert [c[0] for c in client.provider_client.calls] == ["a", "b", "c", "b"]
    assert len(cache) == 2


def test_clients_from_config_share_one_cache(monkeypatch):
    monkeypatch.setattr(cache_mod, "_shared", None)
    monkeypatch.setattr("src.llm.client.build_provider_client", lambda cfg: (_Provider(), None, "m"))
    cfg = {"llm": {"provider": "mock"}}
    a, b = LLMClient.from_config(cfg), LLMClient.from_config({"llm": {"provider": "mock", "embedding_cache_size": 4096}})
    a.embed_text("帶我去 AI 展區")
    b.embed_text("帶我去 AI 展區")
    assert a.embedding_cache is b.embedding_cache and a.embedding_cache.max_items == 4096
    assert len(a.provider_client.calls) == 1 and len(b.provider_client.calls) == 0
    off = LLMClient.from_config({"llm": {"provider": "mock", "embedding_cache_size": 0}})
    assert off.embedding_cache is None
    assert load_llm_runtime_config(cfg).embedding_cache_size == 2048


def test_shared_cache_is_thread_safe():
    cache = EmbeddingCache(8)
    errors = []

    def worker(n):
        try:
            for i in range(200):
                key = cache.key("p", "m", f"t{(n + i) % 16}")
                if cache.get(key) is None:
                    cache.put(key, [float(i)])
        except Exception as e:                                # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(cache) <= 8
