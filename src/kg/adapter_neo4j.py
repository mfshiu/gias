# src/kg/adapter_neo4j.py
"""
Neo4j/Bolt 實作（Adapter）

定位：
- 封裝 neo4j driver / session / transaction 細節
- 提供統一的 read / write 執行入口（回傳 list[dict]）
- 處理：database、fetch_size、timeout、重試（暫時性錯誤）、關閉資源

建議搭配：
- src/kg/client.py：作為對外 KGClient（語意 API）
- src/kg/queries.py / commands.py：只產生 (cypher, params)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
import time

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

JsonDict = Dict[str, Any]
Params = Dict[str, Any]


# -------------------------
# Config
# -------------------------
@dataclass(frozen=True)
class Neo4jAdapterConfig:
    uri: str
    user: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    encrypted: bool = False

    fetch_size: int = 2000
    timeout_sec: int = 15          # server-side tx timeout
    max_retries: int = 2
    retry_backoff_sec: float = 0.5


# -------------------------
# Adapter
# -------------------------
class Neo4jBoltAdapter:
    """
    Neo4j Bolt Adapter：唯一負責 driver / session / tx / retry。
    """

    def __init__(self, config: Neo4jAdapterConfig, logger: Optional[Any] = None):
        self.config = config
        self._logger = logger

        # Neo4j no-auth 時可以不提供 auth
        auth = None
        if self.config.user is not None:
            auth = (self.config.user or "", self.config.password or "")

        self._driver = GraphDatabase.driver(
            self.config.uri,
            auth=auth,
            encrypted=self.config.encrypted,
        )

    # -------------------------
    # Lifecycle
    # -------------------------
    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()

    def __enter__(self) -> "Neo4jBoltAdapter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -------------------------
    # Public APIs
    # -------------------------
    def read(self, cypher: str, params: Optional[Params] = None) -> List[JsonDict]:
        """
        Read-only query. ⚠️ 不得包含 CREATE / MERGE / SET / DELETE 等寫入操作。
        Return: list[dict] (each record -> dict)
        """
        return self._run_with_retry(
            op_name="read",
            runner=lambda session: self._run(session, cypher, params or {}, write=False),
        )

    def write(self, cypher: str, params: Optional[Params] = None) -> List[JsonDict]:
        """
        Write query. 可包含 CREATE / MERGE / SET / DELETE 等寫入操作。
        Return: list[dict] (each record -> dict)
        """
        return self._run_with_retry(
            op_name="write",
            runner=lambda session: self._run(session, cypher, params or {}, write=True),
        )

    # -------------------------
    # Internals
    # -------------------------
    def _run(self, session, cypher: str, params: Params, write: bool) -> List[JsonDict]:
        tx_timeout = self.config.timeout_sec

        def _execute(tx):
            result = tx.run(cypher, params, timeout=tx_timeout)
            return [dict(r) for r in result]

        if write:
            return session.execute_write(_execute)
        return session.execute_read(_execute)

    def _run_with_retry(
        self,
        op_name: str,
        runner: Callable[[Any], List[JsonDict]],
    ) -> List[JsonDict]:
        last_exc: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                with self._driver.session(
                    database=self.config.database,
                    fetch_size=self.config.fetch_size,
                ) as session:
                    return runner(session)

            except (ServiceUnavailable, SessionExpired) as e:
                # 暫時性錯誤：網路/叢集 leader 切換/session 失效等
                last_exc = e
                self._log("warning", f"Neo4jBoltAdapter.{op_name} transient error: {e} (attempt={attempt})")
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_sec * (attempt + 1))
                    continue
                raise

            except Neo4jError as e:
                # Cypher 語法/約束/權限等：通常不重試
                self._log("error", f"Neo4jBoltAdapter.{op_name} neo4j error: {e}")
                raise

            except Exception as e:
                last_exc = e
                self._log("error", f"Neo4jBoltAdapter.{op_name} unexpected error: {e}")
                raise

        if last_exc:
            raise last_exc
        return []

    def _log(self, level: str, msg: str) -> None:
        if not self._logger:
            return
        fn = getattr(self._logger, level, None)
        if callable(fn):
            fn(msg)


# -------------------------
# Factory (optional)
# -------------------------
def build_neo4j_adapter(
    uri: str,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None,
    encrypted: bool = False,
    fetch_size: int = 2000,
    timeout_sec: int = 15,
    max_retries: int = 2,
    retry_backoff_sec: float = 0.5,
    logger: Optional[Any] = None,
) -> Neo4jBoltAdapter:
    cfg = Neo4jAdapterConfig(
        uri=uri,
        user=user,
        password=password,
        database=database,
        encrypted=encrypted,
        fetch_size=fetch_size,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        retry_backoff_sec=retry_backoff_sec,
    )
    return Neo4jBoltAdapter(cfg, logger=logger)
