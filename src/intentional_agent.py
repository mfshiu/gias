from agentflow.core.agent import Agent

from src.llm.client import LLMClient
from src.llm.tasks.intent_tasks import parse_intent
from src.llm.schemas.intent import IntentCandidate

from src.log_helper import init_logging
logger = init_logging()



class IntentionalAgent(Agent):

    def __init__(self, agent_config, intention:str):
        logger.info(f"agent_config: {agent_config}")
        
        self.intention = intention
        self.llm = LLMClient.from_env()
        
        super().__init__('intentional_agent.gias', agent_config)
        
        
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
        
        
    def plan_intention(self, intention:str):
        logger.debug(f"Planning intention: {intention}")
        
        sub_intentions = self.break_down_intention(intention)
        logger.debug(f"Sub-intentions: {sub_intentions}")
        
        return [f"Execute step for: {intention}"]
    
    
    def break_down_intention(self, intention: str):
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
        

def test_break_down_intention():
    """
    單元測試：break_down_intention()
    僅測 LLM-based intention parsing，不啟動 agent lifecycle
    """
    from src.app_helper import get_agent_config

    test_intention = "幫我查一下台北今天的天氣，並整理成摘要"

    logger.info("=== test_break_down_intention ===")
    logger.info(f"Input intention: {test_intention}")

    agent = IntentionalAgent(
        agent_config=get_agent_config(),
        intention=test_intention,
    )

    sub_intentions = agent.break_down_intention(test_intention)

    logger.info("Break down result:")
    for i, si in enumerate(sub_intentions, start=1):
        logger.info(f"  [{i}] {si}")

    logger.info("=== test_break_down_intention completed ===")


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

    # start_agent_process()
    test_break_down_intention()    