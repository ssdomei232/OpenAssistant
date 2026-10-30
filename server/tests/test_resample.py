"""重采样与采样率兼容性（Agent 48 kHz 上传 → Server 16 kHz 处理）。"""
from __future__ import annotations

import io
import wave

import numpy as np

from app.utils import decode_audio, encode_wav, resample_audio

SR_48K = 48000
SR_16K = 16000


def _tone(freq: float, seconds: float = 1.0, sr: int = SR_48K, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    pcm16 = np.clip(samples, -1.0, 1.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((pcm16 * 32767.0).astype("<i2").tobytes())
    return buf.getvalue()


def test_resample_48k_to_16k_preserves_tone_and_length():
    audio = _tone(1000.0)
    out = resample_audio(audio, SR_48K, SR_16K)
    assert out.dtype == np.float32
    assert abs(out.size - SR_16K) <= 2

    spectrum = np.abs(np.fft.rfft(out))
    freqs = np.fft.rfftfreq(out.size, 1.0 / SR_16K)
    peak = freqs[int(np.argmax(spectrum))]
    assert abs(peak - 1000.0) < 25.0
    assert 0.3 < float(np.sqrt(np.mean(out**2))) < 0.5  # 幅度基本保持不变


def test_resample_removes_aliasing_above_nyquist():
    # 15 kHz 超过 16 kHz 的 Nyquist(8 kHz)；不做抗混叠会折叠成 1 kHz
    out = resample_audio(_tone(15000.0), SR_48K, SR_16K)
    assert float(np.sqrt(np.mean(out**2))) < 0.05


def test_decode_48k_wav_returns_16k_samples():
    samples, sr = decode_audio(_wav_bytes(_tone(440.0, seconds=0.5), SR_48K), target_sr=SR_16K)
    assert sr == SR_16K
    assert abs(samples.size - int(0.5 * SR_16K)) <= 2
    assert samples.dtype == np.float32


def test_encode_wav_roundtrip_16k():
    samples = _tone(440.0, seconds=0.25, sr=SR_16K)
    decoded, sr = decode_audio(encode_wav(samples, SR_16K), target_sr=SR_16K)
    assert sr == SR_16K
    assert decoded.size == samples.size
    assert np.max(np.abs(decoded - samples)) < 1e-3


def test_resample_noop_for_same_rate():
    samples = _tone(440.0, seconds=0.1, sr=SR_16K)
    out = resample_audio(samples, SR_16K, SR_16K)
    assert out.size == samples.size
