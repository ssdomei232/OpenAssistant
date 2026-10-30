"""Schedule and todo APIs."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..deps import CurrentUser, DbSession
from ..models import ScheduleItem, Todo
from ..schemas import ScheduleIn, SchedulePatch, TodoIn, TodoPatch
from ..serializers import parse_dt, schedule_out, todo_out
from ..services.entities import add_schedule, add_todo
from ..utils import now_utc

router = APIRouter(tags=["planner"])


def _cursor(value: datetime, item_id: str) -> str:
    return base64.urlsafe_b64encode(f"{value.isoformat()}|{item_id}".encode()).decode()


def _decode(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        occurred, item_id = raw.split("|", 1)
        return parse_dt(occurred), item_id
    except Exception:
        raise HTTPException(status_code=422, detail="invalid_cursor")


@router.get("/schedule")
def list_schedule(
    user: CurrentUser,
    db: DbSession,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    stmt = select(ScheduleItem).where(ScheduleItem.user_id == user.id)
    if not include_deleted:
        stmt = stmt.where(ScheduleItem.deleted_at.is_(None))
    if from_:
        start = parse_dt(from_)
        if start:
            stmt = stmt.where(ScheduleItem.start_at >= start)
    if to:
        end = parse_dt(to)
        if end:
            stmt = stmt.where(ScheduleItem.start_at <= end)
    rows = db.scalars(stmt.order_by(ScheduleItem.start_at).limit(limit)).all()
    return {"items": [schedule_out(row) for row in rows]}


@router.post("/schedule", status_code=201)
def create_schedule(payload: ScheduleIn, user: CurrentUser, db: DbSession) -> dict:
    start = parse_dt(payload.start_at)
    if start is None:
        raise HTTPException(status_code=422, detail="invalid_start_at")
    end = parse_dt(payload.end_at)
    item = add_schedule(
        db,
        user,
        title=payload.title,
        start_at=start,
        end_at=end,
        location=payload.location,
        description=payload.description,
        source="manual",
        remind_minutes_before=payload.remind_minutes_before,
    )
    db.commit()
    db.refresh(item)
    return schedule_out(item)


@router.patch("/schedule/{item_id}")
def patch_schedule(item_id: str, payload: SchedulePatch, user: CurrentUser, db: DbSession) -> dict:
    item = db.get(ScheduleItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="schedule_not_found")
    updates = payload.model_dump(exclude_none=True)
    remind = updates.pop("remind_minutes_before", None)
    for field in ("title", "location", "description", "status"):
        if field in updates:
            setattr(item, field, updates[field])
    if "start_at" in updates:
        start = parse_dt(updates["start_at"])
        if start is None:
            raise HTTPException(status_code=422, detail="invalid_start_at")
        item.start_at = start
    if "end_at" in updates:
        item.end_at = parse_dt(updates["end_at"])
    if remind is not None and item.start_at:
        item.remind_at = item.start_at - timedelta(minutes=max(0, remind))
        item.reminded = False
    db.commit()
    db.refresh(item)
    return schedule_out(item)


@router.delete("/schedule/{item_id}")
def delete_schedule(item_id: str, user: CurrentUser, db: DbSession) -> dict:
    item = db.get(ScheduleItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="schedule_not_found")
    item.deleted_at = now_utc()
    db.commit()
    return {"deleted": True}


@router.get("/todos")
def list_todos(
    user: CurrentUser,
    db: DbSession,
    status: str = Query(default="all", pattern="^(pending|done|all)$"),
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = None,
) -> dict:
    created_cursor, id_cursor = _decode(cursor)
    stmt = select(Todo).where(Todo.user_id == user.id, Todo.deleted_at.is_(None))
    if status != "all":
        stmt = stmt.where(Todo.status == status)
    if created_cursor and id_cursor:
        stmt = stmt.where(
            (Todo.created_at < created_cursor) | ((Todo.created_at == created_cursor) & (Todo.id < id_cursor))
        )
    rows = list(db.scalars(stmt.order_by(Todo.created_at.desc(), Todo.id.desc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return {"items": [todo_out(row) for row in rows], "next_cursor": next_cursor}


@router.post("/todos", status_code=201)
def create_todo(payload: TodoIn, user: CurrentUser, db: DbSession) -> dict:
    item = add_todo(
        db,
        user,
        title=payload.title,
        due_at=parse_dt(payload.due_at),
        source="manual",
        priority=payload.priority,
    )
    db.commit()
    db.refresh(item)
    return todo_out(item)


@router.patch("/todos/{item_id}")
def patch_todo(item_id: str, payload: TodoPatch, user: CurrentUser, db: DbSession) -> dict:
    item = db.get(Todo, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="todo_not_found")
    updates = payload.model_dump(exclude_none=True)
    for field in ("title", "priority"):
        if field in updates:
            setattr(item, field, updates[field])
    if "due_at" in updates:
        item.due_at = parse_dt(updates["due_at"])
    if "status" in updates:
        item.status = updates["status"]
        item.completed_at = now_utc() if item.status == "done" else None
    db.commit()
    db.refresh(item)
    return todo_out(item)


@router.delete("/todos/{item_id}")
def delete_todo(item_id: str, user: CurrentUser, db: DbSession) -> dict:
    item = db.get(Todo, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="todo_not_found")
    item.deleted_at = now_utc()
    db.commit()
    return {"deleted": True}
