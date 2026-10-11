from src.core.actions.models import ActionMatch
from src.kg.action_store import ActionStore
from .dataflow import describe_outputs
from .sub_intent import SubIntent



class ActionSelector:
    def __init__(self, *, kg=None, matcher, logger, action_store: ActionStore | None = None):
        """action_store：查詢 Action 參數與輸出（批次、有快取）；不給時以 kg 建立。"""
        self.kg = kg
        self.matcher = matcher
        self.logger = logger
        self.action_store = action_store or ActionStore(kg)


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
        # 所有 Action 的參數與輸出一次取得（批次查詢、有快取），不再每個 Action 各查一次
        try:
            schemas = self.action_store.get_actions_schema([m.action.name for m in actions])
        except Exception as e:
            self.logger.warning("Failed to read action params/outputs: %s", e)
            schemas = {}

        for m in actions:
            a_name = m.action.name
            a_desc = m.action.description or ""
            schema = schemas.get(a_name) or {}

            # 簽章依參數宣告順序列出
            params = sorted(schema.get("params") or [], key=lambda p: p.get("order") if p.get("order") is not None else 999)
            param_keys = [self._fmt_param_key(p["key"]) for p in params if p.get("key")]

            signature = f"{a_name}({', '.join(param_keys)})" if param_keys else f"{a_name}()"
            outputs = [o for o in schema.get("outputs") or [] if o.get("key")]
            # 列出輸出，讓 LLM 知道後續步驟可以用 inputs 接哪些結果
            known[signature] = f"{a_desc} | Outputs: {describe_outputs(outputs)}" if outputs else a_desc

        return known


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
