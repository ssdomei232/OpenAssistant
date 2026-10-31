"""FunASR provider：时间戳解析、SenseVoice 标签清理、GPU 回退 CPU、provider 选择顺序。"""
from __future__ import annotations

import sys
import types

import numpy as np

import app.providers as providers_module
from app.config import Settings


# ── 假 FunASR 模块 ────────────────────────────────────────────────────────
class _FakeAutoModel:
    """记录构造参数与 generate 参数；cuda 上模拟 CUDA 运行时错误。"""

    created: list["_FakeAutoModel"] = []
    result: object = None
    fail_on_cuda = False

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.device = str(kwargs.get("device", "cpu"))
        self.generate_kwargs: dict = {}
        _FakeAutoModel.created.append(self)

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        if self.fail_on_cuda and self.device.startswith("cuda"):
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return self.result


def _install_fake_funasr(monkeypatch, result: object, *, fail_on_cuda: bool = False) -> type[_FakeAutoModel]:
    _FakeAutoModel.created = []
    _FakeAutoModel.result = result
    _FakeAutoModel.fail_on_cuda = fail_on_cuda
    monkeypatch.setitem(sys.modules, "funasr", types.SimpleNamespace(AutoModel=_FakeAutoModel))
    return _FakeAutoModel


def _audio(seconds: float = 1.4) -> np.ndarray:
    return np.zeros(int(seconds * 16000), dtype=np.float32)


# ── 解析 ──────────────────────────────────────────────────────────────────
def test_funasr_parses_word_timestamps(monkeypatch):
    fake = _install_fake_funasr(
        monkeypatch,
        [
            {
                "key": "audio",
                "text": "帮我 记一下 明天 开会",
                "timestamp": [[0, 200], [200, 600], [600, 900], [900, 1400]],
            }
        ],
    )
    import app.providers.asr as asr_module

    monkeypatch.setattr(asr_module, "_cuda_runtime_available", lambda: True)
    asr = asr_module.FunASRASR("paraformer-zh", device="cuda")
    result = asr.transcribe(_audio(), 16000)

    assert result.text == "帮我 记一下 明天 开会"
    assert [w.word for w in result.words] == ["帮我", "记一下", "明天", "开会"]
    # 毫秒 -> 秒
    assert result.words[0].start == 0.0 and result.words[0].end == 0.2
    assert result.words[-1].start == 0.9 and result.words[-1].end == 1.4
    assert result.language == "zh"

    # 构造参数：默认带 VAD + 标点，走 ModelScope
    built = fake.created[0]
    assert built.kwargs["model"] == "paraformer-zh"
    assert built.kwargs["vad_model"] == "fsmn-vad"
    assert built.kwargs["punc_model"] == "ct-punc"
    assert built.kwargs["hub"] == "ms"
    assert built.generate_kwargs["use_itn"] is True
    assert built.generate_kwargs["input"].shape[0] == _audio().shape[0]


def test_funasr_parses_sentence_info(monkeypatch):
    _install_fake_funasr(
        monkeypatch,
        [
            {
                "key": "audio",
                "text": "你好世界。今天开会。",
                "sentence_info": [
                    {"text": "你好世界。", "start": 0, "end": 800, "timestamp": [[0, 200], [200, 400], [400, 600], [600, 800]]},
                    {
                        "text": "今天开会。",
                        "start": 800,
                        "end": 1400,
                        "timestamp": [[800, 1000], [1000, 1100], [1100, 1250], [1250, 1400]],
                    },
                ],
            }
        ],
    )
    from app.providers.asr import FunASRASR

    result = FunASRASR(device="cpu").transcribe(_audio(), 16000)
    assert result.text == "你好世界。今天开会。"
    # 标点由 CT-Punc 后处理产生，不占时间戳，对齐时附着到前一个字
    assert [w.word for w in result.words] == ["你", "好", "世", "界。", "今", "天", "开", "会。"]
    assert result.words[-1].start == 1.25 and result.words[-1].end == 1.4


def test_funasr_mismatched_timestamp_count_is_split_evenly(monkeypatch):
    _install_fake_funasr(
        monkeypatch,
        [{"text": "你好世界", "timestamp": [[0, 500], [500, 1000]]}],
    )
    from app.providers.asr import FunASRASR

    result = FunASRASR(device="cpu").transcribe(_audio(1.0), 16000)
    assert [w.word for w in result.words] == ["你好", "世界"]


def test_funasr_seconds_timestamps_are_detected(monkeypatch):
    _install_fake_funasr(
        monkeypatch,
        [{"text": "你好", "timestamp": [[0.1, 0.5], [0.5, 0.9]]}],
    )
    from app.providers.asr import FunASRASR

    result = FunASRASR(device="cpu").transcribe(_audio(1.0), 16000)
    assert result.words[0].start == 0.1
    assert result.words[-1].end == 0.9


def test_funasr_without_timestamps_keeps_text(monkeypatch):
    _install_fake_funasr(monkeypatch, [{"text": "没有时间戳的转写"}])
    from app.providers.asr import FunASRASR

    result = FunASRASR(device="cpu").transcribe(_audio(1.0), 16000)
    assert result.text == "没有时间戳的转写"
    assert len(result.words) == 1
    assert result.words[0].start == 0.0 and result.words[0].end == 1.0


def test_funasr_sensevoice_tags_and_language(monkeypatch):
    fake = _install_fake_funasr(
        monkeypatch,
        [{"text": "<|zh|><|NEUTRAL|><|Speech|><|woitn|>你好世界", "timestamp": [[0, 1200]]}],
    )
    from app.providers.asr import FunASRASR

    asr = FunASRASR("iic/SenseVoiceSmall", device="cpu", language="zh")
    assert asr.name == "funasr:SenseVoiceSmall"
    result = asr.transcribe(_audio(1.2), 16000)
    assert result.text == "你好世界"
    assert result.language == "zh"
    assert fake.created[0].generate_kwargs["language"] == "zh"


def test_funasr_cuda_error_falls_back_to_cpu(monkeypatch):
    _install_fake_funasr(
        monkeypatch,
        [{"text": "你好世界", "timestamp": [[0, 500], [500, 1000], [1000, 1200], [1200, 1400]]}],
        fail_on_cuda=True,
    )
    import app.providers.asr as asr_module

    monkeypatch.setattr(asr_module, "_cuda_runtime_available", lambda: True)
    asr = asr_module.FunASRASR("paraformer-zh", device="cuda")
    result = asr.transcribe(_audio(), 16000)
    assert result.text == "你好世界"
    assert asr.device == "cpu"
    assert asr.name.endswith("@cpu")


def test_funasr_unavailable_cuda_goes_straight_to_cpu(monkeypatch):
    fake = _install_fake_funasr(
        monkeypatch,
        [{"text": "你好世界", "timestamp": [[0, 500], [500, 1000], [1000, 1200], [1200, 1400]]}],
    )
    import app.providers.asr as asr_module

    monkeypatch.setattr(asr_module, "_cuda_runtime_available", lambda: False)
    asr = asr_module.FunASRASR("paraformer-zh", device="cuda")
    result = asr.transcribe(_audio(), 16000)
    assert result.text == "你好世界"
    assert asr.device == "cpu"
    # 不应先尝试 cuda 加载
    assert [model.device for model in fake.created] == ["cpu"]


def test_funasr_resamples_non_16k_input(monkeypatch):
    fake = _install_fake_funasr(
        monkeypatch,
        [{"text": "你好世界", "timestamp": [[0, 500], [500, 1000], [1000, 1200], [1200, 1400]]}],
    )
    from app.providers.asr import FunASRASR

    samples = np.zeros(int(1.4 * 48000), dtype=np.float32)
    FunASRASR(device="cpu").transcribe(samples, 48000)
    assert fake.created[0].generate_kwargs["input"].shape[0] == int(1.4 * 16000)


# ── provider 选择 ─────────────────────────────────────────────────────────
def _settings(**overrides) -> Settings:
    base = dict(
        demo_mode=False,
        asr_provider="auto",
        diarization_provider="energy",
        speaker_provider="spectral",
        llm_provider="rule",
    )
    base.update(overrides)
    return Settings(**base)


def test_auto_prefers_funasr(monkeypatch):
    monkeypatch.setattr(providers_module, "_try", lambda path: path == "funasr")
    built = providers_module.build_providers(_settings())
    assert built.modes["asr"] == "funasr"
    assert isinstance(built.asr, providers_module.FunASRASR)


def test_auto_falls_back_to_faster_whisper(monkeypatch):
    monkeypatch.setattr(providers_module, "_try", lambda path: path == "faster_whisper")
    built = providers_module.build_providers(_settings())
    assert built.modes["asr"] == "faster_whisper"
    assert isinstance(built.asr, providers_module.FasterWhisperASR)


def test_explicit_funasr_missing_falls_back_to_mock(monkeypatch):
    monkeypatch.setattr(providers_module, "_try", lambda _path: False)
    built = providers_module.build_providers(_settings(asr_provider="funasr"))
    assert built.modes["asr"] == "mock"
    assert isinstance(built.asr, providers_module.MockASR)


# ── doctor GPU 诊断 ───────────────────────────────────────────────────────
def test_doctor_gpu_report_shape():
    from app.cli import _gpu_report

    report = _gpu_report()
    assert "torch" in report
    assert isinstance(report["cuda_available"], bool)
    if report["cuda_available"]:
        assert report["device_count"] >= 1
    else:
        # CPU 版 torch / 驱动不匹配时应给出可操作的提示
        assert "CUDA 版 torch" in report.get("hint", "")
