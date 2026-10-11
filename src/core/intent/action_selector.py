from src.core.actions.models import ActionMatch
from .dataflow import describe_outputs
from .sub_intent import SubIntent



class ActionSelector:
    def __init__(self, *, kg, matcher, logger):
        self.kg = kg
        self.matcher = matcher
        self.logger = logger


    def _fmt_param_key(self, key: str) -> str:
        parts = (key or "").split("_")
        out = []
        for part in parts:
            if part.lower() == "id":
                out.append("ID")
            else:
                out.append(part[:1].upper() + part[1:])
        return "".join(out) if out else "Param"


    #/ For a list of sub-intentions, select the most relevant action for each, and return a dict of action signatures and descriptions for prompt construction.
    def _to_prompt_format(self, actions: list[ActionMatch]) -> dict[str, str]:
        known: dict[str, str] = {}

        for m in actions:
            a_name = m.action.name
            a_desc = m.action.description or ""

            rows = self.kg.read(
                """
                MATCH (a:Action {name:$name})-[r:HAS_PARAM]->(p:Param)
                RETURN p.key AS key, r.order AS ord
                ORDER BY ord ASC
                """,
                {"name": a_name},
            )

            param_keys = []
            for rr in rows or []:
                k = rr.get("key")
                if k:
                    param_keys.append(self._fmt_param_key(k))

            signature = f"{a_name}({', '.join(param_keys)})" if param_keys else f"{a_name}()"
            outputs = self._outputs(a_name)
            # 列出輸出，讓 LLM 知道後續步驟可以用 inputs 接哪些結果
            known[signature] = f"{a_desc} | Outputs: {describe_outputs(outputs)}" if outputs else a_desc

        return known


    def _outputs(self, action_name: str) -> list[dict]:
        try:
            rows = self.kg.read(
                """
                MATCH (a:Action {name:$name})-[r:HAS_OUTPUT]->(o:Output)
                RETURN o.key AS key, o.type AS type, coalesce(o.desc, o.name) AS desc
                ORDER BY coalesce(r.order, 999), o.key
                """,
                {"name": action_name},
            )
        except Exception as e:
            self.logger.debug("Failed to read outputs of %s: %s", action_name, e)
            return []
        return [r for r in rows or [] if isinstance(r, dict) and r.get("key")]


    # Given a list of sub-intentions, find the best matching action for each, and return a dictionary of action signatures and their descriptions for use in prompt construction.
    # matches：已比對好的結果（與 sub_intentions 一一對應）；給了就直接使用，不再比對一次
    def select_actions(
        self, sub_intentions: list[SubIntent] | list[str], matches: list[list[ActionMatch]] | None = None
    ) -> dict[str, str]:
        action_map: dict[str, ActionMatch] = {}

        for i, si in enumerate(sub_intentions):
            text = si.intent if isinstance(si, SubIntent) else str(si)
            matches_i = matches[i] if matches is not None else self.matcher.match_actions(text)

            for match in matches_i:
                name = match.action.name
                if name not in action_map or match.score > action_map[name].score:
                    action_map[name] = match

        prompt_actions = self._to_prompt_format(list(action_map.values()))
        self.logger.verbose(f"prompt_actions: {prompt_actions}")
        return prompt_actions
