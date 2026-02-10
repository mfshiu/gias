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



class IntentionalAgent(Agent):
    def __init__(self, agent_config, intention: str, *, domain_profile: DomainProfile | None = None):
        self.agent_config = agent_config
        self.intention = intention
        self.llm = LLMClient.from_env()
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

        super().__init__("intentional_agent.gias", agent_config)


    @property
    def kg(self):
        if self._kg is None:
            kg_cfg = self.agent_config.get("kg", {})
            if kg_cfg.get("type") != "neo4j":
                raise RuntimeError("KG type is not neo4j")

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
        norm = self.domain.normalize(intention)
        logger.debug(f"Breaking down intention via LLM: {norm}")

        try:
            result, meta = parse_intent(llm=self.llm, user_text=norm)
            candidates: list[IntentCandidate] = result.candidates or []
            subs: list[SubIntent] = []

            for c in candidates:
                canon = (c.description or c.name or norm).strip()
                canon = self.domain.normalize(canon)
                subs.append(SubIntent(intent=canon, slots=c.slots or {}, raw={"name": c.name, "description": c.description}))

            return subs or [SubIntent(intent=norm, slots={}, raw={"fallback": True})]
        except Exception:
            logger.exception("Failed to break down intention via LLM, fallback to normalized intention.")
            return [SubIntent(intent=norm, slots={}, raw={"fallback": True})]


    def match_actions(self, intention: str, **kwargs):
        return self.matcher.match_actions(intention, **kwargs)


    def plan_intention(self, intention: str) -> dict[str, Any]:
        norm = self.domain.normalize(intention)
        subs = self.break_down_intention(norm)

        unmatched = []
        matched_pairs = []  # (SubIntent, [ActionMatch...])

        for s in subs:
            ms = self.match_actions(s.intent)  # 直接用 matcher
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
            }

        actions = self.selector.select_actions([s for s, _ in matched_pairs])  # 或改用 matched_pairs 的分數
        plan = self.planner.plan(norm, actions)
        plan.setdefault("debug", {})
        plan["debug"]["sub_intentions"] = [s.intent for s in subs]
        logger.debug(f"Generated plan: {json.dumps(plan, indent=2, ensure_ascii=False)}")
        return plan


    def execute_plan(self, plan):
        if plan.get("type") == "leaf_unresolved":
            logger.warning("Plan unresolved, skip execution.")
            return {"ok": False, "message": "抱歉，無法完成此意圖。", "plan": plan}
        logger.debug("Starting plan execution.")
        # TODO
