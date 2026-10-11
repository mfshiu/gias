"""
IntentionManager：意圖的排程與生命週期管理。

- 一次處理一個意圖：從 PENDING / SUSPENDED 中挑優先序最高者（同優先序先到先做）
- 搶占：新意圖的優先序高於正在處理的意圖時，要求目前的意圖停止（取消執行中的動作）；
  它轉為 SUSPENDED，之後輪到時重新規劃續做
- 取消：排隊中的意圖直接 DROPPED；處理中的意圖要求停止，結束後標為 DROPPED
- 每次狀態轉換都寫入 store 並通知 on_status

實際的規劃與執行由 run_intention(intention, stop_event, on_planned) 提供（IntentionalAgent），回傳：
    {"plan": dict, "summary": dict | None, "stopped": bool, "error": str | None}
規劃完成、要開始執行時呼叫 on_planned(plan)；stop_event 被設定時應盡快停止並回傳 stopped=True。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .model import Intention, IntentionStatus, InvalidTransition
from .store import InMemoryIntentionStore, IntentionStore

RunIntention = Callable[[Intention, threading.Event, Callable[[dict[str, Any]], None]], dict[str, Any]]

_S = IntentionStatus
# 停止原因的優先序：取消 > 關閉 > 搶占（例：被搶占後又被取消 → DROPPED）
_STOP_RANK = {"preempted": 1, "shutdown": 2, "cancelled": 3}


def plan_steps(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把 plan 樹攤平成 atomic 步驟摘要（id、task、params）。"""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "atomic" or node.get("is_atomic") is True:
            out.append({"id": node.get("id"), "task": node.get("task"), "params": node.get("params") or {}})
            return
        for child in node.get("sub_plans") or []:
            walk(child)

    walk(plan)
    return out


def _result_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(summary.get("ok")),
        "message": summary.get("message"),
        "state_counts": summary.get("state_counts"),
        "stopped": bool(summary.get("stopped")),
    }


class IntentionManager:
    def __init__(
        self,
        *,
        run_intention: RunIntention,
        store: IntentionStore | None = None,
        on_status: Callable[[dict[str, Any]], None] | None = None,
        preemption: bool = True,
        logger: logging.Logger | None = None,
    ):
        self._run = run_intention
        self.store = store or InMemoryIntentionStore()
        self._on_status = on_status
        self.preemption = preemption
        self.logger = logger or logging.getLogger(__name__)
        self._intentions: dict[str, Intention] = {}
        self._cond = threading.Condition()
        self._current: Intention | None = None
        self._stop_event: threading.Event | None = None
        self._stop_reason: str | None = None
        self._stop_detail = ""
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 生命週期
    # ------------------------------------------------------------------
    def start(self) -> None:
        with self._cond:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._loop, name="IntentionManager", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 30.0, reason: str = "intention manager stopped") -> None:
        """停止排程：要求處理中的意圖停止，尚未結束的意圖標為 INTERRUPTED。"""
        with self._cond:
            self._running = False
            if self._current is not None:
                self._request_stop("shutdown", reason)
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
        with self._cond:
            for intention in self._intentions.values():
                if not intention.is_terminal and intention is not self._current:
                    self._transition(intention, _S.INTERRUPTED, reason)

    # ------------------------------------------------------------------
    # 對外 API
    # ------------------------------------------------------------------
    def submit(self, text: str, *, priority: int = 0, requester: str = "", intention_id: str | None = None) -> Intention:
        text = (text or "").strip()
        if not text:
            raise ValueError("intention text is empty")
        kwargs = {"intention_id": str(intention_id)} if intention_id else {}
        intention = Intention(text=text, priority=int(priority), requester=str(requester or ""), **kwargs)
        with self._cond:
            if intention.intention_id in self._intentions:
                raise ValueError(f"duplicate intention_id: {intention.intention_id}")
            self._intentions[intention.intention_id] = intention
            self._emit(intention)
            current = self._current
            if self.preemption and current is not None and intention.priority > current.priority:
                self._request_stop("preempted", f"preempted by {intention.intention_id}")
            self._cond.notify_all()
        self.logger.info("Intention submitted: %s priority=%d %r", intention.intention_id, intention.priority, text)
        return intention

    def cancel(self, intention_id: str, reason: str = "cancelled by requester") -> bool:
        with self._cond:
            intention = self._intentions.get(intention_id)
            if intention is None or intention.is_terminal:
                return False
            if intention is self._current:
                self._request_stop("cancelled", reason)
            else:
                self._transition(intention, _S.DROPPED, reason)
            self._cond.notify_all()
            return True

    def get(self, intention_id: str) -> dict[str, Any] | None:
        with self._cond:
            intention = self._intentions.get(intention_id)
            return intention.to_dict() if intention else None

    def list(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        with self._cond:
            items = sorted(self._intentions.values(), key=lambda i: i.created_at)
            return [i.to_dict(with_history=False) for i in items if include_terminal or not i.is_terminal]

    def wait_idle(self, timeout: float | None = None) -> bool:
        """等到沒有處理中或排隊中的意圖；逾時回傳 False。"""
        with self._cond:
            return self._cond.wait_for(lambda: self._current is None and self._next() is None, timeout)

    # ------------------------------------------------------------------
    # 排程
    # ------------------------------------------------------------------
    def _next(self) -> Intention | None:
        runnable = [i for i in self._intentions.values() if i.status in (_S.PENDING, _S.SUSPENDED)]
        return max(runnable, key=lambda i: (i.priority, -i.created_at)) if runnable else None

    def _loop(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: not self._running or self._next() is not None)
                if not self._running:
                    return
                intention = self._next()
                resumed = intention.status == _S.SUSPENDED
                self._current = intention
                self._stop_event = threading.Event()
                self._stop_reason, self._stop_detail = None, ""
                self._transition(intention, _S.PLANNING, "resumed after preemption" if resumed else "scheduled")
                stop_event = self._stop_event
            outcome = self._execute(intention, stop_event)
            with self._cond:
                self._finish(intention, outcome)
                self._current = None
                self._stop_event = None
                self._cond.notify_all()

    def _execute(self, intention: Intention, stop_event: threading.Event) -> dict[str, Any]:
        def on_planned(plan: dict[str, Any]) -> None:
            with self._cond:
                intention.plan_steps = plan_steps(plan)
                if intention.status == _S.PLANNING:
                    self._transition(intention, _S.EXECUTING, "plan committed")

        try:
            return self._run(intention, stop_event, on_planned) or {}
        except Exception as e:
            self.logger.exception("Intention %s failed", intention.intention_id)
            return {"error": str(e)}

    def _finish(self, intention: Intention, outcome: dict[str, Any]) -> None:
        summary = outcome.get("summary")
        if isinstance(summary, dict):
            intention.result = _result_summary(summary)
        completed = isinstance(summary, dict) and bool(summary.get("ok")) and not outcome.get("stopped")
        plan = outcome.get("plan") or {}

        if self._stop_reason == "cancelled" and not completed:
            target, reason = _S.DROPPED, self._stop_detail
        elif self._stop_reason == "shutdown" and not completed:
            target, reason = _S.INTERRUPTED, self._stop_detail
        elif self._stop_reason == "preempted" and outcome.get("stopped"):
            target, reason = _S.SUSPENDED, self._stop_detail
        elif outcome.get("error"):
            target, reason = _S.FAILED, f"error: {outcome['error']}"
        elif plan.get("type") == "leaf_unresolved":
            target, reason = _S.FAILED, f"plan unresolved: {plan.get('reason')}"
        elif completed:
            target, reason = _S.ACHIEVED, "all steps done"
        else:
            target, reason = _S.FAILED, (summary or {}).get("message") or "execution failed"

        if target == _S.ACHIEVED and intention.status == _S.PLANNING:
            self._transition(intention, _S.EXECUTING, "plan committed")
        try:
            self._transition(intention, target, reason)
        except InvalidTransition as e:
            self.logger.error("Intention %s: %s", intention.intention_id, e)
            self._transition(intention, _S.FAILED, f"invalid transition to {target.value}")

    def _request_stop(self, kind: str, detail: str) -> None:
        if self._stop_event is None:
            return
        if self._stop_reason is None or _STOP_RANK[kind] > _STOP_RANK[self._stop_reason]:
            self._stop_reason, self._stop_detail = kind, detail
        self._stop_event.set()

    # ------------------------------------------------------------------
    # 狀態輸出
    # ------------------------------------------------------------------
    def _transition(self, intention: Intention, status: IntentionStatus, reason: str) -> None:
        intention.transition(status, reason)
        self.logger.info("Intention %s -> %s (%s)", intention.intention_id, status.value, reason)
        self._emit(intention)

    def _emit(self, intention: Intention) -> None:
        try:
            self.store.save(intention)
        except Exception as e:
            self.logger.warning("Intention store save failed: %s", e)
        if self._on_status is not None:
            try:
                self._on_status(intention.to_dict(with_history=False))
            except Exception as e:
                self.logger.warning("Intention status callback failed: %s", e)
