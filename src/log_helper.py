from colorama import Fore, Style
import logging
import os

from dotenv import load_dotenv
load_dotenv()



class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        'E': Fore.RED,
        'W': Fore.YELLOW,
        'I': Fore.CYAN,
        'D': Fore.WHITE,
        'V': Fore.LIGHTBLACK_EX
    }

    def format(self, record):
        level_char = record.levelname[0]  # Get first letter of log level
        color = self.LEVEL_COLORS.get(level_char, Fore.WHITE)
        message = super().format(record)
        return f"{color}{message}{Style.RESET_ALL}"



LOGGING_LEVEL_VERBOSE = int(logging.DEBUG / 2)
logging.addLevelName(LOGGING_LEVEL_VERBOSE, "VERBOSE")


def verbose(self, message, *args, **kwargs):
    if self.isEnabledFor(LOGGING_LEVEL_VERBOSE):
        self._log(LOGGING_LEVEL_VERBOSE, message, args, **kwargs, stacklevel=2)


logging.Logger.verbose = verbose  # type: ignore


def get_log_level(level):
    levels = {
        'VERBOSE': LOGGING_LEVEL_VERBOSE,
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
    }

    level = levels.get(level, logging.DEBUG)
    return level


def init_logging() -> logging.Logger:
    import os
    import logging

    log_level_env = os.getenv("LOG_LEVEL")
    log_level = get_log_level((log_level_env.upper() if log_level_env else "DEBUG"))
    logger_name = os.getenv("LOGGER_NAME") or "gias"

    # 判斷是否在 pytest 中（pytest 會設定這個 env）
    in_pytest = bool(os.getenv("PYTEST_CURRENT_TEST"))

    # -------------------------
    # 先處理第三方套件降噪（不論 pytest/非 pytest 都套用）
    # -------------------------
    third_party_levels: dict[str, int] = {
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "openai": logging.WARNING,
        "neo4j": logging.WARNING,
        "neo4j.notifications": logging.ERROR,  # 最吵，直接 ERROR
        "urllib3": logging.WARNING,
        "requests": logging.WARNING,
    }
    for name, lvl in third_party_levels.items():
        l = logging.getLogger(name)
        l.setLevel(lvl)
        # 讓它不要自己往 root 冒泡造成 pytest live log 噴滿
        l.propagate = False

    # -------------------------
    # 你的 app logger
    # -------------------------
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)

    if in_pytest:
        # ✅ pytest：交給 pytest 的 log_cli handler 顯示
        # 1) 不自己掛 handler（避免 stdout 被 capture 或重複顯示）
        logger.handlers.clear()
        # 2) 必須 propagate=True，pytest 才收得到
        logger.propagate = True

        logger.info("Logging configured for pytest (propagate to root).")
        return logger

    # -------------------------
    # 非 pytest：維持你原本 stdout + ColorFormatter 的行為
    # -------------------------
    fmt = "%(levelname)1.1s %(asctime)s.%(msecs)03d %(module)18s:%(lineno)03d %(funcName)15s) %(message)s"
    datefmt = "%m-%d %H:%M:%S"
    console_formatter = ColorFormatter(fmt, datefmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)

    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(console_handler)

    logger.info(f"Log name: {logger.name}, Level: {logger.level}, Output: stdout only")
    return logger
