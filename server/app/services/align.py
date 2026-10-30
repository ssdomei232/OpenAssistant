"""Align word-level ASR output with speaker identities and build turns."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..providers.types import ASRWord


@dataclass
class Turn:
    start: float
    end: float
    speaker: str
    text: str
    words: list[dict] = field(default_factory=list)

    @property
    def speaker_label(self) -> str:
        return {"user": "[使用者]", "other": "[其他人]", "unknown": "[未知]"}.get(self.speaker, "[其他人]")


@dataclass
class _Span:
    start: float
    end: float
    speaker: str


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def word_speaker(word: ASRWord, spans: Sequence[_Span], fallback: str = "other") -> str:
    best_speaker, best_overlap = fallback, 0.0
    for span in spans:
        ov = _overlap(word.start, word.end, span.start, span.end)
        if ov > best_overlap:
            best_speaker, best_overlap = span.speaker, ov
    if best_overlap > 0:
        return best_speaker
    # no overlap (e.g. ASR timestamp slightly outside the VAD island): nearest span wins
    if spans:
        mid = (word.start + word.end) / 2
        nearest = min(spans, key=lambda s: min(abs(mid - s.start), abs(mid - s.end)))
        return nearest.speaker
    return fallback


def align(
    words: Iterable[ASRWord],
    identities: Sequence,
    fallback: str = "other",
    merge_gap_s: float = 0.65,
    max_turn_s: float = 45.0,
) -> list[Turn]:
    """Merge words into speaker turns.

    ``identities`` items must expose ``start``, ``end`` and ``label``
    (``SpeakerIdentity``).  ``fallback`` is used when there is no identity
    information at all - anything that is not the user is treated as ``other``
    for command triggering purposes.
    """
    spans = [_Span(i.start, i.end, i.label) for i in identities]
    turns: list[Turn] = []
    for word in words:
        text = getattr(word, "word", getattr(word, "text", ""))
        if not text:
            continue
        speaker = word_speaker(word, spans, fallback=fallback)
        if turns and turns[-1].speaker == speaker and word.start - turns[-1].end <= merge_gap_s:
            turns[-1].end = max(turns[-1].end, word.end)
            turns[-1].text += text
            turns[-1].words.append({"start": word.start, "end": word.end, "text": text})
        elif turns and turns[-1].speaker == speaker and (word.start - turns[-1].end <= merge_gap_s + 0.4):
            turns[-1].end = max(turns[-1].end, word.end)
            turns[-1].text += text
            turns[-1].words.append({"start": word.start, "end": word.end, "text": text})
        else:
            turns.append(
                Turn(
                    start=word.start,
                    end=word.end,
                    speaker=speaker,
                    text=text,
                    words=[{"start": word.start, "end": word.end, "text": text}],
                )
            )
        if turns[-1].end - turns[-1].start > max_turn_s:
            last = turns[-1]
            turns.append(Turn(start=last.end, end=last.end, speaker=speaker, words=[]))
    # drop empty turns, normalise text and timing
    cleaned: list[Turn] = []
    for turn in turns:
        text = turn.text.strip()
        if not text:
            continue
        turn.text = text
        turn.start = round(turn.start, 3)
        turn.end = round(turn.end, 3)
        cleaned.append(turn)
    return cleaned


def transcript_text(turns: Sequence[Turn]) -> str:
    lines = []
    for turn in turns:
        ts = f"{int(turn.start // 60):02d}:{turn.start % 60:04.1f}"
        lines.append(f"[{ts}] {turn.speaker_label} {turn.text}")
    return "\n".join(lines)


def user_only(turns: Sequence[Turn]) -> list[Turn]:
    return [t for t in turns if t.speaker == "user"]


def context_turns(turns: Sequence[Turn]) -> list[Turn]:
    return [t for t in turns if t.speaker != "user"]
