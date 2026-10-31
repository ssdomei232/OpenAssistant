"""The audio → card processing pipeline.

Two phases so that nearby audio can be merged into a single conversation card:

1. **Transcribe** (per segment): diarize → voiceprint identify → ASR →
   align/label → hallucination filter → persist turns in ``AudioSegment.meta``
   and set status ``transcribed``.
2. **Summarise** (per conversation): wait ``OA_CONVERSATION_MERGE_WINDOW_S`` for
   other nearby segments to finish transcribing, group segments whose recording
   times are close (``OA_CONVERSATION_MERGE_GAP_S``), merge their turns and
   produce **one** card / schedule / todos / commands / meeting / profile update.

``process_segment`` keeps the old synchronous behaviour for direct calls
(upload with ``process_now=true``, CLI, demo): transcribe then immediately
summarise the conversation the segment belongs to.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import select

from ..config import default_timezone, get_settings
from ..db import session_scope
from ..models import AudioSegment, Card, TranscriptTurn, User
from ..providers import Providers, get_providers
from ..providers.speaker import identify
from ..utils import decode_audio, iso_utc, now_utc, rms, utc_from
from .align import Turn, align
from .commands import execute_command
from .entities import add_schedule, add_todo
from .meetings import attach_meeting, should_be_meeting
from .profile import maybe_update_profile
from .storage import get_storage
from .summarizer import CardDraft, summarize

logger = logging.getLogger(__name__)

# ASR 在静音/噪声片段上常见的"字幕组"幻觉，命中则丢弃该 turn
_HALLUCINATION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"中文字幕",
        r"字幕志愿者",
        r"请不吝点赞",
        r"订阅.{0,6}(频道|转发|打赏)",
        r"明镜与点点",
        r"感谢观看",
        r"谢谢观看",
        r"字幕由.{0,20}提供",
        r"amara\.org",
        r"Subtitle",
        r"字幕組",
    )
]


def looks_like_hallucination(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    return any(pattern.search(cleaned) for pattern in _HALLUCINATION_PATTERNS)


class ProcessingError(Exception):
    def __init__(self, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _segment_occurred_at(segment: AudioSegment, tz_name: str) -> datetime:
    raw = (segment.meta or {}).get("recorded_at")
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return utc_from(parsed) or now_utc()
        except ValueError:
            pass
    return segment.received_at


# ── phase 1: transcribe ──────────────────────────────────────────────────
def _serialize_turns(turns: Sequence[Turn]) -> list[dict]:
    return [
        {
            "start": round(float(turn.start), 3),
            "end": round(float(turn.end), 3),
            "speaker": turn.speaker,
            "text": turn.text,
            "words": list(turn.words or []),
        }
        for turn in turns
    ]


def _deserialize_turns(raw) -> list[Turn]:
    turns: list[Turn] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        turns.append(
            Turn(
                start=start,
                end=end,
                speaker=str(item.get("speaker") or "other"),
                text=text,
                words=list(item.get("words") or []),
            )
        )
    return turns


def _serialize_identities(identities) -> list[dict]:
    result: list[dict] = []
    for item in identities or []:
        if isinstance(item, dict):
            result.append(item)
            continue
        result.append(
            {
                "start": item.start,
                "end": item.end,
                "anonymous": item.anonymous,
                "label": item.label,
                "similarity": item.similarity,
            }
        )
    return result


def _iso_to_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return utc_from(dt) if dt.tzinfo is not None else dt


def transcribe_segment(segment_id: str, providers: Providers | None = None) -> dict:
    """Phase 1: ASR + speaker labels for one segment; persist turns, no card yet.

    Idempotent on retries: ``done``/``processing``/``transcribed`` segments are
    returned as-is.  Turns are stored in ``segment.meta['transcript']`` so the
    summarisation phase can merge them after the configured wait window.
    """
    providers = providers or get_providers()
    settings = get_settings()
    storage = get_storage()

    with session_scope() as db:
        segment = db.get(AudioSegment, segment_id)
        if segment is None:
            return {"status": "missing"}
        if segment.status in {"done", "processing", "transcribed"}:
            return {"status": segment.status, "card_id": segment.card_id}
        segment.status = "processing"
        segment.attempts += 1
        segment.error = None
        user = db.get(User, segment.user_id)
        if user is None:
            segment.status = "failed"
            segment.error = "user_missing"
            return {"status": "failed", "error": "user_missing"}
        tz_name = user.timezone or default_timezone()
        audio_bytes = storage.read(segment.path)
        meta = dict(segment.meta or {})
        user_id = user.id

    try:
        samples, sr = decode_audio(audio_bytes, target_sr=16000)
        if samples.size == 0:
            raise ProcessingError("empty_audio")
        duration = samples.size / sr if sr else 0.0

        # Deterministic demo / integration-test hook: only honoured when the
        # server runs with OA_DEMO_MODE=1.  Real providers are never bypassed
        # in production.
        demo_turns = (meta or {}).get("demo_turns") if settings.demo_mode else None
        language: str | None = None
        if demo_turns:
            turns = [
                Turn(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    speaker=item.get("speaker", "user"),
                    text=item["text"],
                    words=item.get("words") or [],
                )
                for item in demo_turns
            ]
            identities = []
        else:
            level = rms(samples)
            if level < settings.min_speech_rms:
                logger.info(
                    "segment %s skipped: audio level %.4f below min_speech_rms=%.4f",
                    segment_id,
                    level,
                    settings.min_speech_rms,
                )
                with session_scope() as db:
                    segment = db.get(AudioSegment, segment_id)
                    segment.status = "done"
                    segment.processed_at = now_utc()
                    segment.error = "low_level_audio"
                return {"status": "done", "card_id": None, "turns": 0}
            if duration < settings.diarization_min_seconds:
                # 极短音频做说话人分离没有意义，还会让 pyannote 在短输入上数值退化；
                # 直接按单一说话人区间处理，交给声纹匹配判断是否为使用者。
                from ..providers.types import DiarizationSegment

                logger.info(
                    "segment %s: %.2fs below diarization_min_seconds=%.2f; skipping diarization",
                    segment_id,
                    duration,
                    settings.diarization_min_seconds,
                )
                diarization = [DiarizationSegment(0.0, duration, "SPEAKER_00")]
            else:
                diarization = providers.diarization.diarize(samples, sr)
                if not diarization:
                    from ..providers.types import DiarizationSegment

                    diarization = [DiarizationSegment(0.0, duration, "SPEAKER_00")]

            with session_scope() as db:
                from ..models import Voiceprint

                voiceprint_row = db.scalar(select(Voiceprint).where(Voiceprint.user_id == user_id))
                voiceprint = voiceprint_row.embedding if voiceprint_row else None
                threshold = voiceprint_row.threshold if voiceprint_row else settings.voiceprint_threshold

            identities = identify(
                samples,
                sr,
                diarization,
                providers.speaker,
                voiceprint,
                threshold=threshold,
                min_embed_seconds=settings.embedding_min_seconds,
            )
            asr_result = providers.asr.transcribe(samples, sr)
            language = getattr(asr_result, "language", None)
            turns = align(asr_result.words, identities)
        if turns and not demo_turns:
            filtered = [turn for turn in turns if not looks_like_hallucination(turn.text)]
            if len(filtered) != len(turns):
                logger.info(
                    "segment %s: dropped %s hallucinated turns (likely ASR subtitle artifacts)",
                    segment_id,
                    len(turns) - len(filtered),
                )
            turns = filtered
        if not turns:
            with session_scope() as db:
                segment = db.get(AudioSegment, segment_id)
                segment.status = "done"
                segment.processed_at = now_utc()
                segment.error = "no_speech_recognised"
            return {"status": "done", "card_id": None, "turns": 0}

        transcript = {
            "turns": _serialize_turns(turns),
            "identities": _serialize_identities(identities),
            "language": language,
            "transcribed_at": iso_utc(now_utc()),
        }
        with session_scope() as db:
            segment = db.get(AudioSegment, segment_id)
            if segment is None:
                return {"status": "missing"}
            segment.meta = {**meta, "transcript": transcript}
            segment.status = "transcribed"
            segment.error = None
            db.flush()
        logger.info(
            "segment %s transcribed (%s turns); waiting up to %.1fs to merge nearby audio",
            segment_id,
            len(turns),
            settings.conversation_merge_window_s,
        )
        return {"status": "transcribed", "turns": len(turns), "card_id": None}
    except ProcessingError as exc:
        _mark_failure(segment_id, exc.code, retryable=exc.retryable)
        return {"status": "failed", "error": exc.code}
    except Exception as exc:  # noqa: BLE001 - pipeline robustness
        logger.exception("transcription failed for segment %s", segment_id)
        _mark_failure(segment_id, f"{type(exc).__name__}: {exc}", retryable=True)
        return {"status": "failed", "error": type(exc).__name__}


# ── phase 2: merge nearby segments & summarise once ───────────────────────
@dataclass
class _ConversationSegment:
    segment_id: str
    user_id: str
    occurred_at: datetime
    duration_s: float
    transcribed_at: datetime
    turns: list[Turn]
    identities: list[dict]
    recorded_at: str | None
    device_id: str | None

    @property
    def end_at(self) -> datetime:
        return self.occurred_at + timedelta(seconds=max(self.duration_s, 0.0))


def _load_conversation_state(
    db, users: dict[str, User]
) -> tuple[list[_ConversationSegment], list[tuple[str, datetime, datetime]]]:
    rows = db.scalars(
        select(AudioSegment).where(AudioSegment.status == "transcribed").order_by(AudioSegment.received_at)
    ).all()
    items: list[_ConversationSegment] = []
    for row in rows:
        user = users.get(row.user_id)
        if user is None:
            continue
        tz_name = user.timezone or default_timezone()
        transcript = dict((row.meta or {}).get("transcript") or {})
        turns = _deserialize_turns(transcript.get("turns"))
        duration = float(row.duration_s or 0.0)
        if turns:
            duration = max(duration, float(turns[-1].end))
        occurred_at = _segment_occurred_at(row, tz_name)
        items.append(
            _ConversationSegment(
                segment_id=row.id,
                user_id=row.user_id,
                occurred_at=occurred_at,
                duration_s=duration,
                transcribed_at=_iso_to_dt(transcript.get("transcribed_at")) or row.received_at,
                turns=turns,
                identities=list(transcript.get("identities") or []),
                recorded_at=(row.meta or {}).get("recorded_at"),
                device_id=row.device_id,
            )
        )

    pending = db.scalars(
        select(AudioSegment).where(AudioSegment.status.in_(("queued", "processing")))
    ).all()
    pending_intervals: list[tuple[str, datetime, datetime]] = []
    for row in pending:
        user = users.get(row.user_id)
        if user is None:
            continue
        tz_name = user.timezone or default_timezone()
        start = _segment_occurred_at(row, tz_name)
        end = start + timedelta(seconds=max(float(row.duration_s or 0.0), 0.0))
        pending_intervals.append((row.user_id, start, end))
    return items, pending_intervals


def _group_conversations(items: Sequence[_ConversationSegment], settings) -> list[list[_ConversationSegment]]:
    """Group temporally adjacent segments (per user) into one conversation."""
    ordered = sorted(items, key=lambda item: (item.occurred_at, item.segment_id))
    if not settings.conversation_merge_enabled:
        return [[item] for item in ordered]
    gap_s = settings.conversation_merge_gap_s
    max_segments = max(1, int(settings.conversation_merge_max_segments))
    max_duration_s = settings.conversation_merge_max_duration_s
    groups: list[list[_ConversationSegment]] = []
    current: list[_ConversationSegment] = []
    for item in ordered:
        if not current:
            current = [item]
            continue
        prev = current[-1]
        gap = (item.occurred_at - prev.end_at).total_seconds()
        span = (item.end_at - current[0].occurred_at).total_seconds()
        if gap <= gap_s and len(current) < max_segments and span <= max_duration_s:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)
    return groups


def _group_ready(
    group: Sequence[_ConversationSegment],
    settings,
    now: datetime,
    force_segment_ids: set[str] | None,
    pending_intervals: Sequence[tuple[str, datetime, datetime]],
    force: bool,
) -> bool:
    ids = {item.segment_id for item in group}
    if force:
        return True
    if force_segment_ids and ids & force_segment_ids:
        return True
    if not settings.conversation_merge_enabled:
        return True
    newest = max(item.transcribed_at for item in group)
    if (now - newest).total_seconds() < settings.conversation_merge_window_s:
        return False
    # 还有同用户、录制时间相邻的片段在排队/转写：等它转写完再统一总结
    user_id = group[0].user_id
    start = min(item.occurred_at for item in group)
    end = max(item.end_at for item in group)
    gap = timedelta(seconds=settings.conversation_merge_gap_s)
    for pending_user, pending_start, pending_end in pending_intervals:
        if pending_user != user_id:
            continue
        if pending_start <= end + gap and pending_end >= start - gap:
            return False
    return True


def _merge_turns(group: Sequence[_ConversationSegment]) -> list[Turn]:
    if not group:
        return []
    start = min(item.occurred_at for item in group)
    merged: list[Turn] = []
    for item in sorted(group, key=lambda value: value.occurred_at):
        offset = (item.occurred_at - start).total_seconds()
        for turn in item.turns:
            merged.append(
                Turn(
                    start=round(offset + float(turn.start), 3),
                    end=round(offset + float(turn.end), 3),
                    speaker=turn.speaker,
                    text=turn.text,
                    words=list(turn.words or []),
                )
            )
    merged.sort(key=lambda turn: (turn.start, turn.end))
    return merged


def _mark_group_failed(group: Sequence[_ConversationSegment], error: str) -> None:
    """Summarisation failed: retry on next tick until ``job_max_attempts``."""
    settings = get_settings()
    with session_scope() as db:
        for item in group:
            segment = db.get(AudioSegment, item.segment_id)
            if segment is None or segment.status != "transcribed":
                continue
            segment.error = error[:500]
            if segment.attempts < settings.job_max_attempts:
                continue  # 保持 transcribed，下一次 tick 重试
            segment.status = "failed"
            segment.processed_at = now_utc()


def _finalize_conversation(
    group: Sequence[_ConversationSegment], providers: Providers, settings
) -> dict | None:
    """Summarise one merged conversation and persist card / segment states."""
    user_id = group[0].user_id
    segment_ids = [item.segment_id for item in group]
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            return None
        segments = [db.get(AudioSegment, segment_id) for segment_id in segment_ids]
        if any(segment is None or segment.status != "transcribed" for segment in segments):
            return None  # 并发状态变化（重新处理/删除），本轮跳过
        tz_name = user.timezone or default_timezone()
        occurred_at = min(item.occurred_at for item in group)
        turns = _merge_turns(group)
        if not turns:
            for segment in segments:
                segment.status = "done"
                segment.processed_at = now_utc()
                segment.error = "no_speech_recognised"
            return {"status": "done", "card_id": None, "segment_ids": segment_ids, "segments": len(segments), "turns": 0}

        identities: list[dict] = []
        for item in group:
            for identity in item.identities:
                identities.append({**identity, "segment_id": item.segment_id})

        draft = summarize(
            turns,
            occurred_at=occurred_at,
            tz_name=tz_name,
            llm=providers.llm,
            aliases=list(user.aliases or []),
            profile=user.profile_text or "",
        )
        if draft.discard:
            # 过于简短/无意义的人声音频：AI（或规则兜底）选择丢弃，不生成卡片，
            # 也不触发日程/待办/指令；保留原因便于排查与 App 展示。
            for segment in segments:
                segment.status = "done"
                segment.processed_at = now_utc()
                segment.error = (
                    "discarded_by_ai" if draft.source == "llm" else "discarded_low_value_audio"
                )
                segment.meta = {
                    **(segment.meta or {}),
                    "discard": {
                        "source": draft.source,
                        "reason": draft.discard_reason,
                        "turns": len(turns),
                        "segment_ids": segment_ids,
                    },
                }
            logger.info(
                "conversation %s discarded (%s): %s",
                segment_ids,
                draft.source,
                draft.discard_reason,
            )
            return {
                "status": "done",
                "card_id": None,
                "segment_ids": segment_ids,
                "segments": len(segments),
                "turns": len(turns),
                "discarded": True,
                "discard_reason": draft.discard_reason,
            }

        card = _persist_card(db, user, segments, draft, turns, identities, occurred_at)
        processed_at = now_utc()
        for segment in segments:
            segment.card_id = card.id
            segment.status = "done"
            segment.processed_at = processed_at
            segment.error = None
            segment.meta = {
                **(segment.meta or {}),
                "conversation": {
                    "card_id": card.id,
                    "segment_ids": segment_ids,
                    "merged": len(segment_ids) > 1,
                },
            }
        maybe_update_profile(db, user, providers)
        logger.info(
            "conversation summarized: card=%s segments=%s turns=%s",
            card.id,
            len(segments),
            len(turns),
        )
        return {
            "status": "done",
            "card_id": card.id,
            "segment_ids": segment_ids,
            "segments": len(segments),
            "merged_segments": len(segments),
            "turns": len(turns),
            "user_turns": sum(1 for turn in turns if turn.speaker == "user"),
            "schedule_items": len(draft.schedule_items),
            "todos": len(draft.todos),
            "commands": len(draft.commands),
            "meeting_id": card.meeting_id,
        }


def flush_transcribed_conversations(
    providers: Providers | None = None,
    *,
    force_segment_ids: set[str] | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """Phase 2: summarise conversations whose merge window has elapsed.

    ``force_segment_ids`` summarises the conversation(s) containing those
    segments immediately (used by ``process_segment`` / ``process_now``).
    ``force`` summarises everything (CLI backfill / tests).
    """
    providers = providers or get_providers()
    settings = get_settings()
    moment = now or now_utc()

    with session_scope() as db:
        users = {user.id: user for user in db.scalars(select(User)).all()}
        items, pending_intervals = _load_conversation_state(db, users)

    corrupt = [item for item in items if not item.turns]
    if corrupt:
        with session_scope() as db:
            for item in corrupt:
                segment = db.get(AudioSegment, item.segment_id)
                if segment is None or segment.status != "transcribed":
                    continue
                segment.status = "failed"
                segment.error = "empty_transcript"
                segment.processed_at = now_utc()

    by_user: dict[str, list[_ConversationSegment]] = {}
    for item in items:
        if item.turns:
            by_user.setdefault(item.user_id, []).append(item)

    results: list[dict] = []
    for user_items in by_user.values():
        for group in _group_conversations(user_items, settings):
            if not _group_ready(group, settings, moment, force_segment_ids, pending_intervals, force):
                continue
            try:
                outcome = _finalize_conversation(group, providers, settings)
            except Exception as exc:  # noqa: BLE001 - 单组失败不影响其他对话
                logger.exception("summarising conversation %s failed", [i.segment_id for i in group])
                _mark_group_failed(group, f"{type(exc).__name__}: {exc}")
                outcome = {
                    "status": "failed",
                    "segment_ids": [item.segment_id for item in group],
                    "error": type(exc).__name__,
                }
            if outcome is not None:
                results.append(outcome)
    return results


def process_segment(segment_id: str, providers: Providers | None = None) -> dict:
    """Synchronous convenience path: transcribe then summarise immediately.

    Used by ``process_now=true`` uploads, the demo seeder and CLI backfills.
    The background worker instead uses :func:`transcribe_segment` +
    :func:`flush_transcribed_conversations` so it can wait for nearby audio.
    """
    providers = providers or get_providers()
    outcome = transcribe_segment(segment_id, providers)
    if outcome.get("status") != "transcribed":
        return outcome
    results = flush_transcribed_conversations(providers, force_segment_ids={segment_id})
    for item in results:
        if segment_id in item.get("segment_ids", []):
            return item
    logger.warning("segment %s transcribed but not summarised yet; worker will retry", segment_id)
    return {"status": "transcribed", "card_id": None, "turns": outcome.get("turns", 0)}


def _mark_failure(segment_id: str, error: str, *, retryable: bool) -> None:
    settings = get_settings()
    with session_scope() as db:
        segment = db.get(AudioSegment, segment_id)
        if segment is None:
            return
        segment.error = error[:500]
        if retryable and segment.attempts < settings.job_max_attempts:
            segment.status = "queued"
        else:
            segment.status = "failed"
        segment.processed_at = now_utc()


def _persist_card(
    db,
    user: User,
    segments: Sequence[AudioSegment],
    draft: CardDraft,
    turns: list[Turn],
    identities: list[dict],
    occurred_at: datetime,
) -> Card:
    primary = segments[0]
    users = [t for t in turns if t.speaker == "user"]
    others = [t for t in turns if t.speaker != "user"]
    primary_meta = dict(primary.meta or {})
    card = Card(
        user_id=user.id,
        title=(draft.title or "未命名对话")[:240],
        summary=draft.summary,
        key_points=draft.key_points,
        people=draft.people,
        type=draft.card_type,
        occurred_at=occurred_at,
        duration_s=float((turns[-1].end - turns[0].start) if turns else 0.0),
        user_turns=len(users),
        other_turns=len(others),
        audio_segment_id=primary.id,
        source_meta={
            "providers": get_providers().describe(),
            # 合并对话可能来自多个音频片段；audio_segment_id 保留首个片段以兼容旧客户端
            "audio_segment_ids": [segment.id for segment in segments],
            "segments": [
                {
                    "id": segment.id,
                    "recorded_at": (segment.meta or {}).get("recorded_at"),
                    "duration_s": segment.duration_s,
                    "device_id": segment.device_id,
                }
                for segment in segments
            ],
            "merged": len(segments) > 1,
            "identity": identities,
            "language": primary_meta.get("transcript", {}).get("language")
            if isinstance(primary_meta.get("transcript"), dict)
            else primary_meta.get("language"),
            "recorded_at": primary_meta.get("recorded_at"),
            "device_id": primary_meta.get("device_id"),
        },
    )
    db.add(card)
    db.flush()
    for seq, turn in enumerate(turns):
        db.add(
            TranscriptTurn(
                card_id=card.id,
                seq=seq,
                start=turn.start,
                end=turn.end,
                speaker=turn.speaker,
                text=turn.text,
                words=turn.words,
            )
        )
    db.flush()

    if user.settings_with_defaults().get("auto_schedule", True):
        for item in draft.schedule_items:
            add_schedule(
                db,
                user,
                title=item.title,
                start_at=item.start_at,
                end_at=item.end_at,
                location=item.location,
                description=item.description or "根据录音自动提取",
                source="card",
                card_id=card.id,
            )
        for item in draft.todos:
            add_todo(db, user, title=item.title, due_at=item.due_at, source="card", card_id=card.id)

    # Command execution: draft.commands is already filtered to user-grounded text.
    for item in draft.commands:
        execute_command(
            db,
            user,
            item.text,
            get_providers(),
            card_id=card.id,
            base_utc=occurred_at,
        )

    if draft.is_meeting or should_be_meeting(card, turns):
        attach_meeting(db, user, card, turns, get_providers())
    db.flush()
    return card


def reprocess_segment(segment_id: str) -> None:
    with session_scope() as db:
        segment = db.get(AudioSegment, segment_id)
        if segment is None:
            return
        old_card_id = segment.card_id
        segment.status = "queued"
        segment.attempts = 0
        segment.error = None
        segment.card_id = None
        if old_card_id:
            old_card = db.get(Card, old_card_id)
            if old_card is not None:
                old_card.deleted_at = now_utc()
        db.flush()


def pending_segment_ids(limit: int = 5) -> list[str]:
    with session_scope() as db:
        rows = db.scalars(
            select(AudioSegment)
            .where(AudioSegment.status == "queued")
            .order_by(AudioSegment.received_at)
            .limit(limit)
        ).all()
        return [row.id for row in rows]


def requeue_stuck_segments() -> int:
    """把上次进程遗留的 ``processing`` 片段重新入队（单 worker 架构，重启后可安全恢复）。

    Server 崩溃/重启时，正在处理的片段会永久停留在 processing，普通 worker 只挑选
    queued 片段，导致这些音频再也没有总结。启动 worker 时调用本函数修复。
    """
    with session_scope() as db:
        rows = db.scalars(
            select(AudioSegment).where(AudioSegment.status == "processing")
        ).all()
        for row in rows:
            row.status = "queued"
            row.error = "recovered_after_restart"
        if rows:
            db.flush()
        return len(rows)


def requeue_failed_segments(user_id: str | None = None) -> int:
    """把 failed 片段重置为 queued（模型/依赖修好后恢复历史失败数据）。"""
    with session_scope() as db:
        stmt = select(AudioSegment).where(AudioSegment.status == "failed")
        if user_id:
            stmt = stmt.where(AudioSegment.user_id == user_id)
        rows = db.scalars(stmt).all()
        for row in rows:
            row.status = "queued"
            row.attempts = 0
            row.error = None
            row.processed_at = None
        if rows:
            db.flush()
        return len(rows)
