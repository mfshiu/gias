class PromptBuilder:
    def build_prompt(self, current_intent: str, available_actions: dict[str, str]) -> str:
        tools_description = "\n".join([f"- {k}: {v}" for k, v in available_actions.items()])

        return f"""You are the "GIAS Intent Decomposition Engine".
Break down the User Intent into immediate sub-intents (one level deep only).

### Available Atomic Intents
{tools_description}

### Context
- **Current Intent**: "{current_intent}"

### Rules
1. **One Level Only**: Produce only one level of sub-intents (no deeper nesting).
2. **Atomic Selection**: If a sub-intent matches one of the Available Atomic Intents, set `is_atomic=true` and `atomic_source="pre_defined"`.
- If no atomic intent matches, set `is_atomic=false` and `atomic_source=null` (or "new_generated" only if you truly define a new atomic intent).
3. **Action Field**:
- If `is_atomic=true`, `action` MUST be a function-like call using the atomic intent name and extracted arguments when applicable.
- If `is_atomic=false`, set `action` to an empty string "".
4. **Intent Field**: `intent` MUST be the natural-language sub-intention text derived from the current intent.
5. **Time Awareness**: Only assign `scheduled_start` if a specific, absolute time is mentioned or logically required (e.g., "14:00").
6. **No Relative Time**: Do NOT use relative markers like "T-15m", "ASAP", "tomorrow morning".
7. **Empty Value**: If a sub-intent does not have a confirmed absolute start time, set `scheduled_start` to "".

### Output Format
Return ONLY valid JSON. No markdown, no explanation.

{{
"parent_intent": "string",
"sub_intents": [
    {{
    "id": "string",
    "intent": "string",
    "action": "string",
    "is_atomic": boolean,
    "atomic_source": "pre_defined" | "new_generated" | null,
    "scheduled_start": "string (HH:MM or empty)"
    }}
],
"relationships": [
    {{ "type": "Sequence"|"Parallel", "from_id": "string", "to_id": "string" }}
]
}}
""".strip()
