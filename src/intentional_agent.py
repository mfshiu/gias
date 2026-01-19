from agentflow.core.agent import Agent

from log_helper import init_logging
logger = init_logging()



class IntentionalAgent(Agent):

    def __init__(self, agent_config):
        logger.info(f"agent_config: {agent_config}")
        super().__init__('intentional_agent.gias', agent_config)



if __name__ == '__main__':
    logger.info("This is a placeholder for the IntentionalAgent module.")
    from app_helper import get_agent_config, wait_agent
    agent = IntentionalAgent(agent_config = get_agent_config())
    agent.start_process()
    
    logger.info("Agent process started.")
    wait_agent(agent)
    logger.warning("Agent process terminated.")
    