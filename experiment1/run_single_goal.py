"""
Group A：Single-Goal Navigation（20 cases）

主要 metrics: TSR, PE

執行：
    python -m experiment1.run_single_goal
    python -m experiment1.run_single_goal --ratios 0.3,0.6 --limit 4
"""

from __future__ import annotations

import argparse

from experiment1.batch_runner import add_common_arguments, run_group


def main() -> int:
    parser = argparse.ArgumentParser(description="Experiment1 Single-Goal Navigation")
    add_common_arguments(parser)
    args = parser.parse_args()
    return run_group("single_goal", args)


if __name__ == "__main__":
    raise SystemExit(main())
