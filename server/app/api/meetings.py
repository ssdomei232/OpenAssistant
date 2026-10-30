"""Meeting minutes API."""
from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..deps import CurrentUser, DbSession, ProvidersDep
from ..models import Meeting
from ..schemas import MeetingPatch
from ..serializers import meeting_out, parse_dt
from ..services.meetings import regenerate_minutes
from ..utils import now_utc

router = APIRouter(tags=["meetings"])


def _cursor(value, item_id: str) -> str:
    return base64.urlsafe_b64encode(f"{value.isoformat()}|{item_id}".encode()).decode()


def _decode(cursor: str | None):
    if not cursor:
        return None, None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        started, item_id = raw.split("|", 1)
        return parse_dt(started), item_id
    except Exception:
        raise HTTPException(status_code=422, detail="invalid_cursor")


@router.get("/meetings")
def list_meetings(
    user: CurrentUser,
    db: DbSession,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> dict:
    started_cursor, id_cursor = _decode(cursor)
    stmt = select(Meeting).where(Meeting.user_id == user.id, Meeting.deleted_at.is_(None))
    if from_:
        start = parse_dt(from_)
        if start:
            stmt = stmt.where(Meeting.started_at >= start)
    if to:
        end = parse_dt(to)
        if end:
            stmt = stmt.where(Meeting.started_at <= end)
    if started_cursor and id_cursor:
        stmt = stmt.where(
            (Meeting.started_at < started_cursor)
            | ((Meeting.started_at == started_cursor) & (Meeting.id < id_cursor))
        )
    rows = list(db.scalars(stmt.order_by(Meeting.started_at.desc(), Meeting.id.desc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [meeting_out(row) for row in rows],
        "next_cursor": _cursor(rows[-1].started_at, rows[-1].id) if has_more and rows else None,
    }


@router.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: str, user: CurrentUser, db: DbSession) -> dict:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None or meeting.user_id != user.id:
        raise HTTPException(status_code=404, detail="meeting_not_found")
    return meeting_out(meeting, full=True)


@router.patch("/meetings/{meeting_id}")
def patch_meeting(meeting_id: str, payload: MeetingPatch, user: CurrentUser, db: DbSession) -> dict:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None or meeting.user_id != user.id:
        raise HTTPException(status_code=404, detail="meeting_not_found")
    updates = payload.model_dump(exclude_none=True)
    for field in ("title", "summary", "minutes", "action_items"):
        if field in updates:
            setattr(meeting, field, updates[field])
    meeting.updated_at = now_utc()
    db.commit()
    db.refresh(meeting)
    return meeting_out(meeting, full=True)


@router.post("/meetings/{meeting_id}/regenerate")
def regenerate(meeting_id: str, user: CurrentUser, db: DbSession, providers: ProvidersDep) -> dict:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None or meeting.user_id != user.id:
        raise HTTPException(status_code=404, detail="meeting_not_found")
    regenerate_minutes(db, user, meeting, providers)
    db.commit()
    db.refresh(meeting)
    return meeting_out(meeting, full=True)


@router.delete("/meetings/{meeting_id}")
def delete_meeting(meeting_id: str, user: CurrentUser, db: DbSession) -> dict:
    meeting = db.get(Meeting, meeting_id)
    if meeting is None or meeting.user_id != user.id:
        raise HTTPException(status_code=404, detail="meeting_not_found")
    meeting.deleted_at = now_utc()
    db.commit()
    return {"deleted": True}
