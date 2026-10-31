"""ASR providers: FunASR (production), faster-whisper (legacy fallback) and a mock.

生产环境默认使用 **FunASR**（Paraformer-zh + FSMN-VAD + CT-Punc，见 ``FunASRASR``），
它原生支持中文、词级时间戳与标点恢复。为防止老部署没有安装 FunASR，仍保留
``FasterWhisperASR`` 作为可选回退；两者都不可用时使用确定性的 ``MockASR``。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata

import numpy as np

from .cuda import prepare_cuda_dlls
from .types import ASRResult, ASRWord

logger = logging.getLogger(__name__)

_CUDA_ERROR_HINTS = ("cublas", "cudnn", "cuda", "gpu", "out of memory", "library cublas")


def _looks_like_cuda_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in message for hint in _CUDA_ERROR_HINTS)


def _cuda_runtime_available() -> bool:
    """torch 存在时先判断显卡是否真的可用，避免无谓地按 cuda 加载模型。

    torch 不可用时返回 True，让真实的加载错误走统一的回退/报错逻辑。
    """
    try:
        import torch  # type: ignore
    except Exception:  # noqa: BLE001 - 未安装 torch 时交给 FunASR 自己报错
        return True
    try:
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - 驱动异常
        return False


# A small deterministic corpus used by MockASR so the whole pipeline (cards,
# schedule extraction, commands, meetings) is demoable on machines without a
# GPU / without downloading models.  Real deployments use FunASRASR.
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


# ── FunASR（生产默认） ────────────────────────────────────────────────────────
_FUNASR_SAMPLE_RATE = 16000
# SenseVoice 在文本里附带 <|zh|> / <|NEUTRAL|> / <|Speech|> 等标签，需要去掉
_FUNASR_TAG = re.compile(r"<\|[^|>]*\|>")
_FUNASR_LANGUAGE_TAG = re.compile(r"<\|(zh|en|ja|ko|yue|auto)\|>", re.IGNORECASE)


def _funasr_model_alias(model: str) -> str:
    """``iic/speech_paraformer...`` -> 短名字，用于 /health 展示。"""
    alias = (model or "").strip().rstrip("/").rsplit("/", 1)[-1]
    return alias or "unknown"


def _as_records(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_span(value: object) -> tuple[float, float] | None:
    """FunASR 时间戳可能是 ``[start, end]``，也可能是 ``{"start": ..., "end": ...}``。"""
    if isinstance(value, dict):
        start = value.get("start", value.get("begin", value.get("ts_start")))
        end = value.get("end", value.get("finish", value.get("ts_end")))
        if start is None or end is None:
            # Fun-ASR-Nano 的 dict 时间戳：start_time / end_time，单位秒
            start = value.get("start_time")
            end = value.get("end_time")
        if start is None or end is None:
            return None
        return _safe_float(start), _safe_float(end)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _safe_float(value[0]), _safe_float(value[1])
    return None


def _raw_spans(record: dict) -> list[tuple[float, float]]:
    spans = [span for span in (_coerce_span(v) for v in _as_records(record.get("timestamp"))) if span]
    for sentence in _as_records(record.get("sentence_info")):
        if isinstance(sentence, dict):
            spans += [span for span in (_coerce_span(v) for v in _as_records(sentence.get("timestamp"))) if span]
    return spans


def _funasr_time_scale(records: list[dict], duration: float) -> float:
    """FunASR 的时间戳单位是毫秒；个别版本/模型会返回秒，这里做个兜底判断。"""
    raw_max = 0.0
    for record in records:
        for _start, end in _raw_spans(record):
            raw_max = max(raw_max, end)
        for sentence in _as_records(record.get("sentence_info")):
            if isinstance(sentence, dict):
                raw_max = max(raw_max, _safe_float(sentence.get("start")), _safe_float(sentence.get("end")))
    if raw_max <= 0:
        return 1.0
    # 毫秒值通常远大于音频时长（秒）；秒值不会超过时长
    return 0.001 if raw_max > max(duration, 0.0) * 1.5 else 1.0


def _clean_funasr_text(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return _FUNASR_TAG.sub("", text).strip()


def _text_units(text: str) -> list[str]:
    """按"可对齐单元"切分：中文按字、英文按字符，标点附着到前一个单元。

    FunASR 的标点由 CT-Punc 后处理生成，不占时间戳；因此对齐时要让标点跟随
    前一个 token，避免时间戳数量与字符数对不上。
    """
    units: list[str] = []
    for ch in text:
        if ch.isspace():
            continue
        category = unicodedata.category(ch)
        if category.startswith("P") or category.startswith("S"):
            if units:
                units[-1] += ch
            else:
                units.append(ch)
        else:
            units.append(ch)
    return units


def _split_text_evenly(text: str, pieces: int) -> list[str]:
    units = _text_units(text)
    if not units or pieces <= 0:
        return []
    out: list[str] = []
    for idx in range(pieces):
        start = round(idx * len(units) / pieces)
        end = round((idx + 1) * len(units) / pieces)
        out.append("".join(units[start:end]))
    return out


def _words_from_spans(text: str, spans: list[tuple[float, float]]) -> list[ASRWord]:
    """把 FunASR 文本与逐 token 时间戳配对成 ``ASRWord``。

    Paraformer 的时间戳通常按中文字符（英文按单词）对齐；若数量对不上，
    按字符均分兜底，保证文本不会因为时间戳格式差异而丢失。
    """
    if not spans:
        return []
    cleaned = text or ""
    units = _text_units(cleaned)
    tokens = cleaned.split()
    if len(spans) == len(units):
        pieces = units
    elif len(spans) == len(tokens):
        pieces = tokens
    else:
        pieces = _split_text_evenly(cleaned, len(spans))
    return [
        ASRWord(round(start, 3), round(end, 3), piece)
        for piece, (start, end) in zip(pieces, spans)
        if piece
    ]


def _parse_funasr_result(results: object, duration: float, default_language: str = "zh") -> ASRResult:
    """把 ``AutoModel.generate`` 的返回值转成统一的 ``ASRResult``。"""
    records = [record for record in _as_records(results) if isinstance(record, dict)]
    scale = _funasr_time_scale(records, duration)
    language = ""
    words: list[ASRWord] = []
    texts: list[str] = []

    def scaled(values: object) -> list[tuple[float, float]]:
        spans = [span for span in (_coerce_span(v) for v in _as_records(values)) if span]
        return [(start * scale, end * scale) for start, end in spans]

    for record in records:
        raw_text = record.get("text")
        match = _FUNASR_LANGUAGE_TAG.search(raw_text or "")
        if match and not language:
            language = match.group(1).lower()
        text = _clean_funasr_text(raw_text)
        if text:
            texts.append(text)
        sentence_info = [s for s in _as_records(record.get("sentence_info")) if isinstance(s, dict)]
        if sentence_info:
            for sentence in sentence_info:
                sentence_text = _clean_funasr_text(sentence.get("text") or sentence.get("sentence"))
                spans = scaled(sentence.get("timestamp"))
                if not spans:
                    start = _safe_float(sentence.get("start")) * scale
                    end = _safe_float(sentence.get("end")) * scale
                    if end > start:
                        spans = [(start, end)]
                words.extend(_words_from_spans(sentence_text, spans))
            continue
        words.extend(_words_from_spans(text, scaled(record.get("timestamp"))))

    full_text = "".join(texts)
    if not words and full_text:
        # 模型没给时间戳时至少保留文本，交给 align 用整段区间落到第一个说话人
        words = [ASRWord(0.0, round(max(duration, 0.0), 3), full_text)]
    if not full_text:
        full_text = "".join(word.word for word in words)
    return ASRResult(language=language or default_language or "zh", words=words, text=full_text)


class FunASRASR:
    """FunASR（Paraformer-zh / SenseVoice）生产 ASR，输出词级时间戳。

    默认组合：``paraformer-zh`` + ``fsmn-vad`` + ``ct-punc``，模型通过 ModelScope
    （``hub='ms'``）首次下载。FunASR 只接受 16 kHz 单声道 float32，内部会重采样；
    时间戳按毫秒返回，这里还原成秒并与转写文本逐 token 对齐。
    """

    def __init__(
        self,
        model: str = "paraformer-zh",
        *,
        vad_model: str = "fsmn-vad",
        punc_model: str = "ct-punc",
        device: str = "cuda",
        hub: str = "ms",
        language: str = "zh",
        use_itn: bool = True,
        hotword: str = "",
        batch_size_s: int = 300,
        cuda_dll_dirs: str = "",
    ) -> None:
        self.model = model
        self.vad_model = vad_model
        self.punc_model = punc_model
        self.device = device
        self.hub = hub
        self.language = (language or "").strip().lower()
        self.use_itn = use_itn
        self.hotword = (hotword or "").strip()
        self.batch_size_s = batch_size_s
        self.cuda_dll_dirs = cuda_dll_dirs
        self._model = None
        self._cuda_prepared = False
        self.name = f"funasr:{_funasr_model_alias(model)}"

    # ── 模型加载 ─────────────────────────────────────────────────────────
    def _make_model(self, device: str):
        if os.name == "nt" and not self._cuda_prepared:
            self._cuda_prepared = True
            try:
                prepare_cuda_dlls(self.cuda_dll_dirs)
            except Exception:  # pragma: no cover - 探测失败不影响启动
                logger.debug("failed to prepare CUDA DLL dirs", exc_info=True)
        from funasr import AutoModel  # type: ignore

        kwargs: dict[str, object] = {"model": self.model, "device": device, "disable_update": True}
        if self.vad_model:
            kwargs["vad_model"] = self.vad_model
        if self.punc_model:
            kwargs["punc_model"] = self.punc_model
        if self.hub:
            kwargs["hub"] = self.hub
        return AutoModel(**kwargs)

    def _fallback_cpu(self, reason: str) -> None:
        logger.warning(
            "FunASR GPU 不可用（%s）；自动切换 CPU 继续转写（会明显变慢）。"
            "如需 GPU：确认 torch 的 CUDA 版本与显卡驱动匹配，或把 OA_FUNASR_DEVICE 设为 cpu。",
            reason,
        )
        self.device = "cpu"
        self.name = f"funasr:{_funasr_model_alias(self.model)}@cpu"
        self._model = self._make_model("cpu")

    def _load(self):
        if self.device.lower().startswith("cuda") and not _cuda_runtime_available():
            self._fallback_cpu("torch.cuda.is_available() == False")
            return self._model
        try:
            self._model = self._make_model(self.device)
        except Exception as exc:  # noqa: BLE001 - CUDA/驱动/模型下载错误类型不定
            if not self.device.lower().startswith("cuda"):
                raise
            self._fallback_cpu(f"{type(exc).__name__}: {exc}")
        return self._model

    # ── 转写 ────────────────────────────────────────────────────────────
    def _run(self, audio: np.ndarray, duration: float) -> ASRResult:
        model = self._model or self._load()
        kwargs: dict[str, object] = {
            "input": audio,
            "batch_size_s": self.batch_size_s,
            "use_itn": self.use_itn,
        }
        if self.hotword:
            kwargs["hotword"] = self.hotword
        # SenseVoice 支持 language 参数；Paraformer-zh 本身是中文模型，传了无意义
        if self.language and self.language != "auto" and "sensevoice" in self.model.lower():
            kwargs["language"] = self.language
        results = model.generate(**kwargs)
        return _parse_funasr_result(
            results,
            duration=duration,
            default_language="" if self.language == "auto" else (self.language or "zh"),
        )

    def transcribe(self, samples: np.ndarray, sr: int) -> ASRResult:
        audio = np.asarray(samples, dtype=np.float32)
        if sr and sr != _FUNASR_SAMPLE_RATE:
            from ..utils import resample_audio

            audio = resample_audio(audio, sr, _FUNASR_SAMPLE_RATE)
        duration = audio.size / _FUNASR_SAMPLE_RATE if audio.size else 0.0
        try:
            return self._run(audio, duration)
        except Exception as exc:  # noqa: BLE001 - 第三方库错误类型不定
            if self.device.lower().startswith("cuda") and _looks_like_cuda_error(exc):
                self._fallback_cpu(f"{type(exc).__name__}: {exc}")
                return self._run(audio, duration)
            raise


class FasterWhisperASR:
    """faster-whisper (large-v3 / large-v3-turbo) with word timestamps.

    这是 FunASR 之前的默认 ASR，保留用于兼容老部署；通过
    ``OA_ASR_PROVIDER=faster_whisper`` 显式启用。
    """

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
