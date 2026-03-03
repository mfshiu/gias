# src/observer/audio.py
"""
聽覺觀察者代理

負責捕捉聽覺維度的資訊：
- 語音識別與指令偵測
- 聲調情緒分析
- 環境音特徵（警報、異常聲響）
- 說話者識別

使用場景：
- 展場語音互動監控
- 機器人語音指令接收
- 環境安全監測（警報聲、異常聲響）
"""

from __future__ import annotations

from typing import Any, Optional
from dataclasses import dataclass
from enum import Enum
import time

from src.app_helper import get_agent_config
from src.log_helper import init_logging

from .base import ObserverAgent
from .observation import (
    Observation,
    ObservationType,
    SaliencyLevel,
    Entity,
    Relation,
)

logger = init_logging()


class AudioEventType(Enum):
    """聲音事件類型"""
    SPEECH = "speech"           # 語音
    COMMAND = "command"         # 指令
    AMBIENT = "ambient"         # 環境音
    ALERT = "alert"             # 警報
    MUSIC = "music"             # 音樂
    SILENCE = "silence"         # 靜默


class Emotion(Enum):
    """情緒分類"""
    NEUTRAL = "neutral"
    HAPPY = "happy"
    ANGRY = "angry"
    SAD = "sad"
    SURPRISED = "surprised"
    FEARFUL = "fearful"


@dataclass
class AudioSegment:
    """音訊片段"""
    event_type: AudioEventType
    start_time: float
    duration: float
    confidence: float
    transcript: Optional[str] = None
    speaker_id: Optional[str] = None
    emotion: Optional[Emotion] = None
    decibel: Optional[float] = None
    metadata: dict[str, Any] | None = None

    def to_entity(self) -> Entity:
        props = {
            "start_time": self.start_time,
            "duration": self.duration,
            "event_type": self.event_type.value,
        }
        if self.transcript:
            props["transcript"] = self.transcript
        if self.speaker_id:
            props["speaker_id"] = self.speaker_id
        if self.emotion:
            props["emotion"] = self.emotion.value
        if self.decibel:
            props["decibel"] = self.decibel
        if self.metadata:
            props.update(self.metadata)
        
        label = self.transcript[:50] if self.transcript else self.event_type.value
        
        return Entity(
            entity_type="AudioEvent",
            label=label,
            entity_id=f"audio_{self.start_time:.3f}",
            properties=props,
            confidence=self.confidence,
        )


class AudioObserver(ObserverAgent):
    """
    聽覺觀察者代理
    
    監控音訊輸入（麥克風、音訊串流等），
    偵測語音、指令、環境音等聽覺事件。
    
    子類別需實作：
    - process_audio(): 處理音訊並回傳事件
    """
    
    COMMAND_KEYWORDS = [
        "你好", "嗨", "導航", "帶我去", "在哪裡",
        "hello", "hi", "navigate", "take me to", "where is",
    ]
    
    ALERT_KEYWORDS = [
        "救命", "幫忙", "緊急", "危險", "火災",
        "help", "emergency", "danger", "fire",
    ]

    def __init__(
        self,
        name: str = "audio_observer",
        agent_config: dict[str, Any] | None = None,
        *,
        poll_interval_sec: float = 0.2,
        min_confidence: float = 0.6,
        silence_threshold_db: float = -40.0,
    ):
        agent_config = agent_config or get_agent_config()
        self._min_confidence = min_confidence
        self._silence_threshold = silence_threshold_db
        self._last_speech_time: float = 0
        self._speech_cooldown = 2.0  # 秒
        
        super().__init__(
            name=name,
            agent_config=agent_config,
            poll_interval_sec=poll_interval_sec,
            min_saliency=SaliencyLevel.MODERATE,
        )

    def get_observation_type(self) -> ObservationType:
        return ObservationType.AUDIO

    def get_urgency_keywords(self) -> list[str]:
        return self.ALERT_KEYWORDS + ["警報", "alarm", "scream", "尖叫"]

    def process_audio(self) -> list[AudioSegment]:
        """
        處理音訊並回傳事件
        
        子類別應覆寫此方法以實作實際的音訊處理邏輯。
        預設回傳空列表（模擬模式）。
        
        Returns:
            偵測到的音訊事件列表
        """
        return []

    def classify_speech(self, transcript: str) -> AudioEventType:
        """根據語音內容分類事件類型"""
        transcript_lower = transcript.lower()
        
        for keyword in self.ALERT_KEYWORDS:
            if keyword.lower() in transcript_lower:
                return AudioEventType.ALERT
        
        for keyword in self.COMMAND_KEYWORDS:
            if keyword.lower() in transcript_lower:
                return AudioEventType.COMMAND
        
        return AudioEventType.SPEECH

    def observe(self) -> Optional[Observation]:
        """執行聽覺觀察"""
        segments = self.process_audio()
        
        segments = [seg for seg in segments if seg.confidence >= self._min_confidence]
        
        if not segments:
            return None
        
        non_silence = [s for s in segments if s.event_type != AudioEventType.SILENCE]
        if not non_silence:
            return None
        
        current_time = time.time()
        has_speech = any(s.event_type in (AudioEventType.SPEECH, AudioEventType.COMMAND) 
                         for s in non_silence)
        
        if has_speech:
            if current_time - self._last_speech_time < self._speech_cooldown:
                logger.debug("Speech observation in cooldown")
            else:
                self._last_speech_time = current_time
        
        entities: list[Entity] = []
        for seg in non_silence:
            entities.append(seg.to_entity())
        
        has_alert = any(s.event_type == AudioEventType.ALERT for s in non_silence)
        has_command = any(s.event_type == AudioEventType.COMMAND for s in non_silence)
        
        if has_alert:
            saliency = SaliencyLevel.CRITICAL
        elif has_command:
            saliency = SaliencyLevel.HIGH
        elif has_speech:
            saliency = SaliencyLevel.MODERATE
        else:
            saliency = self.saliency_filter.compute_saliency(
                entity_count=len(entities),
                confidence=min(s.confidence for s in non_silence),
            )
        
        transcripts = [s.transcript for s in non_silence if s.transcript]
        raw_description = "; ".join(transcripts) if transcripts else f"偵測到 {len(non_silence)} 個音訊事件"
        
        return self.create_observation(
            entities=entities,
            raw_description=raw_description,
            confidence=sum(s.confidence for s in non_silence) / len(non_silence),
            saliency=saliency,
            metadata={
                "audio_timestamp": current_time,
                "event_count": len(non_silence),
                "has_alert": has_alert,
                "has_command": has_command,
            },
        )


class SimulatedAudioObserver(AudioObserver):
    """
    模擬聽覺觀察者（用於測試）
    
    可注入模擬的音訊事件。
    """
    
    def __init__(
        self,
        name: str = "simulated_audio_observer",
        agent_config: dict[str, Any] | None = None,
        **kwargs,
    ):
        self._simulated_segments: list[AudioSegment] = []
        super().__init__(name=name, agent_config=agent_config, **kwargs)

    def set_simulated_segments(self, segments: list[AudioSegment]) -> None:
        """設定模擬的音訊事件"""
        self._simulated_segments = segments

    def process_audio(self) -> list[AudioSegment]:
        return self._simulated_segments
