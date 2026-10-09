"""
InfoAgent：提供資訊類 action（ExplainExhibit, AnswerFAQ, LocateFacility,
ProvideSchedule, RecommendExhibits, CrowdStatus）。

訂閱：
- info.request      請求（既有；同時相容舊 publish_sync 路徑與新 task_id 路徑）
- info.cancel       取消（新增）

發佈：
- info.result       任務完成時（新增；僅在請求帶 task_id 時發佈）
- info.progress     進度（新增；advisory）

執行：python -m src.agents.info_agent
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from agentflow.core.agent import Agent

from src.app_helper import get_agent_config
from src.log_helper import init_logging
from src.agents._executor_utils import (
    CancelToken,
    TOPIC_INFO_CANCEL,
    TOPIC_INFO_PROGRESS,
    TOPIC_INFO_REQUEST,
    TOPIC_INFO_RESULT,
    build_progress_payload,
    build_result,
    parse_action_payload,
    parse_cancel_payload,
)
from src.blackboard.client import get_crowd_hotspots, get_open_booths

logger = init_logging()

TOPIC = TOPIC_INFO_REQUEST
TOPIC_CANCEL = TOPIC_INFO_CANCEL
TOPIC_RESULT = TOPIC_INFO_RESULT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _simulate_progress(
    steps: list[str],
    delay: float = 0.2,
    *,
    cancel_token: CancelToken | None = None,
) -> bool:
    """模擬處理過程，逐步顯示進度；中途可被 cancel_token 中斷。

    回傳 True 表示完整跑完，False 表示被取消。
    """
    for i, msg in enumerate(steps, 1):
        if cancel_token is not None and cancel_token.cancelled:
            print(f"  [InfoAgent] (cancelled at step {i-1}/{len(steps)})", flush=True)
            logger.info("InfoAgent progress cancelled: reason=%s", cancel_token.reason)
            return False
        print(f"  [InfoAgent] {i}/{len(steps)} {msg}", flush=True)
        logger.info("InfoAgent progress: %s", msg)
        # 把延遲拆成小片段，方便快速回應 cancel
        slept = 0.0
        slice_dt = 0.05
        while slept < delay:
            if cancel_token is not None and cancel_token.cancelled:
                return False
            time.sleep(min(slice_dt, delay - slept))
            slept += slice_dt
    return True


def _execute(
    agent: "InfoAgent",
    task: str,
    params: dict[str, Any],
    *,
    cancel_token: CancelToken | None = None,
) -> tuple[str, bool]:
    """模擬執行資訊類 task。

    回傳 (message, cancelled)；cancelled=True 時 message 可能為部分結果。
    """
    if task == "CrowdStatus":
        ok = _simulate_progress(
            [
                "向黑板查詢觀察者回報的人潮資料...",
                "整合各 Zone 即時狀態...",
                "產生人潮報告...",
            ],
            cancel_token=cancel_token,
        )
        if not ok:
            return ("CrowdStatus 已取消。", True)
        hotspots = get_crowd_hotspots(agent)
        if hotspots:
            crowded = [h for h in hotspots if h.get("crowd_status") == "Crowded"]
            normal = [h for h in hotspots if h.get("crowd_status") == "Normal"]
            sparse = [h for h in hotspots if h.get("crowd_status") == "Sparse"]
            lines = []
            if crowded:
                zones = ", ".join(h.get("zone", "?") for h in crowded)
                lines.append(f"【黑板觀察】擁擠區: {zones}")
            if normal:
                zones = ", ".join(h.get("zone", "?") for h in normal)
                lines.append(f"【黑板觀察】正常區: {zones}")
            if sparse:
                zones = ", ".join(h.get("zone", "?") for h in sparse)
                lines.append(f"【黑板觀察】稀疏區: {zones}")
            return ("；".join(lines) if lines else "目前各展區人潮狀況正常。", False)
        return ("無法取得黑板人潮資料（請確認觀察者與黑板代理已啟動）。", False)

    if task == "RecommendExhibits":
        interests = params.get("interests", [])
        ok = _simulate_progress(
            [
                f"依興趣 {interests} 搜尋展區...",
                "向黑板查詢人潮與展位狀態...",
                "排序推薦清單...",
            ],
            cancel_token=cancel_token,
        )
        if not ok:
            return ("RecommendExhibits 已取消。", True)
        hotspots = get_crowd_hotspots(agent)
        booths = get_open_booths(agent)
        avoid = [h.get("zone") for h in (hotspots or []) if h.get("crowd_status") == "Crowded"]
        rec = [b.get("exhibitor") or b.get("id") for b in (booths or []) if b.get("id")]
        msg = f"已根據興趣 {interests} 推薦展區。"
        if avoid:
            msg += f" 【黑板觀察】建議避開擁擠區: {', '.join(avoid)}"
        if rec:
            msg += f" 開放展位: {', '.join(rec)}"
        return (msg, False)

    if task == "ExplainExhibit":
        target = params.get("target_name", "?")
        ok = _simulate_progress(
            [
                f"查詢展品「{target}」資料...",
                "載入多媒體說明...",
                "整理介紹內容...",
            ],
            cancel_token=cancel_token,
        )
        if not ok:
            return ("ExplainExhibit 已取消。", True)
        return (f"已介紹展品 {target} 的內容與特色。", False)

    if task == "AnswerFAQ":
        q = params.get("question", "?")
        ok = _simulate_progress(
            [
                f"搜尋 FAQ：{q[:20]}{'...' if len(str(q)) > 20 else ''}",
                "比對知識庫...",
                "產生回覆...",
            ],
            cancel_token=cancel_token,
        )
        if not ok:
            return ("AnswerFAQ 已取消。", True)
        return (f"已回覆 FAQ：{q}", False)

    if task == "LocateFacility":
        ft = params.get("facility_type", "?")
        ok = _simulate_progress(
            [
                f"查詢 {ft} 設施位置...",
                "取得樓層與動線...",
                "規劃最近路線...",
            ],
            cancel_token=cancel_token,
        )
        if not ok:
            return ("LocateFacility 已取消。", True)
        return (f"已找到 {ft} 設施位置。", False)

    if task == "ProvideSchedule":
        ok = _simulate_progress(
            [
                "載入活動行事曆...",
                "篩選今日場次...",
                "整理時間與地點...",
            ],
            cancel_token=cancel_token,
        )
        if not ok:
            return ("ProvideSchedule 已取消。", True)
        return ("已提供活動時間與地點資訊。", False)

    ok = _simulate_progress(["處理中...", "完成"], cancel_token=cancel_token)
    if not ok:
        return (f"{task} 已取消。", True)
    return (f"已提供 {task} 相關資訊。", False)


class InfoAgent(Agent):
    """提供資訊類 action。

    支援兩種呼叫模式：
    - 舊：publish_sync(info.request, payload) → executor 回傳 dict（auto-reply）
    - 新：publish(info.request, payload_with_task_id) → executor 回 info.result
    新模式同時支援 publish(info.cancel, {task_id, reason})。
    """

    def __init__(self, agent_config: dict[str, Any]):
        super().__init__("info_agent.gias", agent_config)
        self._tasks: dict[str, CancelToken] = {}     # task_id -> CancelToken
        self._idempotency_cache: dict[str, dict[str, Any]] = {}
        self._tasks_lock = threading.Lock()

    # ------------------------------------------------------------------
    # AgentFlow lifecycle
    # ------------------------------------------------------------------
    def on_connected(self) -> None:
        logger.info("InfoAgent subscribing: %s, %s", TOPIC, TOPIC_CANCEL)
        self.subscribe(TOPIC, "dict", self._handle)
        self.subscribe(TOPIC_CANCEL, "dict", self._handle_cancel)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _handle(self, topic: str, pcl: Any) -> dict[str, Any]:
        payload = parse_action_payload(pcl)
        task = payload.get("task", "Unknown")
        params = payload.get("params") or {}
        action_id = payload.get("action_id")
        intent = payload.get("intent", "")
        task_id = payload.get("task_id")
        idempotency_key = payload.get("idempotency_key")
        interruptible = payload.get("interruptible", True)

        print(
            f"\n[InfoAgent] 收到請求: task={task} task_id={task_id} params={params}",
            flush=True,
        )
        logger.info("InfoAgent received: task=%s task_id=%s", task, task_id)

        # idempotency：相同 key 在同個 process 內直接回 cache
        if idempotency_key and idempotency_key in self._idempotency_cache:
            cached = self._idempotency_cache[idempotency_key]
            logger.info("InfoAgent idempotency hit: %s", idempotency_key)
            if task_id:
                self._publish_result(cached)
            return cached

        # 註冊 cancel token（即使是舊 sync 路徑也可），供 cancel/info 取消同一 task
        token = CancelToken() if interruptible else None
        if task_id and token is not None:
            with self._tasks_lock:
                self._tasks[task_id] = token

        started_at = _now_iso()
        t0 = time.time()
        cancelled = False
        try:
            message, cancelled = _execute(self, task, params, cancel_token=token)
        except Exception as e:
            logger.exception("InfoAgent execute failed: %s", e)
            message, cancelled = (f"執行失敗：{e}", False)
            error = str(e)
        else:
            error = None
        finally:
            if task_id is not None:
                with self._tasks_lock:
                    self._tasks.pop(task_id, None)

        finished_at = _now_iso()
        elapsed = round(time.time() - t0, 3)
        ok = (error is None) and not cancelled

        if not cancelled:
            print(f"  [InfoAgent] ✓ 完成: {message}", flush=True)
        logger.info(
            "InfoAgent result: task=%s ok=%s cancelled=%s elapsed=%.3fs",
            task,
            ok,
            cancelled,
            elapsed,
        )

        result = build_result(
            task=task,
            message=message,
            action_id=action_id,
            intent=intent,
            task_id=task_id,
            ok=ok,
            cancelled=cancelled,
            error=error,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_sec=elapsed,
        )

        if idempotency_key and ok:
            self._idempotency_cache[idempotency_key] = result

        if task_id:
            self._publish_result(result)
        return result

    def _handle_cancel(self, topic: str, pcl: Any) -> dict[str, Any]:
        data = parse_cancel_payload(pcl)
        task_id = str(data.get("task_id", ""))
        reason = str(data.get("reason", ""))
        if not task_id:
            return {"ok": False, "error": "missing task_id"}
        with self._tasks_lock:
            tok = self._tasks.get(task_id)
        if tok is None:
            logger.info("InfoAgent cancel: task_id=%s not in flight", task_id)
            return {"ok": False, "error": "task not in flight"}
        tok.cancel(reason)
        logger.info("InfoAgent cancel signalled: task_id=%s reason=%s", task_id, reason)
        return {"ok": True, "task_id": task_id}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _publish_result(self, result: dict[str, Any]) -> None:
        try:
            self.publish(TOPIC_RESULT, result)
        except Exception as e:
            logger.warning("InfoAgent publish result failed: %s", e)

    def _publish_progress(self, task_id: str, *, state: str, ratio: float | None = None, note: str = "") -> None:
        payload = build_progress_payload(task_id, state=state, ratio=ratio, note=note)
        try:
            self.publish(TOPIC_INFO_PROGRESS, payload)
        except Exception as e:
            logger.debug("InfoAgent publish progress failed: %s", e)


if __name__ == "__main__":
    agent = InfoAgent(agent_config=get_agent_config())
    agent.start_process()
    from src.app_helper import wait_agent

    wait_agent(agent)
