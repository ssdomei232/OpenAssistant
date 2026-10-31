"""Test configuration.

Environment is configured *before* importing the application so the cached
``Settings`` object points at an isolated SQLite database and storage folder.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

WORKSPACE = Path(__file__).resolve().parents[2]
SERVER_DIR = Path(__file__).resolve().parents[1]
TEST_ROOT = WORKSPACE / ".tmp" / "pytest-server"

shutil.rmtree(TEST_ROOT, ignore_errors=True)
(TEST_ROOT / "storage").mkdir(parents=True, exist_ok=True)

os.environ.update(
    {
        "OA_DATABASE_URL": f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}",
        "OA_STORAGE_DIR": str(TEST_ROOT / "storage"),
        "OA_API_KEY_PEPPER": "test-pepper",
        "OA_BOOTSTRAP_API_KEY": "test-api-key",
        "OA_BOOTSTRAP_USER_NAME": "测试用户",
        "OA_DEMO_MODE": "1",
        "OA_WORKER_ENABLED": "false",
        "OA_SCHEDULER_ENABLED": "false",
        "OA_ASR_PROVIDER": "mock",
        "OA_DIARIZATION_PROVIDER": "energy",
        "OA_SPEAKER_PROVIDER": "spectral",
        "OA_LLM_PROVIDER": "rule",
        "OA_PUSH_ENABLED": "true",
        "OA_LOG_LEVEL": "WARNING",
        # 测试默认不等待合并窗口（否则每张卡都要等 20s）；合并行为在
        # tests/test_conversation_merge.py 里显式设置窗口验证。
        "OA_CONVERSATION_MERGE_WINDOW_S": "0",
    }
)

if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.providers import Providers, set_providers  # noqa: E402
from app.providers.asr import MockASR  # noqa: E402
from app.providers.diarization import EnergyDiarizer  # noqa: E402
from app.providers.llm import RuleBasedLLM  # noqa: E402
from app.providers.push import RecordingPush  # noqa: E402
from app.providers.search import StaticSearchProvider  # noqa: E402
from app.providers.speaker import SpectralEmbedding  # noqa: E402
from app.providers.types import ASRResult, ASRWord, DiarizationSegment  # noqa: E402

API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


# ── deterministic providers for pipeline tests ────────────────────────────
TONES = {
    "user": (220.0, 440.0, 660.0),
    "other": (880.0, 1320.0, 1760.0),
}


def tone_wave(seconds: float, kind: str = "user", sr: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    signal = np.zeros_like(t)
    for idx, freq in enumerate(TONES[kind]):
        signal += (0.5 / (idx + 1)) * np.sin(2 * math.pi * freq * t)
    return np.clip(signal * 0.7, -1.0, 1.0).astype(np.float32)


def concat(*waves: np.ndarray) -> np.ndarray:
    return np.concatenate(waves).astype(np.float32)


class FixedDiarizer:
    """Two anonymous speakers: first 45% / last 55% of the segment."""

    name = "test-fixed"

    def diarize(self, samples: np.ndarray, sr: int) -> list[DiarizationSegment]:
        duration = samples.size / sr
        split = round(duration * 0.45, 3)
        return [
            DiarizationSegment(0.0, split, "SPEAKER_00"),
            DiarizationSegment(split, round(duration, 3), "SPEAKER_01"),
        ]


class SpectralHistogramEmbedding:
    """Deterministic tone fingerprint: energy in log-spaced frequency bins."""

    name = "test-spectral"

    def __init__(self, sr: int = 16000, n_bins: int = 24) -> None:
        self.sr = sr
        self.n_bins = n_bins
        self.edges = np.geomspace(100.0, 1800.0, n_bins + 1)

    def embed(self, samples: np.ndarray, sr: int) -> list[float]:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.size < 512:
            audio = np.pad(audio, (0, 512 - audio.size))
        spectrum = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(audio.size, 1.0 / sr)
        vector = np.zeros(self.n_bins, dtype=np.float64)
        for idx in range(self.n_bins):
            mask = (freqs >= self.edges[idx]) & (freqs < self.edges[idx + 1])
            vector[idx] = spectrum[mask].sum() if mask.any() else 0.0
        norm = np.linalg.norm(vector)
        if norm == 0:
            return [0.0] * self.n_bins
        return (vector / norm).tolist()


class FixedASR:
    """Two fixed sentences aligned to the two diarized halves."""

    name = "test-asr"

    def __init__(self, user_text: str, other_text: str) -> None:
        self.user_text = user_text
        self.other_text = other_text

    def _words(self, text: str, start: float, end: float) -> list[ASRWord]:
        tokens = [ch for ch in text]
        step = max(0.01, (end - start) / max(1, len(tokens)))
        return [ASRWord(start + i * step, start + (i + 1) * step, token) for i, token in enumerate(tokens)]

    def transcribe(self, samples: np.ndarray, sr: int) -> ASRResult:
        duration = samples.size / sr
        split = duration * 0.45
        words = self._words(self.user_text, 0.1, split - 0.05) + self._words(
            self.other_text, split + 0.05, duration - 0.05
        )
        return ASRResult(language="zh", words=words, text=self.user_text + self.other_text)


def build_test_providers(
    user_text: str = "帮我记一下明天下午三点和周磊开会。",
    other_text: str = "明天下午四点我们一起去看电影吧。",
    push: RecordingPush | None = None,
) -> Providers:
    return Providers(
        asr=FixedASR(user_text, other_text),
        diarization=FixedDiarizer(),
        speaker=SpectralHistogramEmbedding(),
        llm=RuleBasedLLM(),
        search_builtin=StaticSearchProvider(),
        search_deepseek=StaticSearchProvider(),
        push=push or RecordingPush(),
        modes={"asr": "test", "diarization": "test", "speaker": "test", "llm": "rule", "search": "static", "push": "recording"},
    )


# ── fixtures ──────────────────────────────────────────────────────────────
@pytest.fixture()
def client():
    from app.providers import get_providers
    from app.services.scheduler import set_scheduler

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    set_scheduler(None)
    with TestClient(app) as test_client:
        # deterministic providers for every test; individual tests may override
        set_providers(build_test_providers())
        yield test_client
    set_providers(None)
    set_scheduler(None)


@pytest.fixture()
def providers(client):
    from app.providers import get_providers

    return get_providers()


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def wav_bytes(samples: np.ndarray, sr: int = 16000) -> bytes:
    from app.utils import encode_wav

    return encode_wav(samples, sr)


def upload_segment(
    client: TestClient,
    samples: np.ndarray,
    *,
    client_segment_id: str,
    recorded_at: datetime | str | None = None,
    device_id: str = "pi-test",
    process_now: bool = True,
    extra_meta: dict | None = None,
):
    meta = {
        "client_segment_id": client_segment_id,
        "device_id": device_id,
        "recorded_at": recorded_at.isoformat() if isinstance(recorded_at, datetime) else recorded_at,
        "duration_s": round(samples.size / 16000, 2),
        "sample_rate": 16000,
        "channels": 1,
        "trigger": "auto",
    }
    if extra_meta:
        meta.update(extra_meta)
    response = client.post(
        f"/api/v1/audio/segments?process_now={'true' if process_now else 'false'}",
        headers=AUTH,
        files={"file": ("segment.wav", wav_bytes(samples), "audio/wav")},
        data={"meta": json.dumps(meta, ensure_ascii=False)},
    )
    return response


def enroll_voiceprint(client: TestClient, samples: np.ndarray, threshold: float = 60.0):
    return client.post(
        "/api/v1/me/voiceprint",
        headers=AUTH,
        files={"file": ("voice.wav", wav_bytes(samples), "audio/wav")},
        data={"threshold": str(threshold)},
    )
