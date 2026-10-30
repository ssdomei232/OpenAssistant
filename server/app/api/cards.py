"""Summary cards: listing, detail, corrections (勘误) and deletion."""
from __future__ import annotations

import base64
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import String, cast, or_, select

from ..deps import CurrentUser, DbSession, ProvidersDep
from ..models import Card, CardCorrection, TranscriptTurn
from ..schemas import CardPatch
from ..serializers import card_detail, card_list_item, parse_dt
from ..services.corrections import apply_correction, delete_card, restore_card

router = APIRouter(tags=["cards"])


def _encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        occurred_at, card_id = raw.split("|", 1)
        return parse_dt(occurred_at), card_id
    except Exception:
        raise HTTPException(status_code=422, detail="invalid_cursor")


@router.get("/cards")
def list_cards(
    user: CurrentUser,
    db: DbSession,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    q: str | None = None,
    type: str | None = None,
    speaker: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> dict:
    stmt = select(Card).where(Card.user_id == user.id)
    if not include_deleted:
        stmt = stmt.where(Card.deleted_at.is_(None))
    if from_:
        start = parse_dt(from_)
        if start:
            stmt = stmt.where(Card.occurred_at >= start)
    if to:
        end = parse_dt(to)
        if end:
            stmt = stmt.where(Card.occurred_at <= end)
    if type:
        stmt = stmt.where(Card.type == type)
    if speaker == "user":
        stmt = stmt.where(Card.user_turns > 0)
    elif speaker == "other":
        stmt = stmt.where(Card.other_turns > 0)
    if q:
        like = f"%{q[:80]}%"
        stmt = stmt.where(
            or_(
                Card.title.like(like),
                Card.summary.like(like),
                cast(Card.people, String).like(like),
                cast(Card.key_points, String).like(like),
            )
        )
    occurred_cursor, id_cursor = _decode_cursor(cursor)
    if occurred_cursor and id_cursor:
        stmt = stmt.where(
            or_(
                Card.occurred_at < occurred_cursor,
                (Card.occurred_at == occurred_cursor) & (Card.id < id_cursor),
            )
        )
    stmt = stmt.order_by(Card.occurred_at.desc(), Card.id.desc()).limit(limit + 1)
    rows = list(db.scalars(stmt).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [card_list_item(row, db) for row in rows]
    corrected_ids = set(
        db.scalars(
            select(CardCorrection.card_id).where(CardCorrection.card_id.in_([row.id for row in rows]))
        ).all()
    ) if rows else set()
    for item in items:
        if item["id"] in corrected_ids:
            item["has_correction"] = True
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = _encode_cursor(f"{last.occurred_at.isoformat()}|{last.id}")
    return {"items": items, "next_cursor": next_cursor}


@router.get("/cards/{card_id}")
def get_card(card_id: str, user: CurrentUser, db: DbSession) -> dict:
    card = db.get(Card, card_id)
    if card is None or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="card_not_found")
    return card_detail(db, card)


@router.patch("/cards/{card_id}")
def patch_card(card_id: str, payload: CardPatch, user: CurrentUser, db: DbSession, providers: ProvidersDep) -> dict:
    card = db.get(Card, card_id)
    if card is None or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="card_not_found")
    updates = payload.model_dump(exclude_none=True)
    note = updates.pop("note", "")
    try:
        card, cascade = apply_correction(db, user, card, updates, providers, note=note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(card)
    detail = card_detail(db, card)
    detail["cascaded"] = cascade
    return detail


@router.delete("/cards/{card_id}")
def remove_card(card_id: str, user: CurrentUser, db: DbSession, providers: ProvidersDep, note: str = "") -> dict:
    card = db.get(Card, card_id)
    if card is None or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="card_not_found")
    result = delete_card(db, user, card, providers, note=note)
    db.commit()
    return result


@router.post("/cards/{card_id}/restore")
def restore(card_id: str, user: CurrentUser, db: DbSession) -> dict:
    card = db.get(Card, card_id)
    if card is None or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="card_not_found")
    restore_card(db, user, card)
    db.commit()
    return {"restored": True}


@router.get("/cards/{card_id}/transcript.txt", response_class=PlainTextResponse)
def transcript(card_id: str, user: CurrentUser, db: DbSession) -> str:
    card = db.get(Card, card_id)
    if card is None or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="card_not_found")
    rows = db.scalars(
        select(TranscriptTurn).where(TranscriptTurn.card_id == card.id).order_by(TranscriptTurn.seq)
    ).all()
    label = {"user": "[使用者]", "other": "[其他人]", "unknown": "[未知]"}
    lines = [f"# {card.title}", "", card.summary, ""]
    for row in rows:
        ts = f"{int(row.start // 60):02d}:{row.start % 60:04.1f}"
        lines.append(f"[{ts}] {label.get(row.speaker, '[其他人]')} {row.text}")
    return "\n".join(lines)
