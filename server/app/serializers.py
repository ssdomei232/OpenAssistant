"""Response serialisers (datetimes are emitted as UTC ISO-8601 with ``Z``)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    SPEAKER_LABELS,
    AuditLog,
    AudioSegment,
    Card,
    CardCorrection,
    Command,
    Device,
    Meeting,
    ScheduledJob,
    ScheduleItem,
    Todo,
    TranscriptTurn,
    User,
    Voiceprint,
)
from .services.storage import get_storage
from .utils import iso_utc, utc_from


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return utc_from(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def user_me(user: User, voiceprint: Voiceprint | None = None) -> dict:
    settings = user.settings_with_defaults()
    return {
        "id": user.id,
        "display_name": user.display_name,
        "aliases": list(user.aliases or []),
        "timezone": user.timezone,
        "search_provider": settings.get("search_provider") or "builtin",
        "voiceprint": voiceprint_out(voiceprint),
        "profile": {
            "text": user.profile_text or "",
            "updated_at": iso_utc(user.profile_updated_at),
            "version": user.profile_version or 0,
        },
        "created_at": iso_utc(user.created_at),
        "settings": settings,
    }


def voiceprint_out(voiceprint: Voiceprint | None) -> dict:
    if voiceprint is None:
        return {"enrolled": False, "threshold": 60.0, "updated_at": None, "quality": None, "sample_seconds": None}
    return {
        "enrolled": True,
        "threshold": voiceprint.threshold,
        "updated_at": iso_utc(voiceprint.updated_at),
        "quality": round(voiceprint.quality, 3),
        "sample_seconds": round(voiceprint.sample_seconds, 2),
    }


def segment_out(segment: AudioSegment) -> dict:
    return {
        "id": segment.id,
        "client_segment_id": segment.client_segment_id,
        "status": segment.status,
        "error": segment.error,
        "card_id": segment.card_id,
        "duration_s": segment.duration_s,
        "received_at": iso_utc(segment.received_at),
        "processed_at": iso_utc(segment.processed_at),
    }


def _audio_available(db: Session | None, card: Card) -> bool:
    if not card.audio_segment_id or db is None:
        return bool(card.audio_segment_id)
    segment = db.get(AudioSegment, card.audio_segment_id)
    if segment is None:
        return False
    try:
        return get_storage().exists(segment.path)
    except Exception:
        return False


def card_list_item(card: Card, db: Session | None = None) -> dict:
    return {
        "id": card.id,
        "title": card.title,
        "summary": card.summary,
        "key_points": list(card.key_points or []),
        "people": list(card.people or []),
        "type": card.type,
        "occurred_at": iso_utc(card.occurred_at),
        "duration_s": card.duration_s,
        "audio_url": f"/api/v1/audio/{card.audio_segment_id}/file" if card.audio_segment_id else None,
        "audio_available": _audio_available(db, card),
        "labels": {"user_turns": card.user_turns, "other_turns": card.other_turns},
        "meeting_id": card.meeting_id,
        "has_correction": bool(card.source_meta and card.source_meta.get("corrected")),
        "deleted_at": iso_utc(card.deleted_at),
    }


def schedule_out(item: ScheduleItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "start_at": iso_utc(item.start_at),
        "end_at": iso_utc(item.end_at),
        "location": item.location,
        "description": item.description,
        "source": item.source,
        "card_id": item.card_id,
        "status": item.status,
        "remind_at": iso_utc(item.remind_at),
        "reminded": item.reminded,
        "created_at": iso_utc(item.created_at),
    }


def todo_out(item: Todo) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "status": item.status,
        "due_at": iso_utc(item.due_at),
        "source": item.source,
        "card_id": item.card_id,
        "priority": item.priority,
        "created_at": iso_utc(item.created_at),
        "completed_at": iso_utc(item.completed_at),
    }


def meeting_out(meeting: Meeting, *, full: bool = False) -> dict:
    data = {
        "id": meeting.id,
        "title": meeting.title,
        "started_at": iso_utc(meeting.started_at),
        "ended_at": iso_utc(meeting.ended_at),
        "participants": meeting.participants or [],
        "summary": meeting.summary,
        "action_items": meeting.action_items or [],
        "card_ids": meeting.card_ids or [],
        "status": meeting.status,
        "audio_url": None,
        "created_at": iso_utc(meeting.created_at),
    }
    if full:
        data.update(
            {
                "minutes": meeting.minutes,
                "topics": meeting.topics or [],
                "decisions": meeting.decisions or [],
                "updated_at": iso_utc(meeting.updated_at),
            }
        )
    return data


def command_out(command: Command) -> dict:
    return {
        "id": command.id,
        "text": command.text,
        "intent": command.intent,
        "status": command.status,
        "result": command.result,
        "pushed": command.pushed,
        "card_id": command.card_id,
        "error": command.error,
        "created_at": iso_utc(command.created_at),
    }


def job_out(job: ScheduledJob, next_run_at: datetime | None = None) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "cron": job.cron,
        "run_at": iso_utc(job.run_at),
        "timezone": job.timezone,
        "prompt": job.prompt,
        "action": job.action,
        "enabled": job.enabled,
        "next_run_at": iso_utc(next_run_at),
        "last_run_at": iso_utc(job.last_run_at),
        "last_result": job.last_result,
        "created_at": iso_utc(job.created_at),
    }


def audit_out(log: AuditLog) -> dict:
    return {
        "id": log.id,
        "action": log.action,
        "entity": log.entity,
        "entity_id": log.entity_id,
        "before": log.before,
        "after": log.after,
        "note": log.note,
        "created_at": iso_utc(log.created_at),
    }


def device_out(device: Device) -> dict:
    config = dict(device.config or {})
    wifi = dict(config.get("wifi") or {})
    if wifi.get("password_enc") or wifi.get("password"):
        wifi["password"] = "******"
    wifi.pop("password_enc", None)
    config["wifi"] = wifi
    return {
        "id": device.id,
        "name": device.name,
        "last_seen": iso_utc(device.last_seen),
        "config": config,
        "status": "online" if device.last_seen else "unknown",
    }


def card_detail(db: Session, card: Card) -> dict:
    turns = db.scalars(
        select(TranscriptTurn).where(TranscriptTurn.card_id == card.id).order_by(TranscriptTurn.seq)
    ).all()
    schedule_items = db.scalars(
        select(ScheduleItem).where(
            ScheduleItem.card_id == card.id, ScheduleItem.deleted_at.is_(None)
        ).order_by(ScheduleItem.start_at)
    ).all()
    todos = db.scalars(
        select(Todo).where(Todo.card_id == card.id, Todo.deleted_at.is_(None)).order_by(Todo.created_at)
    ).all()
    commands = db.scalars(
        select(Command).where(Command.card_id == card.id).order_by(Command.created_at)
    ).all()
    corrections = db.scalars(
        select(CardCorrection).where(CardCorrection.card_id == card.id).order_by(CardCorrection.created_at)
    ).all()
    audio_available = _audio_available(db, card)
    data = card_list_item(card, db)
    data.update(
        {
            "transcript": [
                {
                    "start": round(turn.start, 3),
                    "end": round(turn.end, 3),
                    "speaker": turn.speaker,
                    "speaker_label": SPEAKER_LABELS.get(turn.speaker, "[其他人]"),
                    "text": turn.text,
                }
                for turn in turns
            ],
            "schedule_items": [schedule_out(item) for item in schedule_items],
            "todos": [todo_out(item) for item in todos],
            "commands": [command_out(item) for item in commands],
            "corrections": [
                {
                    "id": c.id,
                    "field": c.field,
                    "before": c.before,
                    "after": c.after,
                    "note": c.note,
                    "at": iso_utc(c.created_at),
                }
                for c in corrections
            ],
            "has_correction": bool(corrections),
            "audio_available": audio_available,
            "source_meta": card.source_meta or {},
            "created_at": iso_utc(card.created_at),
            "updated_at": iso_utc(card.updated_at),
            "deleted_at": iso_utc(card.deleted_at),
        }
    )
    return data
