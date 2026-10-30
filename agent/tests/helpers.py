"""测试辅助（不依赖声卡/网络/蓝牙）。"""

from __future__ import annotations

import hashlib
import math
import struct
import wave
from io import BytesIO
from pathlib import Path

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)  # 480
FRAME_BYTES = FRAME_SAMPLES * 2


def tone_frame(
    frequency: float = 440.0,
    amplitude: float = 8000.0,
    *,
    start_sample: int = 0,
    samples: int = FRAME_SAMPLES,
) -> bytes:
    """生成一帧正弦“语音”（16-bit mono）。"""
    values = [
        int(amplitude * math.sin(2.0 * math.pi * frequency * (start_sample + i) / SAMPLE_RATE))
        for i in range(samples)
    ]
    return struct.pack(f"<{len(values)}h", *values)


def silence_frame(noise: float = 25.0, *, seed: int = 1) -> bytes:
    """生成一帧低噪静音（确定性 LCG）。"""
    values = []
    state = seed
    for _ in range(FRAME_SAMPLES):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        values.append(int((state / 0x7FFFFFFF - 0.5) * 2.0 * noise))
    return struct.pack(f"<{len(values)}h", *values)


def pcm_from_pattern(
    pattern: list[tuple[str, float]],
    *,
    sample_rate: int = SAMPLE_RATE,
    amplitude: float = 8000.0,
    noise: float = 25.0,
) -> bytes:
    """按 ``[("speech", 1.0), ("silence", 0.5)]`` 生成连续 PCM。"""
    chunks: list[bytes] = []
    position = 0
    for kind, seconds in pattern:
        samples = int(round(seconds * sample_rate))
        if kind == "speech":
            frame = tone_frame(
                amplitude=amplitude, start_sample=position, samples=samples
            )
        else:
            frame = silence_frame(noise=noise)[: samples * 2]
            frame = frame + b"\x00" * (samples * 2 - len(frame))
        chunks.append(frame)
        position += samples
    return b"".join(chunks)


def frames_of(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> list[bytes]:
    return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def write_wav_file(path: str | Path, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(wav_bytes(pcm, sample_rate))
    return target


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeClock:
    """可推进的时钟，避免测试 sleep。"""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += float(seconds)
        return self.value
