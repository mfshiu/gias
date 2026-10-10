import time

from .action_params import normalize_params, parse_action_call


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
            return meta
        except Exception as e:
            self.logger.debug("Failed to get action metadata for %s: %s", action_name, e)
        return None

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

    def plan(self, intent, available_actions, *, depth=0, max_depth=4, scheduled_start="N/A", node_id="root", context=None):
        """遞迴拆解 intent。context 是重規劃脈絡（重規劃原因、環境事實），會傳給每一層的拆解。"""
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

        if context:
            result_json = self.decomposer.decompose(intent, available_actions, context=context)
        else:
            result_json = self.decomposer.decompose(intent, available_actions)
        if not result_json:
            current_node["error"] = "Decomposition failed"
            return current_node

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
                    atomic_node["params_schema"] = action_meta.get("params_schema", [])
                    # 依 schema 對應名稱、轉型別、檢查 enum 與必填；問題記在 param_issues
                    params, issues = normalize_params(raw_params, atomic_node["params_schema"], self.enum_alias)
                    atomic_node["params"] = params
                    if issues:
                        atomic_node["param_issues"] = issues

                current_node["sub_plans"].append(atomic_node)
            else:
                time.sleep(0.1)
                child_tree = self.plan(
                    child_intent,
                    available_actions,
                    depth=depth + 1,
                    max_depth=max_depth,
                    scheduled_start=sched_time,
                    node_id=sub_id,
                    context=context,
                )
                if child_tree:
                    current_node["sub_plans"].append(child_tree)

        return current_node
