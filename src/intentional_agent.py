from agentflow.core.agent import Agent

from log_helper import init_logging
logger = init_logging()



class IntentionalAgent(Agent):

    def __init__(self, agent_config, intention:str):
        logger.info(f"agent_config: {agent_config}")
        
        self.intention = intention
        
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
        
        return [f"Execute step for: {intention}"]
    
    
    def execute_plan(self, plan):
        logger.debug("Starting plan execution.")
        


if __name__ == '__main__':
    logger.info("This is a placeholder for the IntentionalAgent module.")
    from app_helper import get_agent_config, wait_agent
    agent = IntentionalAgent(agent_config = get_agent_config(), intention="Say hello.")
    agent.start_process()
    
    logger.info("Agent process started.")
    wait_agent(agent)
    logger.warning("Agent process terminated.")
    