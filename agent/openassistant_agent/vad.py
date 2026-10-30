"""语音活动检测（VAD）。

优先使用 ``webrtcvad``（若安装），否则退回纯标准库实现的
:class:`EnergyVAD`：自适应噪声底 + 可配置阈值系数。两者接口一致，
都按固定帧长（10/20/30 ms）判断一帧是否包含语音。

本模块不依赖 numpy，树莓派上无第三方包也能跑。
"""

from __future__ import annotations

import logging
import math
from array import array
from typing import Iterable, Iterator

__all__ = [
    "EnergyVAD",
    "FrameVAD",
    "WebrtcVAD",
    "create_vad",
    "frame_rms",
    "pcm16_samples",
    "rms_to_dbfs",
]

logger = logging.getLogger(__name__)

SUPPORTED_FRAME_MS = (10, 20, 30)
_INT16_SCALE = 32768.0


def pcm16_samples(frame: bytes) -> array:
    """解码 16-bit little-endian PCM 为 ``array('h')``。"""
    if len(frame) % 2:
        frame = frame[: len(frame) - 1]
    samples = array("h")
    samples.frombytes(frame)
    return samples


def frame_rms(frame: bytes) -> float:
    """一帧 PCM 的 RMS（0..32767）。"""
    samples = pcm16_samples(frame)
    if not samples:
        return 0.0
    total = 0.0
    for value in samples:
        total += float(value) * float(value)
    return math.sqrt(total / len(samples))


def rms_to_dbfs(rms: float) -> float:
    """RMS 转 dBFS（-inf..0），仅用于日志和阈值调试。"""
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(min(rms, _INT16_SCALE) / _INT16_SCALE)


class FrameVAD:
    """逐帧 VAD 接口。"""

    frame_ms: int = 30

    def is_speech(self, frame: bytes) -> bool:
        raise NotImplementedError

    def reset(self) -> None:
        """清空自适应状态（换场景/重连时调用）。"""

    @property
    def threshold(self) -> float | None:
        """当前判定阈值（能量 VAD 暴露，便于日志/自检）。"""
        return None

    @property
    def noise_floor(self) -> float | None:
        return None


class EnergyVAD(FrameVAD):
    """自适应能量门限 VAD。

    噪声底用指数滑动平均估计：

    * 判为静音的帧按 ``noise_alpha`` 更新噪声底；
    * 仅略高于门限（``rms <= threshold * noise_margin``）的帧很可能是环境噪声，
      用更小的系数缓慢跟随，避免噪声升高后永久误判；
    * 明显高于门限的帧视为语音，不污染噪声底。

    门限 = ``max(absolute_floor, noise_floor * energy_factor)``，
    ``energy_factor`` 随 ``aggressiveness`` 增大而增大。
    """

    def __init__(
        self,
        aggressiveness: int = 2,
        frame_ms: int = 30,
        *,
        energy_factor: float | None = None,
        absolute_floor: float = 120.0,
        adaptive_noise: bool = True,
        noise_alpha: float = 0.05,
        noise_margin: float = 4.0,
    ) -> None:
        if frame_ms not in SUPPORTED_FRAME_MS:
            raise ValueError(f"frame_ms must be one of {SUPPORTED_FRAME_MS}")
        if not (0 <= aggressiveness <= 3):
            raise ValueError("aggressiveness must be within 0..3")
        self.aggressiveness = int(aggressiveness)
        self.frame_ms = int(frame_ms)
        # aggressiveness 0 -> 1.5x, 1 -> 1.9x, 2 -> 2.5x, 3 -> 3.3x
        self.energy_factor = (
            float(energy_factor) if energy_factor is not None else 1.5 + 0.6 * self.aggressiveness
        )
        self.absolute_floor = float(absolute_floor)
        self.adaptive_noise = bool(adaptive_noise)
        self.noise_alpha = float(noise_alpha)
        self.noise_margin = float(noise_margin)
        self._frames_seen = 0
        self._noise_floor = max(1.0, self.absolute_floor * 0.5)
        self._threshold = self.absolute_floor

    # -- 状态 ------------------------------------------------------------
    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    def reset(self) -> None:
        self._frames_seen = 0
        self._noise_floor = max(1.0, self.absolute_floor * 0.5)
        self._threshold = self.absolute_floor

    def _update_threshold(self) -> None:
        self._threshold = max(self.absolute_floor, self._noise_floor * self.energy_factor)

    def _update_noise_floor(self, rms: float, alpha: float) -> None:
        self._noise_floor = (1.0 - alpha) * self._noise_floor + alpha * rms
        self._update_threshold()

    # -- 判定 ------------------------------------------------------------
    def is_speech(self, frame: bytes) -> bool:
        rms = frame_rms(frame)
        is_speech = rms > self._threshold

        if not is_speech:
            if self.adaptive_noise:
                self._update_noise_floor(rms, self.noise_alpha)
            else:
                self._noise_floor = rms
                self._update_threshold()
        elif self.adaptive_noise and rms <= self._threshold * self.noise_margin:
            # 只略高于门限、很可能是环境噪声抬升：缓慢跟随
            self._update_noise_floor(rms, self.noise_alpha * 0.4)

        self._frames_seen += 1
        return is_speech


class WebrtcVAD(FrameVAD):
    """``webrtcvad`` 适配器（可选依赖，缺省时由 :func:`create_vad` 兜底）。"""

    def __init__(self, aggressiveness: int = 2, frame_ms: int = 30, sample_rate: int = 16000) -> None:
        try:
            import webrtcvad  # type: ignore
        except ModuleNotFoundError as exc:
            raise ImportError("webrtcvad is not installed") from exc
        if frame_ms not in SUPPORTED_FRAME_MS:
            raise ValueError(f"frame_ms must be one of {SUPPORTED_FRAME_MS}")
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("webrtcvad supports 8/16/32/48 kHz")
        self._webrtcvad = webrtcvad
        self._vad = webrtcvad.Vad(int(aggressiveness))
        self.aggressiveness = int(aggressiveness)
        self.frame_ms = int(frame_ms)
        self.sample_rate = int(sample_rate)
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        self._speech = False

    def is_speech(self, frame: bytes) -> bool:
        if len(frame) != self.frame_bytes:
            self._speech = False
            return False
        try:
            self._speech = bool(self._vad.is_speech(frame, self.sample_rate))
        except Exception:  # pragma: no cover - 底层库异常
            logger.debug("webrtcvad failed on frame", exc_info=True)
            self._speech = False
        return self._speech

    def reset(self) -> None:
        self._speech = False

    @property
    def threshold(self) -> float | None:
        return None


def create_vad(config: object, sample_rate: int = 16000) -> FrameVAD:
    """按配置构造 VAD：优先 webrtcvad，缺失时退回能量 VAD。"""
    vad_config = getattr(config, "vad", config)
    frame_ms = int(getattr(vad_config, "frame_ms", 30))
    aggressiveness = int(getattr(vad_config, "aggressiveness", 2))

    try:
        vad = WebrtcVAD(aggressiveness=aggressiveness, frame_ms=frame_ms, sample_rate=sample_rate)
        logger.debug("using webrtcvad (aggressiveness=%s)", aggressiveness)
        return vad
    except (ImportError, ValueError):
        pass

    energy = EnergyVAD(
        aggressiveness=aggressiveness,
        frame_ms=frame_ms,
        energy_factor=getattr(vad_config, "energy_factor", None),
        absolute_floor=float(getattr(vad_config, "absolute_floor", 120.0)),
        adaptive_noise=bool(getattr(vad_config, "adaptive_noise", True)),
    )
    logger.debug(
        "webrtcvad unavailable, falling back to EnergyVAD (factor=%.2f)",
        energy.energy_factor,
    )
    return energy


def iter_frame_decisions(
    frames: Iterable[bytes], vad: FrameVAD
) -> Iterator[tuple[bytes, bool]]:
    """对帧流逐帧做 VAD，产出 ``(frame, is_speech)``。"""
    for frame in frames:
        yield frame, vad.is_speech(frame)
