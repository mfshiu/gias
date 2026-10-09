"""
NavigationAgent：實際帶領/導航類 action（LocateExhibit, SuggestRoute,
ExplainDirections, NavigationAssistance）。

訂閱：
- navigation.request   請求（既有；同時相容 publish_sync 與 task_id 路徑）
- navigation.cancel    取消（新增）

發佈：
- navigation.result    任務完成（新增；僅在請求帶 task_id 時發佈）
- navigation.progress  進度（新增；advisory）

執行：python -m src.agents.navigation_agent
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
    TOPIC_NAVIGATION_CANCEL,
    TOPIC_NAVIGATION_PROGRESS,
    TOPIC_NAVIGATION_REQUEST,
    TOPIC_NAVIGATION_RESULT,
    UNKNOWN_TASK,
    build_progress_payload,
    build_result,
    is_missing_task,
    parse_action_payload,
    parse_cancel_payload,
)
from src.blackboard.client import get_crowd_hotspots

logger = init_logging()

TOPIC = TOPIC_NAVIGATION_REQUEST
TOPIC_CANCEL = TOPIC_NAVIGATION_CANCEL
TOPIC_RESULT = TOPIC_NAVIGATION_RESULT


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
            print(f"  [NavigationAgent] (cancelled at step {i-1}/{len(steps)})", flush=True)
            logger.info("NavigationAgent progress cancelled: reason=%s", cancel_token.reason)
            return False
        print(f"  [NavigationAgent] {i}/{len(steps)} {msg}", flush=True)
        logger.info("NavigationAgent progress: %s", msg)
        slept = 0.0
        slice_dt = 0.05
        while slept < delay:
            if cancel_token is not None and cancel_token.cancelled:
                return False
            time.sleep(min(slice_dt, delay - slept))
            slept += slice_dt
    return True


def _crowded_zones_from_hotspots(hotspots: list) -> list[str]:
    return [
        h.get("zone")
        for h in (hotspots or [])
        if h.get("zone") and h.get("crowd_status") == "Crowded"
    ]


def _sleep_cancellable(seconds: float, *, cancel_token: CancelToken | None) -> bool:
    """可被取消的 sleep；返回 True 表示睡滿，False 表示被取消。"""
    slept = 0.0
    slice_dt = 0.1
    while slept < seconds:
        if cancel_token is not None and cancel_token.cancelled:
            return False
        time.sleep(min(slice_dt, seconds - slept))
        slept += slice_dt
    return True


def _execute_with_replan(
    agent: "NavigationAgent",
    task: str,
    params: dict[str, Any],
    *,
    en_route_sec: float = 5.0,
    step_delay: float = 0.8,
    cancel_token: CancelToken | None = None,
) -> tuple[str, bool]:
    """導航執行：過程中多次查詢黑板；可被 cancel_token 中斷。"""
    loc = params.get("current_location", "?")
    target = params.get("target_name") or params.get("destination", "?")
    dest_label = params.get("destination") or target

    # 階段 1：初步規劃
    ok = _simulate_progress(
        [
            f"定位起點：{loc}...",
            f"搜尋目標「{target}」...",
            "【第一次查詢黑板】取得即時人潮...",
        ],
        delay=step_delay,
        cancel_token=cancel_token,
    )
    if not ok:
        return ("導航已取消（初步規劃階段）。", True)
    hotspots_1 = get_crowd_hotspots(agent)
    crowded_1 = sorted(_crowded_zones_from_hotspots(hotspots_1))
    if crowded_1:
        print(
            f"  [NavigationAgent] >>> 初步規劃：避開擁擠區 {', '.join(crowded_1)}",
            flush=True,
        )
        if not _sleep_cancellable(step_delay, cancel_token=cancel_token):
            return ("導航已取消。", True)
    else:
        print("  [NavigationAgent] >>> 初步規劃：各區人潮正常，採最短路徑", flush=True)
        if not _sleep_cancellable(step_delay, cancel_token=cancel_token):
            return ("導航已取消。", True)

    # 階段 2：模擬移動中
    print(
        f"  [NavigationAgent] >>> 導航進行中...（等待 {en_route_sec:.0f} 秒）",
        flush=True,
    )
    if not _sleep_cancellable(en_route_sec, cancel_token=cancel_token):
        return ("導航已取消（行進中）。", True)

    # 階段 3：再次查詢
    ok = _simulate_progress(
        ["【第二次查詢黑板】檢查人潮是否有變..."],
        delay=step_delay,
        cancel_token=cancel_token,
    )
    if not ok:
        return ("導航已取消。", True)
    hotspots_2 = get_crowd_hotspots(agent)
    crowded_2 = sorted(_crowded_zones_from_hotspots(hotspots_2))

    msg = f"已規劃從 {loc} 前往 {dest_label} 的路線，請跟我來。"
    if crowded_1 != crowded_2:
        print("  [NavigationAgent] >>> 【路線變更】人潮已有變化！", flush=True)
        if crowded_1 and not crowded_2:
            msg += f" 【路線變更】原擁擠區 {', '.join(crowded_1)} 現已緩解，已改採最短路徑。"
        elif not crowded_1 and crowded_2:
            msg += f" 【路線變更】{', '.join(crowded_2)} 現擁擠，已重新規劃繞行。"
        else:
            msg += (
                f" 【路線變更】人潮變化：{', '.join(crowded_1)} → "
                f"{', '.join(crowded_2)}，已重新規劃路線。"
            )
        if not _sleep_cancellable(step_delay, cancel_token=cancel_token):
            return (msg, True)
    elif crowded_2:
        msg += f" 【黑板觀察】途經區域 {', '.join(crowded_2)} 擁擠，已繞行。"
    return (msg, False)


def _execute(
    agent: "NavigationAgent",
    task: str,
    params: dict[str, Any],
    *,
    cancel_token: CancelToken | None = None,
) -> tuple[str, bool]:
    """執行導航類 task；回傳 (message, cancelled)。

    NOTE: 為避免單元測試耗時過長，預設縮短 en_route_sec。
    """
    if task == "SuggestRoute":
        loc = params.get("current_location", "?")
        ok = _simulate_progress(
            [
                f"取得您的位置：{loc}...",
                "【第一次查詢黑板】取得即時人潮...",
            ],
            delay=0.8,
            cancel_token=cancel_token,
        )
        if not ok:
            return ("SuggestRoute 已取消。", True)
        hotspots_1 = get_crowd_hotspots(agent)
        crowded_1 = sorted(_crowded_zones_from_hotspots(hotspots_1))
        if crowded_1:
            print(f"  [NavigationAgent] >>> 初步規劃：避開 {', '.join(crowded_1)}", flush=True)
        else:
            print("  [NavigationAgent] >>> 初步規劃：各區人潮正常", flush=True)
        if not _sleep_cancellable(0.8, cancel_token=cancel_token):
            return ("SuggestRoute 已取消。", True)
        print("  [NavigationAgent] >>> 模擬規劃中...（5 秒）", flush=True)
        if not _sleep_cancellable(5.0, cancel_token=cancel_token):
            return ("SuggestRoute 已取消（規劃中）。", True)
        ok = _simulate_progress(
            ["【第二次查詢黑板】確認人潮是否有變..."],
            delay=0.8,
            cancel_token=cancel_token,
        )
        if not ok:
            return ("SuggestRoute 已取消。", True)
        hotspots_2 = get_crowd_hotspots(agent)
        crowded_2 = sorted(_crowded_zones_from_hotspots(hotspots_2))
        msg = f"已根據您的位置 {loc} 規劃建議參觀路線。"
        if crowded_1 != crowded_2:
            print("  [NavigationAgent] >>> 【路線變更】人潮已有變化！", flush=True)
            if crowded_1 and not crowded_2:
                msg += f" 【路線變更】原擁擠區 {', '.join(crowded_1)} 現已緩解，已改採最短路徑。"
            elif not crowded_1 and crowded_2:
                msg += f" 【路線變更】{', '.join(crowded_2)} 現擁擠，已重新規劃繞行。"
            else:
                msg += f" 【路線變更】人潮變化：{', '.join(crowded_1)} → {', '.join(crowded_2)}，已重新規劃。"
        elif crowded_2:
            msg += f" 【黑板觀察】已避開擁擠區: {', '.join(crowded_2)}"
        return (msg, False)

    if task == "LocateExhibit":
        return _execute_with_replan(
            agent, task, params,
            en_route_sec=5.0,
            step_delay=0.8,
            cancel_token=cancel_token,
        )

    if task == "ExplainDirections":
        dest = params.get("destination", "?")
        loc = params.get("current_location", "?")
        ok = _simulate_progress(
            [
                f"從 {loc} 到 {dest}...",
                "向黑板查詢即時人潮...",
                "查詢展場平面圖...",
                "標示轉彎點與地標...",
            ],
            cancel_token=cancel_token,
        )
        if not ok:
            return ("ExplainDirections 已取消。", True)
        hotspots = get_crowd_hotspots(agent)
        crowded_zones = [
            h.get("zone")
            for h in (hotspots or [])
            if h.get("crowd_status") == "Crowded"
        ]
        msg = f"從 {loc} 前往 {dest}：直走約 50 公尺，左轉後第二個攤位即為目標。"
        if crowded_zones:
            msg += f" 【黑板觀察】注意：{', '.join(crowded_zones)} 目前擁擠，建議避開。"
        return (msg, False)

    if task == "NavigationAssistance":
        return _execute_with_replan(
            agent, task, params,
            en_route_sec=5.0,
            step_delay=0.8,
            cancel_token=cancel_token,
        )

    ok = _simulate_progress(["處理中...", "完成"], cancel_token=cancel_token)
    if not ok:
        return (f"{task} 已取消。", True)
    return (f"已執行導航任務 {task}。", False)


class NavigationAgent(Agent):
    """實際帶領/導航類 action。

    支援兩種呼叫模式（與 InfoAgent 相同），詳見 docs/monitoring_protocol.md。
    """

    def __init__(self, agent_config: dict[str, Any]):
        super().__init__("navigation_agent.gias", agent_config)
        self._tasks: dict[str, CancelToken] = {}
        self._idempotency_cache: dict[str, dict[str, Any]] = {}
        self._tasks_lock = threading.Lock()

    # ------------------------------------------------------------------
    # AgentFlow lifecycle
    # ------------------------------------------------------------------
    def on_connected(self) -> None:
        logger.info("NavigationAgent subscribing: %s, %s", TOPIC, TOPIC_CANCEL)
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
            f"\n[NavigationAgent] 收到請求: task={task} task_id={task_id} params={params}",
            flush=True,
        )
        logger.info("NavigationAgent received: task=%s task_id=%s", task, task_id)

        # 未綁定 task 的請求無法執行，必須回報失敗（不可落到預設分支回成功）
        if is_missing_task(task):
            logger.warning("NavigationAgent rejected request without task: task_id=%s", task_id)
            result = build_result(
                task=str(task or UNKNOWN_TASK),
                message="請求缺少 task，無法執行。",
                action_id=action_id,
                intent=intent,
                task_id=task_id,
                ok=False,
                error="missing task",
            )
            if task_id:
                self._publish_result(result)
            return result

        if idempotency_key and idempotency_key in self._idempotency_cache:
            cached = self._idempotency_cache[idempotency_key]
            logger.info("NavigationAgent idempotency hit: %s", idempotency_key)
            if task_id:
                self._publish_result(cached)
            return cached

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
            logger.exception("NavigationAgent execute failed: %s", e)
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
            print(f"  [NavigationAgent] ✓ 完成: {message}", flush=True)
        logger.info(
            "NavigationAgent result: task=%s ok=%s cancelled=%s elapsed=%.3fs",
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
            logger.info("NavigationAgent cancel: task_id=%s not in flight", task_id)
            return {"ok": False, "error": "task not in flight"}
        tok.cancel(reason)
        logger.info("NavigationAgent cancel signalled: task_id=%s reason=%s", task_id, reason)
        return {"ok": True, "task_id": task_id}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _publish_result(self, result: dict[str, Any]) -> None:
        try:
            self.publish(TOPIC_RESULT, result)
        except Exception as e:
            logger.warning("NavigationAgent publish result failed: %s", e)

    def _publish_progress(
        self, task_id: str, *, state: str, ratio: float | None = None, note: str = ""
    ) -> None:
        payload = build_progress_payload(task_id, state=state, ratio=ratio, note=note)
        try:
            self.publish(TOPIC_NAVIGATION_PROGRESS, payload)
        except Exception as e:
            logger.debug("NavigationAgent publish progress failed: %s", e)


if __name__ == "__main__":
    agent = NavigationAgent(agent_config=get_agent_config())
    agent.start_process()
    from src.app_helper import wait_agent

    wait_agent(agent)
