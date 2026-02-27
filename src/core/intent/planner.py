import re
import time


def _extract_action_name(action_text: str) -> str:
    """從 action 字串（如 LocateExhibit(target_type, target_name)）提取 action 名稱。"""
    s = (action_text or "").strip()
    if not s:
        return ""
    if "(" in s:
        return s.split("(", 1)[0].strip()
    return s


def _pascal_to_snake(name: str) -> str:
    """TargetType -> target_type"""
    if not name:
        return ""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return s


def _parse_action_params(action_text: str) -> dict:
    """
    從 action 字串解析參數 bindings。
    例：LocateExhibit(TargetType="攤位", TargetName="A12") -> {"target_type": "攤位", "target_name": "A12"}
    """
    out: dict = {}
    s = (action_text or "").strip()
    if "(" not in s or ")" not in s:
        return out
    inner = s[s.index("(") + 1 : s.rindex(")")].strip()
    if not inner:
        return out
    # 簡易解析：Key="Value" 或 Key='Value' 或 Key=Value，以逗號分隔（需處理引號內逗號）
    pattern = r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^,]+)'
    for m in re.finditer(pattern, inner):
        key_pascal = m.group(1).strip()
        val = m.group(2).strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"')
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1].replace("\\'", "'")
        key = _pascal_to_snake(key_pascal)
        out[key] = val
    return out


class RecursivePlanner:
    def __init__(self, *, decomposer, logger, kg=None, action_store=None):
        self.decomposer = decomposer
        self.logger = logger
        self.kg = kg
        self.action_store = action_store

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

    def plan(self, intent, available_actions, *, depth=0, max_depth=4, scheduled_start="N/A", node_id="root"):
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

        result_json = self.decomposer.decompose(intent, available_actions)
        if not result_json:
            current_node["error"] = "Decomposition failed"
            return current_node

        current_node["execution_logic"] = result_json.get("relationships", [])
        sub_intents = result_json.get("sub_intents", [])

        if not sub_intents:
            current_node["type"] = "leaf_no_children"
            current_node["is_atomic"] = True
            return current_node

        for sub in sub_intents:
            sub_id = sub.get("id", "unknown")
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
                params_bindings = _parse_action_params(action) if action else {}

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
                    atomic_node["params"] = params_bindings

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
                )
                if child_tree:
                    current_node["sub_plans"].append(child_tree)

        return current_node
