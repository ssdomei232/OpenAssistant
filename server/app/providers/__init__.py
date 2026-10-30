"""Provider registry: pick real providers when available, fall back gracefully."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from .asr import FasterWhisperASR, MockASR
from .diarization import EnergyDiarizer, PyAnnoteDiarizer
from .llm import OpenAICompatibleLLM, RuleBasedLLM
from .push import DingTalkPush, NullPush
from .resilience import ResilientDiarizer, ResilientEmbedding
from .search import BuiltinSearchProvider, DeepSeekSearchProvider, StaticSearchProvider
from .speaker import PyAnnoteEmbedding, SpectralEmbedding

logger = logging.getLogger(__name__)


@dataclass
class Providers:
    asr: Any
    diarization: Any
    speaker: Any
    llm: Any
    search_builtin: Any
    search_deepseek: Any
    push: Any
    modes: dict[str, str] = field(default_factory=dict)

    def search_for(self, provider: str) -> Any:
        if provider == "deepseek" and self.llm.available:
            return self.search_deepseek
        return self.search_builtin

    def describe(self) -> dict[str, str]:
        return {
            "asr": getattr(self.asr, "name", "unknown"),
            "diarization": getattr(self.diarization, "name", "unknown"),
            "speaker": getattr(self.speaker, "name", "unknown"),
            "llm": getattr(self.llm, "name", "unknown"),
            "search_builtin": getattr(self.search_builtin, "name", "unknown"),
            "push": getattr(self.push, "name", "unknown"),
            "modes": self.modes,
        }


def _try(import_path: str) -> bool:
    try:
        __import__(import_path)
        return True
    except Exception:
        return False


def _pyannote_ready(settings: Settings) -> bool:
    """pyannote 模型是 gated 仓库：没有 HF token 时即使装了依赖也跑不起来。"""
    if not (_try("pyannote.audio") and _try("torch")):
        return False
    if not settings.hf_token:
        logger.info("pyannote.audio is installed but OA_HF_TOKEN is empty; using offline fallback")
        return False
    return True


def build_providers(settings: Settings | None = None, *, force_mock: bool = False) -> Providers:
    settings = settings or get_settings()
    # 演示 / 测试模式强制使用离线实现，避免已安装的 GPU 依赖在无 token / 无模型时拖慢或失败
    force_mock = force_mock or settings.demo_mode
    modes: dict[str, str] = {}

    # ── ASR ───────────────────────────────────────────────────────────────
    asr: Any
    want = "mock" if force_mock else settings.asr_provider
    if want in ("auto", "faster_whisper") and _try("faster_whisper"):
        asr = FasterWhisperASR(
            settings.whisper_model, settings.whisper_device, settings.whisper_compute_type
        )
        modes["asr"] = "faster_whisper"
    else:
        asr = MockASR()
        modes["asr"] = "mock"
        if want == "faster_whisper":
            logger.warning("faster-whisper is not installed; falling back to mock ASR")

    # ── diarization ───────────────────────────────────────────────────────
    diarization: Any
    want = "energy" if force_mock else settings.diarization_provider
    if want == "pyannote" and not _pyannote_ready(settings):
        logger.warning("pyannote requested but unavailable (missing package or OA_HF_TOKEN); using energy diarizer")
    if want in ("auto", "pyannote") and _pyannote_ready(settings):
        diarization = ResilientDiarizer(
            PyAnnoteDiarizer(settings.hf_token, settings.diarization_model),
            EnergyDiarizer(),
            modes,
        )
        modes["diarization"] = "pyannote"
    else:
        diarization = EnergyDiarizer()
        modes["diarization"] = "energy"

    # ── speaker embedding ─────────────────────────────────────────────────
    speaker: Any
    want = "spectral" if force_mock else settings.speaker_provider
    if want == "pyannote" and not _pyannote_ready(settings):
        logger.warning("pyannote embedding requested but unavailable (missing package or OA_HF_TOKEN); using spectral")
    if want in ("auto", "pyannote") and _pyannote_ready(settings):
        speaker = ResilientEmbedding(
            PyAnnoteEmbedding(settings.hf_token, settings.embedding_model),
            SpectralEmbedding(),
            modes,
        )
        modes["speaker"] = "pyannote-embedding"
    else:
        speaker = SpectralEmbedding()
        modes["speaker"] = "spectral"

    # ── LLM ───────────────────────────────────────────────────────────────
    llm: Any
    want = "rule" if force_mock else settings.llm_provider
    if want in ("auto", "openai_compatible") and settings.llm_api_key:
        llm = OpenAICompatibleLLM(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            settings.llm_timeout_s,
            settings.llm_max_tokens,
        )
        modes["llm"] = settings.llm_model
    else:
        llm = RuleBasedLLM()
        modes["llm"] = "rule"
        if want in ("openai_compatible",):
            logger.warning("LLM API key missing; falling back to rule based extraction")

    # ── search ────────────────────────────────────────────────────────────
    if force_mock:
        search_builtin: Any = StaticSearchProvider()
        modes["search"] = "static"
    else:
        search_builtin = BuiltinSearchProvider(settings.search_builtin_endpoint, settings.search_timeout_s, settings.search_max_results)
        modes["search"] = "builtin"
    search_deepseek = DeepSeekSearchProvider(llm, settings.search_timeout_s)

    # ── push ──────────────────────────────────────────────────────────────
    if settings.push_enabled and settings.dingtalk_webhook:
        push: Any = DingTalkPush(settings.dingtalk_webhook, settings.dingtalk_secret)
        modes["push"] = "dingtalk"
    else:
        push = NullPush()
        modes["push"] = "null"

    if force_mock:
        modes["note"] = "demo/forced mock providers"
    logger.info("provider modes: %s", modes)
    return Providers(
        asr=asr,
        diarization=diarization,
        speaker=speaker,
        llm=llm,
        search_builtin=search_builtin,
        search_deepseek=search_deepseek,
        push=push,
        modes=modes,
    )


_providers: Providers | None = None


def get_providers() -> Providers:
    global _providers
    if _providers is None:
        _providers = build_providers()
    return _providers


def set_providers(providers: Providers | None) -> None:
    """Override the process-wide providers (tests / hot reload)."""
    global _providers
    _providers = providers


def reset_providers() -> None:
    global _providers
    _providers = None
