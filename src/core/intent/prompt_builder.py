import json
from typing import Any

# 環境事實可能很多列；超過此長度就截斷，避免 prompt 過長
_MAX_ENV_FACTS_CHARS = 4000


def _format_replan_context(context: dict[str, Any]) -> str:
    """把重規劃脈絡（PlanRepair.build_replan_context）轉成 prompt 段落。"""
    lines = [
        "### Replanning Context",
        "This decomposition replaces part of a plan that could not continue as planned.",
    ]
    replan = context.get("replan") or {}
    if replan.get("reason"):
        lines.append(f"- **Reason**: {replan['reason']}")
    for step in replan.get("affected_steps") or []:
        params = json.dumps(step.get("params") or {}, ensure_ascii=False, default=str)
        detail = f"state={step.get('state')}" + (f", error={step['error']}" if step.get("error") else "")
        lines.append(f"- **Affected step**: {step.get('task')} {params} ({detail})")
    env = context.get("env_facts")
    if env:
        env_json = json.dumps(env, ensure_ascii=False, default=str)
        if len(env_json) > _MAX_ENV_FACTS_CHARS:
            env_json = env_json[:_MAX_ENV_FACTS_CHARS] + " ...(truncated)"
        lines += ["", "### Current Environment (authoritative facts from the Blackboard)", env_json]
    lines += [
        "",
        "### Replanning Rules",
        "8. **Respect the Environment**: Do not route through, recommend, or target places listed as "
        "closed, blocked, crowded, or unavailable when an alternative exists; if an action has a parameter "
        "for such constraints (e.g. avoiding crowds), set it.",
        "9. **Do Not Repeat Failures**: Do not repeat an affected step with the same arguments unless the "
        "reason indicates a transient failure.",
    ]
    return "\n".join(lines)


class PromptBuilder:
    def build_prompt(
        self,
        current_intent: str,
        available_actions: dict[str, str],
        context: dict[str, Any] | None = None,
    ) -> str:
        tools_description = "\n".join([f"- {k}: {v}" for k, v in available_actions.items()])
        # 只有重規劃時才附加脈絡段落；一般規劃的 prompt 維持原樣
        context_section = f"\n{_format_replan_context(context)}\n" if context else ""

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
{context_section}
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
