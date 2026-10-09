"""
Navigation Scenario 測試案例定義（60 個案例）

依據評估設計：
- single_target : 20 例（單目標導航）
- constrained  : 20 例（限制式導航：避開擁擠／封閉／改道）
- multi_step   : 20 例（多步驟導航）

每個案例會在任務進度 **30% 或 60%** 時注入一項動態事件
（人潮壅塞 / 區域關閉 / 改道）。

每類各 10 個基礎模板，分別於 30% / 60% 注入 → 20 案。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Iterable
import json

from navigation_scenario.config import (
    EVENT_KIND_CROWD,
    EVENT_KIND_CLOSURE,
    EVENT_KIND_DETOUR,
)


Category = Literal["single_target", "constrained", "multi_step"]
TargetKind = Literal["zone", "booth", "poi"]


# =============================================================================
# 案例資料結構
# =============================================================================
@dataclass(frozen=True)
class Target:
    """一個導航目標。"""
    id: str
    kind: TargetKind
    label: str


@dataclass(frozen=True)
class DynamicEvent:
    """注入的動態事件。"""
    kind: str                 # EVENT_KIND_*
    target: Any               # Zone 名稱 / Booth id / {"from","to"}
    injection_pct: int        # 30 or 60
    ttl_samples: int = 3      # 持續幾次 sample
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TestCase:
    # 防止 pytest 把這個 dataclass 當成測試類別蒐集
    __test__ = False

    case_id: str
    category: Category
    request: str
    start: str                # POI id（一般 P_Entrance）
    targets: tuple[Target, ...]
    constraints: tuple[str, ...]    # "avoid_crowded" / "avoid_closed" / "avoid_blocked"
    event: DynamicEvent
    expected_replan: bool           # 注入後是否預期需要重規劃
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # 把 frozen tuple 轉成 list 方便 JSON
        d["targets"] = [asdict(t) for t in self.targets]
        d["event"] = asdict(self.event)
        d["constraints"] = list(self.constraints)
        return d


# =============================================================================
# 基礎目標／組合素材
# =============================================================================
# 單目標：10 個典型目的地（zone / booth / poi 混合）
_SINGLE_TARGETS: list[tuple[Target, str]] = [
    (Target("AI_Tech_Area",  "zone",  "AI 展區"),        "請帶我去 AI 展區"),
    (Target("Robotics_Area", "zone",  "機器人展區"),     "我想去機器人展區"),
    (Target("Gaming_Area",   "zone",  "遊戲展區"),       "請引導我到遊戲展區"),
    (Target("VR_Area",       "zone",  "VR 展區"),        "帶我去 VR 展區"),
    (Target("IoT_Area",      "zone",  "IoT 展區"),       "我要去 IoT 展區"),
    (Target("Biotech_Area",  "zone",  "生技展區"),       "請帶我到生技展區"),
    (Target("B_AI1",         "booth", "TechCorp AI"),    "請帶我去 TechCorp AI 攤位"),
    (Target("B_VR1",         "booth", "VirtualReality+"), "請帶我到 VirtualReality+ 攤位"),
    (Target("P_Cafe",        "poi",   "咖啡廳"),         "請帶我去咖啡廳"),
    (Target("P_Restroom_N",  "poi",   "北側洗手間"),     "請帶我去最近的洗手間"),
]

# 限制式：10 組（target + constraint + 對應事件）
_CONSTRAINED_TEMPLATES: list[tuple[Target, tuple[str, ...], str, str, Any]] = [
    # (target, constraints, request_text, event_kind, event_target)
    (
        Target("AI_Tech_Area", "zone", "AI 展區"),
        ("avoid_crowded",),
        "請帶我去 AI 展區，避開人潮擁擠的區域",
        EVENT_KIND_CROWD,
        "AI_Tech_Area",
    ),
    (
        Target("Robotics_Area", "zone", "機器人展區"),
        ("avoid_crowded",),
        "我想去機器人展區，請避開人多的地方",
        EVENT_KIND_CROWD,
        "Robotics_Area",
    ),
    (
        Target("VR_Area", "zone", "VR 展區"),
        ("avoid_closed",),
        "帶我去 VR 展區，避開封閉的區域",
        EVENT_KIND_CLOSURE,
        "Gaming_Area",
    ),
    (
        Target("B_IoT1", "booth", "IoT Solutions"),
        ("avoid_closed",),
        "請引導我到 IoT Solutions 攤位，避開封閉區",
        EVENT_KIND_CLOSURE,
        "Robotics_Area",
    ),
    (
        Target("Biotech_Area", "zone", "生技展區"),
        ("avoid_blocked",),
        "請帶我到生技展區，繞過被封鎖的通道",
        EVENT_KIND_DETOUR,
        {"from": "P_Entrance", "to": "P_Bio1"},
    ),
    (
        Target("B_GM1", "booth", "GameStudio X"),
        ("avoid_blocked",),
        "請帶我去 GameStudio X 攤位，避開不通的路",
        EVENT_KIND_DETOUR,
        {"from": "P_Info", "to": "P_South_Hub"},
    ),
    (
        Target("Gaming_Area", "zone", "遊戲展區"),
        ("avoid_crowded",),
        "請引導我前往遊戲展區，避開擁擠區",
        EVENT_KIND_CROWD,
        "Gaming_Area",
    ),
    (
        Target("Startup_Area", "zone", "新創展區"),
        ("avoid_closed",),
        "請帶我去新創展區，避免封閉的區域",
        EVENT_KIND_CLOSURE,
        "Startup_Area",
    ),
    (
        Target("P_Cafe", "poi", "咖啡廳"),
        ("avoid_crowded", "avoid_blocked"),
        "請帶我去咖啡廳，避免擁擠和不通的路",
        EVENT_KIND_DETOUR,
        {"from": "B_VR1", "to": "P_Cafe"},
    ),
    (
        Target("IoT_Area", "zone", "IoT 展區"),
        ("avoid_closed",),
        "請帶我到 IoT 展區，繞過封閉的區域",
        EVENT_KIND_CLOSURE,
        "IoT_Area",
    ),
]

# 多步驟：10 組（含 2-step 與 3-step）
_MULTI_STEP_TEMPLATES: list[tuple[tuple[Target, ...], tuple[str, ...], str, str, Any]] = [
    (
        (
            Target("Robotics_Area", "zone", "機器人展區"),
            Target("AI_Tech_Area", "zone", "AI 展區"),
        ),
        ("avoid_closed",),
        "請先帶我到機器人展區，接著前往 AI 展區，避開封閉區域",
        EVENT_KIND_CLOSURE,
        "AI_Tech_Area",
    ),
    (
        (
            Target("AI_Tech_Area", "zone", "AI 展區"),
            Target("VR_Area", "zone", "VR 展區"),
        ),
        ("avoid_crowded",),
        "請先帶我到 AI 展區，再到 VR 展區，避開人潮",
        EVENT_KIND_CROWD,
        "VR_Area",
    ),
    (
        (
            Target("Gaming_Area", "zone", "遊戲展區"),
            Target("Food_Court", "zone", "美食區"),
        ),
        ("avoid_blocked",),
        "先帶我到遊戲展區，然後去美食區",
        EVENT_KIND_DETOUR,
        {"from": "B_GM2", "to": "B_VR1"},
    ),
    (
        (
            Target("Biotech_Area", "zone", "生技展區"),
            Target("IoT_Area", "zone", "IoT 展區"),
        ),
        ("avoid_closed",),
        "我想先參觀生技展區，再去 IoT 展區",
        EVENT_KIND_CLOSURE,
        "IoT_Area",
    ),
    (
        (
            Target("Startup_Area", "zone", "新創展區"),
            Target("Gaming_Area", "zone", "遊戲展區"),
            Target("Food_Court", "zone", "美食區"),
        ),
        ("avoid_crowded",),
        "請帶我依序去新創展區、遊戲展區、美食區，避開人潮",
        EVENT_KIND_CROWD,
        "Gaming_Area",
    ),
    (
        (
            Target("AI_Tech_Area", "zone", "AI 展區"),
            Target("Robotics_Area", "zone", "機器人展區"),
            Target("Exit_Hall", "zone", "出口"),
        ),
        ("avoid_closed",),
        "請帶我先看 AI 展區，接著看機器人展區，最後到出口",
        EVENT_KIND_CLOSURE,
        "Robotics_Area",
    ),
    (
        (
            Target("B_RB1", "booth", "Robotics Inc"),
            Target("B_VR1", "booth", "VirtualReality+"),
        ),
        ("avoid_blocked",),
        "請帶我先去 Robotics Inc，然後去 VirtualReality+",
        EVENT_KIND_DETOUR,
        {"from": "B_IoT2", "to": "B_VR1"},
    ),
    (
        (
            Target("P_Restroom_N", "poi", "北側洗手間"),
            Target("B_AI1", "booth", "TechCorp AI"),
        ),
        ("avoid_crowded",),
        "請先帶我去最近的洗手間，再帶我去 TechCorp AI 攤位",
        EVENT_KIND_CROWD,
        "AI_Tech_Area",
    ),
    (
        (
            Target("VR_Area", "zone", "VR 展區"),
            Target("P_Cafe", "poi", "咖啡廳"),
            Target("P_Exit", "poi", "出口"),
        ),
        ("avoid_closed", "avoid_blocked"),
        "請依序帶我去 VR 展區、咖啡廳，最後到出口",
        EVENT_KIND_CLOSURE,
        "Food_Court",
    ),
    (
        (
            Target("Biotech_Area", "zone", "生技展區"),
            Target("AI_Tech_Area", "zone", "AI 展區"),
            Target("Robotics_Area", "zone", "機器人展區"),
        ),
        ("avoid_crowded",),
        "請帶我依序去生技展區、AI 展區、機器人展區，避開擁擠",
        EVENT_KIND_CROWD,
        "AI_Tech_Area",
    ),
]


# =============================================================================
# 生成 60 個案例
# =============================================================================
def _zero_pad(n: int) -> str:
    return f"{n:02d}"


def _expand_with_injection_pcts(
    base: list[tuple[str, Target | tuple[Target, ...], tuple[str, ...], str, str, Any]],
    category: Category,
) -> list[TestCase]:
    """共用展開：每個模板 × (30%, 60%) → 2 個案例。"""
    out: list[TestCase] = []
    for i, (prefix, tgt_field, constraints, request, event_kind, event_target) in enumerate(
        base, start=1
    ):
        if isinstance(tgt_field, Target):
            targets: tuple[Target, ...] = (tgt_field,)
        else:
            targets = tgt_field

        expected_replan = _decide_expected_replan(category, constraints, event_kind)

        for pct in (30, 60):
            case_id = f"{prefix}_{_zero_pad(i)}_{pct}"
            out.append(
                TestCase(
                    case_id=case_id,
                    category=category,
                    request=request,
                    start="P_Entrance",
                    targets=targets,
                    constraints=constraints,
                    event=DynamicEvent(
                        kind=event_kind,
                        target=event_target,
                        injection_pct=pct,
                        ttl_samples=4,
                    ),
                    expected_replan=expected_replan,
                )
            )
    return out


def _decide_expected_replan(
    category: Category, constraints: tuple[str, ...], event_kind: str
) -> bool:
    """注入後是否預期會觸發 replan。

    規則：
    - area_closure / route_detour：路徑可能被阻斷 → 一定需要 replan
    - crowd_congestion：只在有 avoid_crowded 限制時才需要 replan
    """
    if event_kind in (EVENT_KIND_CLOSURE, EVENT_KIND_DETOUR):
        return True
    if event_kind == EVENT_KIND_CROWD:
        return "avoid_crowded" in constraints
    return False


def _build_single_target_cases_v2() -> list[TestCase]:
    """重寫的單目標生成：10 模板 × 2 注入時機 = 20 案。"""
    base: list[tuple[str, Target | tuple[Target, ...], tuple[str, ...], str, str, Any]] = []

    # 為每個 target 指定影響其「目標 zone」的事件
    from navigation_scenario.config import BOOTHS, POIS

    def _zone_of(t: Target) -> str:
        if t.kind == "zone":
            return t.id
        if t.kind == "booth":
            return next(b["zone"] for b in BOOTHS if b["id"] == t.id)
        return next(p["zone"] for p in POIS if p["id"] == t.id)

    for i, (tgt, request) in enumerate(_SINGLE_TARGETS):
        # 交替使用 crowd / closure，讓兩種事件各佔一半
        event_kind = EVENT_KIND_CROWD if i % 2 == 0 else EVENT_KIND_CLOSURE
        base.append(
            (
                "single",
                tgt,
                (),  # single_target 不帶限制
                request,
                event_kind,
                _zone_of(tgt),
            )
        )
    return _expand_with_injection_pcts(base, "single_target")


def _build_constrained_cases() -> list[TestCase]:
    base: list[tuple[str, Target | tuple[Target, ...], tuple[str, ...], str, str, Any]] = []
    for tgt, constraints, request, event_kind, event_target in _CONSTRAINED_TEMPLATES:
        base.append(
            (
                "constrained",
                tgt,
                constraints,
                request,
                event_kind,
                event_target,
            )
        )
    return _expand_with_injection_pcts(base, "constrained")


def _build_multi_step_cases() -> list[TestCase]:
    base: list[tuple[str, Target | tuple[Target, ...], tuple[str, ...], str, str, Any]] = []
    for targets, constraints, request, event_kind, event_target in _MULTI_STEP_TEMPLATES:
        base.append(
            (
                "multistep",
                targets,
                constraints,
                request,
                event_kind,
                event_target,
            )
        )
    return _expand_with_injection_pcts(base, "multi_step")


# =============================================================================
# 公開 API
# =============================================================================
def all_cases() -> list[TestCase]:
    """回傳所有 60 個測試案例。"""
    cases = (
        _build_single_target_cases_v2()
        + _build_constrained_cases()
        + _build_multi_step_cases()
    )
    return cases


def cases_by_category(category: Category) -> list[TestCase]:
    return [c for c in all_cases() if c.category == category]


def case_by_id(case_id: str) -> TestCase | None:
    for c in all_cases():
        if c.case_id == case_id:
            return c
    return None


def filter_cases(
    *,
    category: Category | None = None,
    injection_pct: int | None = None,
    event_kind: str | None = None,
) -> list[TestCase]:
    out = all_cases()
    if category:
        out = [c for c in out if c.category == category]
    if injection_pct is not None:
        out = [c for c in out if c.event.injection_pct == injection_pct]
    if event_kind:
        out = [c for c in out if c.event.kind == event_kind]
    return out


def dump_cases_json(path: str) -> None:
    data = [c.to_dict() for c in all_cases()]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def summary() -> dict[str, int]:
    cs = all_cases()
    return {
        "total": len(cs),
        "single_target": sum(1 for c in cs if c.category == "single_target"),
        "constrained": sum(1 for c in cs if c.category == "constrained"),
        "multi_step": sum(1 for c in cs if c.category == "multi_step"),
        "inject_30pct": sum(1 for c in cs if c.event.injection_pct == 30),
        "inject_60pct": sum(1 for c in cs if c.event.injection_pct == 60),
        "event_crowd": sum(1 for c in cs if c.event.kind == EVENT_KIND_CROWD),
        "event_closure": sum(1 for c in cs if c.event.kind == EVENT_KIND_CLOSURE),
        "event_detour": sum(1 for c in cs if c.event.kind == EVENT_KIND_DETOUR),
    }


if __name__ == "__main__":
    import sys

    s = summary()
    print("=== Navigation Scenario 測試案例摘要 ===")
    for k, v in s.items():
        print(f"  {k:15s} : {v}")

    if "--dump" in sys.argv:
        dump_cases_json("navigation_scenario_cases.json")
        print("\n已輸出 navigation_scenario_cases.json")
