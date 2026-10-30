"""Provider 选择：演示模式强制离线实现；pyannote 需要 HF token 才启用。"""
from __future__ import annotations

import app.providers as providers_module
from app.config import Settings


def test_demo_mode_forces_offline_providers(monkeypatch):
    monkeypatch.setattr(providers_module, "_try", lambda _path: True)
    settings = Settings(demo_mode=True)
    built = providers_module.build_providers(settings)
    assert built.modes["asr"] == "mock"
    assert built.modes["diarization"] == "energy"
    assert built.modes["speaker"] == "spectral"
    assert built.modes["llm"] == "rule"
    assert built.modes["search"] == "static"


def test_pyannote_requires_hf_token(monkeypatch):
    monkeypatch.setattr(providers_module, "_try", lambda _path: True)
    settings = Settings(
        demo_mode=False,
        diarization_provider="auto",
        speaker_provider="auto",
        hf_token="",
    )
    built = providers_module.build_providers(settings)
    assert built.modes["diarization"] == "energy"
    assert built.modes["speaker"] == "spectral"


def test_pyannote_used_when_token_present(monkeypatch):
    monkeypatch.setattr(providers_module, "_try", lambda _path: True)
    settings = Settings(
        demo_mode=False,
        diarization_provider="auto",
        speaker_provider="auto",
        hf_token="hf_test_token",
    )
    built = providers_module.build_providers(settings)
    assert built.modes["diarization"] == "pyannote"
    assert built.modes["speaker"] == "pyannote-embedding"
    # 真实 provider 外面包一层运行时降级，模型失败时自动回退
    assert isinstance(built.diarization, providers_module.ResilientDiarizer)
    assert isinstance(built.speaker, providers_module.ResilientEmbedding)
    assert built.diarization.name == "pyannote"
    assert built.speaker.name == "pyannote-embedding"
