"""EntityMatcher 單元測試（C-08）：以完整名稱、別名與從屬關係比對，不用子字串。"""

from __future__ import annotations

import pytest

from src.core.monitoring.entity_match import EntityMatcher, mentions_name


@pytest.mark.parametrize("text, name, expected", [
    ("前往 A12 攤位", "A1", False),            # 舊行為：子字串命中
    ("前往 B_A12", "B_A1", False),
    ("前往 A1 攤位", "A1", True),
    ("前往A1攤位", "A1", True),                # 中文緊鄰仍可命中
    ("target_name=A1, type=booth", "A1", True),
    ("['Robotics_Area', 'VR_Area']", "VR_Area", True),
    ("AI_Tech_Area_North", "AI_Tech_Area", False),
    ("ai_tech_area", "AI_Tech_Area", True),    # 大小寫不敏感
    ("請帶我去 AI展區", "AI 展區", True),       # 名稱中的空白可對應零個空白
    ("VirtualReality+ 攤位", "VirtualReality+", True),   # 特殊字元會被跳脫
    ("Robotics Inc", "Robotics", True),
    ("", "A1", False),
    ("A1", "", False),
])
def test_mentions_name(text, name, expected):
    assert mentions_name(text, name) is expected


def test_aliases_and_descendants():
    m = EntityMatcher(
        aliases={"AI_Tech_Area": ["AI 展區"], "B_AI1": ["TechCorp AI"]},
        parents={"B_AI1": "AI_Tech_Area", "B_AI2": "AI_Tech_Area"},
    )
    assert m.mentions("前往 AI 展區", "AI_Tech_Area")
    assert m.mentions("帶我去 TechCorp AI 攤位", "AI_Tech_Area")   # 展區事件影響區內攤位
    assert m.mentions("LocateExhibit B_AI2", "AI_Tech_Area")
    assert not m.mentions("前往 AI 展區", "B_AI1")                 # 攤位事件不影響整個展區
    assert not m.mentions("前往 B_AI12", "AI_Tech_Area")


def test_names_for_handles_cycles():
    m = EntityMatcher(parents={"a": "b", "b": "a"})
    assert sorted(m.names_for("a")) == ["a", "b"]
