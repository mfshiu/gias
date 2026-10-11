import json
from typing import Any

from src.llm.untrusted import MAX_DATA_CHARS, UNTRUSTED_RULE, sanitize, wrap

from .dataflow import describe_outputs

# 環境事實可能很多列；超過此長度就截斷，避免 prompt 過長
_MAX_ENV_FACTS_CHARS = 4000
# 已完成步驟的結果：每步最多這麼多字、最多列這麼多步
_MAX_STEP_RESULT_CHARS = 300
_MAX_COMPLETED_STEPS = 10


def _step_result(step: dict[str, Any]) -> str:
    """已完成步驟的結果（文字回覆與輸出）；沒有結果時為空字串。"""
    parts = []
    if step.get("message"):
        parts.append(str(step["message"]))
    if step.get("outputs"):
        parts.append("outputs " + json.dumps(step["outputs"], ensure_ascii=False, default=str))
    text = "; ".join(parts)
    if len(text) > _MAX_STEP_RESULT_CHARS:
        text = text[:_MAX_STEP_RESULT_CHARS] + " ...(truncated)"
    return f" -> {text}" if text else ""


def _format_replan_context(context: dict[str, Any]) -> str:
    """把重規劃脈絡（PlanRepair.build_replan_context）轉成 prompt 段落。"""
    lines = [
        "### Replanning Context",
        "This decomposition replaces part of a plan that could not continue as planned.",
    ]
    replan = context.get("replan") or {}
    if replan.get("reason"):
        lines.append(f"- **Reason**: {sanitize(replan['reason'])}")
    # 步驟的參數、錯誤與結果來自執行端，放在資料區塊中
    step_lines = []
    for step in replan.get("affected_steps") or []:
        params = json.dumps(step.get("params") or {}, ensure_ascii=False, default=str)
        detail = f"state={step.get('state')}" + (f", error={step['error']}" if step.get("error") else "")
        step_lines.append(f"- **Affected step**: {step.get('task')} {params} ({detail}){_step_result(step)}")
    completed = list(context.get("completed_steps") or [])[-_MAX_COMPLETED_STEPS:]
    for step in completed:
        params = json.dumps(step.get("params") or {}, ensure_ascii=False, default=str)
        step_lines.append(f"- **Completed step**: {step.get('task')} {params}{_step_result(step)}")
    if step_lines:
        lines.append(wrap("step_results", "\n".join(step_lines), MAX_DATA_CHARS))
    env = context.get("env_facts")
    if env:
        env_json = json.dumps(env, ensure_ascii=False, default=str)
        if len(env_json) > _MAX_ENV_FACTS_CHARS:
            env_json = env_json[:_MAX_ENV_FACTS_CHARS] + " ...(truncated)"
        lines += ["", "### Current Environment (authoritative facts from the Blackboard)",
                  wrap("env_data", env_json, _MAX_ENV_FACTS_CHARS + 20)]
    lines += [
        "",
        "### Replanning Rules",
        "9. **Respect the Environment**: Do not route through, recommend, or target places listed as "
        "closed, blocked, crowded, or unavailable when an alternative exists; if an action has a parameter "
        "for such constraints (e.g. avoiding crowds), set it.",
        "10. **Do Not Repeat Failures**: Do not repeat an affected step with the same arguments unless the "
        "reason indicates a transient failure.",
    ]
    if completed:
        lines.append(
            "11. **Reuse Completed Results**: Completed steps have finished; do not repeat them. Where a new "
            "step needs one of their results, write the value into its arguments directly."
        )
    return "\n".join(lines)


def _format_given(given: list[dict[str, Any]], feedback: dict[str, Any] | None) -> str:
    """意圖解析已拆出的子意圖：這一層照它們綁定動作與先後，不再自行重拆。"""
    lines = [
        "### Sub-intents (already identified)",
        "The current intent has already been split into the sub-intents below, with the entities taken from the "
        "user's words and the atomic intents matched to each. Use exactly these as your `sub_intents`: keep their "
        "ids and order, and do not add, merge, split or drop any. For each one, decide `is_atomic`, `action` and "
        "`inputs` (a sub-intent that needs several actions is not atomic and will be decomposed later), and give "
        "the `relationships` between them.",
    ]
    for g in given:
        detail = f'- "{g.get("id")}": {sanitize(g.get("intent"))}'
        if g.get("slots"):
            detail += " | entities " + sanitize(json.dumps(g["slots"], ensure_ascii=False, default=str))
        if g.get("actions"):
            detail += " | matched: " + ", ".join(g["actions"])
        lines.append(detail)
    if feedback:
        lines.append(
            f"Your previous answer did not keep these sub-intents (missing ids {feedback.get('missing') or []}, "
            f"unexpected ids {feedback.get('extra') or []}). Return exactly the ids listed above."
        )
    return "\n".join(lines)


def _format_upstream(sources: list[dict[str, Any]]) -> str:
    """在這部分之前執行的步驟與其輸出（planner 的 upstream），讓下層步驟以 inputs 引用。"""
    lines = [
        "### Results Available from Earlier Steps",
        'These steps run before this part of the plan. Use their outputs through `inputs` with `from_id` "@<id>".',
    ]
    for src in sources:
        lines.append(f"- @{src.get('id')} {src.get('task')} ({src.get('intent', '')}): {describe_outputs(src.get('outputs'))}")
    return "\n".join(lines)


class PromptBuilder:
    def build_prompt(
        self,
        current_intent: str,
        available_actions: dict[str, str],
        context: dict[str, Any] | None = None,
    ) -> str:
        tools_description = "\n".join([f"- {k}: {v}" for k, v in available_actions.items()])
        # 重規劃時附加脈絡段落；下層拆解時附加前面步驟可用的輸出
        context = context or {}
        sections = []
        if context.get("replan") or context.get("env_facts") or context.get("completed_steps"):
            sections.append(_format_replan_context(context))
        if context.get("given_sub_intents"):
            sections.append(_format_given(context["given_sub_intents"], context.get("given_sub_intents_feedback")))
        if context.get("upstream_outputs"):
            sections.append(_format_upstream(context["upstream_outputs"]))
        context_section = "".join(f"\n{s}\n" for s in sections)

        return f"""You are the "GIAS Intent Decomposition Engine".
Break down the User Intent into immediate sub-intents (one level deep only).

### Available Atomic Intents
{tools_description}

### Context
- **Current Intent** (from the user):
{wrap("user_input", current_intent)}

### Data Handling
{UNTRUSTED_RULE}

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
8. **Passing Results**: If an atomic sub-intent needs a result produced by an earlier step (see the Outputs of each atomic intent), do not guess the value: list it in `inputs` as {{"param", "from_id", "output"}}, leave that argument out of `action`, and add a Sequence relationship from the producing step. `output` is an output name, optionally indexed (e.g. `booths[0]`), or `message` for the step's text reply. Otherwise set `inputs` to [].
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
    "scheduled_start": "string (HH:MM or empty)",
    "inputs": [{{ "param": "string", "from_id": "string", "output": "string" }}]
    }}
],
"relationships": [
    {{ "type": "Sequence"|"Parallel", "from_id": "string", "to_id": "string" }}
]
}}
""".strip()
