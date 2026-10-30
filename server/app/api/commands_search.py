"""Command and search APIs."""
from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..deps import CurrentUser, DbSession, ProvidersDep
from ..models import Command
from ..providers.search import SearchUnavailable
from ..schemas import CommandIn, SearchIn
from ..serializers import command_out
from ..services.commands import execute_command
from ..utils import now_utc

router = APIRouter(tags=["commands"])


def _encode_cursor(value) -> str:
    return base64.urlsafe_b64encode(value.isoformat().encode()).decode()


def _decode_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        from ..serializers import parse_dt

        return parse_dt(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        raise HTTPException(status_code=422, detail="invalid_cursor")


@router.post("/commands")
def create_command(payload: CommandIn, user: CurrentUser, db: DbSession, providers: ProvidersDep) -> dict:
    command = execute_command(
        db,
        user,
        payload.text,
        providers,
        execute=payload.execute,
        base_utc=now_utc(),
    )
    db.commit()
    db.refresh(command)
    return command_out(command)


@router.get("/commands")
def list_commands(
    user: CurrentUser,
    db: DbSession,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> dict:
    created_before = _decode_cursor(cursor)
    stmt = select(Command).where(Command.user_id == user.id)
    if status:
        stmt = stmt.where(Command.status == status)
    if created_before:
        stmt = stmt.where(Command.created_at < created_before)
    rows = list(db.scalars(stmt.order_by(Command.created_at.desc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [command_out(row) for row in rows],
        "next_cursor": _encode_cursor(rows[-1].created_at) if has_more and rows else None,
    }


@router.post("/commands/{command_id}/retry")
def retry_command(command_id: str, user: CurrentUser, db: DbSession, providers: ProvidersDep) -> dict:
    command = db.get(Command, command_id)
    if command is None or command.user_id != user.id:
        raise HTTPException(status_code=404, detail="command_not_found")
    retried = execute_command(db, user, command.text, providers, execute=True)
    db.commit()
    db.refresh(retried)
    return command_out(retried)


@router.post("/search")
def search(payload: SearchIn, user: CurrentUser, providers: ProvidersDep) -> dict:
    provider_name = payload.provider or user.settings_with_defaults().get("search_provider") or "builtin"
    provider = providers.search_for(provider_name)
    context = f"用户画像（供回答风格参考）：{user.profile_text}" if user.profile_text else ""
    try:
        response = provider.search(payload.query, limit=payload.limit, context=context)
    except SearchUnavailable as exc:
        # graceful fallback: the App selected DeepSeek but it is not configured
        response = providers.search_builtin.search(payload.query, limit=payload.limit, context=context)
        return {
            "query": response.query,
            "provider": response.provider,
            "answer": response.answer,
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet} for r in response.results
            ],
            "took_ms": response.took_ms,
            "fallback_reason": str(exc),
        }
    return {
        "query": response.query,
        "provider": response.provider,
        "answer": response.answer,
        "results": [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in response.results],
        "took_ms": response.took_ms,
    }
