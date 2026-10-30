"""音频采集层。

所有音源统一输出 16-bit little-endian、单声道、16 kHz PCM 块流
（``sample_rate`` / ``channels`` 可配置，默认 16 kHz mono）。

后端：

* :class:`SoundDeviceSource` —— 生产环境，DJI Mic Mini 作为 USB 麦克风；
  依赖可选包 ``sounddevice``，按设备名子串匹配输入设备。
* :class:`FileSource` —— 开发/测试，回放 wav（自动降混为单声道、线性重采样）。
* :class:`SyntheticSource` —— 无硬件自测，生成正弦语音段 + 静音段。

依赖缺失时不会 import 失败，只有在真正使用对应后端时才抛
:class:`AudioSourceError`。
"""

from __future__ import annotations

import io
import logging
import math
import struct
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

__all__ = [
    "AudioSource",
    "AudioSourceError",
    "FileSource",
    "SoundDeviceSource",
    "SyntheticSource",
    "create_source",
    "pcm16_to_wav_bytes",
    "write_wav",
    "SILENCE",
    "SPEECH",
]

logger = logging.getLogger(__name__)

SILENCE = "silence"
SPEECH = "speech"

_DEFAULT_PATTERN: tuple[tuple[str, float], ...] = (
    (SPEECH, 1.0),
    (SILENCE, 0.5),
    (SPEECH, 1.2),
    (SILENCE, 0.7),
)


class AudioSourceError(RuntimeError):
    """音源不可用（缺少依赖、设备未找到、文件格式不支持等）。"""


class AudioSource:
    """PCM 块流音源基类。"""

    def __init__(self, sample_rate: int = 16000, channels: int = 1, block_ms: int = 30) -> None:
        if sample_rate <= 0:
            raise AudioSourceError("sample_rate must be > 0")
        if channels not in (1, 2):
            raise AudioSourceError("channels must be 1 or 2")
        if block_ms <= 0:
            raise AudioSourceError("block_ms must be > 0")
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_ms = block_ms

    # -- 子类实现 --------------------------------------------------------
    def open(self) -> None:  # pragma: no cover - 基类默认无操作
        """准备资源；可重复调用。"""

    def read_block(self) -> bytes | None:
        """读取一块 PCM；返回 ``None`` / ``b""`` 表示流结束。"""
        raise NotImplementedError

    def close(self) -> None:
        """释放资源；可重复调用。"""

    # -- 通用 ------------------------------------------------------------
    @property
    def block_frames(self) -> int:
        return max(1, int(self.sample_rate * self.block_ms / 1000))

    @property
    def block_bytes(self) -> int:
        return self.block_frames * 2 * self.channels

    def blocks(self) -> Iterator[bytes]:
        """迭代全部非空块。"""
        self.open()
        try:
            while True:
                block = self.read_block()
                if not block:
                    break
                yield block
        finally:
            self.close()

    def __enter__(self) -> "AudioSource":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"{type(self).__name__}(sample_rate={self.sample_rate}, "
            f"channels={self.channels}, block_ms={self.block_ms})"
        )


def _require_numpy():
    try:
        import numpy as np  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - 运行环境可选
        raise AudioSourceError(
            "numpy is required for this audio source; install 'numpy'"
        ) from exc
    return np


def pcm16_to_wav_bytes(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """把裸 PCM 包装成 wav 字节串（无第三方依赖）。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def write_wav(
    path: str | Path,
    pcm: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """把 PCM 写成 wav 文件，返回路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return target


# ---------------------------------------------------------------------------
# FileSource
# ---------------------------------------------------------------------------


class FileSource(AudioSource):
    """wav 文件回放（开发模式 / 测试）。

    支持 8/16/32-bit PCM（16-bit 以外会转换），自动降混单声道，
    采样率不一致时做线性重采样（质量足以驱动 VAD）。
    """

    def __init__(
        self,
        path: str | Path,
        sample_rate: int = 16000,
        channels: int = 1,
        block_ms: int = 30,
        *,
        loop: bool = False,
    ) -> None:
        super().__init__(sample_rate=sample_rate, channels=channels, block_ms=block_ms)
        self.path = Path(path)
        self.loop = loop
        self._pcm = b""
        self._offset = 0
        self._loaded = False

    def open(self) -> None:
        if self._loaded:
            self._offset = 0
            return
        self._pcm = self._load_pcm()
        self._offset = 0
        self._loaded = True

    def _load_pcm(self) -> bytes:
        if not self.path.exists():
            raise AudioSourceError(f"input wav not found: {self.path}")
        try:
            with wave.open(str(self.path), "rb") as wav:
                nchannels = wav.getnchannels()
                sampwidth = wav.getsampwidth()
                framerate = wav.getframerate()
                frames = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError) as exc:
            raise AudioSourceError(f"unsupported wav file {self.path}: {exc}") from exc

        samples = _decode_pcm(frames, sampwidth)
        if nchannels > 1:
            samples = _downmix(samples, nchannels)
        if framerate != self.sample_rate:
            samples = _resample(samples, framerate, self.sample_rate)
        return _encode_pcm16(samples)

    def read_block(self) -> bytes | None:
        if not self._loaded:
            self.open()
        if self._offset >= len(self._pcm):
            if self.loop and self._pcm:
                self._offset = 0
            else:
                return None
        end = self._offset + self.block_bytes
        chunk = self._pcm[self._offset : end]
        self._offset = end
        return chunk

    def close(self) -> None:
        self._offset = len(self._pcm) if not self.loop else self._offset


def _decode_pcm(data: bytes, sampwidth: int) -> list[int]:
    if sampwidth == 2:
        samples = array("h")
        samples.frombytes(data[: len(data) - (len(data) % 2)])
        if struct.pack("=H", 1) != struct.pack("<H", 1):  # pragma: no cover - 大端主机
            samples.byteswap()
        return list(samples)
    if sampwidth == 1:
        return [(byte - 128) * 256 for byte in data]
    if sampwidth in (3, 4):
        result: list[int] = []
        step = sampwidth
        for i in range(0, len(data) - step + 1, step):
            chunk = data[i : i + step]
            if step == 3:
                value = int.from_bytes(chunk, "little", signed=True)
                result.append(value >> 8)
            else:
                result.append(int.from_bytes(chunk, "little", signed=True) >> 16)
        return result
    raise AudioSourceError(f"unsupported sample width: {sampwidth} bytes")


def _encode_pcm16(samples: Sequence[int]) -> bytes:
    values = array("h", (_clip16(int(s)) for s in samples))
    if struct.pack("=H", 1) != struct.pack("<H", 1):  # pragma: no cover - 大端主机
        values.byteswap()
    return values.tobytes()


def _clip16(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return value


def _downmix(samples: Sequence[int], channels: int) -> list[int]:
    result = []
    for i in range(0, len(samples) - channels + 1, channels):
        total = 0
        for ch in range(channels):
            total += samples[i + ch]
        result.append(total // channels)
    return result


def _resample(samples: Sequence[int], src_rate: int, dst_rate: int) -> list[int]:
    if not samples or src_rate == dst_rate:
        return list(samples)
    count = max(1, int(round(len(samples) * dst_rate / src_rate)))
    ratio = src_rate / dst_rate
    result: list[int] = []
    last = len(samples) - 1
    for i in range(count):
        position = i * ratio
        left = int(position)
        right = min(left + 1, last)
        frac = position - left
        value = samples[left] * (1.0 - frac) + samples[right] * frac
        result.append(int(value))
    return result


# ---------------------------------------------------------------------------
# SyntheticSource
# ---------------------------------------------------------------------------


@dataclass
class _Tone:
    kind: str  # speech | silence
    seconds: float
    amplitude: float = 8000.0
    frequency: float = 440.0


class SyntheticSource(AudioSource):
    """合成音源自测：交替生成正弦“语音”和低噪静音。

    默认 pattern 约 3.4 秒，``repeat`` 可重复多轮，便于端到端测试。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        block_ms: int = 30,
        *,
        pattern: Sequence[tuple[str, float] | _Tone] | None = None,
        repeat: int = 1,
        speech_amplitude: float = 8000.0,
        speech_frequency: float = 440.0,
        noise_amplitude: float = 30.0,
    ) -> None:
        super().__init__(sample_rate=sample_rate, channels=channels, block_ms=block_ms)
        self.speech_amplitude = speech_amplitude
        self.speech_frequency = speech_frequency
        self.noise_amplitude = noise_amplitude
        self.repeat = max(1, int(repeat))
        tones: list[_Tone] = []
        raw_pattern = pattern if pattern is not None else _DEFAULT_PATTERN
        for item in raw_pattern:
            if isinstance(item, _Tone):
                tones.append(item)
            else:
                kind, seconds = item
                if kind not in (SPEECH, SILENCE):
                    raise AudioSourceError(f"unknown synthetic pattern kind: {kind}")
                tones.append(_Tone(kind, float(seconds)))
        if not tones:
            raise AudioSourceError("synthetic pattern must not be empty")
        self.pattern: tuple[_Tone, ...] = tuple(tones)
        self._pcm = b""
        self._offset = 0
        self._rng = 12345
        self._total_s = sum(t.seconds for t in self.pattern) * self.repeat

    # -- 生成 ------------------------------------------------------------
    def open(self) -> None:
        if self._pcm:
            return
        pcm = bytearray()
        for _ in range(self.repeat):
            for tone in self.pattern:
                pcm += self._render(tone)
        self._pcm = bytes(pcm)
        self._offset = 0

    def _render(self, tone: _Tone) -> bytes:
        count = max(1, int(round(tone.seconds * self.sample_rate)))
        samples = array("h")
        phase_step = 2.0 * math.pi * tone.frequency / self.sample_rate
        amplitude = tone.amplitude if tone.kind == SPEECH else 0.0
        if tone.kind == SPEECH and amplitude == 8000.0:
            amplitude = self.speech_amplitude
            phase_step = 2.0 * math.pi * self.speech_frequency / self.sample_rate
        for i in range(count):
            value = amplitude * math.sin(phase_step * i)
            value += self._noise()
            samples.append(_clip16(int(value)))
        return _encode_pcm16(samples)

    def _noise(self) -> float:
        # 确定性 LCG，避免测试随机性
        self._rng = (1103515245 * self._rng + 12345) & 0x7FFFFFFF
        return (self._rng / 0x7FFFFFFF - 0.5) * 2.0 * self.noise_amplitude

    def read_block(self) -> bytes | None:
        if not self._pcm:
            self.open()
        if self._offset >= len(self._pcm):
            return None
        end = self._offset + self.block_bytes
        chunk = self._pcm[self._offset : end]
        self._offset = end
        return chunk

    @property
    def duration_s(self) -> float:
        return self._total_s


# ---------------------------------------------------------------------------
# SoundDeviceSource
# ---------------------------------------------------------------------------


class SoundDeviceSource(AudioSource):
    """通过 ``sounddevice`` 从 USB 麦克风（DJI Mic Mini）采集。

    ``sounddevice`` 只在 :meth:`open` 时导入，缺少依赖时抛
    :class:`AudioSourceError`。设备名按子串、大小写不敏感匹配。
    """

    def __init__(
        self,
        device: str | int | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        block_ms: int = 30,
    ) -> None:
        super().__init__(sample_rate=sample_rate, channels=channels, block_ms=block_ms)
        self.device = device
        self.device_index: int | None = None
        self._stream = None
        self._sd = None

    def _resolve_device(self, sd: object) -> int | None:
        if isinstance(self.device, int):
            return self.device
        name = (self.device or "").strip()
        try:
            devices = sd.query_devices()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - 依赖 ALSA/Pulse
            raise AudioSourceError(f"cannot query audio devices: {exc}") from exc
        if not isinstance(devices, (list, tuple)):
            try:
                devices = list(devices)  # type: ignore[arg-type]
            except TypeError:  # pragma: no cover
                devices = []
        candidates: list[tuple[int, str]] = []
        for index, info in enumerate(devices):
            try:
                channels = int(info.get("max_input_channels", 0))
            except Exception:  # pragma: no cover - 设备信息异常
                channels = 0
            if channels <= 0:
                continue
            device_name = str(info.get("name", ""))
            candidates.append((index, device_name))
            if name and name.lower() in device_name.lower():
                return index
        if not name:
            try:
                default = sd.default.device  # type: ignore[attr-defined]
                if isinstance(default, (list, tuple)) and default:
                    default = default[0]
                if default is not None and int(default) >= 0:
                    return int(default)
            except Exception:  # pragma: no cover
                pass
            if candidates:
                return candidates[0][0]
            raise AudioSourceError("no audio input device found")
        available = ", ".join(f"{i}:{n}" for i, n in candidates) or "(none)"
        raise AudioSourceError(
            f"audio input device matching {name!r} not found; available: {available}"
        )

    def open(self) -> None:
        if self._stream is not None:
            return
        try:
            import sounddevice as sd  # type: ignore
        except ModuleNotFoundError as exc:
            raise AudioSourceError(
                "sounddevice is not installed; install 'sounddevice' or use "
                "--source file/synthetic"
            ) from exc
        self._sd = sd
        self.device_index = self._resolve_device(sd)
        try:
            stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_frames,
                device=self.device_index,
                channels=self.channels,
                dtype="int16",
            )
            stream.start()
        except Exception as exc:  # pragma: no cover - 需要真实声卡
            raise AudioSourceError(f"cannot open audio input: {exc}") from exc
        self._stream = stream

    def read_block(self) -> bytes | None:
        if self._stream is None:
            self.open()
        assert self._stream is not None
        try:
            data, overflowed = self._stream.read(self.block_frames)
        except Exception as exc:  # pragma: no cover - 运行期设备错误
            raise AudioSourceError(f"audio read failed: {exc}") from exc
        if overflowed:  # pragma: no cover - 实时性告警
            logger.warning("audio input overflow (block dropped by driver)")
        if not data:
            return None
        return bytes(data)

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # pragma: no cover - 关闭失败不影响退出
                logger.debug("error while closing audio stream", exc_info=True)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def create_source(
    config: object,
    *,
    source: str | None = None,
    input_path: str | Path | None = None,
) -> AudioSource:
    """按配置/显式参数构造音源。

    ``config`` 需要提供 ``record`` / ``vad`` 属性（:class:`AgentConfig`）。
    """
    record = getattr(config, "record", config)
    kind = (source or getattr(record, "source", "device") or "device").lower()
    path = input_path if input_path is not None else getattr(record, "input_path", "")
    sample_rate = int(getattr(record, "sample_rate", 16000))
    channels = int(getattr(record, "channels", 1))
    block_ms = int(getattr(record, "block_ms", 30))
    device = getattr(record, "device", None)

    if kind == "file":
        if not path:
            raise AudioSourceError("file source requires input_path (--input demo.wav)")
        return FileSource(path, sample_rate=sample_rate, channels=channels, block_ms=block_ms)
    if kind == "synthetic":
        return SyntheticSource(sample_rate=sample_rate, channels=channels, block_ms=block_ms)
    if kind == "device":
        return SoundDeviceSource(
            device=device,
            sample_rate=sample_rate,
            channels=channels,
            block_ms=block_ms,
        )
    raise AudioSourceError(f"unknown audio source: {kind}")


def iter_frames(blocks: Iterable[bytes], frame_bytes: int) -> Iterator[bytes]:
    """把 PCM 块流切成定长帧（末尾不足一帧的丢弃）。"""
    if frame_bytes <= 0:
        raise ValueError("frame_bytes must be > 0")
    pending = b""
    for block in blocks:
        if not block:
            continue
        pending += block
        while len(pending) >= frame_bytes:
            yield pending[:frame_bytes]
            pending = pending[frame_bytes:]
