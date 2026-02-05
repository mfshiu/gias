import os
import signal
import time
from datetime import datetime
import toml


def get_agent_config():
    config_path = os.getenv(
        "GIAS_CONFIG_PATH",
        os.path.join(os.getcwd(), "gias.toml")
    )
    return toml.load(config_path)


def wait_agent(agent):
    def signal_handler(signal, frame):
        agent.terminate()
    signal.signal(signal.SIGINT, signal_handler)

    time.sleep(1)
    dot_counter = 0    # 秒數計數
    minute_tracker = datetime.now().minute

    while agent.is_active():
        time.sleep(1)

        dot_counter += 1
        if dot_counter % 6 == 0:
            print('.', end='', flush=True)

        current_minute = datetime.now().minute
        if current_minute != minute_tracker:
            print(f"{datetime.now().strftime('%H:%M')}", end='', flush=True)
            minute_tracker = current_minute
    print()
