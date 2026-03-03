# tests/observer/test_observers.py
"""各類觀察者（視覺/聽覺/數位）單元測試"""

import pytest
import time
from unittest.mock import patch, MagicMock

from src.observer.observation import (
    ObservationType,
    SaliencyLevel,
)
from src.observer.visual import (
    VisualObserver,
    SimulatedVisualObserver,
    DetectedObject,
    BoundingBox,
)
from src.observer.audio import (
    AudioObserver,
    SimulatedAudioObserver,
    AudioSegment,
    AudioEventType,
    Emotion,
)
from src.observer.digital import (
    DigitalObserver,
    SimulatedDigitalObserver,
    Metric,
    MetricType,
    ApiStatus,
    HealthStatus,
    TrendDirection,
)


class TestVisualObserver:
    """視覺觀察者測試"""

    @pytest.fixture
    def mock_config(self):
        return {"mqtt": {"broker": "localhost", "port": 1883}}

    def test_observation_type(self, mock_config):
        """確認觀察類型為視覺"""
        with patch.object(VisualObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = VisualObserver.__new__(VisualObserver)
            assert observer.get_observation_type() == ObservationType.VISUAL

    def test_bounding_box_center(self):
        """邊界框中心點計算"""
        bbox = BoundingBox(x=100, y=100, width=50, height=30)
        cx, cy = bbox.center
        assert cx == 125.0
        assert cy == 115.0

    def test_detected_object_to_entity(self):
        """偵測物體轉換為實體"""
        obj = DetectedObject(
            label="person",
            confidence=0.95,
            bbox=BoundingBox(x=10, y=20, width=100, height=200),
            object_id="person_001",
            attributes={"gender": "unknown"},
        )
        
        entity = obj.to_entity()
        
        assert entity.entity_type == "Object"
        assert entity.label == "person"
        assert entity.confidence == 0.95
        assert entity.entity_id == "person_001"
        assert "bbox" in entity.properties
        assert entity.properties["gender"] == "unknown"

    def test_simulated_visual_observe(self, mock_config):
        """模擬視覺觀察"""
        with patch.object(SimulatedVisualObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = SimulatedVisualObserver.__new__(SimulatedVisualObserver)
            observer._observer_id = "test_visual"
            observer._min_confidence = 0.5
            observer._track_positions = True
            observer._last_positions = {}
            observer._movement_threshold = 20.0
            observer._simulated_objects = []
            
            from src.observer.base import SaliencyFilter
            observer.saliency_filter = SaliencyFilter()
            
            objects = [
                DetectedObject(
                    label="visitor",
                    confidence=0.9,
                    bbox=BoundingBox(x=100, y=100, width=50, height=100),
                    object_id="v1",
                ),
                DetectedObject(
                    label="exhibit",
                    confidence=0.85,
                    bbox=BoundingBox(x=200, y=150, width=80, height=60),
                    object_id="e1",
                ),
            ]
            observer.set_simulated_objects(objects)
            
            with patch.object(observer, 'get_observation_type', return_value=ObservationType.VISUAL):
                with patch.object(observer, 'get_urgency_keywords', return_value=[]):
                    result = observer.observe()
            
            assert result is not None
            assert len(result.entities) == 2
            assert result.observation_type == ObservationType.VISUAL

    def test_movement_detection(self, mock_config):
        """位移偵測"""
        with patch.object(SimulatedVisualObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = SimulatedVisualObserver.__new__(SimulatedVisualObserver)
            observer._observer_id = "test_visual"
            observer._min_confidence = 0.5
            observer._track_positions = True
            observer._last_positions = {"obj1": (100.0, 100.0)}
            observer._movement_threshold = 20.0
            observer._simulated_objects = []
            
            from src.observer.base import SaliencyFilter
            observer.saliency_filter = SaliencyFilter()
            
            moving_obj = DetectedObject(
                label="person",
                confidence=0.9,
                bbox=BoundingBox(x=150, y=100, width=50, height=100),
                object_id="obj1",
            )
            observer.set_simulated_objects([moving_obj])
            
            with patch.object(observer, 'get_observation_type', return_value=ObservationType.VISUAL):
                with patch.object(observer, 'get_urgency_keywords', return_value=[]):
                    result = observer.observe()
            
            assert result is not None
            assert "movement" in result.entities[0].properties


class TestAudioObserver:
    """聽覺觀察者測試"""

    @pytest.fixture
    def mock_config(self):
        return {"mqtt": {"broker": "localhost", "port": 1883}}

    def test_observation_type(self, mock_config):
        """確認觀察類型為聽覺"""
        with patch.object(AudioObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = AudioObserver.__new__(AudioObserver)
            assert observer.get_observation_type() == ObservationType.AUDIO

    def test_audio_segment_to_entity(self):
        """音訊片段轉換為實體"""
        segment = AudioSegment(
            event_type=AudioEventType.COMMAND,
            start_time=1.5,
            duration=2.0,
            confidence=0.9,
            transcript="帶我去展區A",
            speaker_id="speaker_001",
            emotion=Emotion.NEUTRAL,
        )
        
        entity = segment.to_entity()
        
        assert entity.entity_type == "AudioEvent"
        assert "帶我去展區A" in entity.label
        assert entity.properties["event_type"] == "command"
        assert entity.properties["speaker_id"] == "speaker_001"
        assert entity.properties["emotion"] == "neutral"

    def test_classify_speech_command(self):
        """語音指令分類"""
        with patch.object(AudioObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = AudioObserver.__new__(AudioObserver)
            observer.COMMAND_KEYWORDS = ["導航", "帶我去", "navigate"]
            observer.ALERT_KEYWORDS = ["救命", "緊急", "help"]
            
            assert observer.classify_speech("請帶我去展區B") == AudioEventType.COMMAND
            assert observer.classify_speech("救命啊！") == AudioEventType.ALERT
            assert observer.classify_speech("今天天氣真好") == AudioEventType.SPEECH

    def test_simulated_audio_observe(self, mock_config):
        """模擬聽覺觀察"""
        with patch.object(SimulatedAudioObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = SimulatedAudioObserver.__new__(SimulatedAudioObserver)
            observer._observer_id = "test_audio"
            observer._min_confidence = 0.6
            observer._last_speech_time = 0
            observer._speech_cooldown = 0.1
            observer._simulated_segments = []
            
            from src.observer.base import SaliencyFilter
            observer.saliency_filter = SaliencyFilter()
            
            segments = [
                AudioSegment(
                    event_type=AudioEventType.COMMAND,
                    start_time=0.0,
                    duration=1.5,
                    confidence=0.95,
                    transcript="導航到入口",
                ),
            ]
            observer.set_simulated_segments(segments)
            
            with patch.object(observer, 'get_observation_type', return_value=ObservationType.AUDIO):
                with patch.object(observer, 'get_urgency_keywords', return_value=[]):
                    result = observer.observe()
            
            assert result is not None
            assert result.saliency == SaliencyLevel.HIGH
            assert result.metadata["has_command"] is True

    def test_alert_priority(self, mock_config):
        """警報優先處理"""
        with patch.object(SimulatedAudioObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = SimulatedAudioObserver.__new__(SimulatedAudioObserver)
            observer._observer_id = "test_audio"
            observer._min_confidence = 0.6
            observer._last_speech_time = 0
            observer._speech_cooldown = 0.1
            observer._simulated_segments = []
            
            from src.observer.base import SaliencyFilter
            observer.saliency_filter = SaliencyFilter()
            
            segments = [
                AudioSegment(
                    event_type=AudioEventType.ALERT,
                    start_time=0.0,
                    duration=1.0,
                    confidence=0.9,
                    transcript="緊急求助",
                ),
            ]
            observer.set_simulated_segments(segments)
            
            with patch.object(observer, 'get_observation_type', return_value=ObservationType.AUDIO):
                with patch.object(observer, 'get_urgency_keywords', return_value=[]):
                    result = observer.observe()
            
            assert result is not None
            assert result.saliency == SaliencyLevel.CRITICAL


class TestDigitalObserver:
    """數位觀察者測試"""

    @pytest.fixture
    def mock_config(self):
        return {"mqtt": {"broker": "localhost", "port": 1883}}

    def test_observation_type(self, mock_config):
        """確認觀察類型為數位"""
        with patch.object(DigitalObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = DigitalObserver.__new__(DigitalObserver)
            assert observer.get_observation_type() == ObservationType.DIGITAL

    def test_metric_to_entity(self):
        """指標轉換為實體"""
        metric = Metric(
            name="cpu_usage",
            value=75.5,
            metric_type=MetricType.GAUGE,
            unit="%",
            tags={"host": "server1"},
        )
        
        entity = metric.to_entity()
        
        assert entity.entity_type == "Metric"
        assert entity.label == "cpu_usage"
        assert entity.properties["value"] == 75.5
        assert entity.properties["unit"] == "%"

    def test_api_status_to_entity(self):
        """API 狀態轉換為實體"""
        api = ApiStatus(
            endpoint="/api/v1/health",
            status_code=200,
            response_time_ms=150.0,
            health=HealthStatus.HEALTHY,
        )
        
        entity = api.to_entity()
        
        assert entity.entity_type == "ApiEndpoint"
        assert entity.properties["status_code"] == 200
        assert entity.properties["health"] == "healthy"

    def test_compute_trend_rising(self):
        """上升趨勢計算"""
        with patch.object(DigitalObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = DigitalObserver.__new__(DigitalObserver)
            observer._trend_window_size = 5
            observer._metric_history = {}
            
            observer.compute_trend("test_metric", 10)
            observer.compute_trend("test_metric", 15)
            observer.compute_trend("test_metric", 20)
            trend = observer.compute_trend("test_metric", 25)
            
            assert trend == TrendDirection.RISING

    def test_compute_trend_falling(self):
        """下降趨勢計算"""
        with patch.object(DigitalObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = DigitalObserver.__new__(DigitalObserver)
            observer._trend_window_size = 5
            observer._metric_history = {}
            
            observer.compute_trend("test_metric", 100)
            observer.compute_trend("test_metric", 80)
            observer.compute_trend("test_metric", 60)
            trend = observer.compute_trend("test_metric", 40)
            
            assert trend == TrendDirection.FALLING

    def test_assess_metric_saliency_critical_cpu(self):
        """CPU 超標觸發關鍵級別"""
        with patch.object(DigitalObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = DigitalObserver.__new__(DigitalObserver)
            observer.CPU_CRITICAL_PERCENT = 95.0
            observer.CPU_WARN_PERCENT = 80.0
            
            metric = Metric(name="cpu_usage", value=98.0, metric_type=MetricType.GAUGE)
            saliency = observer.assess_metric_saliency(metric, TrendDirection.STABLE)
            
            assert saliency == SaliencyLevel.CRITICAL

    def test_assess_api_saliency_unhealthy(self):
        """API 不健康觸發關鍵級別"""
        with patch.object(DigitalObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = DigitalObserver.__new__(DigitalObserver)
            observer.RESPONSE_TIME_CRITICAL_MS = 3000.0
            observer.RESPONSE_TIME_WARN_MS = 1000.0
            observer._last_api_status = {}
            
            api = ApiStatus(
                endpoint="/api/test",
                status_code=503,
                response_time_ms=500.0,
                health=HealthStatus.UNHEALTHY,
            )
            
            saliency = observer.assess_api_saliency(api)
            assert saliency == SaliencyLevel.CRITICAL

    def test_simulated_digital_observe(self, mock_config):
        """模擬數位觀察"""
        with patch.object(SimulatedDigitalObserver, '__init__', lambda self, *args, **kwargs: None):
            observer = SimulatedDigitalObserver.__new__(SimulatedDigitalObserver)
            observer._observer_id = "test_digital"
            observer._trend_window_size = 5
            observer._metric_history = {}
            observer._last_api_status = {}
            observer._simulated_metrics = []
            observer._simulated_apis = []
            
            observer.CPU_CRITICAL_PERCENT = 95.0
            observer.CPU_WARN_PERCENT = 80.0
            observer.MEMORY_CRITICAL_PERCENT = 95.0
            observer.MEMORY_WARN_PERCENT = 80.0
            observer.RESPONSE_TIME_CRITICAL_MS = 3000.0
            observer.RESPONSE_TIME_WARN_MS = 1000.0
            
            from src.observer.base import SaliencyFilter
            observer.saliency_filter = SaliencyFilter(min_saliency=SaliencyLevel.LOW)
            
            metrics = [
                Metric(name="cpu_usage", value=85.0, metric_type=MetricType.GAUGE, unit="%"),
                Metric(name="memory_usage", value=70.0, metric_type=MetricType.GAUGE, unit="%"),
            ]
            apis = [
                ApiStatus(
                    endpoint="/api/health",
                    status_code=200,
                    response_time_ms=100.0,
                    health=HealthStatus.HEALTHY,
                ),
            ]
            observer.set_simulated_metrics(metrics)
            observer.set_simulated_apis(apis)
            
            with patch.object(observer, 'get_observation_type', return_value=ObservationType.DIGITAL):
                with patch.object(observer, 'get_urgency_keywords', return_value=[]):
                    result = observer.observe()
            
            assert result is not None
            assert len(result.entities) == 3
            assert result.metadata["metric_count"] == 2
            assert result.metadata["api_count"] == 1
