"""The audio → card processing pipeline.

Order: diarize → voiceprint identify → ASR → align/label → summarise →
cards/schedules/todos (user-only) → commands (user-only) → meeting → profile.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import AudioSegment, Card, TranscriptTurn, User
from ..providers import Providers, get_providers
from ..providers.speaker import identify
from ..utils import decode_audio, now_utc, utc_from
from .align import Turn, align
from .commands import execute_command
from .entities import add_schedule, add_todo
from .meetings import attach_meeting, should_be_meeting
from .profile import maybe_update_profile
from .storage import get_storage
from .summarizer import CardDraft, summarize

logger = logging.getLogger(__name__)


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


def process_segment(segment_id: str, providers: Providers | None = None) -> dict:
    """Run the full pipeline for one uploaded segment.  Idempotent on retries."""
    providers = providers or get_providers()
    settings = get_settings()
    storage = get_storage()

    with session_scope() as db:
        segment = db.get(AudioSegment, segment_id)
        if segment is None:
            return {"status": "missing"}
        if segment.status in {"done", "processing"}:
            return {"status": segment.status, "card_id": segment.card_id}
        segment.status = "processing"
        segment.attempts += 1
        segment.error = None
        user = db.get(User, segment.user_id)
        if user is None:
            segment.status = "failed"
            segment.error = "user_missing"
            return {"status": "failed", "error": "user_missing"}
        tz_name = user.timezone or "Asia/Shanghai"
        audio_bytes = storage.read(segment.path)
        segment_bytes = audio_bytes
        meta = dict(segment.meta or {})
        occurred_at = _segment_occurred_at(segment, tz_name)
        user_id = user.id

    try:
        samples, sr = decode_audio(segment_bytes, target_sr=16000)
        if samples.size == 0:
            raise ProcessingError("empty_audio")
        duration = samples.size / sr if sr else 0.0

        # Deterministic demo / integration-test hook: only honoured when the
        # server runs with OA_DEMO_MODE=1.  Real providers are never bypassed
        # in production.
        demo_turns = (meta or {}).get("demo_turns") if settings.demo_mode else None
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
            )
            asr_result = providers.asr.transcribe(samples, sr)
            turns = align(asr_result.words, identities)
        if not turns:
            with session_scope() as db:
                segment = db.get(AudioSegment, segment_id)
                segment.status = "done"
                segment.processed_at = now_utc()
                segment.error = "no_speech_recognised"
            return {"status": "done", "card_id": None, "turns": 0}

        with session_scope() as db:
            user = db.get(User, user_id)
            profile = user.profile_text or ""
            aliases = list(user.aliases or [])
            draft = summarize(
                turns,
                occurred_at=occurred_at,
                tz_name=tz_name,
                llm=providers.llm,
                aliases=aliases,
                profile=profile,
            )
            card = _persist_card(db, user, segment_id, draft, turns, identities, occurred_at, meta, settings)
            db.flush()
            segment = db.get(AudioSegment, segment_id)
            segment.card_id = card.id
            segment.status = "done"
            segment.processed_at = now_utc()
            segment.error = None
            maybe_update_profile(db, user, providers)
            result = {
                "status": "done",
                "card_id": card.id,
                "turns": len(turns),
                "user_turns": sum(1 for t in turns if t.speaker == "user"),
                "schedule_items": len(draft.schedule_items),
                "todos": len(draft.todos),
                "commands": len(draft.commands),
                "meeting_id": card.meeting_id,
            }
        return result
    except ProcessingError as exc:
        _mark_failure(segment_id, exc.code, retryable=exc.retryable)
        return {"status": "failed", "error": exc.code}
    except Exception as exc:  # noqa: BLE001 - pipeline robustness
        logger.exception("pipeline failed for segment %s", segment_id)
        _mark_failure(segment_id, f"{type(exc).__name__}: {exc}", retryable=True)
        return {"status": "failed", "error": type(exc).__name__}


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
    segment_id: str,
    draft: CardDraft,
    turns: list[Turn],
    identities,
    occurred_at: datetime,
    meta: dict,
    settings,
) -> Card:
    users = [t for t in turns if t.speaker == "user"]
    others = [t for t in turns if t.speaker != "user"]
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
        audio_segment_id=segment_id,
        source_meta={
            "providers": get_providers().describe(),
            "identity": [
                {"start": i.start, "end": i.end, "anonymous": i.anonymous, "label": i.label, "similarity": i.similarity}
                for i in identities
            ],
            "language": getattr(meta, "get", lambda *_: None)("language"),
            "recorded_at": meta.get("recorded_at"),
            "device_id": meta.get("device_id"),
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
