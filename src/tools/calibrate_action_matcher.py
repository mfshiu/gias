"""
校準 ActionMatcher 的向量門檻（intent.matching.min_score / fallback_min_score）。

計算「場內意圖」與「場外意圖」對各 Action 描述的最高分數（與 Neo4j 向量索引相同：(1 + cos) / 2），
列出分布並建議 fallback_min_score。只呼叫 embedding API，不需要 Neo4j。

執行：
    python -m src.tools.calibrate_action_matcher
    python -m src.tools.calibrate_action_matcher --actions navigation --in-file in.txt --out-file out.txt
（--in-file / --out-file 為每行一句的意圖，會加在內建範例之後）
"""

from __future__ import annotations

import argparse
import importlib.util
import math
from pathlib import Path

from src.app_helper import get_agent_config
from src.llm.client import LLMClient

ROOT = Path(__file__).resolve().parents[2]
ACTION_SEEDS = {
    "expo": ROOT / "tests" / "seed_actions_with_embeddings" / "seed_actions_simple.py",
    "navigation": ROOT / "navigation_scenario" / "seed_actions.py",
}

IN_DOMAIN = [
    "推薦使用者適合的有趣展區或展品", "安排參觀路線並提供怎麼走的指引", "查詢目前會場的人流狀況",
    "提醒途中洗手間及服務台位置", "引導我前往A12攤位", "介紹智慧導覽眼鏡", "今天幾點閉館",
    "下午有什麼活動", "請帶我去 AI 展區", "避開人潮擁擠的區域前往機器人展區", "怎麼走到 VR 展區",
    "我要去最近的洗手間", "現在遊戲展區人多嗎", "帶我去咖啡廳",
]
OUT_OF_DOMAIN = [
    "我的iPhone 17壞了請幫我修理", "幫我訂一張明天去東京的機票", "今天台北天氣如何",
    "幫我寫一首關於秋天的詩", "推薦幾檔值得買的股票", "幫我把這份合約翻譯成英文",
    "計算 123 乘以 456", "幫我預約下週三看牙醫", "解釋一下量子糾纏是什麼",
]


def _load_actions(path: Path) -> list[tuple[str, str]]:
    spec = importlib.util.spec_from_file_location(f"_seed_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [(a["task"], a["desc"]) for a in module.ACTIONS]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _read_lines(path: str | None) -> list[str]:
    if not path:
        return []
    return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="校準 ActionMatcher 的向量門檻")
    parser.add_argument("--actions", choices=sorted(ACTION_SEEDS), default="expo", help="使用哪一組 Action 描述")
    parser.add_argument("--in-file", help="額外的場內意圖（每行一句）")
    parser.add_argument("--out-file", help="額外的場外意圖（每行一句）")
    args = parser.parse_args()

    llm = LLMClient.from_config(get_agent_config())
    actions = [(task, llm.embed_text(desc)) for task, desc in _load_actions(ACTION_SEEDS[args.actions])]

    def best(text: str) -> tuple[float, str]:
        vec = llm.embed_text(text)
        return max((0.5 * (1 + _cos(vec, av)), task) for task, av in actions)

    groups = {"場內": IN_DOMAIN + _read_lines(args.in_file), "場外": OUT_OF_DOMAIN + _read_lines(args.out_file)}
    scores: dict[str, list[float]] = {}
    for label, intents in groups.items():
        rows = sorted(((*best(text), text) for text in intents), key=lambda r: r[0])
        scores[label] = [r[0] for r in rows]
        print(f"\n-- {label}意圖（最高分數 → action）")
        for score, task, text in rows:
            print(f"   {score:.4f}  {task:<22} {text}")

    lo_in, hi_out = min(scores["場內"]), max(scores["場外"])
    print(f"\n場內最低 {lo_in:.4f}；場外最高 {hi_out:.4f}")
    if lo_in > hi_out:
        print(f"建議 fallback_min_score ≈ {(lo_in + hi_out) / 2:.3f}（兩者中點）")
    else:
        print("兩組分數重疊：無法只靠向量門檻區分，建議 fallback_min_score 取略高於場外的典型分數，"
              "並以 ScopeGate 作為第二道把關")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
