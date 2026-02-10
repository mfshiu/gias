import json

class LLMDecomposer:
    def __init__(self, *, llm, prompt_builder, logger):
        self.llm = llm
        self.prompt_builder = prompt_builder
        self.logger = logger

    def decompose(self, intent: str, available_actions: dict[str, str]) -> dict | None:
        try:
            resp = self.llm.chat(
                messages=[
                    {"role": "system", "content": "You are a specialized agent for Time-Aware HTN planning. Return ONLY valid JSON."},
                    {"role": "user", "content": self.prompt_builder.build_prompt(intent, available_actions)},
                ],
                response_format={"type": "json_object"},
            )
            return json.loads(resp.content.strip())
        except Exception:
            self.logger.exception("LLM call failed in decompose_intent")
            return None
