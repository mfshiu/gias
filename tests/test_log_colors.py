# tests/test_log_colors.py
# 測試 log 等級顏色：VERBOSE 深灰、DEBUG 淺灰、INFO 白、WARNING 黃、ERROR 紅
#
# 執行：python -m pytest tests/test_log_colors.py -v -s

import io
import logging

import pytest

from colorama import Fore, Style
from src.log_helper import (
    ColorFormatter,
    LOGGING_LEVEL_VERBOSE,
    RedactSecretsFormatter,
    get_log_level,
)


# ANSI  escape codes（colorama 實際輸出）
_RED = Fore.RED
_YELLOW = Fore.YELLOW
_LIGHT_BLUE = Fore.LIGHTBLUE_EX   # 亮藍（INFO）
_LIGHT_GRAY = Fore.LIGHTBLACK_EX  # 淺灰（VERBOSE）
_BRIGHT_GRAY = Fore.LIGHTWHITE_EX # 亮灰（DEBUG）
_RESET = Style.RESET_ALL


def _make_logger_with_color_formatter():
    """建立使用 ColorFormatter 的 logger，輸出到 StringIO。"""
    logger = logging.getLogger("test_log_colors")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    fmt = "%(levelname)1.1s %(message)s"
    formatter = ColorFormatter(fmt)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger, buf


def test_color_formatter_error_red():
    """ERROR 應為紅色。"""
    logger, buf = _make_logger_with_color_formatter()
    logger.error("error message")
    out = buf.getvalue()
    assert _RED in out
    assert "error message" in out


def test_color_formatter_warning_yellow():
    """WARNING 應為黃色。"""
    logger, buf = _make_logger_with_color_formatter()
    logger.warning("warning message")
    out = buf.getvalue()
    assert _YELLOW in out
    assert "warning message" in out


def test_color_formatter_info_light_blue():
    """INFO 應為亮藍。"""
    logger, buf = _make_logger_with_color_formatter()
    logger.info("info message")
    out = buf.getvalue()
    assert _LIGHT_BLUE in out
    assert "info message" in out


def test_color_formatter_debug_bright_gray():
    """DEBUG 應為亮灰。"""
    logger, buf = _make_logger_with_color_formatter()
    logger.debug("debug message")
    out = buf.getvalue()
    assert _BRIGHT_GRAY in out
    assert "debug message" in out


def test_color_formatter_verbose_light_gray():
    """VERBOSE 應為淺灰。"""
    logging.addLevelName(LOGGING_LEVEL_VERBOSE, "VERBOSE")
    logger, buf = _make_logger_with_color_formatter()
    logger.setLevel(LOGGING_LEVEL_VERBOSE)
    logger.handlers[0].setLevel(LOGGING_LEVEL_VERBOSE)
    logger.verbose("verbose message")
    out = buf.getvalue()
    assert _LIGHT_GRAY in out
    assert "verbose message" in out


def test_all_levels_emit_colored_output():
    """各等級輸出應含對應 ANSI 碼。"""
    logger, buf = _make_logger_with_color_formatter()
    logger.setLevel(LOGGING_LEVEL_VERBOSE)
    logger.handlers[0].setLevel(LOGGING_LEVEL_VERBOSE)

    logger.verbose("v")
    logger.debug("d")
    logger.info("i")
    logger.warning("w")
    logger.error("e")

    out = buf.getvalue()
    assert _RESET in out  # 每行結尾應有 RESET
    assert _RED in out
    assert _YELLOW in out
    assert _LIGHT_BLUE in out
    assert _LIGHT_GRAY in out or _BRIGHT_GRAY in out


def test_redact_formatter_masks_sensitive():
    """RedactSecretsFormatter 應遮罩 api_key。"""
    logger = logging.getLogger("test_redact")
    logger.handlers.clear()
    logger.propagate = False

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    fmt = "%(message)s"
    formatter = RedactSecretsFormatter(ColorFormatter(fmt))
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    logger.info("Config: %s", {"api_key": "sk-secret123", "model": "gpt-4"})
    out = buf.getvalue()
    assert "sk-secret123" not in out
    assert "***" in out
    assert "gpt-4" in out


def test_get_log_level():
    """get_log_level 應回傳正確數值。"""
    assert get_log_level("ERROR") == logging.ERROR
    assert get_log_level("WARNING") == logging.WARNING
    assert get_log_level("INFO") == logging.INFO
    assert get_log_level("DEBUG") == logging.DEBUG
    assert get_log_level("VERBOSE") == LOGGING_LEVEL_VERBOSE


def test_log_colors_visual(capsys):
    """
    視覺測試：執行時用 -s 可看到實際顏色。
    python -m pytest tests/test_log_colors.py::test_log_colors_visual -s
    """
    log = logging.getLogger("gias")
    log.setLevel(LOGGING_LEVEL_VERBOSE)
    for h in log.handlers:
        h.setLevel(LOGGING_LEVEL_VERBOSE)

    log.verbose("VERBOSE 深灰")
    log.debug("DEBUG 淺灰")
    log.info("INFO 白")
    log.warning("WARNING 黃")
    log.error("ERROR 紅")

    captured = capsys.readouterr()
    # log 預設輸出到 stderr
    out = captured.out + captured.err
    assert "DEBUG" in out or "淺灰" in out
    assert "ERROR" in out or "紅" in out
