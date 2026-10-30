"""Entity helpers shared by the pipeline, commands and corrections."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ScheduleItem, Todo, User
from ..utils import now_utc


def add_schedule(
    db: Session,
    user: User,
    *,
    title: str,
    start_at,
    end_at=None,
    location: str | None = None,
    description: str | None = None,
    source: str = "card",
    card_id: str | None = None,
    remind_minutes_before: int = 30,
    commit: bool = True,
) -> ScheduleItem:
    if end_at is None:
        end_at = start_at + timedelta(hours=1)
    item = ScheduleItem(
        user_id=user.id,
        title=(title or "未命名日程")[:240],
        start_at=start_at,
        end_at=end_at,
        location=(location or None),
        description=(description or None),
        source=source,
        card_id=card_id,
        remind_at=start_at - timedelta(minutes=max(0, remind_minutes_before)),
    )
    db.add(item)
    db.flush()
    return item


def add_todo(
    db: Session,
    user: User,
    *,
    title: str,
    due_at=None,
    source: str = "card",
    card_id: str | None = None,
    priority: str = "normal",
    commit: bool = True,
) -> Todo:
    item = Todo(
        user_id=user.id,
        title=(title or "未命名待办")[:400],
        due_at=due_at,
        source=source,
        card_id=card_id,
        priority=priority,
    )
    db.add(item)
    db.flush()
    return item


def soft_delete_derived(db: Session, user_id: str, card_id: str) -> dict[str, int]:
    """Soft delete everything that was derived from a card."""
    now = now_utc()
    schedule_rows = db.scalars(
        select(ScheduleItem).where(
            ScheduleItem.user_id == user_id,
            ScheduleItem.card_id == card_id,
            ScheduleItem.deleted_at.is_(None),
        )
    ).all()
    for row in schedule_rows:
        row.deleted_at = now
    todo_rows = db.scalars(
        select(Todo).where(
            Todo.user_id == user_id,
            Todo.card_id == card_id,
            Todo.deleted_at.is_(None),
        )
    ).all()
    for row in todo_rows:
        row.deleted_at = now
    db.flush()
    return {"schedule": len(schedule_rows), "todos": len(todo_rows)}


def schedule_dict(item: ScheduleItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "start_at": item.start_at,
        "end_at": item.end_at,
        "location": item.location,
        "description": item.description,
        "source": item.source,
        "card_id": item.card_id,
        "status": item.status,
        "remind_at": item.remind_at,
        "reminded": item.reminded,
        "created_at": item.created_at,
    }


def todo_dict(item: Todo) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "status": item.status,
        "due_at": item.due_at,
        "source": item.source,
        "card_id": item.card_id,
        "priority": item.priority,
        "created_at": item.created_at,
        "completed_at": item.completed_at,
    }
