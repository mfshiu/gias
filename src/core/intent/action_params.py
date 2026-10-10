"""
Action 參數的解析與驗證。

LLM 以函式呼叫字串表示 atomic 動作，例如：
    SuggestRoute(CurrentLocation="入口", Interests=["AI", "VR"], AvoidCrowd=true, TimeBudgetMin=60, Limit=null)

- parse_action_call：切出參數並轉成 Python 值（null → None、true/false → bool、整數 / 小數、清單）；
  只在最外層的逗號切分，引號、[]、()、{} 內的逗號不會被切開
- normalize_params：依 Action KG 的 params_schema 對應參數名稱（不分大小寫與底線，"BoothID" → "booth_id"）、
  轉型別、檢查 enum 與必填，回傳 (params, issues)；值為 None 的參數不會出現在 params
"""

from __future__ import annotations

import re
from typing import Any

_NULLS = frozenset({"null", "none", "nil", "undefined"})
_TRUE = frozenset({"true", "yes", "y", "1", "是"})
_FALSE = frozenset({"false", "no", "n", "0", "否"})
_SEPARATORS = frozenset({",", "，"})


def to_snake(name: str) -> str:
    """TargetType → target_type；BoothID → booth_id；已是 snake_case 則不變。"""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", str(name or ""))
    return s.lower()


def _key_norm(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    escape = False
    for ch in text:
        if quote:
            buf.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch in _SEPARATORS and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _literal(raw: str) -> Any:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].replace("\\" + s[0], s[0])
    if s.startswith("[") and s.endswith("]"):
        return [_literal(item) for item in _split_top_level(s[1:-1])]
    low = s.lower()
    if low in _NULLS:
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"[+-]?(0|[1-9]\d*)", s):        # 有前導零的編號（如 007）保留為字串
        return int(s)
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)", s):
        return float(s)
    return s


def parse_action_call(action_text: str) -> dict[str, Any]:
    """解析 "Name(Key=Value, ...)" 的參數，key 保留 LLM 的寫法（尚未對應到 schema）。"""
    s = (action_text or "").strip()
    if "(" not in s or ")" not in s:
        return {}
    inner = s[s.index("(") + 1: s.rindex(")")]
    out: dict[str, Any] = {}
    for part in _split_top_level(inner):
        m = re.match(r"([A-Za-z_]\w*)\s*[=:]\s*(.*)$", part, re.S)
        if m:   # 沒有名稱的位置參數無法對應，略過
            out[m.group(1)] = _literal(m.group(2))
    return out


def _coerce(value: Any, spec: dict[str, Any], enum_alias: dict[str, str]) -> tuple[Any, str | None]:
    """依 schema 型別轉換；無法轉換時回傳 (原值, 問題種類)。"""
    ptype = str(spec.get("type") or "").strip().lower()

    if ptype in ("string", "str", "text"):
        if isinstance(value, list):
            return ", ".join(str(v) for v in value), None
        if isinstance(value, bool):
            return str(value).lower(), None
        return str(value), None

    if ptype in ("int", "integer"):
        if isinstance(value, bool):
            return value, "invalid_type"
        if isinstance(value, int):
            return value, None
        if isinstance(value, float) and value.is_integer():
            return int(value), None
        if isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d+\s*", value):
            return int(value), None
        return value, "invalid_type"

    if ptype in ("number", "float", "double"):
        if isinstance(value, bool):
            return value, "invalid_type"
        if isinstance(value, (int, float)):
            return value, None
        if isinstance(value, str) and re.fullmatch(r"\s*[+-]?(\d+(\.\d*)?|\.\d+)\s*", value):
            return float(value), None
        return value, "invalid_type"

    if ptype in ("bool", "boolean"):
        if isinstance(value, bool):
            return value, None
        text = str(value).strip().lower()
        if text in _TRUE:
            return True, None
        if text in _FALSE:
            return False, None
        return value, "invalid_type"

    if ptype == "enum":
        allowed = [str(a) for a in spec.get("enum") or []]
        text = str(value).strip()
        if text in allowed:
            return text, None
        by_lower = {a.lower(): a for a in allowed}
        if text.lower() in by_lower:
            return by_lower[text.lower()], None
        mapped = enum_alias.get(text)
        if mapped in allowed:
            return mapped, None
        return value, "invalid_enum"

    if ptype.startswith("list") or ptype == "array":
        items = value if isinstance(value, list) else [p for p in re.split(r"[,，、]", str(value)) if p.strip()]
        if "string" in ptype:
            items = [str(i).strip() for i in items]
        return items, None

    return value, None


def normalize_params(
    raw: dict[str, Any],
    schema: list[dict[str, Any]] | None,
    enum_alias: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把 parse_action_call 的結果對應到 schema。

    issues 的種類：missing_required、invalid_type、invalid_enum、unknown_param（不在 schema 內，仍保留）。
    沒有 schema 時只做名稱轉換（snake_case）與移除 None。
    """
    by_norm = {_key_norm(p["key"]): p for p in schema or [] if p.get("key")}
    params: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for raw_key, value in raw.items():
        spec = by_norm.get(_key_norm(raw_key))
        key = spec["key"] if spec else to_snake(raw_key)
        if value is None:
            continue
        if spec is None:
            if schema:
                issues.append({"param": key, "issue": "unknown_param", "value": value})
            params[key] = value
            continue
        coerced, problem = _coerce(value, spec, (enum_alias or {}).get(key) or {})
        params[key] = coerced
        if problem:
            issues.append({"param": key, "issue": problem, "value": value})
    for spec in schema or []:
        key = spec.get("key")
        if key and spec.get("required") and key not in params:
            issues.append({"param": key, "issue": "missing_required"})
    return params, issues
