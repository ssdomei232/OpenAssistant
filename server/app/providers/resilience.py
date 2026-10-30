"""Provider 运行时降级包装。

真实模型（pyannote / faster-whisper）可能因为模型未授权、显卡不可用、网络失败等
原因在**首次调用时**才暴露问题。直接让异常冒泡会导致每段音频都失败重试；
这里在第一次失败后自动切换到离线实现（energy / spectral），并只告警一次，
保证录音/卡片主流程继续可用，同时把 /health 里的 mode 更新为 fallback。
"""
from __future__ import annotations

import logging
from typing import Any

from ..providers.types import DiarizationSegment

logger = logging.getLogger(__name__)


class ResilientDiarizer:
    """主 diarizer 失败时自动降级到能量 VAD 分段。"""

    def __init__(self, primary: Any, fallback: Any, modes: dict[str, str] | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self._failed = False
        self._modes = modes

    @property
    def name(self) -> str:
        return getattr(self.fallback, "name", "energy") if self._failed else getattr(self.primary, "name", "pyannote")

    @property
    def active_name(self) -> str:
        return getattr(self.primary if not self._failed else self.fallback, "name", "unknown")

    def diarize(self, samples: Any, sr: int) -> list[DiarizationSegment]:
        if self._failed:
            return self.fallback.diarize(samples, sr)
        try:
            return self.primary.diarize(samples, sr)
        except Exception as exc:  # noqa: BLE001 - 第三方模型异常类型不可控
            self._failed = True
            if self._modes is not None:
                self._modes["diarization"] = f"{getattr(self.fallback, 'name', 'energy')}(fallback)"
            logger.error(
                "diarization provider %r failed: %s: %s. Falling back to %r for this and "
                "subsequent segments. Check OA_HF_TOKEN / HF model terms, GPU availability, "
                "or set OA_DIARIZATION_PROVIDER=energy to silence this.",
                getattr(self.primary, "name", "pyannote"),
                type(exc).__name__,
                exc,
                getattr(self.fallback, "name", "energy"),
            )
            return self.fallback.diarize(samples, sr)


class ResilientEmbedding:
    """主声纹 embedding 失败时自动降级到谱特征（离线）。"""

    def __init__(self, primary: Any, fallback: Any, modes: dict[str, str] | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self._failed = False
        self._modes = modes

    @property
    def name(self) -> str:
        return getattr(self.fallback, "name", "spectral") if self._failed else getattr(self.primary, "name", "pyannote-embedding")

    @property
    def active_name(self) -> str:
        return getattr(self.primary if not self._failed else self.fallback, "name", "unknown")

    def embed(self, samples: Any, sr: int) -> list[float]:
        if self._failed:
            return self.fallback.embed(samples, sr)
        try:
            return self.primary.embed(samples, sr)
        except Exception as exc:  # noqa: BLE001 - 第三方模型异常类型不可控
            self._failed = True
            if self._modes is not None:
                self._modes["speaker"] = f"{getattr(self.fallback, 'name', 'spectral')}(fallback)"
            logger.error(
                "speaker embedding provider %r failed: %s: %s. Falling back to %r. "
                "Check OA_HF_TOKEN / HF model terms or set OA_SPEAKER_PROVIDER=spectral.",
                getattr(self.primary, "name", "pyannote-embedding"),
                type(exc).__name__,
                exc,
                getattr(self.fallback, "name", "spectral"),
            )
            return self.fallback.embed(samples, sr)
