import re
from colorama import Fore, Style
import logging
import os

from dotenv import load_dotenv
load_dotenv()


# 敏感欄位：log 時以 *** 遮罩，不顯示完整 api_key / password
_SENSITIVE_KEYS = r"api_key|apikey|api-key|password|secret|token|credential|auth"
_REDACT_PATTERNS = [
    # JSON 雙引號: "api_key": "sk-xxx" -> "api_key": "***"
    (re.compile(rf'("(?:{_SENSITIVE_KEYS})"\s*:\s*)"[^"]*"', re.I), r'\1"***"'),
    # JSON/Python repr 單引號: 'api_key': 'sk-xxx'
    (re.compile(rf"('(?:{_SENSITIVE_KEYS})'\s*:\s*)'[^']*'", re.I), r"\1'***'"),
    # key=value 或 key: value（含 Python dict repr）
    (re.compile(rf"((?:{_SENSITIVE_KEYS})\s*[:=]\s*)[^\s,}}\]'\")\s]+", re.I), r"\1***"),
]


def _redact_sensitive(msg: str) -> str:
    """遮罩 log 訊息中的 api_key、password 等敏感值。"""
    if not msg or not isinstance(msg, str):
        return msg
    out = msg
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _redact_dict(obj):
    """遞迴遮罩 dict 中的敏感 key，回傳新 dict（不修改原物件）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key_lower = str(k).lower()
            if any(s in key_lower for s in ("api_key", "apikey", "password", "secret", "token", "credential", "auth")):
                out[k] = "***"
            else:
                out[k] = _redact_dict(v)
        return out
    if isinstance(obj, (list, tuple)):
        return type(obj)(_redact_dict(x) for x in obj)
    return obj


class RedactSecretsFilter(logging.Filter):
    """Logging Filter：遮罩 record.msg 與 record.args 中的 api_key、password 等。"""

    def filter(self, record):
        record.msg = _redact_sensitive(str(record.msg))
        if record.args:
            new_args = []
            for a in record.args:
                if isinstance(a, dict):
                    new_args.append(_redact_dict(a))
                elif isinstance(a, str):
                    new_args.append(_redact_sensitive(a))
                else:
                    new_args.append(a)
            record.args = tuple(new_args)
        return True


class RedactSecretsFormatter(logging.Formatter):
    """Formatter 包裝：先 format 再遮罩敏感欄位。"""

    def __init__(self, delegate: logging.Formatter):
        super().__init__()
        self._delegate = delegate

    def format(self, record):
        s = self._delegate.format(record)
        return _redact_sensitive(s)


class ColorFormatter(logging.Formatter):
    # 等級顏色：VERBOSE 淺灰、DEBUG 亮灰、INFO 白、WARNING 黃、ERROR 紅
    LEVEL_COLORS = {
        'E': Fore.RED,            # ERROR: 紅
        'W': Fore.YELLOW,         # WARNING: 黃
        'I': Fore.LIGHTBLUE_EX,   # INFO: 亮藍
        'D': Fore.LIGHTWHITE_EX,  # DEBUG: 亮灰
        'V': Fore.LIGHTBLACK_EX,  # VERBOSE: 淺灰（較 DEBUG 稍暗）
    }
    LEVEL_STYLE = {}

    def format(self, record):
        level_char = record.levelname[0]
        color = self.LEVEL_COLORS.get(level_char, Fore.WHITE)
        style = self.LEVEL_STYLE.get(level_char, "")
        message = super().format(record)
        return f"{style}{color}{message}{Style.RESET_ALL}"



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


def init_logging(*, pytest_mode: bool | None = None) -> logging.Logger:
    import os
    import logging

    log_level_env = os.getenv("LOG_LEVEL")
    log_level = get_log_level((log_level_env.upper() if log_level_env else "DEBUG"))
    logger_name = os.getenv("LOGGER_NAME") or "gias"

    # 判斷是否在 pytest 中（conftest 傳 pytest_mode=True，或 env 有 PYTEST_CURRENT_TEST）
    in_pytest = pytest_mode if pytest_mode is not None else bool(os.getenv("PYTEST_CURRENT_TEST"))

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
    logger.addFilter(RedactSecretsFilter())

    if in_pytest:
        # ✅ pytest：gias 用 ColorFormatter；同時替換 root 的 formatter，讓 propagate 到 root 的 logger（如 tests.test_intentional_agent）也用我們的顏色
        fmt = "%(levelname)1.1s %(asctime)s.%(msecs)03d %(module)18s:%(lineno)03d %(funcName)15s) %(message)s"
        datefmt = "%m-%d %H:%M:%S"
        color_fmt = ColorFormatter(fmt, datefmt)
        pytest_formatter = RedactSecretsFormatter(color_fmt)

        logger.handlers.clear()
        logger.propagate = True  # gias 也 propagate 到 root，統一由 root 輸出
        logger.setLevel(log_level)

        root = logging.getLogger()
        root.setLevel(log_level)
        for h in root.handlers[:]:
            h.setFormatter(pytest_formatter)
            h.setLevel(min(h.level, log_level))
        if not root.handlers:
            root_handler = logging.StreamHandler()
            root_handler.setLevel(log_level)
            root_handler.setFormatter(pytest_formatter)
            root.addHandler(root_handler)

        logger.info("Logging configured for pytest (ColorFormatter on root).")
        return logger

    # -------------------------
    # 非 pytest：維持你原本 stdout + ColorFormatter 的行為
    # -------------------------
    fmt = "%(levelname)1.1s %(asctime)s.%(msecs)03d %(module)18s:%(lineno)03d %(funcName)15s) %(message)s"
    datefmt = "%m-%d %H:%M:%S"
    color_fmt = ColorFormatter(fmt, datefmt)
    console_formatter = RedactSecretsFormatter(color_fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)

    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(console_handler)

    logger.info(f"Log name: {logger.name}, Level: {logger.level}, Output: stdout only")
    return logger
