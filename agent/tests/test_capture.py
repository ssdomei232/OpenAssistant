"""音频采集：合成源、wav 回放（含重采样/降混）、工厂降级。"""

from __future__ import annotations

import math
import struct
import wave

import pytest

from openassistant_agent.capture import (
    AudioSource,
    AudioSourceError,
    FileSource,
    SyntheticSource,
    create_source,
    iter_frames,
    pcm16_to_wav_bytes,
    write_wav,
)
from openassistant_agent.config import AgentConfig

from .helpers import SAMPLE_RATE, wav_bytes


def test_synthetic_source_streams_blocks():
    source = SyntheticSource(pattern=[("speech", 0.3), ("silence", 0.2)])
    source.open()
    blocks = list(source.blocks())
    assert blocks
    assert all(len(block) == source.block_bytes for block in blocks[:-1])
    assert 0 < len(blocks[-1]) <= source.block_bytes
    assert sum(len(block) for block in blocks) == int(
        round(0.5 * SAMPLE_RATE)
    ) * 2
    assert any(max(abs(v) for v in struct.unpack("<10h", block[:20])) > 1000 for block in blocks)


def test_synthetic_source_validation():
    with pytest.raises(AudioSourceError):
        SyntheticSource(pattern=[("music", 1.0)])
    with pytest.raises(AudioSourceError):
        SyntheticSource(pattern=[])
    with pytest.raises(AudioSourceError):
        SyntheticSource(sample_rate=0)


def test_file_source_roundtrip(tmp_path):
    pcm = b"".join(
        SyntheticSource(pattern=[("speech", 0.4), ("silence", 0.2)]).blocks()
    )
    path = write_wav(tmp_path / "demo.wav", pcm)
    source = FileSource(path)
    blocks = list(source.blocks())
    assert b"".join(blocks) == pcm


def test_file_source_downmixes_and_resamples(tmp_path):
    # 48 kHz 立体声 wav -> 16 kHz 单声道
    frames = 4800  # 0.1s @48k
    samples = []
    for i in range(frames):
        value = int(8000 * math.sin(2 * math.pi * 440 * i / 48000))
        samples.extend([value, value])
    raw = struct.pack(f"<{len(samples)}h", *samples)
    path = tmp_path / "stereo48k.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(raw)

    source = FileSource(path)
    source.open()
    assert source.sample_rate == 16000
    pcm = b"".join(iter(source.blocks()))
    # 0.1s * 16000 * 2 bytes，允许 1 个采样误差
    assert abs(len(pcm) - 3200) <= 4


def test_file_source_missing_or_invalid(tmp_path):
    with pytest.raises(AudioSourceError):
        FileSource(tmp_path / "missing.wav").open()

    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not a wav")
    with pytest.raises(AudioSourceError):
        FileSource(broken).open()


def test_create_source_and_iter_frames():
    config = AgentConfig()
    config.record.source = "synthetic"
    source = create_source(config)
    assert isinstance(source, SyntheticSource)
    assert isinstance(source, AudioSource)

    config.record.source = "file"
    config.record.input_path = "/tmp/x.wav"
    assert isinstance(create_source(config), FileSource)

    config.record.source = "bogus"
    with pytest.raises(AudioSourceError):
        create_source(config)

    frames = list(iter_frames([b"a" * 10, b"b" * 10], 8))
    assert frames == [b"a" * 8, b"a" * 2 + b"b" * 6]


def test_pcm16_to_wav_bytes():
    raw = wav_bytes(b"\x00\x00" * 100)
    assert raw[:4] == b"RIFF"
    assert pcm16_to_wav_bytes(b"\x00\x00" * 100)[:4] == b"RIFF"
