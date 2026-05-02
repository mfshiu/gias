"""
Experiment 1：60 個測試案例（3 組 × 20）

每個案例包含：
  id, group, intention, start_poi, goal_zones (按順序), constraints, expected_actions

groups:
  - single_goal       : 單一目標導航（無限制）
  - constraint_based  : 含限制（避開擁擠 / 改道）
  - multi_step        : 多步驟順序導航（含限制）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from experiment1.config import ZONE_LABELS


@dataclass(frozen=True)
class TestCase:
    id: str
    group: str
    intention: str
    start_poi: str
    goal_zones: tuple[str, ...]
    constraints: tuple[str, ...] = field(default_factory=tuple)
    expected_actions: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


# Helper：依 zone 自然名生成一個導航問句
def _nl(zone: str) -> str:
    return ZONE_LABELS.get(zone, zone)


# -----------------------------------------------------------------------------
# Group A：Single-Goal Navigation（20 例）
# Example: "Guide me to the AI exhibition area."
# 主要 metrics: TSR, PE
# -----------------------------------------------------------------------------
SINGLE_GOAL_TARGETS = [
    "AI_Tech_Area",
    "Robotics_Area",
    "Gaming_Area",
    "VR_Area",
    "Exit_Hall",
]

SINGLE_GOAL_TEMPLATES = [
    "請帶我去{label}。",
    "我想前往{label}，引導我過去。",
    "從目前位置帶領我到{label}。",
    "請導航到{label}。",
]


def _make_single_goal() -> list[TestCase]:
    cases: list[TestCase] = []
    idx = 1
    for tpl in SINGLE_GOAL_TEMPLATES:
        for zone in SINGLE_GOAL_TARGETS:
            cid = f"SG-{idx:02d}"
            cases.append(
                TestCase(
                    id=cid,
                    group="single_goal",
                    intention=tpl.format(label=_nl(zone)),
                    start_poi="P_Entrance",
                    goal_zones=(zone,),
                    constraints=(),
                    expected_actions=("LocateExhibit", "NavigationAssistance"),
                    notes="single-goal navigation",
                )
            )
            idx += 1
            if idx > 20:
                return cases
    return cases


# -----------------------------------------------------------------------------
# Group B：Constraint-Based Navigation（20 例）
# Example: "Guide me to the AI exhibition area while avoiding crowds."
# 主要 metrics: TSR, ISR, RSR
# -----------------------------------------------------------------------------
CONSTRAINT_TEMPLATES = [
    ("請帶我去{label}，避開擁擠的區域。", ("avoid_crowded",)),
    ("我想前往{label}，幫我規劃避開人潮的路線。", ("avoid_crowded",)),
    ("引導我到{label}，避免人多的地方。", ("avoid_crowded",)),
    ("請帶我去{label}，避免封閉區域。", ("avoid_closed",)),
]


def _make_constraint() -> list[TestCase]:
    cases: list[TestCase] = []
    idx = 1
    for tpl, cons in CONSTRAINT_TEMPLATES:
        for zone in SINGLE_GOAL_TARGETS:
            cid = f"CB-{idx:02d}"
            cases.append(
                TestCase(
                    id=cid,
                    group="constraint_based",
                    intention=tpl.format(label=_nl(zone)),
                    start_poi="P_Entrance",
                    goal_zones=(zone,),
                    constraints=cons,
                    expected_actions=("LocateExhibit", "SuggestRoute", "NavigationAssistance"),
                    notes="constraint-based navigation",
                )
            )
            idx += 1
            if idx > 20:
                return cases
    return cases


# -----------------------------------------------------------------------------
# Group C：Multi-Step Navigation（20 例）
# Example: "Go to the robotics exhibition area first, then the AI exhibition
#          area, while avoiding closed areas."
# 主要 metrics: ISR, RSR, PE
# -----------------------------------------------------------------------------
MULTI_STEP_PAIRS = [
    ("Robotics_Area", "AI_Tech_Area"),
    ("Gaming_Area", "VR_Area"),
    ("AI_Tech_Area", "Gaming_Area"),
    ("VR_Area", "Robotics_Area"),
    ("Gaming_Area", "AI_Tech_Area"),
]

MULTI_STEP_TEMPLATES = [
    ("先帶我去{a}，再去{b}，並且避開封閉區域。", ("avoid_closed",)),
    ("我想先參觀{a}，接著去{b}，幫我規劃路線並避開人潮。", ("avoid_crowded",)),
    ("請先去{a}然後去{b}，避免擁擠和封閉的區域。", ("avoid_crowded", "avoid_closed")),
    ("從入口先到{a}再到{b}，請規劃最佳路線。", ()),
]


def _make_multi_step() -> list[TestCase]:
    cases: list[TestCase] = []
    idx = 1
    for tpl, cons in MULTI_STEP_TEMPLATES:
        for a_zone, b_zone in MULTI_STEP_PAIRS:
            cid = f"MS-{idx:02d}"
            cases.append(
                TestCase(
                    id=cid,
                    group="multi_step",
                    intention=tpl.format(a=_nl(a_zone), b=_nl(b_zone)),
                    start_poi="P_Entrance",
                    goal_zones=(a_zone, b_zone),
                    constraints=cons,
                    expected_actions=(
                        "LocateExhibit",
                        "SuggestRoute",
                        "NavigationAssistance",
                    ),
                    notes="multi-step navigation",
                )
            )
            idx += 1
            if idx > 20:
                return cases
    return cases


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def build_test_cases() -> list[TestCase]:
    cases = _make_single_goal() + _make_constraint() + _make_multi_step()
    assert len(cases) == 60, f"Expected 60 cases, got {len(cases)}"
    return cases


def cases_by_group(group: str) -> list[TestCase]:
    return [c for c in build_test_cases() if c.group == group]


def filter_cases(cases: Iterable[TestCase], *, limit: int | None = None) -> list[TestCase]:
    out = list(cases)
    if limit is not None and limit > 0:
        out = out[:limit]
    return out


if __name__ == "__main__":
    for c in build_test_cases():
        print(f"{c.id} [{c.group:>16s}] {c.intention}  → goals={c.goal_zones} cons={c.constraints}")
    print(f"\nTotal: {len(build_test_cases())} cases")
