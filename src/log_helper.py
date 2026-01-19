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



def init_logging() -> logging.Logger:
    log_level = getattr(logging, os.getenv('LOG_LEVEL').upper(), logging.INFO)
    logger_name = os.getenv('LOGGER_NAME') or 'gias'

    # 設定 Formatter
    fmt = '%(levelname)1.1s %(asctime)s.%(msecs)03d %(module)18s:%(lineno)03d %(funcName)15s) %(message)s'
    datefmt = '%m-%d %H:%M:%S'
    console_formatter = ColorFormatter(fmt, datefmt)

    # Console handler（唯一 handler，只輸出到 stdout）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)

    # 設定 logger
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(console_handler)
    logger.setLevel(log_level)
    logger.info(f"Log name: {logger.name}, Level: {logger.level}, Output: stdout only")
    
    return logger