# tests/observer/test_observer_agent.py
"""ObserverAgent 單元測試"""

import pytest
import time
from unittest.mock import MagicMock, patch

from src.observer.observation import (
    Observation,
    ObservationType,
    SaliencyLevel,
    Entity,
)
from src.observer.base import ObserverAgent, SaliencyFilter


class TestSaliencyFilter:
    """重要性過濾器測試"""

    def test_should_report_low_saliency(self):
        """低重要性不應上報"""
        sf = SaliencyFilter(min_saliency=SaliencyLevel.MODERATE)
        
        obs = Observation.create(
            observer_id="test",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.LOW,
        )
        
        assert not sf.should_report(obs)

    def test_should_report_high_saliency(self):
        """高重要性應該上報"""
        sf = SaliencyFilter(min_saliency=SaliencyLevel.MODERATE)
        
        obs = Observation.create(
            observer_id="test",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.HIGH,
        )
        
        assert sf.should_report(obs)

    def test_cooldown_filtering(self):
        """冷卻時間內不應重複上報"""
        sf = SaliencyFilter(min_saliency=SaliencyLevel.MODERATE, cooldown_sec=1.0)
        
        obs = Observation.create(
            observer_id="test",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.HIGH,
        )
        
        assert sf.should_report(obs)
        assert not sf.should_report(obs)

    def test_cooldown_expired(self):
        """冷卻時間過後可以再次上報"""
        sf = SaliencyFilter(min_saliency=SaliencyLevel.LOW, cooldown_sec=0.1)
        
        obs = Observation.create(
            observer_id="test",
            observation_type=ObservationType.VISUAL,
            saliency=SaliencyLevel.MODERATE,
        )
        
        assert sf.should_report(obs)
        time.sleep(0.15)
        assert sf.should_report(obs)

    def test_state_change_detection(self):
        """狀態變化偵測"""
        sf = SaliencyFilter()
        
        assert sf.update_state("key1", "value_a")  # first time
        assert not sf.update_state("key1", "value_a")  # no change
        assert sf.update_state("key1", "value_b")  # changed

    def test_compute_saliency_basic(self):
        """基本重要性計算"""
        sf = SaliencyFilter()
        
        saliency = sf.compute_saliency(
            entity_count=0,
            relation_count=0,
            state_changed=False,
            confidence=1.0,
        )
        
        assert saliency == SaliencyLevel.TRIVIAL

    def test_compute_saliency_with_entities(self):
        """有實體時提高重要性"""
        sf = SaliencyFilter()
        
        saliency = sf.compute_saliency(
            entity_count=5,
            relation_count=2,
            state_changed=True,
            confidence=0.9,
        )
        
        assert saliency >= SaliencyLevel.MODERATE

    def test_compute_saliency_urgency_keywords(self):
        """緊急關鍵字提高重要性"""
        sf = SaliencyFilter()
        
        # 無緊急關鍵字：基礎分 1.0 + entity 0.3 = 1.3 -> TRIVIAL
        saliency_without = sf.compute_saliency(
            entity_count=1,
            urgency_keywords=[],
            raw_description="偵測到一般情況",
        )
        
        # 有緊急關鍵字：基礎分 1.0 + entity 0.3 + keyword 1.0 = 2.3 -> LOW
        saliency_with = sf.compute_saliency(
            entity_count=1,
            urgency_keywords=["緊急", "危險"],
            raw_description="偵測到危險情況",
        )
        
        # 緊急關鍵字應提高重要性
        assert saliency_with > saliency_without
        
        # 更多 entities + 狀態變化 + 緊急關鍵字 -> MODERATE 以上
        saliency_combined = sf.compute_saliency(
            entity_count=3,
            state_changed=True,
            urgency_keywords=["緊急", "危險"],
            raw_description="偵測到危險情況",
        )
        assert saliency_combined >= SaliencyLevel.MODERATE


class ConcreteObserver(ObserverAgent):
    """用於測試的具體觀察者實作"""
    
    def __init__(self, *args, **kwargs):
        self._mock_observations = []
        self._observe_call_count = 0
        super().__init__(*args, **kwargs)
    
    def get_observation_type(self) -> ObservationType:
        return ObservationType.VISUAL
    
    def observe(self):
        self._observe_call_count += 1
        if self._mock_observations:
            return self._mock_observations.pop(0)
        return None
    
    def set_mock_observations(self, observations):
        self._mock_observations = list(observations)


class TestObserverAgentBase:
    """ObserverAgent 基類測試"""

    @pytest.fixture
    def mock_config(self):
        return {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
            },
        }

    def test_observer_id_generation(self, mock_config):
        """觀察者 ID 自動生成"""
        with patch.object(ObserverAgent, '__init__', lambda self, *args, **kwargs: None):
            obs = ConcreteObserver.__new__(ConcreteObserver)
            obs._observer_id = "test_12345678"
            assert obs.observer_id == "test_12345678"

    def test_create_observation_helper(self, mock_config):
        """create_observation 便利方法"""
        with patch.object(ObserverAgent, '__init__', lambda self, *args, **kwargs: None):
            obs_agent = ConcreteObserver.__new__(ConcreteObserver)
            obs_agent._observer_id = "visual_observer_test"
            obs_agent.saliency_filter = SaliencyFilter()
            
            with patch.object(obs_agent, 'get_observation_type', return_value=ObservationType.VISUAL):
                with patch.object(obs_agent, 'get_urgency_keywords', return_value=[]):
                    observation = obs_agent.create_observation(
                        entities=[Entity(entity_type="Person", label="Test")],
                        raw_description="Test observation",
                        confidence=0.9,
                    )
            
            assert observation.observer_id == "visual_observer_test"
            assert observation.observation_type == ObservationType.VISUAL
            assert len(observation.entities) == 1

    def test_create_observation_with_explicit_saliency(self, mock_config):
        """使用明確指定的重要性"""
        with patch.object(ObserverAgent, '__init__', lambda self, *args, **kwargs: None):
            obs_agent = ConcreteObserver.__new__(ConcreteObserver)
            obs_agent._observer_id = "test"
            obs_agent.saliency_filter = SaliencyFilter()
            
            with patch.object(obs_agent, 'get_observation_type', return_value=ObservationType.DIGITAL):
                with patch.object(obs_agent, 'get_urgency_keywords', return_value=[]):
                    observation = obs_agent.create_observation(
                        raw_description="Critical alert",
                        saliency=SaliencyLevel.CRITICAL,
                    )
            
            assert observation.saliency == SaliencyLevel.CRITICAL

    def test_urgency_keywords_default(self, mock_config):
        """預設緊急關鍵字"""
        with patch.object(ObserverAgent, '__init__', lambda self, *args, **kwargs: None):
            obs_agent = ConcreteObserver.__new__(ConcreteObserver)
            obs_agent._observer_id = "test"
            
            with patch.object(ObserverAgent, 'get_urgency_keywords', return_value=["緊急", "error"]):
                keywords = ObserverAgent.get_urgency_keywords(obs_agent)
            
            assert "緊急" in keywords
            assert "error" in keywords


class TestControlCommands:
    """控制命令測試"""

    @pytest.fixture
    def mock_observer(self):
        with patch.object(ObserverAgent, '__init__', lambda self, *args, **kwargs: None):
            obs = ConcreteObserver.__new__(ConcreteObserver)
            obs._observer_id = "test_observer"
            obs._poll_interval = 1.0
            obs._running = True
            obs.saliency_filter = SaliencyFilter(min_saliency=SaliencyLevel.MODERATE)
            return obs

    def test_set_saliency_command(self, mock_observer):
        """設定重要性門檻命令"""
        result = mock_observer._handle_control(
            "observer.test.control",
            {"command": "set_saliency", "level": 4}
        )
        
        assert result["ok"]
        assert result["min_saliency"] == 4
        assert mock_observer.saliency_filter.min_saliency == SaliencyLevel.HIGH

    def test_set_interval_command(self, mock_observer):
        """設定輪詢間隔命令"""
        result = mock_observer._handle_control(
            "observer.test.control",
            {"command": "set_interval", "interval": 2.5}
        )
        
        assert result["ok"]
        assert result["poll_interval"] == 2.5
        assert mock_observer._poll_interval == 2.5

    def test_status_command(self, mock_observer):
        """取得狀態命令"""
        with patch.object(mock_observer, 'get_observation_type', return_value=ObservationType.VISUAL):
            result = mock_observer._handle_control(
                "observer.test.control",
                {"command": "status"}
            )
        
        assert result["ok"]
        assert result["observer_id"] == "test_observer"
        assert result["observation_type"] == "visual"
        assert result["running"] is True

    def test_unknown_command(self, mock_observer):
        """未知命令"""
        result = mock_observer._handle_control(
            "observer.test.control",
            {"command": "unknown_cmd"}
        )
        
        assert not result["ok"]
        assert "Unknown command" in result["error"]

    def test_handle_parcel_payload(self, mock_observer):
        """處理 Parcel 包裝的 payload"""
        mock_parcel = MagicMock()
        mock_parcel.content = {"command": "status"}
        
        with patch.object(mock_observer, 'get_observation_type', return_value=ObservationType.AUDIO):
            result = mock_observer._handle_control(
                "observer.test.control",
                mock_parcel
            )
        
        assert result["ok"]
        assert result["observation_type"] == "audio"
