"""Speaker diarization providers.

Production: pyannote.audio.  Offline fallback: a VAD/energy segmenter that
splits the audio into anonymous speaker turns without clustering.
"""
from __future__ import annotations

import numpy as np

from .types import DiarizationSegment


class EnergyDiarizer:
    """VAD-based fallback: every speech island becomes an anonymous turn.

    It cannot merge the same speaker across turns (that is the voiceprint
    matcher's job) and cannot split overlapping speakers, but it keeps the
    whole pipeline functional without pyannote/torch.
    """

    name = "energy"

    def __init__(
        self,
        frame_s: float = 0.03,
        min_speech_s: float = 0.25,
        min_silence_s: float = 0.30,
        max_segment_s: float = 20.0,
        energy_factor: float = 2.2,
    ) -> None:
        self.frame_s = frame_s
        self.min_speech_s = min_speech_s
        self.min_silence_s = min_silence_s
        self.max_segment_s = max_segment_s
        self.energy_factor = energy_factor

    def diarize(self, samples: np.ndarray, sr: int) -> list[DiarizationSegment]:
        frame = max(1, int(self.frame_s * sr))
        n = samples.size // frame
        segments: list[DiarizationSegment] = []
        if n <= 0:
            return segments
        frames = samples[: n * frame].reshape(n, frame)
        energies = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
        noise_floor = float(np.percentile(energies, 20))
        threshold = max(noise_floor * self.energy_factor, 0.0035)
        voiced = energies > threshold

        min_speech_frames = max(1, int(self.min_speech_s / self.frame_s))
        min_silence_frames = max(1, int(self.min_silence_s / self.frame_s))

        start: int | None = None
        silence_run = 0
        for i, flag in enumerate(voiced):
            if start is None:
                if flag:
                    start = i
                    silence_run = 0
                continue
            if flag:
                silence_run = 0
                continue
            silence_run += 1
            if silence_run >= min_silence_frames:
                end = i - silence_run + 1
                if end - start >= min_speech_frames:
                    segments.extend(self._split(start, end))
                start = None
                silence_run = 0
        if start is not None and n - start >= min_speech_frames:
            segments.extend(self._split(start, n))

        if not segments:
            return []
        # label anonymous speakers round-robin after large gaps (>1.5s)
        labelled: list[DiarizationSegment] = []
        speaker_idx = 0
        prev_end = segments[0].start
        for seg in segments:
            if seg.start - prev_end > 1.5:
                speaker_idx = (speaker_idx + 1) % 6
            labelled.append(
                DiarizationSegment(start=seg.start, end=seg.end, speaker=f"SPEAKER_{speaker_idx:02d}")
            )
            prev_end = seg.end
        return labelled

    def _split(self, start_frame: int, end_frame: int) -> list[DiarizationSegment]:
        max_frames = max(1, int(self.max_segment_s / self.frame_s))
        segments: list[DiarizationSegment] = []
        cursor = start_frame
        while cursor < end_frame:
            stop = min(end_frame, cursor + max_frames)
            segments.append(
                DiarizationSegment(
                    start=round(cursor * self.frame_s, 3),
                    end=round(stop * self.frame_s, 3),
                    speaker="SPEAKER_00",
                )
            )
            cursor = stop
        return segments


class PyAnnoteDiarizer:
    """pyannote.audio speaker diarization (requires torch + HF token)."""

    def __init__(self, hf_token: str = "", model: str = "pyannote/speaker-diarization-3.1") -> None:
        self.name = "pyannote"
        self.hf_token = hf_token
        self.model_name = model
        self._pipeline = None

    def _load(self):
        import torch  # type: ignore
        from pyannote.audio import Pipeline  # type: ignore

        pipeline = Pipeline.from_pretrained(self.model_name, use_auth_token=self.hf_token or None)
        if torch.cuda.is_available():
            pipeline.to(torch.device("cuda"))
        self._pipeline = pipeline
        return pipeline

    def diarize(self, samples: np.ndarray, sr: int) -> list[DiarizationSegment]:
        import torch  # type: ignore

        pipeline = self._pipeline or self._load()
        waveform = torch.from_numpy(np.asarray(samples, dtype=np.float32)).unsqueeze(0)
        output = pipeline({"waveform": waveform, "sample_rate": sr})
        # pyannote >=3.1 returns DiarizeOutput; older versions return Annotation
        annotation = getattr(output, "speaker_diarization", output)
        segments: list[DiarizationSegment] = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            segments.append(
                DiarizationSegment(start=float(turn.start), end=float(turn.end), speaker=str(speaker))
            )
        segments.sort(key=lambda s: s.start)
        return segments
