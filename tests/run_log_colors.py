#!/usr/bin/env python
# tests/run_log_colors.py
# 直接執行測試 log 顏色（不用 pytest）
#
# 執行：python tests/run_log_colors.py
# 或：  python -m tests.run_log_colors

import logging
import sys
from pathlib import Path

# Windows 下 colorama 需 init 才能正確顯示 ANSI 顏色
try:
    import colorama
    colorama.init()
except ImportError:
    pass

# 確保專案根目錄在 path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.log_helper import (
    LOGGING_LEVEL_VERBOSE,
    init_logging,
)


def main():
    init_logging()
    log = logging.getLogger("gias")
    log.setLevel(LOGGING_LEVEL_VERBOSE)

    print("=== Log 等級顏色測試 ===\n")

    log.verbose("VERBOSE 深灰")
    log.debug("DEBUG 淺灰")
    log.info("INFO 白")
    log.warning("WARNING 黃")
    log.error("ERROR 紅")

    print("\n=== 完成 ===")


if __name__ == "__main__":
    main()
