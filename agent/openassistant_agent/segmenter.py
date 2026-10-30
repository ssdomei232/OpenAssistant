"""语音切片。

把逐帧 VAD 判定组装成可上传的 :class:`SpeechSegment`：

* ``preroll_s`` / ``postroll_s``：语音前后各保留一段上下文；
* 相邻语音之间短于 ``min_silence_ms`` 的静音视为同一段（合并）；
* 实际语音短于 ``min_speech_ms`` 的片段直接丢弃（不上传）；
* 单段超过 ``max_segment_s`` 强制切分，保证上传体量可控。

只输出包含人声的片段。
"""

from __future__ import annotations

import io
import math
import wave
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from .capture import iter_frames
from .vad import FrameVAD, create_vad

__all__ = [
    "Segmenter",
    "SpeechSegment",
    "frame_bytes_for",
    "run_segmenter",
    "split_frames",
]

_EPS = 1e-9


def frame_bytes_for(sample_rate: int, frame_ms: int, channels: int = 1) -> int:
    """一帧 PCM 的字节数。"""
    samples = int(sample_rate * frame_ms / 1000)
    if samples <= 0:
        raise ValueError("frame_ms too small for sample_rate")
    return samples * 2 * channels


def split_frames(pcm: bytes, frame_bytes: int) -> list[bytes]:
    """把 PCM 切成定长帧，末帧补零到整帧（用于离线处理）。"""
    if frame_bytes <= 0:
        raise ValueError("frame_bytes must be > 0")
    frames = [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]
    if frames and len(frames[-1]) < frame_bytes:
        frames[-1] = frames[-1] + b"\x00" * (frame_bytes - len(frames[-1]))
    return frames


@dataclass
class SpeechSegment:
    """一个含人声的音频片段。"""

    start: float  # 相对采集流起点的秒数
    end: float
    pcm: bytes  # 16-bit mono PCM
    speech_ratio: float = 0.0
    sample_rate: int = 16000
    channels: int = 1
    # 片段内的语音区间（相对片段起点，秒），写入上传 meta.vad.segments
    speech_spans: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return round(self.end - self.start, 6)

    @property
    def nbytes(self) -> int:
        return len(self.pcm)

    @property
    def frames(self) -> int:
        frame_bytes = 2 * self.channels
        return len(self.pcm) // frame_bytes

    def to_wav_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(self.pcm)
        return buffer.getvalue()

    def to_meta(self) -> dict:
        """片段自身的描述（duration / vad），供上传 meta 使用。"""
        return {
            "duration_s": self.duration_s,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "vad": {
                "speech_ratio": round(self.speech_ratio, 4),
                "segments": [[round(a, 3), round(b, 3)] for a, b in self.speech_spans],
            },
        }


class Segmenter:
    """流式切片器：``push(frame, is_speech)`` -> 已完成片段列表。"""

    def __init__(
        self,
        vad_config: object,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_ms: int | None = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.frame_ms = int(frame_ms or getattr(vad_config, "frame_ms", 30))
        self.frame_s = self.frame_ms / 1000.0
        self.frame_bytes = frame_bytes_for(self.sample_rate, self.frame_ms, self.channels)

        self.min_speech_ms = int(getattr(vad_config, "min_speech_ms", 300))
        self.min_silence_ms = int(getattr(vad_config, "min_silence_ms", 500))
        self.max_segment_s = float(getattr(vad_config, "max_segment_s", 60.0))
        self.preroll_s = float(getattr(vad_config, "preroll_s", 0.3))
        self.postroll_s = float(getattr(vad_config, "postroll_s", 0.3))

        if self.max_segment_s <= 0:
            raise ValueError("max_segment_s must be > 0")
        self.min_speech_frames = max(
            1, int(math.ceil(self.min_speech_ms / self.frame_ms)) if self.min_speech_ms > 0 else 0
        )
        self.min_silence_frames = max(
            1, int(math.ceil(self.min_silence_ms / self.frame_ms))
        ) if self.min_silence_ms > 0 else 1
        self.preroll_frames = max(0, int(round(self.preroll_s / self.frame_s)))

        self._history: deque[tuple[float, bytes, bool]] = deque(maxlen=self.preroll_frames)
        self._frames: list[tuple[float, bytes, bool]] = []
        self._index = 0
        self._in_speech = False
        self._seg_start = 0.0
        self._last_speech_end = 0.0
        self._silence = 0

    # -- 状态 ------------------------------------------------------------
    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def elapsed_s(self) -> float:
        return self._index * self.frame_s

    def reset(self) -> None:
        self._history.clear()
        self._frames = []
        self._index = 0
        self._in_speech = False
        self._seg_start = 0.0
        self._last_speech_end = 0.0
        self._silence = 0

    # -- 主流程 ----------------------------------------------------------
    def push(self, frame: bytes, is_speech: bool) -> list[SpeechSegment]:
        """输入一帧及其 VAD 判定，返回本次产生的完整片段（通常 0 或 1 个）。"""
        if len(frame) != self.frame_bytes:
            raise ValueError(
                f"frame must be exactly {self.frame_bytes} bytes, got {len(frame)}"
            )
        t = self._index * self.frame_s
        self._index += 1
        out: list[SpeechSegment] = []

        if not self._in_speech:
            self._history.append((t, frame, bool(is_speech)))
            if is_speech:
                frames = list(self._history)
                self._history.clear()
                self._start_segment(frames)
                out.extend(self._check_max_length())
            return out

        self._frames.append((t, frame, bool(is_speech)))
        if is_speech:
            self._silence = 0
            self._last_speech_end = t + self.frame_s
            out.extend(self._check_max_length())
        else:
            self._silence += 1
            if self._silence >= self.min_silence_frames:
                segment = self._close(self._last_speech_end + self.postroll_s)
                if segment is not None:
                    out.append(segment)
        return out

    def flush(self) -> list[SpeechSegment]:
        """流结束时收尾：把仍在进行的语音段闭合。"""
        out: list[SpeechSegment] = []
        if self._in_speech and self._frames:
            segment = self._close(self._last_speech_end + self.postroll_s)
            if segment is not None:
                out.append(segment)
        self._history.clear()
        self._frames = []
        self._in_speech = False
        return out

    # -- 内部 ------------------------------------------------------------
    def _start_segment(self, frames: list[tuple[float, bytes, bool]]) -> None:
        self._in_speech = True
        self._frames = frames
        self._seg_start = frames[0][0]
        self._silence = 0
        speech = [f for f in frames if f[2]]
        self._last_speech_end = (speech[-1][0] + self.frame_s) if speech else frames[0][0]

    def _check_max_length(self) -> list[SpeechSegment]:
        if not self._frames:
            return []
        cut_end = self._seg_start + self.max_segment_s
        last_end = self._frames[-1][0] + self.frame_s
        if last_end <= cut_end + 1e-6:
            return []

        split_at = 0
        for i, (t, _frame, _speech) in enumerate(self._frames):
            if t + self.frame_s <= cut_end + 1e-6:
                split_at = i + 1
            else:
                break
        split_at = max(1, split_at)
        kept = self._frames[:split_at]
        rest = self._frames[split_at:]

        segment = self._build_segment(kept, enforce_min_speech=False)
        self._frames = rest
        if rest:
            self._seg_start = rest[0][0]
            self._silence = 0
            speech = [f for f in rest if f[2]]
            self._last_speech_end = (
                (speech[-1][0] + self.frame_s) if speech else rest[0][0]
            )
        else:  # pragma: no cover - rest 至少包含触发切分的那一帧
            self._in_speech = False
            self._seg_start = 0.0
        return [segment] if segment is not None else []

    def _close(self, end_t: float) -> SpeechSegment | None:
        frames = self._frames
        if not frames:
            self._in_speech = False
            self._silence = 0
            return None

        # 只保留起点早于 end_t 的帧（postroll 之外的拖尾静音丢弃）
        kept = [f for f in frames if f[0] < end_t - _EPS]
        if not kept:
            kept = frames[:1]
        segment = self._build_segment(kept)

        self._frames = []
        self._in_speech = False
        self._silence = 0
        self._seg_start = 0.0
        return segment

    def _build_segment(
        self,
        frames: Sequence[tuple[float, bytes, bool]],
        *,
        enforce_min_speech: bool = True,
    ) -> SpeechSegment | None:
        if not frames:
            return None
        speech_frames = sum(1 for f in frames if f[2])
        if enforce_min_speech and speech_frames < self.min_speech_frames:
            return None
        if speech_frames == 0:
            return None

        pcm = b"".join(f[1] for f in frames)
        start = frames[0][0]
        end = frames[-1][0] + self.frame_s
        spans: list[tuple[float, float]] = []
        run_start: float | None = None
        for t, _frame, is_speech in frames:
            if is_speech:
                if run_start is None:
                    run_start = t
            elif run_start is not None:
                spans.append((run_start - start, t - start))
                run_start = None
        if run_start is not None:
            spans.append((run_start - start, end - start))

        return SpeechSegment(
            start=round(start, 6),
            end=round(end, 6),
            pcm=pcm,
            speech_ratio=round(speech_frames / len(frames), 4),
            sample_rate=self.sample_rate,
            channels=self.channels,
            speech_spans=[(round(a, 3), round(b, 3)) for a, b in spans],
        )


def run_segmenter(
    blocks: Iterable[bytes],
    vad_config: object,
    *,
    vad: FrameVAD | None = None,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Iterator[SpeechSegment]:
    """把 PCM 块流一次性切成语音片段（离线工具/测试用）。"""
    active_vad = vad or create_vad(vad_config, sample_rate=sample_rate)
    segmenter = Segmenter(vad_config, sample_rate=sample_rate, channels=channels)
    fb = frame_bytes_for(sample_rate, int(getattr(vad_config, "frame_ms", 30)), channels)
    for frame in iter_frames(blocks, fb):
        for segment in segmenter.push(frame, active_vad.is_speech(frame)):
            yield segment
    for segment in segmenter.flush():
        yield segment
