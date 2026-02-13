# src/core/intentional_agent.py
# IntentionalAgent: An agent that plans and executes actions based on user intentions.

import json
from typing import Any

from agentflow.core.agent import Agent
from llm.client import LLMClient
from llm.tasks.intent_tasks import parse_intent
from llm.schemas.intent import IntentCandidate

from log_helper import init_logging
logger = init_logging()

from kg.action_store import ActionStore
from kg.adapter_neo4j import Neo4jBoltAdapter

from core.intent.domain_profile import DomainProfile
from core.intent.sub_intent import SubIntent
from core.intent.embedder import LLMEmbedder
from core.intent.action_matcher import ActionMatcher
from core.intent.action_selector import ActionSelector
from core.intent.prompt_builder import PromptBuilder
from core.intent.llm_decomposer import LLMDecomposer
from core.intent.planner import RecursivePlanner
from core.intent.scope_gate import ScopeGate


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
        self.planner = RecursivePlanner(decomposer=self.decomposer, logger=logger)
        self.scope_gate = ScopeGate(llm=self.llm, logger=logger)

        super().__init__("intentional_agent.gias", agent_config)

    @property
    def kg(self):
        if self._kg is None:
            kg_cfg = self.agent_config.get("kg", {})
            if kg_cfg.get("type") != "neo4j":
                raise RuntimeError("KG type is not neo4j")

            # ✅ 注意：你的 toml 結構是 [kg] + [kg.neo4j]，adapter 要吃 kg_cfg["neo4j"]
            self._kg = Neo4jBoltAdapter.from_config(
                kg_cfg["neo4j"],
                logger=logger,
            )
        return self._kg


    def on_activate(self):
        plan = self.plan_intention(self.intention)
        if plan.get("type") == "leaf_unresolved":
            logger.warning("Abort: %s", plan.get("unmatched_sub_intentions"))
            return
        self.execute_plan(plan)


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
        1) LLM 拆解子意圖（subs）
        2) 對每個 sub-intent 做 action match（含 slots）
        - 若任何 sub-intent 無可行 action → 直接 leaf_unresolved
        3) ✅ 新增：Scope Gate（可用 config 開關）
        - 僅允許「由可用 actions 能力集合」覆蓋的意圖進入 planner
        - 避免 planner 用 pre_defined 繞過 matcher 產生錯誤可執行計畫
        4) select_actions + planner.plan
        5) ✅ 新增：計畫驗證（plan validation）
        - 若 planner 產生了不在 allowed_actions 內的 atomic action → 視為不可執行（leaf_unresolved）
        """
        norm = self.domain.normalize(intention)
        subs = self.break_down_intention(norm)

        unmatched: list[str] = []
        matched_pairs: list[tuple[SubIntent, list[ActionMatch]]] = []  # (SubIntent, [ActionMatch...])

        # 1) match per sub-intent (with slots)
        for s in subs:
            ms = self.match_actions(s.intent, slots=s.slots)
            if not ms:
                unmatched.append(s.intent)
            else:
                matched_pairs.append((s, ms))

        if unmatched:
            return {
                "id": "root",
                "intent": norm,
                "depth": 0,
                "scheduled_start": "N/A",
                "type": "leaf_unresolved",
                "reason": "Some sub-intents have no matched actions.",
                "unmatched_sub_intentions": unmatched,
                "matched_sub_intentions": [s.intent for s, _ in matched_pairs],
                "sub_plans": [],
                "execution_logic": [],
                "debug": {"sub_intentions": [s.intent for s in subs]},
            }

        # 2) 依每個 sub-intent 的匹配結果挑選 action
        #    注意：selector 回傳 dict[str, str]（signature -> description），供 planner/decomposer 使用
        chosen_actions = self.selector.select_actions([s for s, _ in matched_pairs])

        # 建立 allowed action 名單（從 selector 的 dict keys 提取 action name）
        def _action_name_from_sig(sig: str) -> str:
            s = (sig or "").strip()
            return s.split("(", 1)[0].strip() if "(" in s else s

        if isinstance(chosen_actions, dict):
            allowed_action_names = {_action_name_from_sig(k) for k in chosen_actions if k}
        else:
            allowed_action_names = {a.name for a in chosen_actions if getattr(a, "name", None)}
        if not allowed_action_names:
            return {
                "id": "root",
                "intent": norm,
                "depth": 0,
                "scheduled_start": "N/A",
                "type": "leaf_unresolved",
                "reason": "No allowed actions selected.",
                "unmatched_sub_intentions": [],
                "matched_sub_intentions": [s.intent for s, _ in matched_pairs],
                "sub_plans": [],
                "execution_logic": [],
                "debug": {"sub_intentions": [s.intent for s in subs]},
            }

        # 3) ✅ Scope Gate（避免 planner 產生 pre_defined 繞過 matcher）
        enable_scope_gate = bool(
            self.agent_config.get("intent", {}).get("enable_scope_gate", False)
            or self.agent_config.get("intentional_agent", {}).get("enable_scope_gate", False)
        )

        # LLM-based scope gate（通用、不列舉詞彙），需要你在 __init__ 內準備 self.scope_gate（建議）
        # 若你尚未導入 ScopeGate 類別，也可先把 enable_scope_gate 設 False
        if enable_scope_gate:
            try:
                # 只提供「本次允許的 actions」給 gate 判斷，避免它用整個 action store 亂合理化
                if isinstance(chosen_actions, dict):
                    allowed_actions_basic = [
                        {"name": _action_name_from_sig(k), "description": (v or "")}
                        for k, v in chosen_actions.items()
                    ]
                else:
                    allowed_actions_basic = [
                        {"name": a.name, "description": getattr(a, "description", "") or ""}
                        for a in chosen_actions
                    ]
                decision = self.scope_gate.decide(user_intent=norm, available_actions=allowed_actions_basic)
                if not getattr(decision, "can_execute", False):
                    return {
                        "id": "root",
                        "intent": norm,
                        "depth": 0,
                        "scheduled_start": "N/A",
                        "type": "leaf_unresolved",
                        "reason": getattr(decision, "reason", "") or "Scope gate rejected.",
                        "unmatched_sub_intentions": [],
                        "matched_sub_intentions": [s.intent for s in subs],
                        "sub_plans": [],
                        "execution_logic": [],
                        "debug": {
                            "sub_intentions": [s.intent for s in subs],
                            "scope_gate": {"can_execute": False, "reason": getattr(decision, "reason", "")},
                            "allowed_actions": sorted(list(allowed_action_names)),
                        },
                    }
            except Exception as e:
                # gate 失敗時的策略：
                # - 若你想更保守：直接拒絕（推薦在測試/嚴格模式）
                # - 若你想更寬鬆：放行（但會增加錯誤規劃風險）
                strict = bool(self.agent_config.get("intent", {}).get("scope_gate_strict", True))
                logger.warning("Scope gate error: %s", e)
                if strict:
                    return {
                        "id": "root",
                        "intent": norm,
                        "depth": 0,
                        "scheduled_start": "N/A",
                        "type": "leaf_unresolved",
                        "reason": f"Scope gate failed: {e}",
                        "unmatched_sub_intentions": [],
                        "matched_sub_intentions": [s.intent for s in subs],
                        "sub_plans": [],
                        "execution_logic": [],
                        "debug": {
                            "sub_intentions": [s.intent for s in subs],
                            "scope_gate": {"error": str(e)},
                            "allowed_actions": sorted(list(allowed_action_names)),
                        },
                    }

        # 4) planner 生成 plan
        plan = self.planner.plan(norm, chosen_actions)

        # 5) ✅ Plan validation：若 planner 產生了未被允許的 atomic action → 拒絕
        def _walk(node: dict[str, Any]) -> list[dict[str, Any]]:
            out = [node]
            for ch in node.get("sub_plans") or []:
                if isinstance(ch, dict):
                    out.extend(_walk(ch))
            return out

        def _extract_action_name(action_text: str) -> str:
            # 允許：ActionName(...) 或 "ActionName"（保守取 '(' 前）
            s = (action_text or "").strip()
            if not s:
                return ""
            if "(" in s:
                return s.split("(", 1)[0].strip()
            return s

        nodes = _walk(plan) if isinstance(plan, dict) else []
        atomic_nodes = [
            n for n in nodes
            if (n.get("type") == "atomic") or (n.get("is_atomic") is True)
        ]

        illegal_atoms: list[dict[str, Any]] = []
        for n in atomic_nodes:
            act = n.get("action")
            if not isinstance(act, str):
                continue
            act_name = _extract_action_name(act)
            if act_name and (act_name not in allowed_action_names):
                illegal_atoms.append({"id": n.get("id"), "action": act, "action_name": act_name})

        if illegal_atoms:
            return {
                "id": "root",
                "intent": norm,
                "depth": 0,
                "scheduled_start": "N/A",
                "type": "leaf_unresolved",
                "reason": "Planner produced actions outside allowed set.",
                "unmatched_sub_intentions": [],
                "matched_sub_intentions": [s.intent for s in subs],
                "sub_plans": [],
                "execution_logic": [],
                "debug": {
                    "sub_intentions": [s.intent for s in subs],
                    "allowed_actions": sorted(list(allowed_action_names)),
                    "illegal_atomic_nodes": illegal_atoms,
                },
            }

        plan.setdefault("debug", {})
        plan["debug"]["sub_intentions"] = [s.intent for s in subs]
        plan["debug"]["allowed_actions"] = sorted(list(allowed_action_names))

        logger.debug(f"Generated plan: {json.dumps(plan, indent=2, ensure_ascii=False)}")
        return plan


    def execute_plan(self, plan):
        if plan.get("type") == "leaf_unresolved":
            logger.warning("Plan unresolved, skip execution.")
            return {"ok": False, "message": "抱歉，無法完成此意圖。", "plan": plan}
        logger.debug("Starting plan execution.")
        # TODO
