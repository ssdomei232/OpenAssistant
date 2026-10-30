"""ASR providers: faster-whisper (production) and a deterministic mock."""
from __future__ import annotations

import hashlib
import logging
import os
import re

import numpy as np

from .cuda import prepare_cuda_dlls
from .types import ASRResult, ASRWord

logger = logging.getLogger(__name__)

_CUDA_ERROR_HINTS = ("cublas", "cudnn", "cuda", "gpu", "out of memory", "library cublas")


def _looks_like_cuda_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in message for hint in _CUDA_ERROR_HINTS)

# A small deterministic corpus used by MockASR so the whole pipeline (cards,
# schedule extraction, commands, meetings) is demoable on machines without a
# GPU / without downloading models.  Real deployments use FasterWhisperASR.
MOCK_CORPUS = [
    "帮我记一下明天下午三点和周磊在三号会议室讨论 Q3 排期。",
    "好的，那我把资料提前发给你，你看看还有没有要补充的。",
    "另外提醒我周五之前把预算表交给财务，别忘了。",
    "这个方案我觉得可以先做一个小范围试点，风险比较可控。",
    "下周一的评审会改到上午十点，地点还是原来的会议室。",
    "帮我查一下上海明天的天气怎么样，要不要带伞。",
    "我最近在关注 AI 编程助手和开源模型的进展。",
    "中午吃什么？要不还是去楼下那家面馆吧。",
    "这个需求先放一放，等客户确认了预算再启动。",
    "会议纪要记得发我一份，我晚点看。",
]


class MockASR:
    """Deterministic ASR for development, CI and demos.

    Splits the audio into pseudo-utterances at silence gaps and assigns text
    from ``MOCK_CORPUS`` keyed by a hash of the audio content.  Confidence is
    not modelled - the point is a stable, offline pipeline.
    """

    name = "mock"

    def __init__(self, seed_salt: str = "openassistant") -> None:
        self.salt = seed_salt

    def transcribe(self, samples: np.ndarray, sr: int) -> ASRResult:
        duration = float(samples.size) / sr if sr else 0.0
        if duration <= 0:
            return ASRResult(text="", words=[])

        digest = hashlib.sha256(np.asarray(samples, dtype=np.float32).tobytes()).hexdigest()
        start_index = int(digest[:8], 16) % len(MOCK_CORPUS)

        # Split into 2-5 utterances placed across the audio, aligned to silence.
        intervals = _speech_intervals(samples, sr)
        if not intervals:
            intervals = [(0.0, duration)]
        chunks: list[tuple[float, float]] = []
        target = max(1, min(5, len(intervals)))
        step = max(1, len(intervals) // target)
        for i in range(0, len(intervals), step):
            a, b = intervals[i]
            if chunks and a - chunks[-1][1] < 0.25:
                chunks[-1] = (chunks[-1][0], b)
            else:
                chunks.append((a, b))
        if not chunks:
            chunks = [(0.0, duration)]

        words: list[ASRWord] = []
        texts: list[str] = []
        for idx, (a, b) in enumerate(chunks):
            sentence = MOCK_CORPUS[(start_index + idx) % len(MOCK_CORPUS)]
            texts.append(sentence)
            tokens = _tokenize(sentence)
            if not tokens:
                continue
            span = max(0.4, b - a)
            per = span / len(tokens)
            cursor = a
            for tok in tokens:
                words.append(ASRWord(round(cursor, 3), round(cursor + per, 3), tok))
                cursor += per
        return ASRResult(language="zh", words=words, text="".join(texts))


def _tokenize(sentence: str) -> list[str]:
    """Split Chinese text into word-ish tokens, keeping punctuation attached."""
    tokens = re.findall(r"[A-Za-z0-9]+|[^\sA-Za-z0-9]", sentence)
    merged: list[str] = []
    for token in tokens:
        if merged and len(merged[-1]) + len(token) <= 3 and token not in "，。！？、；：":
            merged[-1] += token
        else:
            merged.append(token)
    return merged


def _speech_intervals(samples: np.ndarray, sr: int, frame_s: float = 0.05) -> list[tuple[float, float]]:
    """Cheap energy-based speech intervals used by the mock ASR only."""
    frame = max(1, int(frame_s * sr))
    n = samples.size // frame
    if n <= 0:
        return []
    trimmed = samples[: n * frame].reshape(n, frame)
    energies = np.sqrt(np.mean(np.square(trimmed, dtype=np.float64), axis=1))
    if energies.size == 0:
        return []
    threshold = max(float(np.median(energies)) * 1.6, 0.004)
    voiced = energies > threshold
    intervals: list[tuple[float, float]] = []
    start: int | None = None
    for i, flag in enumerate(voiced):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start >= 2:
                intervals.append((start * frame_s, i * frame_s))
            start = None
    if start is not None:
        intervals.append((start * frame_s, n * frame_s))
    # merge close intervals
    merged: list[list[float]] = []
    for a, b in intervals:
        if merged and a - merged[-1][1] < 0.4:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


class FasterWhisperASR:
    """faster-whisper (large-v3 / large-v3-turbo) with word timestamps."""

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        *,
        cuda_dll_dirs: str = "",
        language: str = "zh",
    ) -> None:
        self.name = f"faster-whisper:{model_size}"
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cuda_dll_dirs = cuda_dll_dirs
        self.language = (language or "").strip()
        self._model = None
        self._cuda_prepared = False

    # ── 模型加载 ─────────────────────────────────────────────────────────
    def _make_model(self, device: str, compute_type: str):
        if os.name == "nt" and not self._cuda_prepared:
            self._cuda_prepared = True
            try:
                prepare_cuda_dlls(self.cuda_dll_dirs)
            except Exception:  # pragma: no cover - 探测失败不影响启动
                logger.debug("failed to prepare CUDA DLL dirs", exc_info=True)
        from faster_whisper import WhisperModel  # type: ignore

        return WhisperModel(self.model_size, device=device, compute_type=compute_type)

    def _fallback_cpu(self, reason: str) -> None:
        logger.warning(
            "faster-whisper GPU 不可用（%s）；自动切换 CPU int8 继续转写（会明显变慢）。"
            "如需 GPU：安装 CUDA 12 + cuDNN 9 运行时，或用 OA_CUDA_DLL_DIRS 指向包含 "
            "cublas64_12.dll/cudnn*_9.dll 的目录。",
            reason,
        )
        self.device = "cpu"
        self.compute_type = "int8"
        self.name = f"faster-whisper:{self.model_size}@cpu"
        self._model = self._make_model("cpu", "int8")

    def _load(self):
        try:
            self._model = self._make_model(self.device, self.compute_type)
        except Exception as exc:  # noqa: BLE001 - CUDA/驱动/模型下载错误类型不定
            if self.device == "cpu":
                raise
            self._fallback_cpu(f"{type(exc).__name__}: {exc}")
        return self._model

    # ── 转写 ────────────────────────────────────────────────────────────
    def _run(self, audio: np.ndarray) -> ASRResult:
        model = self._model or self._load()
        kwargs = {"word_timestamps": True, "vad_filter": True, "condition_on_previous_text": False}
        if self.language:
            kwargs["language"] = self.language
        segments, info = model.transcribe(audio, **kwargs)
        words: list[ASRWord] = []
        texts: list[str] = []
        # generator 是惰性的：编码/推理错误可能在迭代时抛出，必须在这里捕获
        for segment in segments:
            texts.append(segment.text.strip())
            for word in getattr(segment, "words", None) or []:
                words.append(ASRWord(float(word.start), float(word.end), word.word))
        return ASRResult(
            language=getattr(info, "language", self.language or "zh") or "zh",
            words=words,
            text="".join(texts),
        )

    def transcribe(self, samples: np.ndarray, sr: int) -> ASRResult:
        audio = np.asarray(samples, dtype=np.float32)
        try:
            return self._run(audio)
        except Exception as exc:  # noqa: BLE001 - 第三方库错误类型不定
            if self.device != "cpu" and _looks_like_cuda_error(exc):
                self._fallback_cpu(f"{type(exc).__name__}: {exc}")
                return self._run(audio)
            raise
