import time

class RecursivePlanner:
    def __init__(self, *, decomposer, logger):
        self.decomposer = decomposer
        self.logger = logger

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

                current_node["sub_plans"].append({
                    "id": sub_id,
                    "intent": child_intent,
                    "action": action,
                    "depth": depth + 1,
                    "scheduled_start": sched_time,
                    "type": "atomic",
                    "is_atomic": True,
                    "atomic_source": source,
                    "sub_plans": [],
                })
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
