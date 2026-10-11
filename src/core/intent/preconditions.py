"""
Action 的前置條件與效果：LLM 產生的計畫在執行前先經過 KG 的符號驗證。

Action KG 中的資料（由 seed 寫入，見 src/kg/action_store.write_action_conditions）：

    (:Action)-[:REQUIRES]->(:Precondition {key, arg, op, value, desc, severity})
    (:Action)-[:PRODUCES]->(:Effect {key, arg, value, value_arg, desc})

- key：狀態變數名稱（例：zone_state）。怎麼查由 DomainProfile.state_probes 定義（Cypher，參數 $arg），
  KG 資料本身不含可執行的查詢
- arg：把 action 的哪個參數值當作查詢對象（例：target_name）；空白表示全域狀態變數
- op：eq / ne / in / not_in / exists / not_exists；value：比較值（in / not_in 為清單）
- severity：block（違反就不可執行，預設）或 warn（只記錄）
- Effect：執行完成後，預期 (key, arg) 的狀態變成 value，或變成參數 value_arg 的值。
  計畫中後續步驟的前置條件會先看這些預期狀態，找不到才查 Blackboard

比較時「查無此狀態」視為值 None（例：not_in ["Closed"] 成立、exists 不成立）；
Blackboard 無法查詢或缺少參數時記為 unverified，不算違反。
"""

from __future__ import annotations

from typing import Any, Callable

# probe(key, arg) -> (available, value)；available 為 False 表示無法查詢（例：黑板代理不在線、未定義此狀態變數）
Probe = Callable[[str, "str | None"], "tuple[bool, Any]"]
Resolve = Callable[[Any], Any]

_OPS = ("eq", "ne", "in", "not_in", "exists", "not_exists")


def _arg_value(node: dict[str, Any], spec: dict[str, Any], resolve: Resolve | None) -> tuple[bool, Any]:
    """回傳 (ok, 參數值)；spec 指定的參數不存在時 ok 為 False。"""
    arg = spec.get("arg")
    if not arg:
        return True, None
    params = node.get("params") or {}
    if arg not in params or params[arg] in (None, ""):
        return False, None
    value = params[arg]
    return True, resolve(value) if resolve else value


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _holds(op: str, expected: Any, actual: Any) -> bool:
    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None
    a = None if actual is None else str(actual)
    if op == "eq":
        return a == str(expected)
    if op == "ne":
        return a != str(expected)
    allowed = {str(v) for v in _as_list(expected)}
    if op == "in":
        return a in allowed
    return a not in allowed          # not_in


def _overlay_key(key: str, arg_value: Any) -> tuple[str, str]:
    return key, "" if arg_value is None else str(arg_value)


def check_node(
    node: dict[str, Any],
    probe: Probe,
    *,
    overlay: dict[tuple[str, str], Any] | None = None,
    resolve: Resolve | None = None,
) -> list[dict[str, Any]]:
    """檢查單一 atomic 節點的前置條件；回傳違反（violated）與無法驗證（unverified）的項目。"""
    findings: list[dict[str, Any]] = []
    for spec in node.get("preconditions") or []:
        key = str(spec.get("key") or "")
        op = str(spec.get("op") or "eq")
        if not key or op not in _OPS:
            continue
        base = {
            "node_id": node.get("id"),
            "task": node.get("task"),
            "key": key,
            "arg": spec.get("arg"),
            "op": op,
            "expected": spec.get("value"),
            "desc": spec.get("desc") or f"{key} {op} {spec.get('value')}",
            "severity": spec.get("severity") or "block",
        }
        ok, arg_value = _arg_value(node, spec, resolve)
        if not ok:
            findings.append({**base, "status": "unverified", "actual": None, "note": "parameter missing"})
            continue
        base["arg_value"] = arg_value
        okey = _overlay_key(key, arg_value)
        if overlay is not None and okey in overlay:
            actual, source = overlay[okey], "predicted"
        else:
            available, actual = probe(key, arg_value)
            if not available:
                findings.append({**base, "status": "unverified", "actual": None, "note": "state unavailable"})
                continue
            source = "blackboard"
        if not _holds(op, spec.get("value"), actual):
            findings.append({**base, "status": "violated", "actual": actual, "source": source})
    return findings


def apply_effects(node: dict[str, Any], overlay: dict[tuple[str, str], Any], *, resolve: Resolve | None = None) -> None:
    """把節點的預期效果寫進 overlay（預期狀態）。"""
    params = node.get("params") or {}
    for effect in node.get("effects") or []:
        key = str(effect.get("key") or "")
        if not key:
            continue
        ok, arg_value = _arg_value(node, effect, resolve)
        if not ok:
            continue
        value_arg = effect.get("value_arg")
        if value_arg:
            if params.get(value_arg) in (None, ""):
                continue
            value = resolve(params[value_arg]) if resolve else params[value_arg]
        else:
            value = effect.get("value")
        overlay[_overlay_key(key, arg_value)] = value


def check_plan(plan: dict[str, Any], probe: Probe, *, resolve: Resolve | None = None) -> list[dict[str, Any]]:
    """依執行順序檢查整個計畫：較早層級節點的效果，會成為後續層級節點的預期狀態（同一層彼此不可見）。"""
    from src.core.monitoring.cursor import PlanCursor   # 延後 import，避免循環相依

    findings: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], overlay: dict[tuple[str, str], Any]) -> dict[tuple[str, str], Any]:
        if node.get("type") == "atomic" or node.get("is_atomic") is True:
            findings.extend(check_node(node, probe, overlay=overlay, resolve=resolve))
            produced: dict[tuple[str, str], Any] = {}
            apply_effects(node, produced, resolve=resolve)
            return produced
        produced_all: dict[tuple[str, str], Any] = {}
        current = dict(overlay)
        for level in PlanCursor.compute_execution_levels(node.get("sub_plans") or [], node.get("execution_logic") or []):
            level_effects: dict[tuple[str, str], Any] = {}
            for child in level:
                if isinstance(child, dict):
                    level_effects.update(walk(child, current))
            current.update(level_effects)
            produced_all.update(level_effects)
        return produced_all

    if isinstance(plan, dict):
        walk(plan, {})
    return findings


def blocking(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if f["status"] == "violated" and f["severity"] == "block"]


def describe(finding: dict[str, Any]) -> str:
    """給 log、錯誤訊息與重規劃 prompt 用的一行說明。"""
    target = f"[{finding['arg_value']}]" if finding.get("arg_value") is not None else ""
    return (f"{finding['desc']} ({finding['key']}{target}: expected {finding['op']} {finding['expected']}, "
            f"actual {finding.get('actual')})")
