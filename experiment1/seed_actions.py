"""
Experiment 1 actions 種子程式

Wraps `tests.seed_actions_with_embeddings.seed_actions_simple`，將展場 10 個 action
（含 embedding）寫入 [kg.neo4j_actions] 指向的 Neo4j database。

執行：
    python -m experiment1.seed_actions
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        # 直接執行既有的 seed 腳本
        from tests.seed_actions_with_embeddings import seed_actions_simple
    except Exception as e:  # pragma: no cover
        print(f"[experiment1.seed_actions] import failed: {e}", file=sys.stderr)
        return 1

    if hasattr(seed_actions_simple, "main"):
        seed_actions_simple.main()
        return 0

    # 後備：手動觸發 module-level body（若該腳本未提供 main）
    print(
        "[experiment1.seed_actions] tests.seed_actions_with_embeddings.seed_actions_simple "
        "沒有提供 main()；請改執行 `python -m tests.seed_actions_with_embeddings.seed_actions_simple`",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
