"""
Group C：Multi-Step Navigation（20 cases）

主要 metrics: ISR, RSR, PE

執行：
    python -m experiment1.run_multi_step
    python -m experiment1.run_multi_step --ratios 0.3,0.6 --limit 4
"""

from __future__ import annotations

import argparse

from experiment1.batch_runner import add_common_arguments, run_group


def main() -> int:
    parser = argparse.ArgumentParser(description="Experiment1 Multi-Step Navigation")
    add_common_arguments(parser)
    args = parser.parse_args()
    return run_group("multi_step", args)


if __name__ == "__main__":
    raise SystemExit(main())
