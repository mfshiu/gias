# src/llm/retry.py
from __future__ import annotations

import time
from typing import Any, Callable, Optional


def is_retriable_exception(e: Exception) -> bool:
    msg = str(e).lower()
    retriable_keywords = [
        "rate limit",
        "429",
        "timeout",
        "timed out",
        "temporarily",
        "temporary",
        "overloaded",
        "connection reset",
        "connection aborted",
        "service unavailable",
        "503",
    ]
    return any(k in msg for k in retriable_keywords)


def call_with_retry(
    fn: Callable[[], Any],
    *,
    max_retries: int,
    backoff: float,
    jitter: float,
    is_retriable: Callable[[Exception], bool] = is_retriable_exception,
    wrap_exception: Optional[Callable[[Exception], Exception]] = None,
) -> Any:
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            retriable = is_retriable(e)
            if attempt >= max_retries or not retriable:
                if wrap_exception:
                    raise wrap_exception(e) from e
                raise

            sleep_s = (backoff ** attempt)
            sleep_s = sleep_s + (jitter * (0.5 - (time.time() % 1)))
            sleep_s = max(0.0, sleep_s)
            time.sleep(sleep_s)

    raise RuntimeError(f"Retry failed: {last_exc}")
