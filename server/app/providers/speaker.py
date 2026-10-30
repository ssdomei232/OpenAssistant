"""Speaker identification: voiceprint embeddings + conservative matching.

``[使用者]`` (user) is only assigned when cosine similarity * 100 reaches the
configured threshold (default 60).  Everything else is ``other`` - or
``unknown`` when the user has not enrolled a voiceprint yet.
"""
from __future__ import annotations

import os
import tempfile
import wave
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .types import DiarizationSegment


@dataclass
class SpeakerIdentity:
    start: float
    end: float
    anonymous: str
    label: str  # user | other | unknown
    similarity: float


# ── embeddings ────────────────────────────────────────────────────────────
class SpectralEmbedding:
    """Cheap offline voiceprint: log-mel statistics + L2 normalisation.

    This is *not* a research-grade speaker embedding.  It is a deterministic,
    dependency-free fallback that lets the identity plumbing (enrolment,
    thresholding, labelling) work on any machine; production deployments use
    :class:`PyAnnoteEmbedding` or CAM++.
    """

    name = "spectral"

    def __init__(self, n_mels: int = 32, n_fft: int = 512, hop: int = 160) -> None:
        self.n_mels = n_mels
        self.n_fft = n_fft
        self.hop = hop
        self._filters: np.ndarray | None = None

    def embed(self, samples: np.ndarray, sr: int) -> list[float]:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.size < self.n_fft:
            audio = np.pad(audio, (0, self.n_fft - audio.size))
        frames = np.lib.stride_tricks.sliding_window_view(audio, self.n_fft)[:: self.hop]
        window = np.hanning(self.n_fft).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
        mel = spectrum @ self._mel_filters(sr).T
        log_mel = np.log(mel + 1e-8)
        # emphasise the speaker-specific envelope: mean + std + short-term dynamics
        mean = log_mel.mean(axis=0)
        std = log_mel.std(axis=0)
        delta = np.abs(np.diff(log_mel, axis=0)).mean(axis=0) if log_mel.shape[0] > 1 else np.zeros(self.n_mels)
        feature = np.concatenate([mean, std, delta]).astype(np.float64)
        feature -= feature.mean()
        norm = np.linalg.norm(feature)
        if norm == 0:
            return [0.0] * feature.size
        return (feature / norm).tolist()

    def _mel_filters(self, sr: int) -> np.ndarray:
        if self._filters is not None:
            return self._filters

        def hz_to_mel(hz: float) -> float:
            return 2595.0 * np.log10(1.0 + hz / 700.0)

        def mel_to_hz(mel: float) -> float:
            return 700.0 * (10 ** (mel / 2595.0) - 1.0)

        low, high = 50.0, min(sr / 2.0, 8000.0)
        mel_points = np.linspace(hz_to_mel(low), hz_to_mel(high), self.n_mels + 2)
        hz_points = mel_to_hz(mel_points)
        bins = np.floor((self.n_fft + 1) * hz_points / sr).astype(int)
        bins = np.clip(bins, 0, self.n_fft // 2)
        filters = np.zeros((self.n_mels, self.n_fft // 2 + 1), dtype=np.float32)
        for m in range(1, self.n_mels + 1):
            left, center, right = bins[m - 1], bins[m], bins[m + 1]
            if center == left:
                center = left + 1
            if right <= center:
                right = center + 1
            for k in range(left, min(center, filters.shape[1])):
                filters[m - 1, k] = (k - left) / max(1, center - left)
            for k in range(center, min(right, filters.shape[1])):
                filters[m - 1, k] = (right - k) / max(1, right - center)
        self._filters = filters
        return filters


class PyAnnoteEmbedding:
    """pyannote/embedding speaker verification model."""

    name = "pyannote-embedding"

    def __init__(self, hf_token: str = "", model: str = "pyannote/embedding") -> None:
        self.hf_token = hf_token
        self.model_name = model
        self._inference = None

    def _load(self):
        import inspect

        from pyannote.audio import Inference, Model  # type: ignore

        params = inspect.signature(Model.from_pretrained).parameters
        use_token = "token" in params or "use_auth_token" not in params
        kwargs: dict[str, object] = {}
        if self.hf_token:
            kwargs["token" if use_token else "use_auth_token"] = self.hf_token

        candidates = [self.model_name]
        # pyannote 4 推荐 wespeaker；pyannote/embedding 是旧模型且可能需要 omegaconf
        if "wespeaker" not in self.model_name:
            candidates.append("pyannote/wespeaker-voxceleb-resnet34-LM")
        errors: list[str] = []
        model = None
        for candidate in dict.fromkeys(candidates):
            try:
                model = Model.from_pretrained(candidate, **kwargs)
            except TypeError as exc:
                message = str(exc)
                if self.hf_token and "unexpected keyword argument" in message:
                    alternate = "use_auth_token" if use_token else "token"
                    try:
                        model = Model.from_pretrained(candidate, **{alternate: self.hf_token})
                    except Exception as retry_exc:  # noqa: BLE001
                        errors.append(f"{candidate}: {type(retry_exc).__name__}: {retry_exc}")
                        continue
                else:
                    errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
                    continue
            except Exception as exc:  # noqa: BLE001 - HF 错误类型随版本变化
                errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
                continue
            if model is not None:
                self.model_name = candidate
                break
            errors.append(f"{candidate}: Model.from_pretrained returned None")

        if model is None:
            raise RuntimeError(
                self._embedding_help(RuntimeError("; ".join(errors)))
            )
        self._inference = Inference(model, window="whole")
        return self._inference
        self._inference = Inference(model, window="whole")
        return self._inference

    def _embedding_help(self, exc: Exception | None) -> str:
        detail = f"{type(exc).__name__}: {exc}" if exc is not None else "Model.from_pretrained returned None"
        return (
            f"无法加载 pyannote 声纹模型 {self.model_name!r}：{detail}。"
            f"如果提示 gated/403，请用同一 HuggingFace 账号访问 "
            f"https://hf.co/{self.model_name} 接受使用条件，并确认 OA_HF_TOKEN 正确；"
            "也可以设置 OA_SPEAKER_PROVIDER=spectral 使用离线谱特征。"
        )

    def _temp_wav(self, samples: np.ndarray, sr: int) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="oa-emb-")
        os.close(fd)
        pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sr))
            wf.writeframes((pcm * 32767.0).astype("<i2").tobytes())
        return path

    def embed(self, samples: np.ndarray, sr: int) -> list[float]:
        import torch  # type: ignore

        inference = self._inference or self._load()
        waveform = torch.from_numpy(np.asarray(samples, dtype=np.float32)).unsqueeze(0)
        try:
            embedding = inference({"waveform": waveform, "sample_rate": sr})
        except (TypeError, ValueError):
            # pyannote 4 只保证文件路径输入：写临时 wav 兜底
            path = self._temp_wav(samples, sr)
            try:
                embedding = inference(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:  # pragma: no cover
                    pass
        vector = np.asarray(embedding, dtype=np.float64).reshape(-1)
        norm = np.linalg.norm(vector)
        return (vector / norm).tolist() if norm else vector.tolist()


# ── identification ────────────────────────────────────────────────────────
def embed_for(samples: np.ndarray, sr: int, start: float, end: float, provider: Any) -> list[float]:
    a = max(0, int(start * sr))
    b = min(samples.size, int(end * sr))
    chunk = np.asarray(samples[a:b], dtype=np.float32)
    if chunk.size < int(0.12 * sr):
        chunk = np.asarray(samples, dtype=np.float32)
    return provider.embed(chunk, sr)


def identify(
    samples: np.ndarray,
    sr: int,
    diarization: Sequence[DiarizationSegment],
    embedding_provider: Any,
    voiceprint: Sequence[float] | None,
    threshold: float = 60.0,
    cluster_threshold: float = 0.72,
    max_other_speakers: int = 6,
) -> list[SpeakerIdentity]:
    """Assign ``user``/``other``/``unknown`` to each diarized segment.

    Segments that are not the user are additionally clustered so meetings can
    report how many different people spoke.  Clustering is greedy on cosine
    similarity of the segment embeddings.
    """
    if not diarization:
        return []
    emb_cache: list[np.ndarray] = []
    raw: list[dict[str, Any]] = []
    for seg in diarization:
        emb = np.asarray(embed_for(samples, sr, seg.start, seg.end, embedding_provider), dtype=np.float64)
        norm = np.linalg.norm(emb)
        emb = emb / norm if norm else emb
        emb_cache.append(emb)
        similarity = float(np.dot(emb, np.asarray(voiceprint, dtype=np.float64))) * 100.0 if (
            voiceprint and len(voiceprint) == emb.size
        ) else 0.0
        raw.append({"seg": seg, "emb": emb, "similarity": similarity})

    unlabeled = [r for r in raw if not (voiceprint and r["similarity"] >= threshold)]

    # greedy clustering of the remaining speakers
    clusters: list[dict[str, Any]] = []
    for item in unlabeled:
        best_idx, best_sim = -1, -1.0
        for idx, cluster in enumerate(clusters):
            sim = float(np.dot(item["emb"], cluster["centroid"]))
            if sim > best_sim:
                best_idx, best_sim = idx, sim
        if best_idx >= 0 and best_sim >= cluster_threshold and len(clusters) < max_other_speakers:
            cluster = clusters[best_idx]
            cluster["members"].append(item["emb"])
            centroid = np.mean(cluster["members"], axis=0)
            norm = np.linalg.norm(centroid)
            cluster["centroid"] = centroid / norm if norm else centroid
            item["anonymous"] = cluster["anonymous"]
            item["cluster"] = best_idx
        else:
            item["anonymous"] = f"SPEAKER_{len(clusters) + 1:02d}"
            item["cluster"] = len(clusters)
            clusters.append({"members": [item["emb"]], "centroid": item["emb"], "anonymous": item["anonymous"]})

    identities: list[SpeakerIdentity] = []
    for item in raw:
        is_matched = voiceprint is not None and item["similarity"] >= threshold
        if is_matched:
            label = "user"
            anonymous = "SPEAKER_00"
        elif voiceprint is None:
            label = "unknown"
        else:
            label = "other"
        identities.append(
            SpeakerIdentity(
                start=item["seg"].start,
                end=item["seg"].end,
                anonymous=anonymous if is_matched else item.get("anonymous", item["seg"].speaker),
                label=label,
                similarity=round(item["similarity"], 2),
            )
        )
    identities.sort(key=lambda i: i.start)
    return identities
