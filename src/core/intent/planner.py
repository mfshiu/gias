import time

from .action_params import match_param_key, normalize_params, parse_action_call


def _extract_action_name(action_text: str) -> str:
    """從 action 字串（如 LocateExhibit(target_type, target_name)）提取 action 名稱。"""
    s = (action_text or "").strip()
    if not s:
        return ""
    if "(" in s:
        return s.split("(", 1)[0].strip()
    return s


def _child_node_id(parent_id: str, local_id: str) -> str:
    """把 LLM 給的同層 id 轉成全域唯一的階層式 id。

    LLM 每一層都會從 "1" 開始編號，而 PlanCursor 以 id 索引整棵樹，
    因此非 root 的子節點一律加上父節點前綴（例：父 "2" 的子節點 "1" → "2.1"）。
    root 的子節點維持原 id，與既有 plan 格式相容。
    """
    if parent_id in ("", "root"):
        return local_id
    if local_id.startswith(f"{parent_id}."):
        return local_id
    return f"{parent_id}.{local_id}"


def _remap_relationships(relationships: list, id_map: dict[str, str]) -> list[dict]:
    """把 relationships 的 from_id / to_id 從同層 id 換成全域 id；對應不到的保留原值。"""
    out: list[dict] = []
    for rel in relationships or []:
        if not isinstance(rel, dict):
            continue
        r = dict(rel)
        for k in ("from_id", "to_id"):
            v = r.get(k)
            if v is not None and str(v) in id_map:
                r[k] = id_map[str(v)]
        out.append(r)
    return out


def _successors(ids: list[str], relationships: list[dict]) -> dict[str, set[str]]:
    """同層節點 id → 依 Sequence 關係在它之後執行的節點 id。"""
    from src.core.monitoring.cursor import _reachable   # 延後 import，避免循環相依

    deps: dict[str, set[str]] = {i: set() for i in ids}
    for rel in relationships or []:
        if (rel.get("type") or "").strip().lower() != "sequence":
            continue
        a, b = str(rel.get("from_id", "")), str(rel.get("to_id", ""))
        if a in deps and b in deps and a != b:
            deps[b].add(a)
    within = set(ids)
    return {i: {j for j in ids if i in _reachable(j, deps, within)} for i in ids}


def _output_sources(node: dict) -> list[dict]:
    """節點（含子孫）中可被後續步驟引用的 atomic 步驟與其輸出。"""
    if node.get("type") == "atomic" or node.get("is_atomic") is True:
        if not node.get("task"):
            return []
        return [{"id": node.get("id"), "task": node.get("task"), "intent": node.get("intent", ""),
                 "outputs": list(node.get("outputs") or [])}]
    return [src for child in node.get("sub_plans") or [] if isinstance(child, dict) for src in _output_sources(child)]


def _parse_inputs(raw, id_map: dict[str, str], schema: list[dict]) -> tuple[list[dict], list[dict]]:
    """把 LLM 的 inputs 轉成節點上的 [{"param", "from", "output"}]；from_id 為同層 id，或 "@<id>" 指前面的步驟。"""
    inputs: list[dict] = []
    issues: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        param = item.get("param") or item.get("parameter")
        source = str(item.get("from_id") or item.get("from") or "").strip()
        output = str(item.get("output") or "").strip()
        if not param or not source or not output:
            issues.append({"param": param, "issue": "invalid_input", "detail": "incomplete input", "value": item})
            continue
        key, known = match_param_key(param, schema)
        if schema and not known:
            issues.append({"param": key, "issue": "invalid_input", "detail": "unknown parameter", "value": item})
            continue
        source_id = source[1:] if source.startswith("@") else id_map.get(source, source)
        inputs.append({"param": key, "from": source_id, "output": output})
    return inputs, issues


class RecursivePlanner:
    def __init__(self, *, decomposer, logger, kg=None, action_store=None, enum_alias=None):
        self.decomposer = decomposer
        self.logger = logger
        self.kg = kg
        self.action_store = action_store
        # param_key -> {顯示值: enum 值}（DomainProfile.enum_alias），驗證 enum 參數時使用
        self.enum_alias = enum_alias or {}

    def _get_action_metadata(self, action_name: str) -> dict | None:
        """
        取得 Action 的 topic、task、params 等可呼叫服務的資訊。
        回傳 {topic, task, action_id, params_schema, params} 或 None。
        """
        if not self.kg or not action_name:
            return None
        try:
            rows = self.kg.query(
                """
                MATCH (a:Action {name: $action_name})
                RETURN a.id AS action_id, a.topic AS topic, a.task AS task,
                       a.display_name AS display_name, a.description AS description
                LIMIT 1
                """,
                {"action_name": action_name},
            )
            if not rows:
                return None
            meta = dict(rows[0])
            if self.action_store:
                meta["params_schema"] = self.action_store.get_action_params(action_name) or []
            else:
                meta["params_schema"] = []
            meta.update(self._get_action_conditions(action_name))
            meta["outputs"] = self._get_action_outputs(action_name)
            return meta
        except Exception as e:
            self.logger.debug("Failed to get action metadata for %s: %s", action_name, e)
        return None

    def _get_action_conditions(self, action_name: str) -> dict:
        """Action 的前置條件與效果；取不到時視為沒有（不影響規劃）。"""
        getter = getattr(self.action_store, "get_action_conditions", None)
        if not callable(getter):
            return {"preconditions": [], "effects": []}
        try:
            cond = getter(action_name) or {}
        except Exception as e:
            self.logger.debug("Failed to get conditions for %s: %s", action_name, e)
            cond = {}
        return {"preconditions": list(cond.get("preconditions") or []), "effects": list(cond.get("effects") or [])}

    def _get_action_outputs(self, action_name: str) -> list[dict]:
        """Action 宣告的輸出（給後續步驟的 inputs 引用）；取不到時視為沒有。"""
        getter = getattr(self.action_store, "get_action_outputs", None)
        if not callable(getter):
            return []
        try:
            return list(getter(action_name) or [])
        except Exception as e:
            self.logger.debug("Failed to get outputs for %s: %s", action_name, e)
            return []

    def _align_to_given(self, intent, available_actions, call_context: dict, result_json: dict, current_node: dict) -> dict:
        """拆解結果必須保留意圖解析給的子意圖（同一組 id）。

        不一致時帶著差異重問一次；仍不一致就以給定的子意圖為準：保留對應的項目、
        補回缺少的子意圖（交給下一層拆解）、移除多出的項目，並記在 debug.sub_intent_alignment。
        """
        given = call_context["given_sub_intents"]
        expected = [str(g["id"]) for g in given]

        def diff(result: dict) -> tuple[list[str], list[str]]:
            ids = [str(s.get("id") or "").strip() for s in result.get("sub_intents") or [] if isinstance(s, dict)]
            return [i for i in expected if i not in ids], [i for i in ids if i not in expected]

        missing, extra = diff(result_json)
        if not missing and not extra:
            return result_json
        self.logger.warning("Decomposition changed the given sub-intents (missing %s, extra %s); asking again",
                            missing, extra)
        retry_context = {**call_context, "given_sub_intents_feedback": {"missing": missing, "extra": extra}}
        retry = self.decomposer.decompose(intent, available_actions, context=retry_context)
        if retry:
            result_json = retry
            missing, extra = diff(result_json)
            if not missing and not extra:
                return result_json

        by_id: dict[str, dict] = {}
        for s in result_json.get("sub_intents") or []:
            if isinstance(s, dict):
                by_id.setdefault(str(s.get("id") or "").strip(), s)
        aligned = [
            by_id.get(str(g["id"])) or {"id": str(g["id"]), "intent": g["intent"], "is_atomic": False,
                                        "action": "", "atomic_source": None}
            for g in given
        ]
        relationships = [
            r for r in result_json.get("relationships") or []
            if isinstance(r, dict) and str(r.get("from_id")) in expected and str(r.get("to_id")) in expected
        ]
        self.logger.warning("Aligned decomposition to the given sub-intents: restored %s, dropped %s", missing, extra)
        current_node["debug"] = {"sub_intent_alignment": {"restored": missing, "dropped": extra}}
        return {**result_json, "sub_intents": aligned, "relationships": relationships}

    def _get_agent_by_action(self, action_name: str) -> dict | None:
        """
        依 Action 名稱查詢實作該 Action 的 Agent 資訊。
        回傳 {agent_id, agent_name, agent_description, agent_status} 或 None。
        """
        if not self.kg or not action_name:
            return None
        try:
            rows = self.kg.read(
                """
                MATCH (ag:Agent)-[:IMPLEMENTS]->(a:Action {name: $action_name})
                RETURN ag.id AS agent_id, ag.name AS agent_name,
                       ag.description AS agent_description, ag.status AS agent_status
                LIMIT 1
                """,
                {"action_name": action_name},
            )
            if rows:
                return rows[0]
        except Exception as e:
            self.logger.debug("Failed to get agent by action %s: %s", action_name, e)
        return None

    def plan(self, intent, available_actions, *, depth=0, max_depth=4, scheduled_start="N/A", node_id="root",
             context=None, upstream=None, given_sub_intents=None):
        """遞迴拆解 intent。context 是重規劃脈絡（重規劃原因、環境事實），會傳給每一層的拆解；
        upstream 是在這部分之前執行的步驟與其輸出，讓下層的步驟以 inputs 引用（見 dataflow.py）。
        given_sub_intents：意圖解析已拆出的子意圖（[{id, intent, slots, actions}]），只用於這一層：
        LLM 照這些子意圖綁定動作與先後，不再自行重拆（見 _align_to_given）。"""
        indent = "    " * depth
        prefix = "└── " if depth > 0 else "[ROOT] "

        current_node = {
            "id": node_id,
            "intent": intent,
            "depth": depth,
            "scheduled_start": scheduled_start,
            "type": "composite",
            "sub_plans": [],
            "execution_logic": [],
        }

        if depth >= max_depth:
            print(f"{indent}[{scheduled_start}] 🔴 [NEW] {intent} (Type: leaf_forced_atomic)")
            current_node["type"] = "leaf_forced_atomic"
            current_node["is_atomic"] = True
            current_node["atomic_source"] = "new_generated"
            return current_node

        print(f"{indent}{prefix}處理意圖: {intent}")

        extra = {}
        if upstream:
            extra["upstream_outputs"] = upstream
        if given_sub_intents:
            extra["given_sub_intents"] = given_sub_intents
        call_context = {**(context or {}), **extra} if extra else context
        if call_context:
            result_json = self.decomposer.decompose(intent, available_actions, context=call_context)
        else:
            result_json = self.decomposer.decompose(intent, available_actions)
        if not result_json:
            current_node["error"] = "Decomposition failed"
            return current_node
        if given_sub_intents:
            result_json = self._align_to_given(intent, available_actions, call_context, result_json, current_node)

        sub_intents = result_json.get("sub_intents", [])

        if not sub_intents:
            current_node["type"] = "leaf_no_children"
            current_node["is_atomic"] = True
            return current_node

        # LLM 回傳的 id 只在同層有效：先轉成全域唯一 id，再改寫 relationships
        child_ids: list[str] = []
        id_map: dict[str, str] = {}
        for idx, sub in enumerate(sub_intents, 1):
            local_id = str(sub.get("id") or "").strip() or str(idx)
            global_id = _child_node_id(node_id, local_id)
            if global_id in child_ids:
                self.logger.warning("Duplicate sub-intent id %r under %r; renamed", local_id, node_id)
                global_id = f"{global_id}#{idx}"
            child_ids.append(global_id)
            id_map.setdefault(local_id, global_id)
        current_node["execution_logic"] = _remap_relationships(result_json.get("relationships", []), id_map)
        runs_after = _successors(child_ids, current_node["execution_logic"])

        for sub, sub_id in zip(sub_intents, child_ids):
            child_intent = sub.get("intent", "")
            action = sub.get("action", "")
            is_atomic = sub.get("is_atomic", False)
            source = sub.get("atomic_source")
            sched_time = sub.get("scheduled_start", "")

            if is_atomic:
                marker = "🟢 [EXEC]" if source == "pre_defined" else "🔴 [NEW]"
                print(f"{indent}    [{sched_time}] {marker} {child_intent} (Type: {source})")

                action_name = _extract_action_name(action)
                agent_info = self._get_agent_by_action(action_name) if action_name else None
                action_meta = self._get_action_metadata(action_name) if action_name else None
                raw_params = parse_action_call(action) if action else {}

                atomic_node = {
                    "id": sub_id,
                    "intent": child_intent,
                    "action": action,
                    "depth": depth + 1,
                    "scheduled_start": sched_time,
                    "type": "atomic",
                    "is_atomic": True,
                    "atomic_source": source,
                    "sub_plans": [],
                }
                if agent_info:
                    atomic_node["agent"] = agent_info
                if action_meta:
                    atomic_node["topic"] = action_meta.get("topic")
                    atomic_node["task"] = action_meta.get("task")
                    atomic_node["action_id"] = action_meta.get("action_id")
                    schema = atomic_node["params_schema"] = action_meta.get("params_schema", [])
                    if action_meta.get("preconditions"):
                        atomic_node["preconditions"] = action_meta["preconditions"]
                    if action_meta.get("effects"):
                        atomic_node["effects"] = action_meta["effects"]
                    if action_meta.get("outputs"):
                        atomic_node["outputs"] = action_meta["outputs"]
                    # 接前面步驟輸出的參數：派工時才代入，這裡不取 action 字串中的值
                    inputs, input_issues = _parse_inputs(sub.get("inputs"), id_map, schema)
                    bound = {i["param"] for i in inputs}
                    if inputs:
                        atomic_node["inputs"] = inputs
                        raw_params = {k: v for k, v in raw_params.items() if match_param_key(k, schema)[0] not in bound}
                    # 依 schema 對應名稱、轉型別、檢查 enum 與必填；問題記在 param_issues
                    params, issues = normalize_params(raw_params, schema, self.enum_alias)
                    issues = [i for i in issues if not (i["issue"] == "missing_required" and i["param"] in bound)]
                    issues += input_issues
                    atomic_node["params"] = params
                    if issues:
                        atomic_node["param_issues"] = issues

                current_node["sub_plans"].append(atomic_node)
            else:
                time.sleep(0.1)
                # 已規劃、且不排在這個子意圖之後的兄弟步驟，其輸出可被子意圖內的步驟引用
                available = list(upstream or []) + [
                    src for prev in current_node["sub_plans"]
                    if str(prev.get("id")) not in runs_after.get(sub_id, set())
                    for src in _output_sources(prev)
                ]
                child_tree = self.plan(
                    child_intent,
                    available_actions,
                    depth=depth + 1,
                    max_depth=max_depth,
                    scheduled_start=sched_time,
                    node_id=sub_id,
                    context=context,
                    upstream=available,
                )
                if child_tree:
                    current_node["sub_plans"].append(child_tree)

        return current_node
