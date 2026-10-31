"""Corrections (勘误), deletion and cascading cleanup with audit trail."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import default_timezone
from ..models import AuditLog, Card, CardCorrection, Meeting, ScheduleItem, Todo, TranscriptTurn, User
from ..providers import Providers
from ..utils import now_utc
from .align import Turn
from .entities import add_schedule, add_todo, soft_delete_derived
from .profile import refresh_profile
from .push_service import push_text
from .summarizer import _rule_extract

EDITABLE_FIELDS = ("title", "summary", "key_points", "people", "type")
CARD_TYPES = {"conversation", "meeting", "media", "other"}


def card_snapshot(card: Card) -> dict[str, Any]:
    return {
        "title": card.title,
        "summary": card.summary,
        "key_points": list(card.key_points or []),
        "people": list(card.people or []),
        "type": card.type,
        "deleted_at": card.deleted_at.isoformat() if card.deleted_at else None,
    }


def _audit(
    db: Session,
    user: User,
    action: str,
    entity: str,
    entity_id: str,
    before: Any = None,
    after: Any = None,
    note: str = "",
) -> AuditLog:
    log = AuditLog(
        user_id=user.id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        note=note,
    )
    db.add(log)
    db.flush()
    return log


def _card_turns(db: Session, card: Card) -> list[Turn]:
    rows = db.scalars(
        select(TranscriptTurn).where(TranscriptTurn.card_id == card.id).order_by(TranscriptTurn.seq)
    ).all()
    return [Turn(start=r.start, end=r.end, speaker=r.speaker, text=r.text, words=r.words or []) for r in rows]


def rederive_derived(db: Session, user: User, card: Card) -> dict[str, int]:
    """Recompute schedules/todos that were derived from this card."""
    now = now_utc()
    old_schedule = db.scalars(
        select(ScheduleItem).where(
            ScheduleItem.user_id == user.id,
            ScheduleItem.card_id == card.id,
            ScheduleItem.deleted_at.is_(None),
        )
    ).all()
    old_todos = db.scalars(
        select(Todo).where(Todo.user_id == user.id, Todo.card_id == card.id, Todo.deleted_at.is_(None))
    ).all()
    for row in list(old_schedule) + list(old_todos):
        row.deleted_at = now
    db.flush()

    if card.type in {"media", "other"} or card.deleted_at is not None:
        return {"schedule": 0, "todos": 0}

    turns = _card_turns(db, card)
    if not turns:
        return {"schedule": 0, "todos": 0}
    draft = _rule_extract(turns, card.occurred_at, user.timezone or default_timezone())
    created = {"schedule": 0, "todos": 0}
    for item in draft.schedule_items:
        add_schedule(
            db,
            user,
            title=item.title,
            start_at=item.start_at,
            end_at=item.end_at,
            location=item.location,
            description=item.description or "根据勘误后的卡片重新提取",
            source="card",
            card_id=card.id,
        )
        created["schedule"] += 1
    for item in draft.todos:
        add_todo(
            db,
            user,
            title=item.title,
            due_at=item.due_at,
            source="card",
            card_id=card.id,
        )
        created["todos"] += 1
    return created


def apply_correction(
    db: Session,
    user: User,
    card: Card,
    updates: dict[str, Any],
    providers: Providers,
    *,
    note: str = "",
) -> tuple[Card, dict[str, int]]:
    if card.deleted_at is not None:
        raise ValueError("card_deleted")
    before = card_snapshot(card)
    changed: list[str] = []
    for field in EDITABLE_FIELDS:
        if field not in updates or updates[field] is None:
            continue
        value = updates[field]
        if field == "type":
            if value not in CARD_TYPES:
                raise ValueError("invalid_card_type")
        if field == "key_points":
            value = [str(v).strip() for v in value if str(v).strip()][:12]
        if field == "people":
            value = [str(v).strip() for v in value if str(v).strip()][:12]
        old_value = getattr(card, field)
        if old_value == value:
            continue
        setattr(card, field, value)
        changed.append(field)
        db.add(
            CardCorrection(
                user_id=user.id,
                card_id=card.id,
                field=field,
                before=old_value,
                after=value,
                note=note,
            )
        )
    if not changed:
        return card, {"schedule": 0, "todos": 0}
    card.updated_at = now_utc()
    db.flush()

    cascade: dict[str, int] = {"schedule": 0, "todos": 0}
    if "type" in changed or "summary" in changed or "title" in changed or "key_points" in changed:
        cascade = rederive_derived(db, user, card)
    _audit(db, user, "card.correct", "card", card.id, before, card_snapshot(card), note)
    user.profile_dirty = True
    if user.settings_with_defaults().get("auto_profile_update", True):
        refresh_profile(db, user, providers, reason="correction")
    if user.settings_with_defaults().get("correction_notice", True):
        push_text(
            db,
            user,
            f"已按你的勘误更新卡片「{card.title}」。相关日程/待办已同步调整，画像已重新生成。",
            providers,
            title="OpenAssistant · 勘误已生效",
        )
    return card, cascade


def delete_card(
    db: Session,
    user: User,
    card: Card,
    providers: Providers,
    *,
    note: str = "",
) -> dict[str, Any]:
    if card.deleted_at is not None:
        return {"deleted": True, "cascaded": {"schedule": 0, "todos": 0, "meetings": 0}, "profile_dirty": False}
    before = card_snapshot(card)
    card.deleted_at = now_utc()
    db.flush()
    cascade = soft_delete_derived(db, user.id, card.id)
    meetings_removed = 0
    meetings = db.scalars(
        select(Meeting).where(Meeting.user_id == user.id, Meeting.deleted_at.is_(None))
    ).all()
    for meeting in meetings:
        card_ids = [cid for cid in (meeting.card_ids or []) if cid != card.id]
        if card_ids == list(meeting.card_ids or []):
            continue
        meeting.card_ids = card_ids
        if not card_ids:
            meeting.deleted_at = now_utc()
            meetings_removed += 1
        else:
            meeting.updated_at = now_utc()
    card.meeting_id = None
    _audit(db, user, "card.delete", "card", card.id, before, {"deleted": True}, note)
    user.profile_dirty = True
    if user.settings_with_defaults().get("auto_profile_update", True):
        refresh_profile(db, user, providers, reason="deletion")
    if user.settings_with_defaults().get("correction_notice", True):
        summary = "、".join(f"{k} {v} 条" for k, v in cascade.items() if v) or "无"
        push_text(
            db,
            user,
            f"已删除卡片「{before['title']}」，并级联清理了 {summary} 相关条目；用户画像已重新生成。",
            providers,
            title="OpenAssistant · 内容已删除",
        )
    return {
        "deleted": True,
        "cascaded": {**cascade, "meetings": meetings_removed},
        "profile_dirty": bool(user.profile_dirty),
    }


def restore_card(db: Session, user: User, card: Card) -> Card:
    if card.deleted_at is None:
        return card
    card.deleted_at = None
    card.updated_at = now_utc()
    db.flush()
    _audit(db, user, "card.restore", "card", card.id, {"deleted": True}, {"deleted": False}, "")
    return card
