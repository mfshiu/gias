"""
意圖結束時給使用者的回覆：把各步驟的結果彙整成一段話。

- llm（預設）：交給 LLM 依步驟結果撰寫；LLM 失敗或回覆為空時改用 template
- template：固定格式列出各步驟的結果
- off：不產生回覆

設定：[intent.response] mode。回覆放在執行摘要的 response（{"text", "source"}），
並寫進意圖的 result.response。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.intent.dataflow import step_outputs
from src.llm.untrusted import MAX_DATA_CHARS, UNTRUSTED_RULE, wrap

RESPONSE_MODES = ("llm", "template", "off")

# 被重規劃取代的步驟不列入回覆
_REPLACED_STATES = frozenset({"obsolete", "skipped"})
_MAX_STEPS_JSON_CHARS = 6000

_SYSTEM_PROMPT = (
    "You are a service assistant writing the final reply to a user whose request was carried out "
    "as several steps by other agents. " + UNTRUSTED_RULE
)


def step_reports(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    """執行摘要中各步驟的結果（依執行順序）；監測迴圈與同步執行兩種摘要格式都適用。"""
    reports: list[dict[str, Any]] = []
    for r in (summary or {}).get("results") or []:
        if not isinstance(r, dict):
            continue
        state = r.get("state") or ("done" if r.get("ok") else "failed")
        if state in _REPLACED_STATES:
            continue
        payload = r.get("result") if isinstance(r.get("result"), dict) else {}
        report: dict[str, Any] = {
            "intent": r.get("intent") or "",
            "task": r.get("task") or "",
            "state": state,
            "message": str(payload.get("message") or ""),
        }
        outputs = step_outputs(payload)
        if outputs:
            report["outputs"] = outputs
        error = r.get("error") or payload.get("error")
        if error and state != "done":
            report["error"] = str(error)
        adapted = [a.get("reason") or a.get("kind") for a in r.get("adaptations") or [] if isinstance(a, dict)]
        if adapted:
            report["adaptations"] = adapted
        reports.append(report)
    return reports


def template_response(intent: str, *, ok: bool, steps: list[dict[str, Any]], reason: str | None = None) -> str:
    """不經 LLM 的回覆：列出各步驟的結果。reason 不為 None 表示無法規劃。"""
    if reason is not None:
        return f"抱歉，無法處理「{intent}」：{reason}"
    lines = [f"已完成「{intent}」。" if ok else f"「{intent}」未能全部完成。"]
    for s in steps:
        label = s.get("intent") or s.get("task") or "步驟"
        if s.get("state") == "done":
            lines.append(f"・{label}：{s.get('message') or '完成'}")
        else:
            lines.append(f"・{label}：未完成（{s.get('error') or s.get('state')}）")
    return "\n".join(lines)


class ResponseComposer:
    def __init__(self, llm: Any = None, *, mode: str = "llm", logger: logging.Logger | None = None):
        mode = str(mode or "llm").strip().lower()
        self.mode = mode if mode in RESPONSE_MODES else "llm"
        self.llm = llm
        self.logger = logger or logging.getLogger(__name__)

    def compose(
        self,
        intent: str,
        *,
        plan: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """回傳 {"text", "source"}；mode 為 off 時回傳 None。plan 為 leaf_unresolved 時說明無法處理的原因。"""
        if self.mode == "off":
            return None
        unresolved = isinstance(plan, dict) and plan.get("type") == "leaf_unresolved"
        reason = str(plan.get("reason") or "unable to plan") if unresolved else None
        ok = not unresolved and bool((summary or {}).get("ok"))
        steps = [] if unresolved else step_reports(summary)
        if self.mode == "llm" and self.llm is not None:
            text = self._llm_reply(intent, ok=ok, steps=steps, reason=reason)
            if text:
                return {"text": text, "source": "llm"}
        return {"text": template_response(intent, ok=ok, steps=steps, reason=reason), "source": "template"}

    def _llm_reply(self, intent: str, *, ok: bool, steps: list[dict[str, Any]], reason: str | None) -> str | None:
        facts = json.dumps(steps, ensure_ascii=False, default=str)
        if len(facts) > _MAX_STEPS_JSON_CHARS:
            facts = facts[:_MAX_STEPS_JSON_CHARS] + " ...(truncated)"
        if reason is not None:
            outcome = f"could not be planned: {reason}"
        else:
            outcome = "completed" if ok else "not fully completed"
        prompt = f"""User request:
{wrap("user_input", intent)}
Outcome: {outcome}
Step results in execution order (JSON):
{wrap("step_results", facts, MAX_DATA_CHARS + 20)}

Write the reply to the user.
- Reply in the language of the user request.
- Use only the information above; do not invent places, times or other facts.
- Combine the results into one answer: what was found or done, and what the user should do next, if anything.
- If some steps failed or the request could not be handled, say so briefly and suggest an alternative.
- Plain text, at most 5 sentences. No markdown, no JSON."""
        try:
            resp = self.llm.chat(messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
        except Exception as e:
            self.logger.warning("Composing the reply with the LLM failed; using the template: %s", e)
            return None
        text = getattr(resp, "content", None)
        return text.strip() if isinstance(text, str) and text.strip() else None
