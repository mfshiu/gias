# src/core/intentional_agent.py
# IntentionalAgent: 規劃並執行使用者意圖。
#
# 重構摘要（v2）：
# - 保留既有 plan_intention / execute_plan / _compute_execution_levels 等 API，
#   舊測試與舊呼叫端不受影響。
# - 新增 execute_plan_with_monitoring(plan)：完整 PRA 監測迴圈，
#   使用 async dispatch（publish + subscribe info.result/navigation.result），
#   支援 cancel、retry、subtree replan、root replan、預算守門。
# - 訂閱黑板變更事件並送進 ExecutionMonitor，trigger 依規則決定再思考。
# - on_activate 預設走監測路徑；可透過 config 關閉退回舊路徑。
#
# 意圖管理（A-01）：
# - 意圖是有生命週期的 Intention（src/core/intention），由 IntentionManager 依優先序排程，
#   高優先序可搶占（被搶占的意圖之後重新規劃續做），可取消，狀態寫入 Blackboard 並發佈到 intention.status。
# - 建構時給 intention：處理完這個意圖就結束（既有啟動腳本的行為）。
#   不給 intention：常駐服務，經 MQTT intention.submit / intention.cancel / intention.query 接收意圖。

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from agentflow.core.agent import Agent

from src.llm.client import LLMClient
from src.llm.tasks.intent_tasks import parse_intent
from src.llm.schemas.intent import IntentCandidate

from src.log_helper import init_logging
logger = init_logging()

from src.kg.action_store import ActionStore
from src.kg.adapter_neo4j import Neo4jBoltAdapter

from src.core.intent.domain_profile import DomainProfile
from src.core.intent.sub_intent import SubIntent
from src.core.intent.embedder import LLMEmbedder
from src.core.intent.action_matcher import ActionMatcher
from src.core.intent.action_selector import ActionSelector
from src.core.intent.prompt_builder import PromptBuilder
from src.core.intent.llm_decomposer import LLMDecomposer
from src.core.intent.planner import RecursivePlanner
from src.core.intent.scope_gate import ScopeGate
from src.core.intent import dataflow
from src.core.intent import preconditions as pre
from src.core.intention import (
    TOPIC_INTENTION_CANCEL,
    TOPIC_INTENTION_QUERY,
    TOPIC_INTENTION_STATUS,
    TOPIC_INTENTION_SUBMIT,
    BlackboardIntentionStore,
    Intention,
    IntentionManager,
)
from src.core.intention.response import ResponseComposer
from src.agents._executor_utils import (
    build_action_payload,
    build_cancel_payload,
    cancel_topic_for,
    channel_of_request_topic,
    is_missing_task,
    new_task_id,
    parse_action_payload,
    resolve_request_topic,
    result_topic_for,
)
from src.core.monitoring import (
    ActionResult,
    Budget,
    BudgetGuard,
    DuplicateNodeIdError,
    ExecutionMonitor,
    LLMReplanAdvisor,
    NodeState,
    PlanCursor,
    PlanRepair,
    ReplanDecision,
    ReplanTrigger,
    TriggerKind,
    find_dependency_cycles,
    find_duplicate_node_ids,
)
from src.core.monitoring.trigger import TriggerConfig
from src.core.monitoring.events import INPUT_UNAVAILABLE, PRECONDITION_FAILED
from src.blackboard.client import (
    subscribe_blackboard,
    subscriber_topic,
    try_query_blackboard,
    unsubscribe_blackboard,
    write_blackboard,
)


def _has_preconditions(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    return bool(node.get("preconditions")) or any(_has_preconditions(c) for c in node.get("sub_plans") or [])


# ----------------------------------------------------------------------
# IntentionalAgent
# ----------------------------------------------------------------------
class IntentionalAgent(Agent):
    def __init__(
        self,
        agent_config,
        intention: str | None = None,
        *,
        domain_profile: DomainProfile | None = None,
        exit_when_idle: bool | None = None,
    ):
        """intention：啟動時要處理的意圖；不給則作為常駐服務經 MQTT 接收意圖。
        exit_when_idle：沒有待處理意圖時是否結束；預設「有給 intention 才結束」（既有啟動腳本的行為）。
        """
        self.agent_config = agent_config
        self.intention = intention
        self.exit_when_idle = bool(intention) if exit_when_idle is None else bool(exit_when_idle)

        # 完全使用 gias.toml（由 get_agent_config() 讀入的 agent_config）
        self.llm = LLMClient.from_config(agent_config)

        self.domain = domain_profile or DomainProfile()

        self._kg = None
        # Action 的參數、輸出、前置條件等查詢結果的快取時效（秒）；重新 seed 後最晚這麼久會讀到新資料
        cache_ttl = float((agent_config.get("intent") or {}).get("action_cache_ttl_sec", 300))
        self.action_store = ActionStore(self.kg, cache_ttl_sec=cache_ttl)

        # composed modules
        self.embedder = LLMEmbedder(self.llm)
        matching = (agent_config.get("intent") or {}).get("matching") or {}
        self.matcher = ActionMatcher(
            action_store=self.action_store,
            embedder=self.embedder,
            domain=self.domain,
            logger=logger,
            min_score=float(matching.get("min_score", 0.75)),
            fallback_min_score=float(matching.get("fallback_min_score", 0.6)),
        )
        self.selector = ActionSelector(kg=self.kg, matcher=self.matcher, logger=logger, action_store=self.action_store)
        self.prompt_builder = PromptBuilder()
        self.decomposer = LLMDecomposer(llm=self.llm, prompt_builder=self.prompt_builder, logger=logger)
        self.planner = RecursivePlanner(
            decomposer=self.decomposer,
            logger=logger,
            kg=self.kg,
            action_store=self.action_store,
            enum_alias=self.domain.enum_alias,
            cache_ttl_sec=cache_ttl,
            # 彼此沒有先後的非原子子意圖平行拆解；同時最多這麼多個 LLM 呼叫
            max_parallel=int((agent_config.get("intent") or {}).get("planner_max_parallel", 4)),
        )
        self.scope_gate = ScopeGate(llm=self.llm, logger=logger)
        # 意圖結束時給使用者的回覆（[intent.response] mode：llm / template / off）
        response_cfg = (agent_config.get("intent") or {}).get("response") or {}
        self.responder = ResponseComposer(self.llm, mode=response_cfg.get("mode", "llm"), logger=logger)

        # ---- 監測子系統（v2）-----------------------------------------
        self._monitor: ExecutionMonitor | None = None
        # 已訂閱 <channel>.result / <channel>.progress 的執行端頻道
        self._result_channels: set[str] = set()
        self._bb_subs_active: bool = False
        self._monitor_lock_topics: set[str] = set()
        self._stop_event = threading.Event()

        # ---- 意圖管理（A-01）--------------------------------------------
        # 執行時派工用的 dispatcher（None 表示經 broker publish；測試可注入）
        self.execution_dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None = None
        self._alias_index: dict[str, str] | None = None
        self._bb_down_until = 0.0
        self._completed_steps: dict[str, list[dict[str, Any]]] = {}
        self.intentions = IntentionManager(
            run_intention=self._run_intention,
            on_status=self._publish_intention_status,
            preemption=bool(self._intention_config().get("preemption", True)),
            logger=logger,
        )

        super().__init__("intentional_agent.gias", agent_config)

    @property
    def kg(self):
        if self._kg is None:
            kg_cfg = self.agent_config.get("kg", {})
            if kg_cfg.get("type") != "neo4j":
                raise RuntimeError("KG type is not neo4j")

            base = kg_cfg.get("neo4j")
            actions_overrides = kg_cfg.get("neo4j_actions")
            if not isinstance(base, dict):
                raise RuntimeError("Missing [kg.neo4j] config in gias.toml")
            if not isinstance(actions_overrides, dict):
                raise RuntimeError("Missing [kg.neo4j_actions] config in gias.toml")

            merged = {**base, **actions_overrides}
            self._kg = Neo4jBoltAdapter.from_config(merged, logger=logger)
        return self._kg

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_activate(self):
        self._start_intention_service()
        if self.intention:
            self.intentions.submit(self.intention, requester="startup")
        if self.exit_when_idle:
            self.intentions.wait_idle()
            for item in self.intentions.list():
                logger.info("Intention %s finished: %s (%s)", item["intention_id"], item["status"], item["reason"])
                reply = (item.get("result") or {}).get("response")
                if reply:
                    print(f"\n[IntentionalAgent] {reply}\n", flush=True)
            self._terminate()

    def on_terminating(self):
        self.intentions.stop(reason="agent terminating")
        store = self.intentions.store
        if hasattr(store, "close"):
            store.close()
        super().on_terminating()

    # ------------------------------------------------------------------
    # 意圖管理（A-01）
    # ------------------------------------------------------------------
    def _intention_config(self) -> dict[str, Any]:
        cfg = (self.agent_config or {}).get("intent") or {}
        section = cfg.get("intentions") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}

    def _start_intention_service(self) -> None:
        """設定持久化、（常駐服務時）訂閱 MQTT 介面，並啟動排程。"""
        cfg = self._intention_config()
        if str(cfg.get("store", "blackboard")).lower() == "blackboard":
            self.intentions.store = BlackboardIntentionStore(
                self._write_blackboard, manager=str(cfg.get("manager_name", "intentional_agent")), logger=logger,
            )
            if not self.exit_when_idle:
                # 只有常駐服務會在重啟時接手：上次未完成的意圖標為中斷，不自動續做
                self.intentions.store.mark_unfinished_interrupted("unfinished when the service restarted")
        if not self.exit_when_idle:
            for topic, handler in (
                (TOPIC_INTENTION_SUBMIT, self._handle_intention_submit),
                (TOPIC_INTENTION_CANCEL, self._handle_intention_cancel),
                (TOPIC_INTENTION_QUERY, self._handle_intention_query),
            ):
                try:
                    self.subscribe(topic, "dict", handler)
                except Exception as e:
                    logger.warning("subscribe %s failed: %s", topic, e)
        self.intentions.start()

    def _run_intention(
        self,
        intention: Intention,
        stop_event: threading.Event,
        on_planned: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        """IntentionManager 的執行函式：規劃 → 承諾 → 以監測迴圈執行。"""
        context = self._resume_context(intention) if intention.attempts > 1 else None
        plan = self.plan_intention(intention.text, context=context)
        if plan.get("type") == "leaf_unresolved":
            summary = {"ok": False, "message": plan.get("reason")}
            summary["response"] = self.compose_response(intention.text, plan=plan)
            return {"plan": plan, "summary": summary}
        if stop_event.is_set():
            return {"plan": plan, "stopped": True}
        on_planned(plan)
        if not self._monitoring_enabled():
            summary = self.execute_plan(plan)
            summary["response"] = self.compose_response(intention.text, plan=plan, summary=summary)
            return {"plan": plan, "summary": summary}
        summary = self.execute_plan_with_monitoring(plan, dispatcher=self.execution_dispatcher, stop_event=stop_event)
        done = [
            {"id": r.get("id"), "task": r.get("task"), "intent": r.get("intent"), "state": "done",
             "message": (r.get("result") or {}).get("message") or "", "outputs": dataflow.step_outputs(r.get("result"))}
            for r in summary.get("results") or [] if r.get("state") == "done"
        ]
        self._completed_steps.setdefault(intention.intention_id, []).extend(done)
        if not summary.get("stopped"):
            summary["response"] = self.compose_response(intention.text, plan=plan, summary=summary)
        return {"plan": plan, "summary": summary, "stopped": bool(summary.get("stopped"))}

    def compose_response(
        self, intention: str, *, plan: dict[str, Any], summary: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """意圖結束時給使用者的回覆：{"text", "source"}（見 src/core/intention/response.py）。"""
        try:
            return self.responder.compose(intention, plan=plan, summary=summary)
        except Exception as e:
            logger.warning("Composing the reply failed: %s", e)
            return None

    def _resume_context(self, intention: Intention) -> dict[str, Any]:
        """被搶占後續做：告訴規劃者已完成的步驟（不要重做）與目前環境。"""
        context: dict[str, Any] = {"replan": {
            "kind": "resume",
            "reason": "resumed after being preempted by a higher-priority intention; "
                      "the affected steps below were already completed and must not be repeated",
            "affected_steps": list(self._completed_steps.get(intention.intention_id, [])),
        }}
        if self.domain.env_fact_queries:
            facts = self._collect_env_facts()
            if facts:
                context["env_facts"] = facts
        return context

    def _publish_intention_status(self, status: dict[str, Any]) -> None:
        try:
            self.publish(TOPIC_INTENTION_STATUS, status)
        except Exception as e:
            logger.debug("publish %s failed: %s", TOPIC_INTENTION_STATUS, e)

    def _write_blackboard(self, cypher: str, params: dict[str, Any]) -> bool:
        return write_blackboard(self, cypher, params, timeout=self._bb_timeout())

    def _handle_intention_submit(self, topic: str, pcl: Any) -> dict[str, Any]:
        data = parse_action_payload(pcl)
        try:
            intention = self.intentions.submit(
                str(data.get("text") or data.get("intention") or ""),
                priority=int(data.get("priority", 0) or 0),
                requester=str(data.get("requester") or ""),
                intention_id=data.get("intention_id"),
            )
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "intention_id": intention.intention_id, "status": intention.status.value}

    def _handle_intention_cancel(self, topic: str, pcl: Any) -> dict[str, Any]:
        data = parse_action_payload(pcl)
        intention_id = str(data.get("intention_id") or "")
        ok = self.intentions.cancel(intention_id, str(data.get("reason") or "cancelled by requester"))
        return {"ok": ok, "intention_id": intention_id}

    def _handle_intention_query(self, topic: str, pcl: Any) -> dict[str, Any]:
        data = parse_action_payload(pcl)
        intention_id = data.get("intention_id")
        if intention_id:
            found = self.intentions.get(str(intention_id))
            return {"ok": found is not None, "intention": found}
        return {"ok": True, "intentions": self.intentions.list(include_terminal=bool(data.get("include_terminal", True)))}

    def request_stop(self) -> None:
        """要求監測迴圈停止：取消所有執行中的動作後結束（可由其他 thread 呼叫）。"""
        self._stop_event.set()

    def _monitoring_enabled(self) -> bool:
        cfg = self.agent_config or {}
        m = cfg.get("intent", {}).get("monitoring") if isinstance(cfg.get("intent"), dict) else None
        if isinstance(m, dict):
            return bool(m.get("enabled", True))
        return True

    # ------------------------------------------------------------------
    # break_down_intention（未變動，保留原行為）
    # ------------------------------------------------------------------
    def break_down_intention(self, intention: str) -> list[SubIntent]:
        norm = self.domain.normalize(intention)
        logger.debug(f"Breaking down intention via LLM: {norm}")

        def _safe_str(x) -> str:
            return (x or "").strip()

        def _normalize_slots(slots: dict | None) -> dict:
            s = dict(slots or {})
            s.setdefault("_source_text", norm)
            s.setdefault("_normalized_text", norm)
            return s

        def _token_overlap_ratio(a: str, b: str) -> float:
            a = _safe_str(a)
            b = _safe_str(b)
            if not a or not b:
                return 0.0
            sa, sb = set(a), set(b)
            inter = len(sa & sb)
            denom = max(1, len(sa | sb))
            return inter / denom

        try:
            result, meta = parse_intent(llm=self.llm, user_text=norm)
            candidates: list[IntentCandidate] = result.candidates or []
            subs: list[SubIntent] = []

            for c in candidates:
                name = _safe_str(getattr(c, "name", ""))
                desc = _safe_str(getattr(c, "description", ""))
                slots = _normalize_slots(getattr(c, "slots", None) or {})

                canon = (desc or name or norm).strip()
                canon = self.domain.normalize(canon)

                slot_keys = [k for k in slots.keys() if not str(k).startswith("_")]
                overlap = _token_overlap_ratio(norm, canon)

                if (len(slot_keys) == 0) and (overlap < 0.25):
                    subs.append(
                        SubIntent(
                            intent=norm,
                            slots=slots,
                            raw={
                                "fallback_reason": "llm_over_abstract_without_slots",
                                "llm_name": name,
                                "llm_description": desc,
                                "meta": getattr(meta, "template_name", None),
                            },
                        )
                    )
                else:
                    subs.append(
                        SubIntent(
                            intent=canon,
                            slots=slots,
                            raw={
                                "name": name,
                                "description": desc,
                                "meta": getattr(meta, "template_name", None),
                            },
                        )
                    )

            return subs or [SubIntent(intent=norm, slots=_normalize_slots({}), raw={"fallback": True})]
        except Exception:
            logger.exception("Failed to break down intention via LLM, fallback to normalized intention.")
            return [SubIntent(intent=norm, slots={"_source_text": norm, "_normalized_text": norm}, raw={"fallback": True})]

    def match_actions(self, intention: str, **kwargs):
        return self.matcher.match_actions(intention, **kwargs)

    # ------------------------------------------------------------------
    # plan_intention
    # ------------------------------------------------------------------
    def plan_intention(self, intention: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """規劃意圖。context 為重規劃脈絡（見 PlanRepair.build_replan_context），只在重規劃時提供。

        意圖只拆解一次：意圖解析（break_down_intention）拆出的子意圖用來比對動作，
        也直接成為計畫的第一層（兩個以上時），planner 只為它們綁定動作、參數與先後。
        """
        norm = self.domain.normalize(intention)
        subs = self.break_down_intention(norm)

        matched_pairs, unmatched = self._match_subs(subs)
        if unmatched:
            return self._make_unresolved(
                intent=norm, subs=subs,
                reason="Some sub-intents have no matched actions.",
                unmatched=unmatched,
                matched=[s.intent for s, _ in matched_pairs],
            )

        chosen_actions = self.selector.select_actions(
            [s for s, _ in matched_pairs], matches=[ms for _, ms in matched_pairs],
        )
        allowed_action_names = self._extract_allowed_action_names(chosen_actions)
        if not allowed_action_names:
            return self._make_unresolved(
                intent=norm, subs=subs,
                reason="No allowed actions selected.",
                matched=[s.intent for s, _ in matched_pairs],
            )

        gate_reject = self._run_scope_gate(norm, subs, chosen_actions, allowed_action_names)
        if gate_reject is not None:
            return gate_reject

        given = self._given_sub_intents(matched_pairs, chosen_actions)
        plan = self._decompose(norm, chosen_actions, context, given)
        rejected, param_issues = self._validate_plan(plan, norm, subs, allowed_action_names)
        if rejected is not None:
            return rejected

        # LLM 提出計畫、KG 驗證：前置條件不成立時，帶著違反原因重新規劃一次
        findings = self._check_plan_preconditions(plan)
        if pre.blocking(findings):
            logger.warning("Plan violates preconditions; replanning once: %s",
                           [pre.describe(f) for f in pre.blocking(findings)])
            plan = self._decompose(norm, chosen_actions, self._precondition_context(findings, context), given)
            rejected, param_issues = self._validate_plan(plan, norm, subs, allowed_action_names)
            if rejected is not None:
                return rejected
            findings = self._check_plan_preconditions(plan)
            if pre.blocking(findings):
                return self._make_unresolved(
                    intent=norm, subs=subs,
                    reason="Plan violates action preconditions.",
                    matched=[s.intent for s in subs],
                    extra_debug={
                        "allowed_actions": sorted(allowed_action_names),
                        "precondition_findings": findings,
                    },
                )

        plan.setdefault("debug", {})
        plan["debug"]["sub_intentions"] = [s.intent for s in subs]
        plan["debug"]["allowed_actions"] = sorted(allowed_action_names)
        if param_issues:
            plan["debug"]["param_issues"] = param_issues
        if findings:
            plan["debug"]["precondition_findings"] = findings
        logger.debug(f"Generated plan: {json.dumps(plan, indent=2, ensure_ascii=False)}")
        return plan

    def _decompose(
        self, norm: str, chosen_actions: Any, context: dict[str, Any] | None,
        given: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if context:
            kwargs["context"] = context
        if given:
            kwargs["given_sub_intents"] = given
        return self.planner.plan(norm, chosen_actions, **kwargs)

    def _given_sub_intents(
        self, matched_pairs: list[tuple[SubIntent, list[Any]]], chosen_actions: Any
    ) -> list[dict[str, Any]] | None:
        """意圖解析拆出的子意圖（兩個以上時）作為計畫第一層，附上抽出的實體與各自比對到的動作。

        只有一個子意圖（或解析失敗而以整句代替）時不限制，由 planner 照常拆解。
        """
        if any((s.raw or {}).get("fallback") for s, _ in matched_pairs):
            return None
        given: list[dict[str, Any]] = []
        seen: set[str] = set()
        for s, ms in matched_pairs:
            if s.intent in seen:
                continue
            seen.add(s.intent)
            names = {getattr(getattr(m, "action", m), "name", None) for m in ms or []}
            signatures = [k for k in chosen_actions if self._action_name_from_sig(k) in names] \
                if isinstance(chosen_actions, dict) else []
            slots = {k: v for k, v in (s.slots or {}).items() if not str(k).startswith("_") and v not in (None, "")}
            given.append({"id": str(len(given) + 1), "intent": s.intent, "slots": slots, "actions": signatures})
        return given if len(given) >= 2 else None

    def _validate_plan(
        self, plan: dict[str, Any], norm: str, subs: list[SubIntent], allowed_action_names: set[str]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """結構與參數驗證；回傳 (被拒絕時的 leaf_unresolved 或 None, 參數問題)。"""

        def reject(reason: str, **debug: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            return self._make_unresolved(
                intent=norm, subs=subs, reason=reason, matched=[s.intent for s in subs],
                extra_debug={"allowed_actions": sorted(allowed_action_names), **debug},
            ), []

        illegal_atoms = self._find_illegal_atomic_actions(plan, allowed_action_names)
        if illegal_atoms:
            return reject("Planner produced actions outside allowed set.", illegal_atomic_nodes=illegal_atoms)
        unexecutable = self._find_unexecutable_nodes(plan)
        if unexecutable:
            return reject("Planner produced nodes that cannot be dispatched.", unexecutable_nodes=unexecutable)
        duplicate_ids = find_duplicate_node_ids(plan)
        if duplicate_ids:
            return reject("Planner produced duplicate node ids.", duplicate_node_ids=duplicate_ids)
        cycles = find_dependency_cycles(plan)
        if cycles:
            return reject("Planner produced cyclic Sequence dependencies.", dependency_cycles=cycles)
        # 步驟之間的資料傳遞：無效的 inputs 記為參數問題；來源與目標沒有先後時補上 Sequence
        added = dataflow.check_inputs(plan)
        if added:
            logger.info("Added Sequence relationships for data dependencies: %s", added)

        param_issues = self._collect_param_issues(plan)
        if param_issues:
            blocking = [i for i in param_issues if i["issue"] in self._BLOCKING_PARAM_ISSUES]
            if blocking and self._param_validation_mode() == "strict":
                return reject("Planner produced invalid action parameters.", param_issues=param_issues)
            logger.warning("Plan has parameter issues (intent.param_validation=warn): %s", param_issues)
        return None, param_issues

    # ------------------------------------------------------------------
    # 前置條件（A-02）
    # ------------------------------------------------------------------
    def _preconditions_config(self) -> dict[str, Any]:
        section = ((self.agent_config or {}).get("intent") or {}).get("preconditions")
        return section if isinstance(section, dict) else {}

    def _check_plan_preconditions(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        if not self._preconditions_config().get("enabled", True) or not _has_preconditions(plan):
            return []
        return pre.check_plan(plan, self._state_probe(), resolve=self._resolve_entity)

    def _precondition_context(self, findings: list[dict[str, Any]], context: dict[str, Any] | None) -> dict[str, Any]:
        """重新規劃用的脈絡：列出違反前置條件的步驟（保留原本脈絡中的步驟與環境事實）。"""
        ctx = dict(context or {})
        prior = dict(ctx.get("replan") or {})
        steps = [
            {"id": f["node_id"], "task": f["task"],
             "params": {f["arg"]: f["arg_value"]} if f.get("arg") else {},
             "state": "invalid", "error": f"{PRECONDITION_FAILED}: {pre.describe(f)}"}
            for f in pre.blocking(findings)
        ]
        reason = "the previous plan violates action preconditions; choose actions or targets whose preconditions hold"
        ctx["replan"] = {
            "kind": "precondition",
            "reason": f"{prior['reason']}; {reason}" if prior.get("reason") else reason,
            "affected_steps": list(prior.get("affected_steps") or []) + steps,
        }
        if "env_facts" not in ctx and self.domain.env_fact_queries:
            facts = self._collect_env_facts()
            if facts:
                ctx["env_facts"] = facts
        return ctx

    def _state_probe(self) -> pre.Probe:
        """依 DomainProfile.state_probes 經黑板代理查詢狀態；同一次檢查內快取，黑板無法查詢時暫停 30 秒。"""
        cache: dict[tuple[str, Any], tuple[bool, Any]] = {}
        probes = self.domain.state_probes or {}

        def probe(key: str, arg: Any) -> tuple[bool, Any]:
            ck = (key, str(arg))
            if ck in cache:
                return cache[ck]
            cypher = probes.get(key)
            if not cypher or time.monotonic() < self._bb_down_until:
                return False, None
            rows = try_query_blackboard(self, cypher, {"arg": arg}, timeout=self._bb_timeout())
            if rows is None:
                self._bb_down_until = time.monotonic() + 30.0
                return False, None
            cache[ck] = (True, rows[0].get("value") if rows else None)
            return cache[ck]

        return probe

    def _resolve_entity(self, value: Any) -> Any:
        """把參數值（可能是中文名稱或別名）對應到實體 id；對應不到就原樣回傳。"""
        if not isinstance(value, str):
            return value
        if self._alias_index is None:
            index: dict[str, str] = {}
            for entity, aliases in (self.domain.entity_aliases or {}).items():
                for name in [entity, *aliases]:
                    index.setdefault("".join(str(name).lower().split()), entity)
            self._alias_index = index
        return self._alias_index.get("".join(value.lower().split()), value)

    def _bind_inputs(self, rec, cursor: PlanCursor) -> str | None:
        """把前面步驟的輸出代入 params（A-03）；取不到時回傳錯誤訊息，不派工。"""
        node = rec.node
        if not node.get("inputs"):
            return None

        def lookup(source_id: str) -> tuple[str | None, dict[str, Any] | None]:
            source = cursor.record(source_id)
            return (source.state.value, source.last_result) if source is not None else (None, None)

        params, error = dataflow.resolve_inputs(node, lookup, enum_alias=self.domain.enum_alias)
        if error:
            return f"{INPUT_UNAVAILABLE}: {error}"
        cursor.update_pending_params(rec.node_id, params)
        return None

    def _dispatch_precondition_failure(self, rec, cursor: PlanCursor) -> str | None:
        """派工前再檢查一次前置條件（狀態可能在規劃後改變）；不成立時回傳錯誤訊息。"""
        node = rec.node
        cfg = self._preconditions_config()
        if not node.get("preconditions") or not cfg.get("enabled", True) or not cfg.get("check_before_dispatch", True):
            return None
        overlay: dict[tuple[str, str], Any] = {}
        done = sorted((r for r in cursor.all_records() if r.state == NodeState.DONE), key=lambda r: r.finished_at or 0)
        for r in done:   # 已完成步驟的效果視為目前狀態
            pre.apply_effects(r.node, overlay, resolve=self._resolve_entity)
        findings = pre.check_node(node, self._state_probe(), overlay=overlay, resolve=self._resolve_entity)
        warnings = [f for f in findings if f["status"] == "violated" and f["severity"] != "block"]
        if warnings:
            logger.info("Precondition warnings for node %s: %s", rec.node_id, [pre.describe(f) for f in warnings])
        failed = pre.blocking(findings)
        return f"{PRECONDITION_FAILED}: " + "; ".join(pre.describe(f) for f in failed) if failed else None

    # ------------------------------------------------------------------
    # plan_intention helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _action_name_from_sig(sig: str) -> str:
        s = (sig or "").strip()
        return s.split("(", 1)[0].strip() if "(" in s else s

    def _match_subs(
        self, subs: list[SubIntent]
    ) -> tuple[list[tuple[SubIntent, list[Any]]], list[str]]:
        matched_pairs: list[tuple[SubIntent, list[Any]]] = []
        unmatched: list[str] = []
        for s in subs:
            ms = self.match_actions(s.intent, slots=s.slots)
            if ms:
                matched_pairs.append((s, ms))
            else:
                unmatched.append(s.intent)
        return matched_pairs, unmatched

    def _extract_allowed_action_names(self, chosen_actions: Any) -> set[str]:
        if isinstance(chosen_actions, dict):
            return {self._action_name_from_sig(k) for k in chosen_actions if k}
        return {a.name for a in chosen_actions if getattr(a, "name", None)}

    def _to_basic_actions(self, chosen_actions: Any) -> list[dict[str, str]]:
        if isinstance(chosen_actions, dict):
            return [
                {"name": self._action_name_from_sig(k), "description": (v or "")}
                for k, v in chosen_actions.items()
            ]
        return [
            {"name": a.name, "description": getattr(a, "description", "") or ""}
            for a in chosen_actions
        ]

    def _scope_gate_enabled(self) -> bool:
        cfg = self.agent_config
        return bool(
            cfg.get("intent", {}).get("enable_scope_gate", False)
            or cfg.get("intentional_agent", {}).get("enable_scope_gate", False)
        )

    def _run_scope_gate(
        self,
        norm: str,
        subs: list[SubIntent],
        chosen_actions: Any,
        allowed_action_names: set[str],
    ) -> dict[str, Any] | None:
        if not self._scope_gate_enabled():
            return None

        try:
            decision = self.scope_gate.decide(
                user_intent=norm,
                available_actions=self._to_basic_actions(chosen_actions),
            )
        except Exception as e:
            logger.warning("Scope gate error: %s", e)
            if not bool(self.agent_config.get("intent", {}).get("scope_gate_strict", True)):
                return None
            return self._make_unresolved(
                intent=norm, subs=subs,
                reason=f"Scope gate failed: {e}",
                matched=[s.intent for s in subs],
                extra_debug={
                    "scope_gate": {"error": str(e)},
                    "allowed_actions": sorted(allowed_action_names),
                },
            )

        if getattr(decision, "can_execute", False):
            return None

        return self._make_unresolved(
            intent=norm, subs=subs,
            reason=getattr(decision, "reason", "") or "Scope gate rejected.",
            matched=[s.intent for s in subs],
            extra_debug={
                "scope_gate": {
                    "can_execute": False,
                    "reason": getattr(decision, "reason", ""),
                },
                "allowed_actions": sorted(allowed_action_names),
            },
        )

    def _find_illegal_atomic_actions(
        self, plan: dict[str, Any], allowed_action_names: set[str]
    ) -> list[dict[str, Any]]:
        if not isinstance(plan, dict):
            return []

        def _walk(node: dict[str, Any]) -> list[dict[str, Any]]:
            out = [node]
            for ch in node.get("sub_plans") or []:
                if isinstance(ch, dict):
                    out.extend(_walk(ch))
            return out

        illegal: list[dict[str, Any]] = []
        for n in _walk(plan):
            if not (n.get("type") == "atomic" or n.get("is_atomic") is True):
                continue
            act = n.get("action")
            if not isinstance(act, str):
                continue
            act_name = self._action_name_from_sig(act)
            if act_name and (act_name not in allowed_action_names):
                illegal.append({"id": n.get("id"), "action": act, "action_name": act_name})
        return illegal

    # strict 模式下會讓計畫被拒絕的參數問題（unknown_param 只記錄，不拒絕）
    _BLOCKING_PARAM_ISSUES = frozenset({"missing_required", "invalid_type", "invalid_enum", "invalid_input"})

    def _param_validation_mode(self) -> str:
        """intent.param_validation：warn（預設，記錄並照常執行）或 strict（有問題就拒絕計畫）。"""
        mode = str((self.agent_config.get("intent") or {}).get("param_validation", "warn")).strip().lower()
        return mode if mode in ("warn", "strict") else "warn"

    @staticmethod
    def _collect_param_issues(plan: dict[str, Any]) -> list[dict[str, Any]]:
        """收集 planner 在 atomic 節點上記錄的 param_issues（附上節點 id 與 task）。"""
        found: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            for issue in node.get("param_issues") or []:
                found.append({"id": node.get("id"), "task": node.get("task"), **issue})
            for child in node.get("sub_plans") or []:
                walk(child)

        walk(plan)
        return found

    @staticmethod
    def _find_unexecutable_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
        """找出無法派工的節點。

        - atomic 但沒有綁定 task（例：leaf_no_children、leaf_forced_atomic、action 為空）
        - composite 但沒有任何子節點（例：LLM 拆解失敗）
        這些節點若放行，前者會被當成 "Unknown" 送出，後者會被當成已完成，兩者都會造成假成功。
        """
        if not isinstance(plan, dict):
            return []

        found: list[dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            info = {"id": node.get("id"), "type": node.get("type"), "intent": node.get("intent", "")}
            if node.get("type") == "atomic" or node.get("is_atomic") is True:
                if is_missing_task(node.get("task")):
                    found.append({**info, "action": node.get("action", ""), "reason": "no_bound_task"})
                elif resolve_request_topic(node.get("topic")) is None:
                    # 不知道要送給哪個執行端（不再預設送往 info）
                    found.append({**info, "action": node.get("action", ""), "reason": "no_topic"})
                return
            children = [c for c in (node.get("sub_plans") or []) if isinstance(c, dict)]
            if not children:
                found.append({**info, "reason": node.get("error") or "empty_composite"})
                return
            for child in children:
                _walk(child)

        _walk(plan)
        return found

    @staticmethod
    def _make_unresolved(
        *,
        intent: str,
        subs: list[SubIntent],
        reason: str,
        unmatched: list[str] | None = None,
        matched: list[str] | None = None,
        extra_debug: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        debug: dict[str, Any] = {"sub_intentions": [s.intent for s in subs]}
        if extra_debug:
            debug.update(extra_debug)
        return {
            "id": "root",
            "intent": intent,
            "depth": 0,
            "scheduled_start": "N/A",
            "type": "leaf_unresolved",
            "reason": reason,
            "unmatched_sub_intentions": unmatched or [],
            "matched_sub_intentions": matched or [],
            "sub_plans": [],
            "execution_logic": [],
            "debug": debug,
        }

    # ------------------------------------------------------------------
    # Execution level utilities（保留，仍可被外部測試呼叫）
    # ------------------------------------------------------------------
    def _compute_execution_levels(
        self, sub_plans: list[dict], execution_logic: list[dict]
    ) -> list[list[dict]]:
        return PlanCursor.compute_execution_levels(sub_plans, execution_logic)

    def _compute_execution_order(
        self, sub_plans: list[dict], execution_logic: list[dict]
    ) -> list[dict]:
        levels = self._compute_execution_levels(sub_plans, execution_logic)
        return [n for level in levels for n in level]

    # ------------------------------------------------------------------
    # Legacy synchronous execution path（與 v1 行為一致）
    # ------------------------------------------------------------------
    def _execute_atomic_node(self, node: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
        """執行單一 atomic：透過 publish_sync 同步 RPC。"""
        topic = node.get("topic")
        task = node.get("task") or "Unknown"
        params = node.get("params") or {}
        action_id = node.get("action_id")
        intent = node.get("intent", "")

        if is_missing_task(node.get("task")):
            logger.warning("Dispatch skipped: node=%s has no bound task", node.get("id"))
            return {"ok": False, "result": {"error": "node has no bound task; not dispatched"}}

        topic_name = resolve_request_topic(topic)
        if topic_name is None:
            logger.warning("Dispatch skipped: node=%s has no topic", node.get("id"))
            return {"ok": False, "result": {"error": "node has no topic; not dispatched"}}
        payload = build_action_payload(task=task, params=params, action_id=action_id, intent=intent)

        try:
            pcl = self.publish_sync(topic_name, payload, timeout=timeout)
            resp = getattr(pcl, "content", None) if pcl else None
            err = getattr(pcl, "error", None) if pcl else None
            ok = resp.get("ok", False) if isinstance(resp, dict) else (err is None)
            logger.info("Action completed: task=%s topic=%s ok=%s", task, topic_name, ok)
            return {
                "ok": ok,
                "result": resp if isinstance(resp, dict) else {"response": resp, "error": err},
            }
        except TimeoutError as e:
            logger.warning("Action timeout: task=%s topic=%s err=%s", task, topic_name, e)
            return {"ok": False, "result": {"payload": payload, "error": str(e)}}
        except Exception as e:
            logger.warning("Publish failed: %s", e)
            return {"ok": False, "result": {"payload": payload, "error": str(e)}}

    def _bind_inputs_sync(
        self, node: dict[str, Any], done: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], str | None]:
        """同步執行路徑的資料傳遞：回傳 (代入後的節點, 錯誤訊息)。done 是已執行節點 id → 結果。"""
        if not node.get("inputs"):
            return node, None

        def lookup(source_id: str) -> tuple[str | None, dict[str, Any] | None]:
            r = done.get(source_id)
            if r is None:
                return None, None
            payload = r.get("result") if isinstance(r.get("result"), dict) else {}
            return ("done" if r.get("ok") else "failed"), payload

        params, error = dataflow.resolve_inputs(node, lookup, enum_alias=self.domain.enum_alias)
        if error:
            return node, f"{INPUT_UNAVAILABLE}: {error}"
        return {**node, "params": params}, None

    def _execute_node(
        self, node: dict[str, Any], done: dict[str, dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        done = {} if done is None else done

        if (node.get("type") == "atomic") or (node.get("is_atomic") is True):
            node, error = self._bind_inputs_sync(node, done)
            r = {"ok": False, "result": {"error": error}} if error else self._execute_atomic_node(node)
            done[str(node.get("id"))] = r
            results.append({"id": node.get("id"), "intent": node.get("intent"), **r})
            return results

        sub_plans = node.get("sub_plans") or []
        execution_logic = node.get("execution_logic") or []
        levels = self._compute_execution_levels(sub_plans, execution_logic)

        for level in levels:
            if len(level) == 1:
                results.extend(self._execute_node(level[0], done))
            else:
                with ThreadPoolExecutor(max_workers=len(level)) as ex:
                    futures = [ex.submit(self._execute_node, child, done) for child in level]
                    for i, future in enumerate(futures):
                        try:
                            results.extend(future.result())
                        except Exception as e:
                            child = level[i]
                            logger.warning("Parallel node failed: id=%s err=%s", child.get("id"), e)
                            results.append({
                                "id": child.get("id"),
                                "intent": child.get("intent"),
                                "ok": False,
                                "result": {"error": str(e)},
                            })

        return results

    def execute_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """同步執行 plan（與 v1 相同行為）。"""
        if plan.get("type") == "leaf_unresolved":
            logger.warning("Plan unresolved, skip execution.")
            return {"ok": False, "message": "抱歉，無法完成此意圖。", "plan": plan}

        logger.debug("Starting plan execution.")
        results = self._execute_node(plan)
        all_ok = all(r.get("ok", False) for r in results)
        return {
            "ok": all_ok,
            "message": "執行完成。" if all_ok else "部分執行失敗。",
            "plan": plan,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Monitoring-aware execution path（v2）
    # ------------------------------------------------------------------
    def execute_plan_with_monitoring(
        self,
        plan: dict[str, Any],
        *,
        budget: Budget | None = None,
        trigger_config: TriggerConfig | None = None,
        dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
        env_facts_provider: Callable[[], dict[str, Any]] | None = None,
        subtree_planner: Callable[[str, dict[str, Any] | None], dict[str, Any] | None] | None = None,
        root_planner: Callable[..., dict[str, Any] | None] | None = None,
        llm_decider: Callable[..., ReplanDecision] | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """執行 plan 並啟用監測迴圈。

        Args:
            plan: 要執行的 plan tree
            budget: 自訂預算；不指定時依 agent_config 推算
            trigger_config: 自訂觸發策略
            dispatcher: 自訂 dispatch 函式（測試用），簽名 (node, payload) -> None。
                若不指定，使用 self.publish 對 broker 派工。
            env_facts_provider: 取得當下環境事實的函式；不指定時依 DomainProfile.env_fact_queries 查黑板
            subtree_planner: 子樹重生函式 (sub_intent, context) -> plan dict
            root_planner: 根層重生函式 (intent) 或 (intent, context) -> plan dict
            llm_decider: 自訂 LLM 判斷器（測試用）；不指定時使用 LLMReplanAdvisor，
                是否啟用由 intent.monitoring.enable_llm_assist 決定
            stop_event: 被設定時取消執行中的動作並結束（意圖被搶占或取消）；summary["stopped"] 為 True
        """
        if plan.get("type") == "leaf_unresolved":
            logger.warning("Plan unresolved, skip execution.")
            return {"ok": False, "message": "抱歉，無法完成此意圖。", "plan": plan}

        logger.debug("Starting monitored plan execution.")

        # 1) 建立 cursor / monitor / guard / trigger / repair
        try:
            cursor = PlanCursor(plan)
        except DuplicateNodeIdError as e:
            logger.warning("Plan rejected: %s", e)
            return {"ok": False, "message": "計畫節點 id 重複，無法執行。", "plan": plan, "error": str(e)}
        cycles = find_dependency_cycles(plan)
        if cycles:
            # plan_intention 會拒絕這種計畫；直接傳入時 cursor 會逐一打破循環後執行
            logger.warning("Plan has cyclic Sequence dependencies; executing with cycles broken: %s", cycles)
        budget = budget or self._build_budget()
        guard = BudgetGuard(budget)
        monitor = self._ensure_monitor()
        trigger = ReplanTrigger(
            config=trigger_config or self._build_trigger_config(),
            budget_guard=guard,
            llm=self.llm,
            logger=logger,
            llm_decider=llm_decider or LLMReplanAdvisor(self.llm, logger=logger),
        )
        repair = PlanRepair(
            budget_guard=guard,
            planner_callback=subtree_planner or self._default_subtree_planner,
            root_planner_callback=root_planner or self._default_root_planner,
            env_facts_provider=env_facts_provider or self._default_env_facts_provider(),
            logger=logger,
        )

        # 2) 確保訂閱 result topics 與黑板環境事件（若 broker 已連線）
        self._ensure_result_subscriptions(self._plan_channels(plan))
        self._ensure_env_subscriptions()

        try:
            # 3) 主迴圈
            loop_intent = plan.get("intent") or self.intention
            idle_polls = 0
            stopped = False
            while not cursor.done() and not guard.exhausted():
                if self._stop_event.is_set() or (stop_event is not None and stop_event.is_set()):
                    logger.warning("Monitored execution stopped by request.")
                    self._cancel_in_flight(cursor, reason="stopped", dispatcher=dispatcher)
                    stopped = True
                    break

                # 3a) drain inbox
                changes = monitor.drain_changes()
                results = monitor.drain_results()

                # 3b) 把 results 套回 cursor；未知或過期（retry 前舊派工）的結果不交給 trigger
                fresh: list[ActionResult] = []
                for ar in results:
                    rec = cursor.accept_action_result(ar.task_id, {
                        "task_id": ar.task_id,
                        "ok": ar.ok,
                        "cancelled": ar.cancelled,
                        "error": ar.error,
                        "message": ar.message,
                        "result": ar.result,
                        "task": ar.task,
                    })
                    if rec is not None:
                        fresh.append(ar)

                # 3b-1) 進度回報：記到節點上並當作心跳（延長閒置逾時）
                for update in monitor.drain_progress():
                    cursor.record_progress(update.task_id, update.to_dict(), at=update.received_at)

                # 3b-2) 逾時未回報的節點視同失敗，交給 trigger 走 retry / replan
                fresh.extend(self._expire_overdue_atomics(cursor, budget, dispatcher=dispatcher))

                # 3b-3) 執行端自行做的調整（改路線、放寬限制）記到節點上，交給 trigger 判斷
                adaptations = []
                for ad in monitor.drain_adaptations():
                    rec = cursor.record_adaptation(ad.task_id, ad.to_dict())
                    if rec is not None:
                        logger.info("Executor adapted node %s (%s): %s", rec.node_id, ad.kind, ad.reason)
                        adaptations.append(ad)

                # 3c) 諮詢 trigger
                decision = trigger.decide(
                    action_results=fresh,
                    env_changes=changes,
                    cursor=cursor,
                    intent=loop_intent,
                    adaptations=adaptations,
                )
                if decision.kind == TriggerKind.ABORT:
                    logger.warning("Trigger ABORT: %s", decision.reason)
                    self._cancel_in_flight(cursor, reason=f"abort:{decision.reason}", dispatcher=dispatcher)
                    break
                if decision.is_actionable():
                    self._apply_decision(decision, cursor, repair, intent=loop_intent, dispatcher=dispatcher)

                # 3d) 派工 ready atomics
                ready = cursor.next_ready_atomics()
                dispatched_now = 0
                for rec in ready:
                    self._dispatch_atomic(rec, cursor, monitor, dispatcher=dispatcher, budget=budget)
                    dispatched_now += 1

                # 3e) 等待信號或 idle 自旋限制
                if dispatched_now == 0 and not changes and not results and not fresh and not adaptations:
                    got = monitor.wait_for_signal(timeout=budget.poll_interval_sec)
                    if not got:
                        idle_polls += 1
                        # 避免無限 idle（例如 dispatcher 是 mock 但忘了回傳）：
                        # 若所有 atomic 皆 IN_FLIGHT 而沒有任何結果，繼續等（node_timeout_sec 保證不會無限等）；
                        # 若全 PENDING 也沒人 dispatch 成功，視為卡住 → ABORT
                        if idle_polls >= 5 and not any(
                            r.state == NodeState.IN_FLIGHT for r in cursor.all_records()
                        ):
                            logger.warning("Monitoring loop idle with no in-flight tasks; aborting.")
                            break
                    else:
                        idle_polls = 0
        finally:
            self._release_env_subscriptions()

        # 4) 收尾
        summary = cursor.summary()
        summary["stopped"] = stopped
        summary["budget"] = guard.snapshot()
        summary["message"] = "執行完成。" if summary.get("ok") else "部分執行失敗或被中斷。"
        return summary

    # ------------------------------------------------------------------
    # Monitoring helpers
    # ------------------------------------------------------------------
    def _build_budget(self) -> Budget:
        m = self._monitoring_config()
        # node_timeout_sec <= 0 表示不限（TOML 無 null 可用）
        node_timeout = float(m.get("node_timeout_sec", 30.0))
        return Budget(
            max_replans=int(m.get("max_replans", 3)),
            max_retries_per_node=int(m.get("max_retries_per_node", 1)),
            deadline_sec=m.get("deadline_sec"),
            poll_interval_sec=float(m.get("poll_interval_sec", 0.2)),
            cancel_grace_sec=float(m.get("cancel_grace_sec", 3.0)),
            node_timeout_sec=node_timeout if node_timeout > 0 else None,
        )

    def _ensure_monitor(self) -> ExecutionMonitor:
        if self._monitor is None:
            self._monitor = ExecutionMonitor(logger=logger)
        return self._monitor

    def _monitoring_config(self) -> dict[str, Any]:
        cfg = self.agent_config or {}
        m = cfg.get("intent", {}).get("monitoring") if isinstance(cfg.get("intent"), dict) else None
        return m if isinstance(m, dict) else {}

    def _build_trigger_config(self) -> TriggerConfig:
        """依 intent.monitoring 設定與 DomainProfile 建立 TriggerConfig。"""
        m = self._monitoring_config()
        tc = TriggerConfig(
            enable_llm_assist=bool(m.get("enable_llm_assist", False)),
            llm_max_calls=int(m.get("llm_max_calls", 10)),
            entity_aliases={k: list(v) for k, v in (self.domain.entity_aliases or {}).items()},
            entity_parents=dict(self.domain.entity_parents or {}),
        )
        prefixes = self._topic_prefixes(self.domain.env_subscriptions)
        if prefixes:
            tc.relevant_topic_prefixes = prefixes
        return tc

    @staticmethod
    def _topic_prefixes(patterns: list[str] | None) -> tuple[str, ...]:
        """把訂閱 pattern 轉成 trigger 用的 topic 前綴（"Zone/*/*" → "Zone/"）。"""
        prefixes: list[str] = []
        for pattern in patterns or []:
            prefix = str(pattern).split("*", 1)[0]
            if prefix not in prefixes:
                prefixes.append(prefix)
        return tuple(prefixes)

    def _bb_requester_id(self) -> str:
        return str(getattr(self, "agent_id", "") or "intentional_agent")

    def _bb_timeout(self) -> float:
        return float(self._monitoring_config().get("blackboard_timeout_sec", 2.0))

    def _ensure_env_subscriptions(self) -> None:
        """向黑板代理訂閱環境變動，事件轉入 ExecutionMonitor；可重入。

        pattern 來自 DomainProfile.env_subscriptions。黑板代理沒回應時只記 warning，
        監測迴圈仍會依動作結果運作。
        """
        if self._bb_subs_active:
            return
        patterns = [p for p in (self.domain.env_subscriptions or []) if p]
        if not patterns:
            logger.info("Domain profile has no env_subscriptions; environment changes will not trigger replanning.")
            return
        monitor = self._ensure_monitor()
        requester_id = self._bb_requester_id()
        try:
            self.subscribe(subscriber_topic(requester_id), "dict", monitor.on_env_event)
        except Exception as e:
            logger.warning("Subscribe blackboard events failed: %s", e)
            return
        subscribed: list[str] = []
        for pattern in patterns:
            if subscribe_blackboard(self, pattern, requester_id=requester_id, timeout=self._bb_timeout()) is None:
                # 黑板代理沒回應時不再逐一等待逾時
                logger.warning("Blackboard subscription failed for %s; is BlackboardAgent running?", pattern)
                break
            subscribed.append(pattern)
        self._bb_subs_active = bool(subscribed)
        if subscribed:
            logger.info("Watching blackboard changes: %s", subscribed)

    def _release_env_subscriptions(self) -> None:
        if not self._bb_subs_active:
            return
        unsubscribe_blackboard(self, requester_id=self._bb_requester_id(), timeout=self._bb_timeout())
        self._bb_subs_active = False

    def _default_env_facts_provider(self) -> Callable[[], dict[str, Any]] | None:
        return self._collect_env_facts if self.domain.env_fact_queries else None

    def _collect_env_facts(self) -> dict[str, Any]:
        """依 DomainProfile.env_fact_queries 經黑板代理查詢目前的環境事實（給重規劃用）。"""
        facts: dict[str, Any] = {}
        unavailable: list[str] = []
        for name, cypher in (self.domain.env_fact_queries or {}).items():
            rows = try_query_blackboard(self, cypher, timeout=self._bb_timeout())
            if rows is None:
                unavailable.append(name)
            else:
                facts[name] = rows
        if unavailable:
            # 讓 LLM 知道是「查不到」而不是「沒有異常」
            facts["_unavailable"] = unavailable
        return facts

    @staticmethod
    def _plan_channels(plan: dict[str, Any]) -> list[str]:
        """計畫中 atomic 節點會派工到的執行端頻道（依出現順序、不重複）。"""
        channels: list[str] = []

        def walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            if node.get("type") == "atomic" or node.get("is_atomic") is True:
                channel = channel_of_request_topic(node.get("topic"))
                if channel and channel not in channels:
                    channels.append(channel)
                return
            for child in node.get("sub_plans") or []:
                walk(child)

        walk(plan)
        return channels

    def _ensure_result_subscriptions(self, channels) -> None:
        """訂閱各執行端頻道的 <channel>.result 與 <channel>.progress；每個頻道只訂閱一次。

        頻道由計畫中的 topic 決定，新增執行端不必修改這裡。
        broker 尚未連線時（例如測試環境）會吞下例外，由 dispatcher 直接餵 monitor。
        """
        monitor = self._ensure_monitor()
        for channel in channels:
            if not channel or channel in self._result_channels:
                continue
            self._result_channels.add(channel)
            for kind, handler in (("result", monitor.on_action_result), ("progress", monitor.on_progress)):
                topic = f"{channel}.{kind}"
                try:
                    self.subscribe(topic, "dict", handler)
                except Exception as e:
                    logger.debug("subscribe %s failed: %s", topic, e)

    def _dispatch_atomic(
        self,
        rec,
        cursor: PlanCursor,
        monitor: ExecutionMonitor,
        *,
        dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None,
        budget: Budget,
    ) -> None:
        node = rec.node
        if is_missing_task(node.get("task")):
            logger.warning("Dispatch skipped: node=%s has no bound task", rec.node_id)
            cursor.mark_failed(rec.node_id, "node has no bound task; not dispatched")
            return
        # 先代入前面步驟的輸出，再以實際參數檢查前置條件
        failure = self._bind_inputs(rec, cursor) or self._dispatch_precondition_failure(rec, cursor)
        if failure:
            # 不派工；以失敗結果交給監測迴圈，trigger 會直接升級為重規劃（不重試）
            task_id = new_task_id(prefix=str(rec.node_id))
            cursor.mark_dispatched(rec.node_id, task_id=task_id)
            monitor.bind_task(task_id, rec.node_id)
            logger.warning("Not dispatching node %s: %s", rec.node_id, failure)
            monitor.on_action_result("dispatch_check", {
                "task_id": task_id, "ok": False, "error": failure, "task": node.get("task"),
            })
            return
        topic_name = resolve_request_topic(node.get("topic"))
        if topic_name is None:
            logger.warning("Dispatch skipped: node=%s has no topic", rec.node_id)
            cursor.mark_failed(rec.node_id, "node has no topic; not dispatched")
            return
        # 重規劃換上的子樹可能用到新的執行端：派工前先訂閱它的結果與進度
        self._ensure_result_subscriptions([channel_of_request_topic(topic_name)])
        task_id = new_task_id(prefix=str(rec.node_id))
        cursor.mark_dispatched(rec.node_id, task_id=task_id)
        monitor.bind_task(task_id, rec.node_id)

        payload = build_action_payload(
            task=node.get("task") or "Unknown",
            params=node.get("params") or {},
            action_id=node.get("action_id"),
            intent=node.get("intent", ""),
            task_id=task_id,
            idempotency_key=f"{rec.node_id}:{rec.attempts}",
            deadline_sec=node.get("deadline_sec"),
            interruptible=node.get("interruptible"),
            side_effect=node.get("side_effect"),
        )

        try:
            if dispatcher is not None:
                dispatcher(node, payload)
            else:
                self.publish(topic_name, payload)
            logger.info(
                "Dispatched atomic: node=%s task=%s topic=%s task_id=%s",
                rec.node_id, payload["task"], topic_name, task_id,
            )
        except Exception as e:
            logger.warning("Dispatch failed: node=%s err=%s", rec.node_id, e)
            cursor.mark_failed(rec.node_id, str(e), result={"payload": payload, "error": str(e)})
            monitor.unbind_task(task_id)

    def _expire_overdue_atomics(
        self,
        cursor: PlanCursor,
        budget: Budget,
        *,
        dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None,
    ) -> list[ActionResult]:
        """把派工後超過時限仍未回報的節點標為失敗，並對 executor 送 cancel。

        回傳對應的 ActionResult，讓 trigger 用與一般失敗相同的規則決定 retry / replan。
        """
        expired: list[ActionResult] = []
        now = time.time()
        for rec in cursor.overdue_in_flight(default_timeout_sec=budget.node_timeout_sec, now=now):
            task_id = rec.task_id or ""
            last = max(rec.dispatched_at or now, rec.last_activity_at or 0.0)
            error = (f"timeout: no result {now - (rec.dispatched_at or now):.1f}s after dispatch, "
                     f"last report {now - last:.1f}s ago")
            logger.warning("Action timeout: node=%s task_id=%s %s", rec.node_id, task_id, error)
            self._send_cancel(rec, reason="timeout", dispatcher=dispatcher)
            payload = {"task_id": task_id, "ok": False, "error": error, "task": rec.node.get("task") or ""}
            if cursor.accept_action_result(task_id, payload) is not None:
                expired.append(ActionResult.from_payload(payload, node_id=rec.node_id))
        return expired

    def _cancel_in_flight(
        self,
        cursor: PlanCursor,
        *,
        reason: str,
        dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None,
    ) -> None:
        """送 cancel 給目前仍在 IN_FLIGHT 的所有 task。"""
        for rec in cursor.all_records():
            if rec.state != NodeState.IN_FLIGHT or not rec.task_id:
                continue
            self._send_cancel(rec, reason=reason, dispatcher=dispatcher)
            cursor.cancel_in_flight(rec.node_id, reason=reason)

    def _apply_decision(
        self,
        decision: ReplanDecision,
        cursor: PlanCursor,
        repair: PlanRepair,
        *,
        intent: str,
        dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        """套用 trigger 的決策。

        - RETRY_NODE / REPAIR_NODE：同一節點要重派，先取消它執行中的派工再重設
        - REPLAN_SUBTREE / REPLAN_ROOT：先產生並換上新計畫，成功後才取消被取代的派工；
          重規劃失敗時原本的派工繼續執行，不會被無聲丟棄
        """
        if decision.kind in (TriggerKind.REPLAN_SUBTREE, TriggerKind.REPLAN_ROOT):
            in_flight = {r.node_id: r.task_id for r in cursor.all_records() if r.state == NodeState.IN_FLIGHT}
            outcome = repair.apply(decision, cursor, intent=intent)
            if outcome.get("applied"):
                for nid, task_id in in_flight.items():
                    rec = cursor.record(nid)
                    # replace_subtree / replace_root 會把被取代的 IN_FLIGHT 節點標為 OBSOLETE
                    if rec is not None and rec.state == NodeState.OBSOLETE and rec.task_id == task_id:
                        self._send_cancel(rec, reason=f"decision:{decision.kind.value}", dispatcher=dispatcher)
        else:
            self._cancel_for_redispatch(cursor, decision, dispatcher=dispatcher)
            outcome = repair.apply(decision, cursor, intent=intent)
        logger.info("Repair outcome: %s", outcome)
        return outcome

    def _cancel_for_redispatch(
        self,
        cursor: PlanCursor,
        decision: ReplanDecision,
        *,
        dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None,
    ) -> None:
        """RETRY_NODE / REPAIR_NODE：取消 affected_node_ids 中仍在執行的派工。

        標為 CANCELLED 才能 reset_for_retry；舊派工遲到的結果會被 cursor 忽略。
        """
        for nid in decision.affected_node_ids:
            rec = cursor.record(nid)
            if rec is None or rec.state != NodeState.IN_FLIGHT or not rec.task_id:
                continue
            self._send_cancel(rec, reason=f"decision:{decision.kind.value}", dispatcher=dispatcher)
            cursor.mark_cancelled(nid, result={"reason": f"decision:{decision.kind.value}"})

    def _send_cancel(
        self,
        rec,
        *,
        reason: str,
        dispatcher: Callable[[dict[str, Any], dict[str, Any]], None] | None,
    ) -> None:
        cancel_topic = cancel_topic_for(rec.node.get("topic"))
        if cancel_topic is None:
            logger.warning("Cancel skipped: node=%s has no topic", rec.node_id)
            return
        payload = build_cancel_payload(task_id=rec.task_id or "", reason=reason)
        try:
            if dispatcher is not None:
                # dispatcher 可選擇是否也處理 cancel：簽名 (node, payload)
                # 為了向後相容，這裡僅嘗試呼叫 dispatcher 並把 cancel 註記在 payload
                cancel_node = dict(rec.node)
                cancel_node["_cancel"] = True
                cancel_node["_cancel_topic"] = cancel_topic
                dispatcher(cancel_node, payload)
            else:
                self.publish(cancel_topic, payload)
            logger.info("Cancel sent: node=%s task_id=%s reason=%s", rec.node_id, rec.task_id, reason)
        except Exception as e:
            logger.warning("Cancel publish failed: %s", e)

    # ------------------------------------------------------------------
    # Default planner callbacks（接到 RecursivePlanner）
    # ------------------------------------------------------------------
    def _default_subtree_planner(
        self, sub_intent: str, context: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """REPLAN_SUBTREE 預設實作：以 sub_intent 走一遍 plan_intention。

        context（重規劃原因、受影響步驟、環境事實）會放進拆解 prompt，
        讓 LLM 依新的狀況產生不同的子計畫。
        """
        if not sub_intent:
            return None
        try:
            sub_plan = self.plan_intention(sub_intent, context=context)
        except Exception as e:
            logger.warning("Subtree planning failed: %s", e)
            return None
        if sub_plan.get("type") == "leaf_unresolved":
            return None
        return sub_plan

    def _default_root_planner(self, intent: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not intent:
            return None
        try:
            new_plan = self.plan_intention(intent, context=context)
        except Exception as e:
            logger.warning("Root planning failed: %s", e)
            return None
        if new_plan.get("type") == "leaf_unresolved":
            return None
        return new_plan
