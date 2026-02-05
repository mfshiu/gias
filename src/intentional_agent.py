# src/intentional_agent.py

from agentflow.core.agent import Agent

from src.llm.client import LLMClient
from src.llm.tasks.intent_tasks import parse_intent
from src.llm.schemas.intent import IntentCandidate

from src.log_helper import init_logging
logger = init_logging()

from src.kg.action_store import ActionStore
from src.kg.adapter_neo4j import Neo4jBoltAdapter


class IntentionalAgent(Agent):

    def __init__(self, agent_config, intention: str):
        self.agent_config = agent_config
        self.intention = intention
        self.llm = LLMClient.from_env()

        self._kg = None  # lazy init
        self.action_store = ActionStore(self.kg)

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
        logger.debug(f"Agent activated with intention: {self.intention}")
        
        # 1. Break down the intention into sub-intentions (plan)
        plan = self.plan_intention(self.intention)
        logger.info(f"Generated plan: {plan}")
        
        # 2. Execute each sub-intention
        self.execute_plan(plan)
        logger.info("Intention execution completed.")
        
        # 3. Finalize
        logger.debug("Finalizing agent process.")


    def _embed_text(self, text: str) -> list[float]:
        # 依你的 LLMClient 實作改成「唯一正確」的方法
        # 下面是保守寫法：找常見 embedding method
        for fn_name in ("embed_text", "embed", "embedding", "embeddings"):
            fn = getattr(self.llm, fn_name, None)
            if callable(fn):
                v = fn(text)
                if isinstance(v, dict) and "embedding" in v:
                    return v["embedding"]
                if isinstance(v, list):
                    return v
        raise AttributeError("LLMClient 沒有 embedding 方法（embed/embed_text/embeddings）。")


    def match_actions(self, intention: str, *, top_k: int = 10, min_score: float = 0.75):
        logger.debug(f"Matching actions for sub-intention: {intention}")

        q_vec = self._embed_text(intention)
        dim = len(q_vec)

        # 確保 vector index 存在（第一次跑會建立）
        self.action_store.ensure_action_desc_index(dimensions=dim)

        # Neo4j 端作相似度搜尋
        rows = self.action_store.search_actions_by_vector(
            vector=q_vec,
            top_k=top_k,
            min_score=min_score,
        )

        actions = []
        for r in rows:
            name = r.get("name") or "UnnamedAction"
            desc = r.get("description") or ""
            score = r.get("score")
            actions.append(f"{name} (score={score:.3f}) - {desc}")

        logger.info(f"Matched actions: {len(actions)}")
        return actions
    

    def plan_intention(self, intention:str):
        logger.debug(f"Planning intention: {intention}")
        
        sub_intentions = self.break_down_intention(intention)
        logger.debug(f"Sub-intentions: {sub_intentions}")

        # Match actions for each sub-intention
        actions = []
        for sub_intention in sub_intentions:
            actions.extend(self.match_actions(sub_intention))
        actions = list(set(actions))  # 去重覆
        logger.debug(f"Planned actions: {actions}")
        
        return actions
    
    
    def break_down_intention(self, intention: str) -> list:
        """
        使用 LLM-based intent parsing 將高階意圖拆解為子意圖
        回傳：List[str] 或 List[IntentCandidate]
        """
        logger.debug(f"Breaking down intention via LLM: {intention}")

        try:
            result, meta = parse_intent(
                llm=self.llm,
                user_text=intention,
            )

            candidates = result.candidates  # List[IntentCandidate]

            logger.info(
                f"Intent parsed by template={getattr(meta, 'template_name', 'N/A')}, "
                f"version={getattr(meta, 'version', 'N/A')}, "
                f"count={len(candidates)}"
            )

            # 👉 這裡先回傳「語意清楚的文字描述」
            #    之後你可以改成回傳 IntentCandidate 本身
            sub_intentions = [
                f"{c.name}: {c.description} | slots={c.slots}"
                for c in candidates
            ]

            return sub_intentions

        except Exception as e:
            logger.exception("Failed to break down intention via LLM, fallback to raw intention.")
            # 保底策略：至少不讓 agent 掛掉
            return [f"Unparsed intention: {intention}"]    
    
    def execute_plan(self, plan):
        logger.debug("Starting plan execution.")


def start_agent_process():
    """Start the intentional agent process."""
    logger.info("This is a placeholder for the IntentionalAgent module.")
    from app_helper import get_agent_config, wait_agent
    agent = IntentionalAgent(agent_config = get_agent_config(), intention="Say hello.")
    agent.start_process()
    
    logger.info("Agent process started.")
    wait_agent(agent)
    logger.warning("Agent process terminated.")
    
    
if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()

    start_agent_process()
