"""
PlanCursor：plan 樹的執行狀態機。

不直接執行任何 IO，只負責：
- 把 plan 樹中的 atomic 節點包裝成 NodeRecord 並維護狀態
- 依 composite 的 execution_logic 計算 ready 集合
- 支援 mark_dispatched / mark_done / mark_failed / mark_cancelled / mark_obsolete
- 支援 replace_subtree：把指定 composite 的 sub_plans + execution_logic 換成新版本

設計準則：
- 所有讀取與寫入都對 cursor 內部結構，**不直接修改原 plan**（避免 callers 仍持有舊參考時混淆）；
  cursor 內部對 plan 做 deep copy 以隔離；公開 summary()/snapshot() 才把狀態回吐。
- 不引入任何 agentflow / kg / llm 相依，純資料邏輯，便於單元測試。
"""

from __future__ import annotations

import copy
import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from .events import NodeState, TERMINAL_STATES


def _is_atomic_node(node: dict[str, Any]) -> bool:
    if not isinstance(node, dict):
        return False
    return node.get("type") == "atomic" or node.get("is_atomic") is True


class DuplicateNodeIdError(ValueError):
    """plan 樹中出現重複的節點 id。

    cursor 以 id 索引節點；id 重複會讓後出現的節點被略過（不執行卻不報錯），
    因此一律拒絕。
    """


def _iter_node_ids(node: Any) -> Iterable[str]:
    """依 cursor 的走訪規則列出樹中所有節點 id（不進入 atomic 的 sub_plans）。

    沒有 id 的節點會由 _ensure_id 補上唯一 id，這裡略過。
    """
    if not isinstance(node, dict):
        return
    nid = node.get("id")
    if nid:
        yield str(nid)
    if _is_atomic_node(node):
        return
    for child in node.get("sub_plans") or []:
        yield from _iter_node_ids(child)


def find_duplicate_node_ids(plan: dict[str, Any] | None) -> list[str]:
    """回傳 plan 樹中重複的節點 id（composite 與 atomic 共用同一個命名空間）。"""
    seen: set[str] = set()
    dups: list[str] = []
    for nid in _iter_node_ids(plan):
        if nid in seen:
            if nid not in dups:
                dups.append(nid)
        else:
            seen.add(nid)
    return dups


def _positive_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


@dataclass(slots=True)
class NodeRecord:
    """單一 atomic 節點的執行記錄。"""

    node_id: str
    node: dict[str, Any]
    parent_id: str | None = None
    state: NodeState = NodeState.PENDING
    task_id: str | None = None
    attempts: int = 0
    last_result: dict[str, Any] | None = None
    error: str | None = None
    dispatched_at: float | None = None
    finished_at: float | None = None

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def is_successful_terminal(self) -> bool:
        return self.state == NodeState.DONE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "parent_id": self.parent_id,
            "intent": self.node.get("intent", ""),
            "task": self.node.get("task"),
            "topic": self.node.get("topic"),
            "state": self.state.value,
            "task_id": self.task_id,
            "attempts": self.attempts,
            "error": self.error,
            "last_result": self.last_result,
            "dispatched_at": self.dispatched_at,
            "finished_at": self.finished_at,
        }


class PlanCursor:
    """plan 執行狀態機。

    使用方式：
        cursor = PlanCursor(plan)
        while not cursor.done():
            for rec in cursor.next_ready_atomics():
                task_id = cursor.mark_dispatched(rec.node_id)
                ... dispatch ...
                cursor.mark_done(rec.node_id, result_dict)  # or mark_failed
        summary = cursor.summary()
    """

    # 對外的 NodeState alias，方便 tests / callers 不需另 import
    State = NodeState

    def __init__(self, plan: dict[str, Any]):
        self._plan = copy.deepcopy(plan) if plan else {"id": "root", "sub_plans": [], "execution_logic": []}
        dups = find_duplicate_node_ids(self._plan)
        if dups:
            raise DuplicateNodeIdError(f"Duplicate node ids in plan: {dups}")
        self._records: dict[str, NodeRecord] = {}
        self._composite_parent: dict[str, str | None] = {}   # composite_id -> parent_id
        self._task_index: dict[str, str] = {}                # task_id -> node_id
        self._replan_log: list[dict[str, Any]] = []
        self._task_id_counter = itertools.count(1)
        self._index_tree(self._plan, parent_id=None)

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    def _index_tree(self, node: dict[str, Any], *, parent_id: str | None) -> None:
        if not isinstance(node, dict):
            return
        node_id = self._ensure_id(node)
        if _is_atomic_node(node):
            if node_id not in self._records:
                self._records[node_id] = NodeRecord(
                    node_id=node_id, node=node, parent_id=parent_id
                )
            return
        # composite
        self._composite_parent[node_id] = parent_id
        for child in node.get("sub_plans") or []:
            if isinstance(child, dict):
                self._index_tree(child, parent_id=node_id)

    def _ensure_id(self, node: dict[str, Any]) -> str:
        nid = node.get("id")
        if not nid:
            nid = f"node-{uuid.uuid4().hex[:8]}"
            node["id"] = nid
        return str(nid)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------
    def plan(self) -> dict[str, Any]:
        """回傳 cursor 內部的 plan 副本（已含 IA 動過的狀態）。"""
        return self._plan

    def root(self) -> dict[str, Any]:
        return self._plan

    def all_records(self) -> list[NodeRecord]:
        return list(self._records.values())

    def record(self, node_id: str) -> NodeRecord | None:
        return self._records.get(node_id)

    def find_by_task_id(self, task_id: str) -> NodeRecord | None:
        nid = self._task_index.get(task_id)
        return self._records.get(nid) if nid else None

    def composite_node(self, composite_id: str) -> dict[str, Any] | None:
        return self._find_node_in_tree(self._plan, composite_id)

    def _find_node_in_tree(self, node: Any, target_id: str) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        if node.get("id") == target_id:
            return node
        for child in node.get("sub_plans") or []:
            r = self._find_node_in_tree(child, target_id)
            if r is not None:
                return r
        return None

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------
    @staticmethod
    def compute_execution_levels(
        sub_plans: list[dict], execution_logic: list[dict]
    ) -> list[list[dict]]:
        """共用版本：與 IntentionalAgent._compute_execution_levels 同義。

        放在這裡是為了讓 cursor 與 IA 解耦；IA 可以委派給 cursor。
        """
        id_to_node = {str(n.get("id", "")): n for n in (sub_plans or []) if isinstance(n, dict)}
        if not id_to_node:
            return [list(sub_plans or [])] if sub_plans else []

        deps: dict[str, list[str]] = {to_id: [] for to_id in id_to_node}
        for rel in execution_logic or []:
            if (rel.get("type") or "").strip().lower() == "sequence":
                from_id = str(rel.get("from_id", ""))
                to_id = str(rel.get("to_id", ""))
                if from_id in id_to_node and to_id in id_to_node and from_id != to_id:
                    deps[to_id].append(from_id)

        levels: list[list[dict]] = []
        remaining = set(id_to_node.keys())
        while remaining:
            ready = [nid for nid in remaining if all(d not in remaining for d in deps[nid])]
            if not ready:
                break
            level = [id_to_node[nid] for nid in sorted(ready)]
            levels.append(level)
            for nid in ready:
                remaining.discard(nid)

        seen = {n.get("id") for level in levels for n in level}
        orphans = [n for n in (sub_plans or []) if isinstance(n, dict) and n.get("id") not in seen]
        if orphans:
            if levels:
                levels.insert(0, orphans)
            else:
                levels.append(orphans)
        return levels if levels else []

    def next_ready_atomics(self) -> list[NodeRecord]:
        """回傳目前可以立即派工的 atomic NodeRecord 清單。

        規則：
        - 從 root 往下走 composite；每個 composite 依其 execution_logic 排層
        - 同一 composite 內，前面的 levels 若有未完成 atomic（含 IN_FLIGHT），則該 composite
          當前層為「尚未完成」的層
        - 對「當前層」中：
            - atomic 且 PENDING → 列為 ready
            - atomic 且 IN_FLIGHT → 不列入（但占用 level）
            - composite → 遞迴下探
        """
        ready: list[NodeRecord] = []
        self._collect_ready(self._plan, ready)
        return ready

    def _collect_ready(self, node: dict[str, Any], ready: list[NodeRecord]) -> None:
        if not isinstance(node, dict):
            return
        if _is_atomic_node(node):
            # 單獨 atomic 當 root 的情況（沒進到 composite 層）
            rec = self._records.get(str(node.get("id", "")))
            if rec is not None and rec.state == NodeState.PENDING:
                ready.append(rec)
            return

        sub_plans = node.get("sub_plans") or []
        execution_logic = node.get("execution_logic") or []
        levels = self.compute_execution_levels(sub_plans, execution_logic)

        for level in levels:
            # 該層是否仍有未完成的節點
            level_has_unfinished = False
            for child in level:
                if not isinstance(child, dict):
                    continue
                child_id = str(child.get("id", ""))
                if _is_atomic_node(child):
                    rec = self._records.get(child_id)
                    if rec is None:
                        continue
                    if rec.state == NodeState.PENDING:
                        ready.append(rec)
                        level_has_unfinished = True
                    elif rec.state == NodeState.IN_FLIGHT:
                        level_has_unfinished = True
                    elif rec.state in TERMINAL_STATES:
                        # 完成（含 OBSOLETE/SKIPPED）不阻塞層級
                        pass
                else:
                    # composite child：遞迴看內部
                    prev_len = len(ready)
                    self._collect_ready(child, ready)
                    composite_unfinished = self._composite_has_unfinished(child)
                    if composite_unfinished:
                        level_has_unfinished = True
                    if len(ready) > prev_len:
                        # 有新 ready 加入
                        pass
            if level_has_unfinished:
                # 該 composite 的下一層尚未到，停在這裡
                return

    def _composite_has_unfinished(self, node: dict[str, Any]) -> bool:
        if not isinstance(node, dict):
            return False
        if _is_atomic_node(node):
            rec = self._records.get(str(node.get("id", "")))
            if rec is None:
                return False
            return rec.state not in TERMINAL_STATES
        for child in node.get("sub_plans") or []:
            if self._composite_has_unfinished(child):
                return True
        return False

    def done(self) -> bool:
        """全部 atomic 都終態？（DONE / FAILED / CANCELLED / OBSOLETE / SKIPPED）"""
        return all(r.is_terminal() for r in self._records.values())

    def overdue_in_flight(
        self, *, default_timeout_sec: float | None, now: float | None = None
    ) -> list[NodeRecord]:
        """回傳派工後超過時限仍未回報結果的 IN_FLIGHT 節點。

        時限優先取節點自身的 deadline_sec，否則用 default_timeout_sec；兩者皆無則不逾時。
        """
        now = time.time() if now is None else now
        overdue: list[NodeRecord] = []
        for rec in self._records.values():
            if rec.state != NodeState.IN_FLIGHT or rec.dispatched_at is None:
                continue
            limit = _positive_float(rec.node.get("deadline_sec"))
            if limit is None:
                limit = _positive_float(default_timeout_sec)
            if limit is not None and now - rec.dispatched_at >= limit:
                overdue.append(rec)
        return overdue

    def all_successful(self) -> bool:
        terminals = [r for r in self._records.values() if r.is_terminal()]
        if not terminals:
            return False
        return all(r.state == NodeState.DONE for r in terminals if r.state != NodeState.OBSOLETE and r.state != NodeState.SKIPPED) and any(r.state == NodeState.DONE for r in terminals)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def mark_dispatched(self, node_id: str, *, task_id: str | None = None) -> str:
        rec = self._records[node_id]
        if rec.state not in (NodeState.PENDING, NodeState.FAILED, NodeState.CANCELLED):
            raise ValueError(f"Cannot dispatch node {node_id} in state {rec.state}")
        if rec.task_id and rec.task_id in self._task_index:
            self._task_index.pop(rec.task_id, None)
        tid = task_id or self._new_task_id(node_id)
        rec.task_id = tid
        rec.state = NodeState.IN_FLIGHT
        rec.attempts += 1
        rec.dispatched_at = time.time()
        rec.error = None
        self._task_index[tid] = node_id
        return tid

    def _new_task_id(self, node_id: str) -> str:
        n = next(self._task_id_counter)
        return f"{node_id}-t{n}-{uuid.uuid4().hex[:6]}"

    def _accept_result(self, rec: NodeRecord, new_state: NodeState, *, result: dict[str, Any] | None, error: str | None) -> None:
        rec.state = new_state
        rec.last_result = result
        rec.error = error
        rec.finished_at = time.time()

    def mark_done(self, node_id: str, result: dict[str, Any] | None) -> None:
        rec = self._records[node_id]
        self._accept_result(rec, NodeState.DONE, result=result, error=None)

    def mark_failed(self, node_id: str, error: str, *, result: dict[str, Any] | None = None) -> None:
        rec = self._records[node_id]
        self._accept_result(rec, NodeState.FAILED, result=result, error=error)

    def mark_cancelled(self, node_id: str, *, result: dict[str, Any] | None = None) -> None:
        rec = self._records[node_id]
        self._accept_result(rec, NodeState.CANCELLED, result=result, error=None)

    def mark_obsolete(self, node_id: str, *, reason: str = "") -> None:
        rec = self._records.get(node_id)
        if rec is None:
            return
        if rec.is_terminal() and rec.state != NodeState.IN_FLIGHT:
            return  # already terminal, don't overwrite
        rec.state = NodeState.OBSOLETE
        rec.error = reason or rec.error
        rec.finished_at = time.time()

    def mark_skipped(self, node_id: str, *, reason: str = "") -> None:
        rec = self._records.get(node_id)
        if rec is None:
            return
        if rec.is_terminal():
            return
        rec.state = NodeState.SKIPPED
        rec.error = reason or rec.error
        rec.finished_at = time.time()

    # ------------------------------------------------------------------
    # Result routing
    # ------------------------------------------------------------------
    def accept_action_result(self, task_id: str, payload: dict[str, Any]) -> NodeRecord | None:
        """依 task_id 把 executor 的回傳結果掛到對應 NodeRecord。

        若 node 已是 terminal（含 OBSOLETE）：仍接收結果（更新 last_result 供 debug），
        但不改變 state。
        若 task_id 不是該節點目前這次派工（retry 前的舊派工遲到的結果）：忽略並回傳 None，
        避免舊結果覆寫新一次派工的狀態。
        """
        nid = self._task_index.get(task_id)
        if not nid:
            return None
        rec = self._records.get(nid)
        if rec is None:
            return None
        if rec.task_id != task_id:
            return None
        ok = bool(payload.get("ok", False)) if isinstance(payload, dict) else False
        cancelled = bool(payload.get("cancelled", False)) if isinstance(payload, dict) else False
        err = payload.get("error") if isinstance(payload, dict) else None
        rec.last_result = payload if isinstance(payload, dict) else {"raw": payload}

        if rec.state in (NodeState.OBSOLETE, NodeState.SKIPPED):
            # 已被淘汰：不改 state，只保留結果供觀察
            return rec
        if cancelled:
            rec.state = NodeState.CANCELLED
        elif ok:
            rec.state = NodeState.DONE
        else:
            rec.state = NodeState.FAILED
            if err and not rec.error:
                rec.error = str(err)
        rec.finished_at = time.time()
        return rec

    # ------------------------------------------------------------------
    # Replan operations
    # ------------------------------------------------------------------
    def reset_for_retry(self, node_id: str, *, new_params: dict[str, Any] | None = None) -> None:
        """把已 FAILED 的節點轉回 PENDING，可選擇覆蓋 params。"""
        rec = self._records[node_id]
        if not (rec.state in (NodeState.FAILED, NodeState.CANCELLED)):
            raise ValueError(f"Cannot retry node {node_id} in state {rec.state}")
        rec.state = NodeState.PENDING
        rec.task_id = None
        rec.last_result = None
        rec.error = None
        rec.finished_at = None
        rec.dispatched_at = None
        if new_params:
            existing = dict(rec.node.get("params") or {})
            existing.update(new_params)
            rec.node["params"] = existing

    def update_pending_params(self, node_id: str, new_params: dict[str, Any]) -> bool:
        """覆蓋尚未派工（PENDING）節點的 params；節點不存在或不是 PENDING 時回傳 False。"""
        rec = self._records.get(node_id)
        if rec is None or rec.state != NodeState.PENDING:
            return False
        existing = dict(rec.node.get("params") or {})
        existing.update(new_params or {})
        rec.node["params"] = existing
        return True

    def cancel_in_flight(self, node_id: str, *, reason: str = "") -> None:
        """把 IN_FLIGHT 節點先標為 OBSOLETE（IA 之後會送 *.cancel 給 executor）。"""
        rec = self._records.get(node_id)
        if rec is None:
            return
        if rec.state == NodeState.IN_FLIGHT:
            rec.state = NodeState.OBSOLETE
            rec.error = reason or rec.error
            rec.finished_at = time.time()

    def replace_subtree(self, composite_id: str, new_subtree: dict[str, Any]) -> bool:
        """把指定 composite 的內容替換成新生成的子樹。

        - 原 composite 內所有 atomic：
            - IN_FLIGHT → mark_obsolete（IA 視情況送 cancel）
            - PENDING → mark_skipped
            - 其餘 terminal → 保留原狀
        - 新 subtree 中的 atomic 進入 cursor 索引，初始 PENDING
        - 回傳 True 表示成功；新子樹的 id 自身重複或與既有節點衝突時回傳 False，cursor 不做任何改動
        """
        composite = self._find_node_in_tree(self._plan, composite_id)
        if composite is None or _is_atomic_node(composite):
            return False
        new_sub_plans = list(new_subtree.get("sub_plans") or [])
        new_ids = [nid for child in new_sub_plans for nid in _iter_node_ids(child)]
        # 被替換掉的舊 composite 後代 id 可重用；舊 atomic 仍留在 _records 中，不可重用
        replaced_ids = set(_iter_node_ids(composite)) - {composite_id}
        taken = (set(_iter_node_ids(self._plan)) - replaced_ids) | set(self._records)
        if len(set(new_ids)) != len(new_ids) or any(nid in taken for nid in new_ids):
            return False
        # 1) 把舊子樹的 atomic 標記為「已被取代」
        #    任何非 DONE 的舊節點都應視為被替換掉，不影響最終 ok 判定
        old_ids = self._collect_atomic_ids(composite)
        for nid in old_ids:
            rec = self._records.get(nid)
            if rec is None:
                continue
            if rec.state == NodeState.DONE:
                continue
            if rec.state == NodeState.IN_FLIGHT:
                self.mark_obsolete(nid, reason=f"replaced by subtree of {composite_id}")
            elif rec.state == NodeState.PENDING:
                self.mark_skipped(nid, reason=f"replaced by subtree of {composite_id}")
            elif rec.state in (NodeState.FAILED, NodeState.CANCELLED):
                # 已失敗/被取消的舊節點已由新 subtree 取代，標記為 OBSOLETE
                rec.state = NodeState.OBSOLETE
                rec.error = (rec.error + " | replaced") if rec.error else "replaced"
        # 2) 替換 composite 內容
        new_exec_logic = list(new_subtree.get("execution_logic") or [])
        composite["sub_plans"] = new_sub_plans
        composite["execution_logic"] = new_exec_logic
        # 3) 索引新子樹
        for child in new_sub_plans:
            self._index_tree(child, parent_id=composite_id)
        self._replan_log.append({
            "kind": "replace_subtree",
            "composite_id": composite_id,
            "old_atomic_ids": list(old_ids),
            "new_atomic_ids": list(self._collect_atomic_ids(composite)) ,
            "at": time.time(),
        })
        return True

    def replace_root(self, new_plan: dict[str, Any]) -> None:
        """整個 plan 換掉。所有非 DONE 的舊節點皆視為被取代。

        新 plan 的 id 自身重複，或與保留中的舊 atomic 記錄衝突時，
        拋出 DuplicateNodeIdError，cursor 不做任何改動。
        """
        new_plan_copy = copy.deepcopy(new_plan)
        dups = find_duplicate_node_ids(new_plan_copy)
        clashes = [nid for nid in _iter_node_ids(new_plan_copy) if nid in self._records]
        if dups or clashes:
            raise DuplicateNodeIdError(
                f"New root plan has duplicate ids {dups} or ids clashing with existing records {clashes}"
            )
        old_ids = list(self._records.keys())
        for nid in old_ids:
            rec = self._records[nid]
            if rec.state == NodeState.DONE:
                continue
            if rec.state == NodeState.IN_FLIGHT:
                self.mark_obsolete(nid, reason="replaced by new root")
            elif rec.state == NodeState.PENDING:
                self.mark_skipped(nid, reason="replaced by new root")
            elif rec.state in (NodeState.FAILED, NodeState.CANCELLED):
                rec.state = NodeState.OBSOLETE
                rec.error = (rec.error + " | replaced") if rec.error else "replaced"
        # 保留舊 records（仍可由 task_id 索引），但建立全新一棵
        self._plan = new_plan_copy
        self._composite_parent.clear()
        self._index_tree(new_plan_copy, parent_id=None)
        self._replan_log.append({
            "kind": "replace_root",
            "old_atomic_ids": old_ids,
            "at": time.time(),
        })

    def _collect_atomic_ids(self, node: dict[str, Any]) -> list[str]:
        out: list[str] = []
        if not isinstance(node, dict):
            return out
        if _is_atomic_node(node):
            nid = node.get("id")
            if nid:
                out.append(str(nid))
            return out
        for child in node.get("sub_plans") or []:
            out.extend(self._collect_atomic_ids(child))
        return out

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """執行完成後產出 IA 對外回傳的 summary。"""
        records = sorted(self._records.values(), key=lambda r: (r.dispatched_at or 0))
        terminal_counts: dict[str, int] = {}
        for r in records:
            terminal_counts[r.state.value] = terminal_counts.get(r.state.value, 0) + 1
        # 「成功」定義：所有非 OBSOLETE/SKIPPED 的節點都是 DONE，且至少有一個 DONE
        non_dropped = [r for r in records if r.state not in (NodeState.OBSOLETE, NodeState.SKIPPED)]
        all_ok = bool(non_dropped) and all(r.state == NodeState.DONE for r in non_dropped)
        return {
            "ok": all_ok,
            "plan": self._plan,
            "results": [self._record_result(r) for r in records],
            "state_counts": terminal_counts,
            "replan_log": list(self._replan_log),
        }

    @staticmethod
    def _record_result(rec: NodeRecord) -> dict[str, Any]:
        return {
            "id": rec.node_id,
            "intent": rec.node.get("intent", ""),
            "task": rec.node.get("task"),
            "topic": rec.node.get("topic"),
            "state": rec.state.value,
            "task_id": rec.task_id,
            "attempts": rec.attempts,
            "ok": rec.state == NodeState.DONE,
            "result": rec.last_result,
            "error": rec.error,
        }
