"""切片逻辑：preroll/postroll、短语音丢弃、静音合并、最大长度切分。"""

from __future__ import annotations

import io
import wave

import pytest

from openassistant_agent.config import VadConfig
from openassistant_agent.segmenter import (
    Segmenter,
    SpeechSegment,
    frame_bytes_for,
    run_segmenter,
    split_frames,
)
from openassistant_agent.vad import EnergyVAD

from .helpers import FRAME_BYTES, pcm_from_pattern, silence_frame, tone_frame


def _segments(pcm: bytes, **vad_overrides):
    config = VadConfig(
        aggressiveness=2,
        frame_ms=30,
        min_speech_ms=200,
        min_silence_ms=300,
        max_segment_s=10.0,
        preroll_s=0.2,
        postroll_s=0.2,
    )
    for key, value in vad_overrides.items():
        setattr(config, key, value)
    vad = EnergyVAD(aggressiveness=2, frame_ms=30)
    return list(run_segmenter([pcm], config, vad=vad))


def test_two_segments_boundaries_and_ratio():
    pcm = pcm_from_pattern([("speech", 1.0), ("silence", 0.6), ("speech", 1.0)])
    segments = _segments(pcm)
    assert len(segments) == 2
    first, second = segments
    assert first.start == pytest.approx(0.0, abs=0.05)
    assert first.end == pytest.approx(1.2, abs=0.05)
    assert first.duration_s == pytest.approx(1.2, abs=0.05)
    assert first.speech_ratio == pytest.approx(1.0 / 1.2, abs=0.05)
    assert first.speech_spans
    assert second.start == pytest.approx(1.4, abs=0.08)
    assert second.speech_ratio > 0.6
    assert all(len(segment.pcm) % FRAME_BYTES == 0 for segment in segments)


def test_short_blip_is_dropped():
    pcm = pcm_from_pattern([("silence", 0.5), ("speech", 0.06), ("silence", 0.6)])
    assert _segments(pcm, min_speech_ms=300) == []


def test_short_silence_is_merged():
    pcm = pcm_from_pattern([("speech", 0.5), ("silence", 0.2), ("speech", 0.5)])
    segments = _segments(pcm, min_silence_ms=300)
    assert len(segments) == 1
    assert segments[0].duration_s == pytest.approx(1.2, abs=0.08)
    assert 0.7 < segments[0].speech_ratio <= 1.0


def test_long_silence_splits_segments():
    pcm = pcm_from_pattern([("speech", 0.5), ("silence", 0.9), ("speech", 0.5)])
    segments = _segments(pcm, min_silence_ms=300)
    assert len(segments) == 2


def test_max_segment_s_forces_split():
    pcm = pcm_from_pattern([("speech", 5.0)])
    segments = _segments(pcm, max_segment_s=1.0, min_speech_ms=100, postroll_s=0.0)
    assert len(segments) >= 5
    for segment in segments:
        assert segment.duration_s <= 1.0 + 1e-6
        assert segment.duration_s >= 0.9
        assert segment.speech_ratio == pytest.approx(1.0, abs=0.02)
    # 片段连续、无重叠
    for left, right in zip(segments, segments[1:]):
        assert right.start >= left.end - 1e-6


def test_segment_wav_roundtrip_and_meta():
    pcm = pcm_from_pattern([("speech", 0.6)])
    segments = _segments(pcm)
    assert len(segments) == 1
    segment = segments[0]
    wav = segment.to_wav_bytes()
    with wave.open(io.BytesIO(wav), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.getnframes() * 2 == len(segment.pcm)
    meta = segment.to_meta()
    assert meta["sample_rate"] == 16000
    assert meta["channels"] == 1
    assert meta["vad"]["speech_ratio"] == pytest.approx(segment.speech_ratio)
    assert meta["vad"]["segments"]


def test_segmenter_input_validation_and_flush():
    segmenter = Segmenter(VadConfig(), sample_rate=16000)
    with pytest.raises(ValueError):
        segmenter.push(b"\x00\x00", True)

    # 单帧语音短于 min_speech_ms，flush 时被丢弃
    assert segmenter.push(tone_frame(), True) == []
    assert segmenter.flush() == []

    # 连续 10 帧（300ms）语音在 flush 时形成一个片段
    segmenter.reset()
    for _ in range(10):
        assert segmenter.push(tone_frame(), True) == []
    flushed = segmenter.flush()
    assert len(flushed) == 1
    assert flushed[0].duration_s == pytest.approx(0.3, abs=0.031)
    assert segmenter.flush() == []


def test_split_frames_and_frame_bytes():
    assert frame_bytes_for(16000, 30) == FRAME_BYTES
    frames = split_frames(b"\x00\x01" * 700, FRAME_BYTES)
    assert all(len(frame) == FRAME_BYTES for frame in frames)
    assert len(frames) == 2


def test_speech_segment_helpers():
    segment = SpeechSegment(start=1.0, end=2.5, pcm=b"\x00\x00" * 100, speech_ratio=0.5)
    assert segment.duration_s == pytest.approx(1.5)
    assert segment.nbytes == 200
    assert segment.frames == 100
