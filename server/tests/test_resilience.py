"""Provider 运行时降级与 pyannote 兼容性测试。"""
from __future__ import annotations

import sys

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


def test_embedding_falls_back_to_wespeaker(monkeypatch):
    pytest.importorskip("pyannote.audio")

    from app.providers import speaker as speaker_module

    import pyannote.audio as pa  # type: ignore

    calls: list[str] = []

    def fake_from_pretrained(name, **kwargs):
        calls.append(name)
        if name == "pyannote/embedding":
            raise RuntimeError("No module named 'omegaconf'")
        return object()

    class _FakeInference:
        def __init__(self, model, window=None):
            self.model = model
            self.window = window

    monkeypatch.setattr(pa.Model, "from_pretrained", staticmethod(fake_from_pretrained))
    monkeypatch.setattr(pa, "Inference", _FakeInference)

    embedding = speaker_module.PyAnnoteEmbedding(hf_token="hf_test", model="pyannote/embedding")
    inference = embedding._load()
    assert isinstance(inference, _FakeInference)
    assert calls == ["pyannote/embedding", "pyannote/wespeaker-voxceleb-resnet34-LM"]
    assert embedding.model_name == "pyannote/wespeaker-voxceleb-resnet34-LM"


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


# ── faster-whisper CUDA 失败自动回退 CPU ─────────────────────────────────


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text
        self.words = []


class _FakeInfo:
    language = "zh"


class _FakeWhisperModel:
    def __init__(self, model_size, device, compute_type):
        self.device = device
        self.compute_type = compute_type

    def transcribe(self, audio, **kwargs):
        if self.device == "cuda":
            # 真实场景：cublas DLL 缺失，错误在迭代/推理阶段才抛出
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return iter([_FakeSegment("你好，测试。")]), _FakeInfo()


def test_faster_whisper_cuda_error_falls_back_to_cpu(monkeypatch):
    import types

    fake_module = types.SimpleNamespace(WhisperModel=_FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    from app.providers.asr import FasterWhisperASR

    asr = FasterWhisperASR("large-v3-turbo", device="cuda", compute_type="float16", language="zh")
    result = asr.transcribe(np.zeros(16000, dtype=np.float32), 16000)
    assert result.text == "你好，测试。"
    assert asr.device == "cpu"
    assert asr.compute_type == "int8"
    assert asr.name.endswith("@cpu")


def test_looks_like_cuda_error():
    from app.providers.asr import _looks_like_cuda_error

    assert _looks_like_cuda_error(RuntimeError("Library cublas64_12.dll is not found"))
    assert _looks_like_cuda_error(RuntimeError("cuDNN error: CUDNN_STATUS_NOT_INITIALIZED"))
    assert not _looks_like_cuda_error(ValueError("bad audio format"))

