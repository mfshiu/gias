# src/blackboard/access.py
"""
blackboard.control 的存取控制（S-01）。

- 共用金鑰：[blackboard.access] token。設定後，每個 control 命令都必須帶相同的 "token"，否則拒絕；
  client helper、感測器、觀察者會自動從自己的設定讀取並附上（with_token）。未設定時照常運作，
  BlackboardAgent 啟動時記錄警告
- query 一律在唯讀交易執行（Neo4j 拒絕在唯讀交易中寫入）
- 不論是否有金鑰，一律拒絕的 Cypher（check_cypher）：LOAD CSV（讀取伺服器或遠端檔案）、dbms / apoc 程序、
  建立或刪除索引與約束、資料庫與使用者管理、權限設定、切換資料庫（USE）

注意：金鑰隨 MQTT 訊息傳送；broker 未啟用 TLS 時可被竊聽（見 S-02）。
"""

from __future__ import annotations

import hmac
import re
from typing import Any

_FLAGS = re.IGNORECASE | re.MULTILINE
FORBIDDEN_CYPHER: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bLOAD\s+CSV\b", _FLAGS), "LOAD CSV"),
    (re.compile(r"\bCALL\s+(dbms|apoc)\s*\.", _FLAGS), "dbms / apoc procedures"),
    (re.compile(r"\bCALL\s+db\s*\.\s*(create|drop|clear|index\s*\.\s*fulltext\s*\.\s*(create|drop))", _FLAGS),
     "schema procedures"),
    (re.compile(r"^\s*(CREATE|DROP)\s+(OR\s+REPLACE\s+)?(\w+\s+)?(INDEX|CONSTRAINT)\b", _FLAGS), "index / constraint changes"),
    (re.compile(r"^\s*(CREATE|DROP|ALTER|START|STOP|RENAME)\s+(OR\s+REPLACE\s+)?(COMPOSITE\s+)?"
                r"(DATABASE|ALIAS|USER|ROLE|SERVER)\b", _FLAGS), "database / user administration"),
    (re.compile(r"^\s*(GRANT|DENY|REVOKE)\b", _FLAGS), "privilege administration"),
    (re.compile(r"(^|\{)\s*USE\s", _FLAGS), "switching databases"),
)


def check_cypher(cypher: str) -> str | None:
    """不允許的 Cypher 回傳原因，允許時回傳 None。"""
    for pattern, reason in FORBIDDEN_CYPHER:
        if pattern.search(cypher or ""):
            return reason
    return None


def access_token(config: Any) -> str | None:
    """設定中的 [blackboard.access] token；沒有設定時回傳 None。"""
    if not isinstance(config, dict):
        return None
    access = (config.get("blackboard") or {}).get("access") if isinstance(config.get("blackboard"), dict) else None
    token = access.get("token") if isinstance(access, dict) else None
    return token if isinstance(token, str) and token else None


def control_token(agent: Any) -> str | None:
    """agent 自己設定中的金鑰（agentflow 的 self.config，或 IntentionalAgent 的 agent_config）。"""
    for attr in ("config", "agent_config"):
        token = access_token(getattr(agent, attr, None))
        if token:
            return token
    return None


def with_token(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """送往 blackboard.control 的命令：有設定金鑰時附上。"""
    token = control_token(agent)
    return {**payload, "token": token} if token else payload


class AccessPolicy:
    def __init__(self, token: str | None = None):
        self.token = token

    @classmethod
    def from_config(cls, config: Any) -> "AccessPolicy":
        return cls(access_token(config))

    def authorized(self, token: Any) -> bool:
        if not self.token:
            return True
        return isinstance(token, str) and hmac.compare_digest(token.encode("utf-8"), self.token.encode("utf-8"))
