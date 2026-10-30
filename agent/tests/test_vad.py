"""能量 VAD 的有/无语音判定与自适应噪声底。"""

from __future__ import annotations

import pytest

from openassistant_agent.config import VadConfig
from openassistant_agent.vad import (
    EnergyVAD,
    FrameVAD,
    create_vad,
    frame_rms,
    rms_to_dbfs,
)

from .helpers import FRAME_BYTES, silence_frame, tone_frame


def test_frame_rms_and_dbfs():
    assert frame_rms(b"\x00\x00" * (FRAME_BYTES // 2)) == 0.0
    assert frame_rms(tone_frame(amplitude=8000)) > 4000
    assert frame_rms(silence_frame(noise=20)) < 100
    assert rms_to_dbfs(0) <= -100
    assert rms_to_dbfs(32768) == pytest.approx(0.0, abs=0.1)


def test_energy_vad_detects_speech_and_silence():
    vad = EnergyVAD(aggressiveness=2, frame_ms=30)
    speech = [tone_frame() for _ in range(40)]
    assert sum(vad.is_speech(frame) for frame in speech) >= 38

    fresh = EnergyVAD(aggressiveness=2, frame_ms=30)
    quiet = [silence_frame(noise=25, seed=i) for i in range(60)]
    assert sum(fresh.is_speech(frame) for frame in quiet) == 0
    assert fresh.threshold >= fresh.absolute_floor


def test_energy_vad_adapts_to_noise_floor():
    vad = EnergyVAD(aggressiveness=2, frame_ms=30)
    initial_threshold = vad.threshold
    # 持续的中等噪声（rms≈400）会被缓慢学习为噪声底，随后不再判为语音
    noisy = [tone_frame(frequency=200, amplitude=560) for _ in range(200)]
    decisions = [vad.is_speech(frame) for frame in noisy]
    assert vad.threshold > initial_threshold * 2
    assert not any(decisions[-30:]), "noise floor should adapt and stop triggering"
    assert vad.noise_floor > 100


def test_energy_vad_aggressiveness_raises_threshold():
    low = EnergyVAD(aggressiveness=0, frame_ms=30)
    high = EnergyVAD(aggressiveness=3, frame_ms=30)
    assert high.energy_factor > low.energy_factor
    assert high.threshold >= low.threshold


def test_energy_vad_validation_and_reset():
    with pytest.raises(ValueError):
        EnergyVAD(aggressiveness=4)
    with pytest.raises(ValueError):
        EnergyVAD(frame_ms=15)

    vad = EnergyVAD(aggressiveness=2, frame_ms=30)
    for frame in [silence_frame(seed=i) for i in range(5)]:
        vad.is_speech(frame)
    vad.noise_floor  # 状态存在
    vad.reset()
    assert vad.noise_floor < 200


def test_create_vad_returns_framevad():
    config = VadConfig(aggressiveness=2, frame_ms=30)
    vad = create_vad(config, sample_rate=16000)
    try:
        assert isinstance(vad, FrameVAD)
        assert vad.frame_ms == 30
        assert vad.is_speech(silence_frame()) is False
    finally:
        vad.reset()


def test_webrtc_adapter_frame_size_guard():
    from openassistant_agent.vad import WebrtcVAD

    try:
        vad = WebrtcVAD(aggressiveness=1, frame_ms=30)
    except ImportError:
        pytest.skip("webrtcvad not installed")
    assert vad.is_speech(b"\x00" * 10) is False  # 长度不对时不崩溃
