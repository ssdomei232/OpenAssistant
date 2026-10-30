"""Provider 运行时降级与 pyannote 兼容性测试。"""
from __future__ import annotations

import numpy as np
import pytest

from app.providers.resilience import ResilientDiarizer, ResilientEmbedding
from app.providers.types import DiarizationSegment


class _BoomDiarizer:
    name = "boom"

    def __init__(self) -> None:
        self.calls = 0

    def diarize(self, samples, sr):
        self.calls += 1
        raise RuntimeError("gated repo: please accept terms")


class _FakeDiarizer:
    name = "fake-energy"

    def __init__(self) -> None:
        self.calls = 0

    def diarize(self, samples, sr):
        self.calls += 1
        return [DiarizationSegment(0.0, 1.0, "SPEAKER_00")]


class _BoomEmbedding:
    name = "boom-embedding"

    def embed(self, samples, sr):
        raise RuntimeError("401 unauthorized")


class _FakeEmbedding:
    name = "fake-spectral"

    def embed(self, samples, sr):
        return [1.0, 0.0, 0.0]


def test_resilient_diarizer_falls_back_once():
    modes: dict[str, str] = {"diarization": "pyannote"}
    primary = _BoomDiarizer()
    fallback = _FakeDiarizer()
    diarizer = ResilientDiarizer(primary, fallback, modes)

    first = diarizer.diarize(np.zeros(16000, dtype=np.float32), 16000)
    assert len(first) == 1
    assert primary.calls == 1
    assert fallback.calls == 1
    assert modes["diarization"] == "fake-energy(fallback)"
    assert diarizer.name == "fake-energy"

    second = diarizer.diarize(np.zeros(16000, dtype=np.float32), 16000)
    assert len(second) == 1
    assert primary.calls == 1  # 不再重试
    assert fallback.calls == 2


def test_resilient_embedding_falls_back():
    modes: dict[str, str] = {"speaker": "pyannote-embedding"}
    embedding = ResilientEmbedding(_BoomEmbedding(), _FakeEmbedding(), modes)
    vector = embedding.embed(np.zeros(16000, dtype=np.float32), 16000)
    assert vector == [1.0, 0.0, 0.0]
    assert modes["speaker"] == "fake-spectral(fallback)"
    assert embedding.name == "fake-spectral"


def test_pyannote_diarizer_uses_token_and_returns_segments(monkeypatch):
    pytest.importorskip("pyannote.audio")
    pytest.importorskip("torch")

    from app.providers import diarization as diar_module

    captured: dict[str, object] = {}

    class _Turn:
        start, end = 0.0, 1.5

    class _Annotation:
        def itertracks(self, yield_label=False):
            yield _Turn(), "segment", "SPEAKER_00"

    class _Pipeline:
        def to(self, *_args):
            return self

        def __call__(self, file):
            captured["call_with_dict"] = isinstance(file, dict)
            return _Annotation()

    def fake_from_pretrained(model, **kwargs):
        captured["model"] = model
        captured["kwargs"] = kwargs
        return _Pipeline()

    import pyannote.audio as pa  # type: ignore

    monkeypatch.setattr(pa.Pipeline, "from_pretrained", staticmethod(fake_from_pretrained))

    diarizer = diar_module.PyAnnoteDiarizer(hf_token="hf_test", model="pyannote/speaker-diarization-3.1")
    segments = diarizer.diarize(np.zeros(16000, dtype=np.float32), 16000)
    assert captured["model"] == "pyannote/speaker-diarization-3.1"
    assert captured["kwargs"].get("token") == "hf_test"
    assert captured["call_with_dict"] is True
    assert len(segments) == 1
    assert segments[0].speaker == "SPEAKER_00"
    assert segments[0].end == 1.5


def test_pyannote_load_error_has_actionable_message(monkeypatch):
    pytest.importorskip("pyannote.audio")
    pytest.importorskip("torch")

    from app.providers import diarization as diar_module

    class _Gated(Exception):
        pass

    def fake_from_pretrained(model, **kwargs):
        raise _Gated("403 gated repo")

    import pyannote.audio as pa  # type: ignore

    monkeypatch.setattr(pa.Pipeline, "from_pretrained", staticmethod(fake_from_pretrained))
    diarizer = diar_module.PyAnnoteDiarizer(hf_token="hf_test")
    with pytest.raises(RuntimeError) as excinfo:
        diarizer._load()
    message = str(excinfo.value)
    assert "hf.co/pyannote/speaker-diarization-3.1" in message
    assert "OA_HF_TOKEN" in message
