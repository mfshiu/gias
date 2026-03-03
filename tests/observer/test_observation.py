# tests/observer/test_observation.py
"""觀察結果資料結構單元測試"""

import pytest
import json
from datetime import datetime, timezone

from src.observer.observation import (
    Observation,
    ObservationType,
    SaliencyLevel,
    Entity,
    Relation,
)


class TestEntity:
    """Entity 資料結構測試"""

    def test_create_entity(self):
        entity = Entity(
            entity_type="Person",
            label="訪客A",
            entity_id="visitor_001",
            properties={"age_group": "adult"},
            confidence=0.95,
        )
        assert entity.entity_type == "Person"
        assert entity.label == "訪客A"
        assert entity.entity_id == "visitor_001"
        assert entity.confidence == 0.95
        assert entity.properties["age_group"] == "adult"

    def test_entity_to_dict(self):
        entity = Entity(
            entity_type="Object",
            label="展品",
            confidence=0.8,
        )
        d = entity.to_dict()
        assert d["entity_type"] == "Object"
        assert d["label"] == "展品"
        assert d["confidence"] == 0.8
        assert d["entity_id"] is None

    def test_entity_from_dict(self):
        data = {
            "entity_type": "Zone",
            "label": "入口區",
            "entity_id": "zone_entrance",
            "properties": {"capacity": 50},
            "confidence": 1.0,
        }
        entity = Entity.from_dict(data)
        assert entity.entity_type == "Zone"
        assert entity.label == "入口區"
        assert entity.properties["capacity"] == 50

    def test_confidence_validation(self):
        with pytest.raises(ValueError):
            Entity(entity_type="Test", label="test", confidence=1.5)
        
        with pytest.raises(ValueError):
            Entity(entity_type="Test", label="test", confidence=-0.1)


class TestRelation:
    """Relation 資料結構測試"""

    def test_create_relation(self):
        source = Entity(entity_type="Person", label="訪客")
        target = Entity(entity_type="Zone", label="展區A")
        
        relation = Relation(
            relation_type="LOCATED_IN",
            source=source,
            target=target,
            confidence=0.9,
        )
        
        assert relation.relation_type == "LOCATED_IN"
        assert relation.source.label == "訪客"
        assert relation.target.label == "展區A"

    def test_relation_to_dict(self):
        source = Entity(entity_type="Robot", label="GuideBot")
        target = Entity(entity_type="Person", label="訪客")
        
        relation = Relation(
            relation_type="INTERACTS_WITH",
            source=source,
            target=target,
            properties={"interaction_type": "greeting"},
        )
        
        d = relation.to_dict()
        assert d["relation_type"] == "INTERACTS_WITH"
        assert d["source"]["label"] == "GuideBot"
        assert d["target"]["label"] == "訪客"
        assert d["properties"]["interaction_type"] == "greeting"


class TestSaliencyLevel:
    """重要性等級測試"""

    def test_saliency_ordering(self):
        assert SaliencyLevel.TRIVIAL < SaliencyLevel.LOW
        assert SaliencyLevel.LOW < SaliencyLevel.MODERATE
        assert SaliencyLevel.MODERATE < SaliencyLevel.HIGH
        assert SaliencyLevel.HIGH < SaliencyLevel.CRITICAL

    def test_saliency_values(self):
        assert int(SaliencyLevel.TRIVIAL) == 1
        assert int(SaliencyLevel.CRITICAL) == 5


class TestObservation:
    """Observation 完整觀察結果測試"""

    def test_create_observation(self):
        obs = Observation.create(
            observer_id="visual_observer_001",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.HIGH,
            confidence=0.85,
            raw_description="偵測到 3 位訪客進入展區",
        )
        
        assert obs.observer_id == "visual_observer_001"
        assert obs.observation_type == ObservationType.VISUAL
        assert obs.saliency == SaliencyLevel.HIGH
        assert obs.confidence == 0.85
        assert "3 位訪客" in obs.raw_description
        assert obs.observation_id  # should be auto-generated

    def test_observation_with_entities(self):
        entities = [
            Entity(entity_type="Person", label="訪客1", confidence=0.9),
            Entity(entity_type="Person", label="訪客2", confidence=0.85),
        ]
        
        obs = Observation.create(
            observer_id="visual_observer",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.MODERATE,
            entities=entities,
        )
        
        assert len(obs.entities) == 2
        assert obs.entities[0].label == "訪客1"

    def test_observation_to_dict(self):
        obs = Observation.create(
            observer_id="audio_observer",
            observation_type=ObservationType.AUDIO,
            saliency=SaliencyLevel.HIGH,
            confidence=0.95,
            raw_description="偵測到語音指令",
            metadata={"language": "zh-TW"},
        )
        
        d = obs.to_dict()
        
        assert d["observer_id"] == "audio_observer"
        assert d["observation_type"] == "audio"
        assert d["saliency"] == 4
        assert d["confidence"] == 0.95
        assert d["metadata"]["language"] == "zh-TW"

    def test_observation_json_roundtrip(self):
        original = Observation.create(
            observer_id="digital_observer",
            observation_type=ObservationType.DIGITAL,
            saliency=SaliencyLevel.CRITICAL,
            entities=[Entity(entity_type="Metric", label="CPU", confidence=1.0)],
            raw_description="CPU 使用率超過 95%",
        )
        
        json_str = original.to_json()
        restored = Observation.from_json(json_str)
        
        assert restored.observation_id == original.observation_id
        assert restored.observer_id == original.observer_id
        assert restored.saliency == original.saliency
        assert len(restored.entities) == 1
        assert restored.entities[0].label == "CPU"

    def test_is_significant(self):
        low_obs = Observation.create(
            observer_id="test",
            observation_type=ObservationType.SYSTEM,
            saliency=SaliencyLevel.LOW,
        )
        
        high_obs = Observation.create(
            observer_id="test",
            observation_type=ObservationType.SYSTEM,
            saliency=SaliencyLevel.HIGH,
        )
        
        assert not low_obs.is_significant(min_saliency=SaliencyLevel.MODERATE)
        assert high_obs.is_significant(min_saliency=SaliencyLevel.MODERATE)

    def test_merge_entities(self):
        obs1 = Observation.create(
            observer_id="visual",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.MODERATE,
            entities=[Entity(entity_type="Person", label="A", entity_id="p1")],
        )
        
        obs2 = Observation.create(
            observer_id="visual",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.MODERATE,
            entities=[
                Entity(entity_type="Person", label="A", entity_id="p1"),  # duplicate
                Entity(entity_type="Person", label="B", entity_id="p2"),  # new
            ],
        )
        
        obs1.merge_entities(obs2)
        
        assert len(obs1.entities) == 2
        labels = [e.label for e in obs1.entities]
        assert "A" in labels
        assert "B" in labels


class TestObservationType:
    """觀察類型測試"""

    def test_observation_types(self):
        assert ObservationType.VISUAL.value == "visual"
        assert ObservationType.AUDIO.value == "audio"
        assert ObservationType.DIGITAL.value == "digital"
        assert ObservationType.SYSTEM.value == "system"
        assert ObservationType.COMPOSITE.value == "composite"
