import logging
import os
import re
import signal
import time
from datetime import datetime
from typing import Any

import toml

logger = logging.getLogger(__name__)

# 設定值中的 ${VAR}：載入時換成環境變數 VAR 的值（S-03：機密不必寫在 gias.toml）
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# 視為機密的設定鍵：以明文寫在設定檔時提出警告
SECRET_KEYS = frozenset({"api_key", "password", "token", "secret", "keyfile_password"})
_warned_paths: set[str] = set()


def expand_env(value: Any, missing: set[str] | None = None) -> Any:
    """把字串中的 ${VAR} 換成環境變數的值（dict / list 遞迴處理）；未設定的變數換成空字串並記在 missing。"""
    if isinstance(value, dict):
        return {k: expand_env(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v, missing) for v in value]
    if isinstance(value, str) and "${" in value:
        def repl(m: re.Match) -> str:
            name = m.group(1)
            if name not in os.environ and missing is not None:
                missing.add(name)
            return os.environ.get(name, "")
        return _ENV_REF.sub(repl, value)
    return value


def literal_secrets(config: Any, prefix: str = "") -> list[str]:
    """設定中以明文寫入的機密（非空字串、且不是 ${VAR} 參照）的鍵路徑，例如 "llm.openai.api_key"。"""
    found: list[str] = []
    if isinstance(config, dict):
        for key, value in config.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                found.extend(literal_secrets(value, path))
            elif str(key).lower() in SECRET_KEYS and isinstance(value, str) and value and "${" not in value:
                found.append(path)
    return found


def get_agent_config():
    """讀取 gias.toml（或 GIAS_CONFIG_PATH），展開 ${VAR} 環境變數參照。

    同一個檔案在每個 process 只提醒一次：機密以明文寫在檔案中、或參照的環境變數未設定（不輸出值）。
    """
    config_path = os.getenv(
        "GIAS_CONFIG_PATH",
        os.path.join(os.getcwd(), "gias.toml")
    )
    raw = toml.load(config_path)
    missing: set[str] = set()
    config = expand_env(raw, missing)
    if config_path not in _warned_paths:
        _warned_paths.add(config_path)
        plain = literal_secrets(raw)
        if plain:
            logger.warning(
                "%s stores secrets in plain text: %s. Reference environment variables instead "
                "(e.g. api_key = \"${OPENAI_API_KEY}\"); see docs/secrets.md.", config_path, ", ".join(plain))
        if missing:
            logger.warning("%s references unset environment variables: %s", config_path, ", ".join(sorted(missing)))
    return config


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
