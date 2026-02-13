from src.core.actions.models import ActionMatch
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
            known[signature] = a_desc

        return known


    # Given a list of sub-intentions, find the best matching action for each, and return a dictionary of action signatures and their descriptions for use in prompt construction.
    def select_actions(self, sub_intentions: list[SubIntent] | list[str]) -> dict[str, str]:
        action_map: dict[str, ActionMatch] = {}

        for si in sub_intentions:
            text = si.intent if isinstance(si, SubIntent) else str(si)
            matches = self.matcher.match_actions(text)

            for match in matches:
                name = match.action.name
                if name not in action_map or match.score > action_map[name].score:
                    action_map[name] = match

        prompt_actions = self._to_prompt_format(list(action_map.values()))
        self.logger.debug(f"prompt_actions: {prompt_actions}")
        return prompt_actions
