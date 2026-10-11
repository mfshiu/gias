"""
步驟之間的資料傳遞：後面步驟的參數可以接前面步驟的輸出。

Action KG 宣告每個 Action 的輸出（由 seed 寫入，見 src/kg/action_store.write_action_outputs）：

    (:Action)-[:HAS_OUTPUT]->(:Output {key, name, desc, type})

執行端把輸出放在結果的 result 欄位（例：RecommendExhibits → result.booths）。

LLM 拆解時，atomic 子意圖以 inputs 把參數接到前面步驟的輸出：

    "inputs": [{"param": "waypoints", "from_id": "1", "output": "booths"}]

planner 轉成節點上的 inputs: [{"param", "from", "output"}]（from 為計畫中的節點 id）。
- output：輸出名稱，可加索引或欄位（booths[0]、route.path）；message 代表該步驟的文字回覆
- 規劃後（check_inputs）：來源必須是同一份計畫中的另一個 atomic 節點，且宣告了這個輸出；
  來源與目標沒有先後關係時補上 Sequence，來源排在目標之後則視為無效。無效的 input 會移除，
  並記在節點的 param_issues（invalid_input）
- 派工前（resolve_inputs）：來源完成後取出實際值，依參數 schema 轉型後放進 params；
  來源沒有完成或沒有產生該輸出時不派工
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .action_params import coerce_value

MESSAGE = "message"

# lookup(node_id) -> (狀態, 結果 payload)；狀態為 NodeState 的值（"done"、"failed"…），找不到節點時為 None
Lookup = Callable[[str], "tuple[str | None, dict[str, Any] | None]"]

_PATH_TOKEN = re.compile(r"\[(-?\d+)\]|([^.\[\]]+)")


def parse_path(output: Any) -> list[str | int]:
    """booths[0] → ["booths", 0]；route.path → ["route", "path"]。"""
    path: list[str | int] = []
    for m in _PATH_TOKEN.finditer(str(output or "").strip()):
        path.append(int(m.group(1)) if m.group(1) is not None else m.group(2).strip())
    return [p for p in path if p != ""]


def step_outputs(payload: dict[str, Any] | None) -> dict[str, Any]:
    """結果 payload 中的輸出（result 欄位，不含執行端的調整記錄）。"""
    result = (payload or {}).get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return {}
    return {k: v for k, v in result.items() if k != "adaptations"}


def extract(payload: dict[str, Any] | None, output: Any) -> Any:
    """從來源步驟的結果取出 output；取不到或為空值時回傳 None。"""
    path = parse_path(output)
    if not path or not isinstance(payload, dict):
        return None
    head, rest = path[0], path[1:]
    value: Any = payload.get(MESSAGE) if head == MESSAGE else step_outputs(payload).get(head)
    for step in rest:
        if isinstance(step, int) and isinstance(value, (list, tuple)) and -len(value) <= step < len(value):
            value = value[step]
        elif isinstance(step, str) and isinstance(value, dict):
            value = value.get(step)
        else:
            return None
    return None if value is None or value == "" or value == [] or value == {} else value


def describe_outputs(outputs: list[dict[str, Any]] | None) -> str:
    """給 prompt 用：booths (list[string]): 推薦的攤位 id; message: 文字回覆。"""
    items = [
        f"{o['key']} ({o.get('type') or 'string'}): {o.get('desc') or o.get('name') or ''}".rstrip(": ")
        for o in outputs or [] if o.get("key")
    ]
    return "; ".join(items + [f"{MESSAGE}: text reply"])


# ----------------------------------------------------------------------
# 規劃後：驗證 inputs、補上先後關係
# ----------------------------------------------------------------------
def _is_atomic(node: Any) -> bool:
    return isinstance(node, dict) and (node.get("type") == "atomic" or node.get("is_atomic") is True)


def _index(plan: dict[str, Any]) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """節點 id → (節點, 從 root 到它的路徑)。"""
    found: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}

    def walk(node: Any, path: list[dict[str, Any]]) -> None:
        if not isinstance(node, dict):
            return
        here = path + [node]
        found.setdefault(str(node.get("id", "")), (node, here))
        if not _is_atomic(node):
            for child in node.get("sub_plans") or []:
                walk(child, here)

    walk(plan, [])
    return found


def _order(source_path: list[dict[str, Any]], target_path: list[dict[str, Any]]) -> tuple[str, dict[str, Any], str, str]:
    """兩個節點在共同上層 composite 中的先後：("before" / "after" / "unordered", composite, 來源分支 id, 目標分支 id)。"""
    from src.core.monitoring.cursor import _reachable, _sequence_deps   # 延後 import，避免循環相依

    i = 0
    while i < min(len(source_path), len(target_path)) and source_path[i] is target_path[i]:
        i += 1
    composite = target_path[i - 1]
    src_branch, tgt_branch = str(source_path[i].get("id", "")), str(target_path[i].get("id", ""))
    ids_to_nodes, deps = _sequence_deps(composite.get("sub_plans"), composite.get("execution_logic"))
    ids = set(ids_to_nodes)
    if src_branch in _reachable(tgt_branch, deps, ids):
        return "before", composite, src_branch, tgt_branch
    if tgt_branch in _reachable(src_branch, deps, ids):
        return "after", composite, src_branch, tgt_branch
    return "unordered", composite, src_branch, tgt_branch


def _input_problem(node_id: str, spec: dict[str, Any], index: dict) -> str | None:
    source_id = str(spec.get("from") or "")
    if not spec.get("param"):
        return "missing param"
    if source_id == node_id:
        return "refers to itself"
    if source_id not in index:
        return f"step {source_id or '?'} not found"
    source, _ = index[source_id]
    if not _is_atomic(source):
        return f"step {source_id} is not an action step"
    path = parse_path(spec.get("output"))
    if not path or not isinstance(path[0], str):
        return "missing output"
    declared = {str(o.get("key")) for o in source.get("outputs") or [] if o.get("key")}
    if path[0] != MESSAGE and path[0] not in declared:
        return f"step {source_id} ({source.get('task')}) has no output {path[0]!r}"
    return None


def check_inputs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """驗證計畫中所有 inputs；回傳補上的 Sequence 關係。無效的 input 移到節點的 param_issues。"""
    index = _index(plan)
    added: list[dict[str, Any]] = []
    for node_id, (node, path) in index.items():
        if not _is_atomic(node) or not node.get("inputs"):
            continue
        kept: list[dict[str, Any]] = []
        for spec in node["inputs"]:
            problem = _input_problem(node_id, spec, index)
            if problem is None:
                order, composite, src_branch, tgt_branch = _order(index[str(spec["from"])][1], path)
                if order == "after":
                    problem = f"step {spec['from']} runs after this step"
                elif order == "unordered":
                    rel = {"type": "Sequence", "from_id": src_branch, "to_id": tgt_branch}
                    composite.setdefault("execution_logic", []).append(rel)
                    added.append({"composite_id": composite.get("id"), **rel})
            if problem is None:
                kept.append(spec)
                continue
            issues = node.setdefault("param_issues", [])
            issues.append({"param": spec.get("param"), "issue": "invalid_input", "detail": problem,
                           "value": {"from": spec.get("from"), "output": spec.get("output")}})
            required = {s.get("key") for s in node.get("params_schema") or [] if s.get("required")}
            if spec.get("param") in required and spec.get("param") not in (node.get("params") or {}):
                issues.append({"param": spec.get("param"), "issue": "missing_required"})
        if kept:
            node["inputs"] = kept
        else:
            node.pop("inputs", None)
    return added


# ----------------------------------------------------------------------
# 派工前：代入實際值
# ----------------------------------------------------------------------
def resolve_inputs(
    node: dict[str, Any],
    lookup: Lookup,
    *,
    enum_alias: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """回傳 (代入後的 params, None) 或 (None, 錯誤說明)。"""
    params = dict(node.get("params") or {})
    schema = {s["key"]: s for s in node.get("params_schema") or [] if s.get("key")}
    for spec in node.get("inputs") or []:
        source_id, output, param = str(spec.get("from") or ""), spec.get("output"), spec.get("param")
        state, payload = lookup(source_id)
        if state != "done":
            return None, f"step {source_id} is {state or 'missing'} (needed for {param})"
        value = extract(payload, output)
        if value is None:
            return None, f"step {source_id} produced no {output!r} (needed for {param})"
        pspec = schema.get(param)
        if pspec is not None:
            value, problem = coerce_value(value, pspec, (enum_alias or {}).get(param) or {})
            if problem:
                shown = json.dumps(value, ensure_ascii=False, default=str)[:80]
                return None, f"{output!r} from step {source_id} is not a valid {param} ({problem}: {shown})"
        params[param] = value
    return params, None
