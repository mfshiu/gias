# src/core/intentional_agent.py
# IntentionalAgent: An agent that plans and executes actions based on user intentions.

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

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
from src.agents._executor_utils import resolve_request_topic, build_action_payload


class IntentionalAgent(Agent):
    def __init__(self, agent_config, intention: str, *, domain_profile: DomainProfile | None = None):
        self.agent_config = agent_config
        self.intention = intention

        # ✅ 完全使用 gias.toml（由 get_agent_config() 讀入的 agent_config）
        self.llm = LLMClient.from_config(agent_config)

        self.domain = domain_profile or DomainProfile()

        self._kg = None
        self.action_store = ActionStore(self.kg)

        # composed modules
        self.embedder = LLMEmbedder(self.llm)
        self.matcher = ActionMatcher(action_store=self.action_store, embedder=self.embedder, domain=self.domain, logger=logger)
        self.selector = ActionSelector(kg=self.kg, matcher=self.matcher, logger=logger)
        self.prompt_builder = PromptBuilder()
        self.decomposer = LLMDecomposer(llm=self.llm, prompt_builder=self.prompt_builder, logger=logger)
        self.planner = RecursivePlanner(
            decomposer=self.decomposer,
            logger=logger,
            kg=self.kg,
            action_store=self.action_store,
        )
        self.scope_gate = ScopeGate(llm=self.llm, logger=logger)

        super().__init__("intentional_agent.gias", agent_config)

    @property
    def kg(self):
        if self._kg is None:
            kg_cfg = self.agent_config.get("kg", {})
            if kg_cfg.get("type") != "neo4j":
                raise RuntimeError("KG type is not neo4j")

            # ActionStore 需查 actions database（seed_actions_simple 寫入處）
            base = kg_cfg.get("neo4j")
            actions_overrides = kg_cfg.get("neo4j_actions")
            if not isinstance(base, dict):
                raise RuntimeError("Missing [kg.neo4j] config in gias.toml")
            if not isinstance(actions_overrides, dict):
                raise RuntimeError("Missing [kg.neo4j_actions] config in gias.toml")

            merged = {**base, **actions_overrides}
            self._kg = Neo4jBoltAdapter.from_config(merged, logger=logger)
        return self._kg


    def on_activate(self):
        plan = self.plan_intention(self.intention)
        if plan.get("type") == "leaf_unresolved":
            logger.warning("Abort: %s", plan.get("unmatched_sub_intentions"))
            self._terminate()
            return
        result = self.execute_plan(plan)
        logger.info("Plan execution finished: ok=%s", result.get("ok", False))
        self._terminate()


    def break_down_intention(self, intention: str) -> list[SubIntent]:
        """
        將使用者意圖拆解為一層 sub-intents，並盡可能保留可落地的 slots。
        通用性設計重點：
        - 不在此處列舉任何特定領域詞彙
        - 優先使用 LLM 輸出的 slots（若缺少則補上通用欄位）
        - 明確保留原始意圖（避免被 LLM 過度抽象化）
        - 若 LLM 輸出過度抽象（slots 幾乎空且文本與原文相似度很低），改以原文作為 sub-intent
        """
        norm = self.domain.normalize(intention)
        logger.debug(f"Breaking down intention via LLM: {norm}")

        def _safe_str(x) -> str:
            return (x or "").strip()

        def _normalize_slots(slots: dict | None) -> dict:
            """
            通用 slot 清理：
            - 只保留 dict
            - key/value 轉成可 JSON 化的簡單型別
            - 補上保留欄位（不列舉領域詞彙）
            """
            s = dict(slots or {})
            # 保留原始意圖，避免後續偷換目標時無從追溯
            s.setdefault("_source_text", norm)
            # 可選：保留正規化後意圖
            s.setdefault("_normalized_text", norm)
            return s

        def _token_overlap_ratio(a: str, b: str) -> float:
            """
            很輕量的字元集合重疊率，用來偵測 LLM 是否把意圖抽象到失真。
            0~1，越高代表越像。
            """
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

                # canon：優先用 desc，其次 name，最後用原始 norm
                canon = (desc or name or norm).strip()
                canon = self.domain.normalize(canon)

                # ---- 失真防護（通用）----
                # 若 LLM 給的 canon 太抽象（與原文重疊很低）且 slots 幾乎是空的
                # 則改回用原文 norm，避免「偷換目標」造成錯誤可執行計畫
                slot_keys = [k for k in slots.keys() if not str(k).startswith("_")]
                overlap = _token_overlap_ratio(norm, canon)

                if (len(slot_keys) == 0) and (overlap < 0.25):
                    # 仍保留 LLM 的 raw 供 debug，但以原文作為可執行子意圖
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


    def plan_intention(self, intention: str) -> dict[str, Any]:
        """
        通用規劃流程（更保守、更能拒絕）：
        1) LLM 拆解子意圖；對每個 sub-intent 做 action match（含 slots）
        2) selector 挑選 chosen_actions，建立 allowed_action_names
        3) Scope Gate（可用 config 開關）：能力集合是否足以完成意圖
        4) planner 生成 plan
        5) Plan validation：planner 不可使用 allowed 之外的 atomic action
        """
        norm = self.domain.normalize(intention)
        subs = self.break_down_intention(norm)

        # 1) match per sub-intent
        matched_pairs, unmatched = self._match_subs(subs)
        if unmatched:
            return self._make_unresolved(
                intent=norm, subs=subs,
                reason="Some sub-intents have no matched actions.",
                unmatched=unmatched,
                matched=[s.intent for s, _ in matched_pairs],
            )

        # 2) selector 挑選 + allowed action 白名單
        chosen_actions = self.selector.select_actions([s for s, _ in matched_pairs])
        allowed_action_names = self._extract_allowed_action_names(chosen_actions)
        if not allowed_action_names:
            return self._make_unresolved(
                intent=norm, subs=subs,
                reason="No allowed actions selected.",
                matched=[s.intent for s, _ in matched_pairs],
            )

        # 3) Scope Gate
        gate_reject = self._run_scope_gate(norm, subs, chosen_actions, allowed_action_names)
        if gate_reject is not None:
            return gate_reject

        # 4) planner 生成 plan
        plan = self.planner.plan(norm, chosen_actions)

        # 5) Plan validation
        illegal_atoms = self._find_illegal_atomic_actions(plan, allowed_action_names)
        if illegal_atoms:
            return self._make_unresolved(
                intent=norm, subs=subs,
                reason="Planner produced actions outside allowed set.",
                matched=[s.intent for s in subs],
                extra_debug={
                    "allowed_actions": sorted(allowed_action_names),
                    "illegal_atomic_nodes": illegal_atoms,
                },
            )

        plan.setdefault("debug", {})
        plan["debug"]["sub_intentions"] = [s.intent for s in subs]
        plan["debug"]["allowed_actions"] = sorted(allowed_action_names)
        logger.debug(f"Generated plan: {json.dumps(plan, indent=2, ensure_ascii=False)}")
        return plan

    # ------------------------------------------------------------------
    # plan_intention helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _action_name_from_sig(sig: str) -> str:
        """從 'ActionName(Param1, ...)' 取出 'ActionName'；非簽章字串原樣回傳。"""
        s = (sig or "").strip()
        return s.split("(", 1)[0].strip() if "(" in s else s

    def _match_subs(
        self, subs: list[SubIntent]
    ) -> tuple[list[tuple[SubIntent, list[Any]]], list[str]]:
        """對每個 sub-intent 做 action match（帶 slots），回傳 (matched_pairs, unmatched_intents)。"""
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
        """從 selector 的輸出（dict 或 list[ActionDef]）擷取 action 名稱白名單。"""
        if isinstance(chosen_actions, dict):
            return {self._action_name_from_sig(k) for k in chosen_actions if k}
        return {a.name for a in chosen_actions if getattr(a, "name", None)}

    def _to_basic_actions(self, chosen_actions: Any) -> list[dict[str, str]]:
        """把 chosen_actions 轉成 ScopeGate 可吃的 [{name, description}, ...]。"""
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
        """執行 Scope Gate；通過或停用時回傳 None，被擋下時回傳 leaf_unresolved dict。"""
        if not self._scope_gate_enabled():
            return None

        try:
            decision = self.scope_gate.decide(
                user_intent=norm,
                available_actions=self._to_basic_actions(chosen_actions),
            )
        except Exception as e:
            # 嚴格模式拒絕，否則放行（但仍記 log）
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
        """走訪 plan 樹找出不在 allowed 集合內的 atomic action 節點。"""
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
        """統一產生 leaf_unresolved 形式的回傳 dict。"""
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


    def _compute_execution_levels(
        self, sub_plans: list[dict], execution_logic: list[dict]
    ) -> list[list[dict]]:
        """
        依 execution_logic 計算執行層級（每層可並行）。
        - Sequence(from_id, to_id): to_id 須在 from_id 完成後執行
        - Parallel(from_id, to_id): 無依賴，同層可並行
        回傳 list[list[dict]]：每層為可並行執行的節點清單。
        """
        id_to_node = {str(n.get("id", "")): n for n in sub_plans if isinstance(n, dict)}
        if not id_to_node:
            return [list(sub_plans)] if sub_plans else []

        deps = {to_id: [] for to_id in id_to_node}
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

        # 未在 levels 中的節點（無 id 或未納入圖）補為第一層
        seen = {n.get("id") for level in levels for n in level}
        orphans = [n for n in sub_plans if isinstance(n, dict) and n.get("id") not in seen]
        if orphans:
            levels.insert(0, orphans) if levels else levels.append(orphans)
        return levels if levels else []

    def _compute_execution_order(
        self, sub_plans: list[dict], execution_logic: list[dict]
    ) -> list[dict]:
        """相容用：將 levels 攤平為順序（供測試或 fallback）。"""
        levels = self._compute_execution_levels(sub_plans, execution_logic)
        return [n for level in levels for n in level]

    def _execute_atomic_node(self, node: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
        """
        執行單一 atomic 節點，透過 publish_sync 發送 action 請求至 InfoAgent / NavigationAgent，
        並等待處理完成後才回傳（block）。
        """
        topic = node.get("topic")
        task = node.get("task") or "Unknown"
        params = node.get("params") or {}
        action_id = node.get("action_id")
        intent = node.get("intent", "")

        topic_name = resolve_request_topic(topic)
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

    def _execute_node(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        """遞迴執行 plan 節點。atomic 直接執行；composite 依 levels 分層，同層並行執行。"""
        results: list[dict[str, Any]] = []

        if (node.get("type") == "atomic") or (node.get("is_atomic") is True):
            r = self._execute_atomic_node(node)
            results.append({"id": node.get("id"), "intent": node.get("intent"), **r})
            return results

        sub_plans = node.get("sub_plans") or []
        execution_logic = node.get("execution_logic") or []
        levels = self._compute_execution_levels(sub_plans, execution_logic)

        for level in levels:
            if len(level) == 1:
                results.extend(self._execute_node(level[0]))
            else:
                # 同層多節點：並行執行
                with ThreadPoolExecutor(max_workers=len(level)) as ex:
                    futures = [ex.submit(self._execute_node, child) for child in level]
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
        """
        依 plan 與 execution_logic 規範執行計畫。
        - Sequence: from_id 完成後才執行 to_id
        - Parallel: 無依賴，可並行
        """
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
