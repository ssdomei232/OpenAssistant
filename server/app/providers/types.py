"""Shared provider data types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Word:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass
class DiarizationSegment:
    start: float
    end: float
    speaker: str  # anonymous label, e.g. SPEAKER_00


@dataclass
class ASRWord:
    start: float
    end: float
    word: str


@dataclass
class ASRResult:
    language: str = "zh"
    words: list[ASRWord] = field(default_factory=list)
    text: str = ""

    def full_text(self) -> str:
        return self.text or " ".join(w.word for w in self.words)


@dataclass
class SpeakerMatch:
    speaker: str  # user | other | unknown
    similarity: float
    anonymous: str


class ASRProvider(Protocol):
    name: str

    def transcribe(self, samples: Any, sr: int) -> ASRResult: ...


class Diarizer(Protocol):
    name: str

    def diarize(self, samples: Any, sr: int) -> list[DiarizationSegment]: ...


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, samples: Any, sr: int) -> list[float]: ...


class LLMProvider(Protocol):
    name: str
    available: bool

    def complete(self, system: str, user: str, temperature: float = 0.3, max_tokens: int | None = None) -> str: ...


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class SearchResponse:
    query: str
    provider: str
    answer: str
    results: list[SearchResult] = field(default_factory=list)
    took_ms: int = 0


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 5, context: str = "") -> SearchResponse: ...


class PushProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    def send(self, title: str, markdown: str) -> tuple[bool, str | None]: ...
