"""Push delivery with a durable outbox (DingTalk webhook)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PushOutbox, User
from ..providers import Providers
from ..utils import now_utc


def _backoff_seconds(attempts: int) -> int:
    return min(1800, 30 * (2 ** max(0, attempts - 1)))


def queue_push(
    db: Session,
    user: User,
    title: str,
    markdown: str,
    providers: Providers,
    *,
    force: bool = False,
) -> PushOutbox:
    """Persist the message and try to deliver it immediately."""
    settings = user.settings_with_defaults()
    outbox = PushOutbox(user_id=user.id, title=title[:240], text=markdown, status="pending")
    if not force and not settings.get("push_enabled", True):
        outbox.status = "skipped"
        outbox.last_error = "push_disabled_by_user"
        db.add(outbox)
        db.flush()
        return outbox

    db.add(outbox)
    db.flush()
    ok, error = providers.push.send(title, markdown)
    if ok:
        outbox.status = "sent"
        outbox.attempts = 1
        outbox.sent_at = now_utc()
    else:
        outbox.attempts = 1
        outbox.last_error = error
        if not providers.push.is_configured():
            outbox.status = "skipped"
        elif outbox.attempts >= 5:
            outbox.status = "failed"
        else:
            outbox.status = "pending"
            outbox.next_attempt_at = now_utc() + timedelta(seconds=_backoff_seconds(outbox.attempts))
    return outbox


def drain_outbox(db: Session, providers: Providers, limit: int = 20) -> int:
    """Retry pending messages whose backoff expired."""
    if not providers.push.is_configured():
        return 0
    now = now_utc()
    rows = db.scalars(
        select(PushOutbox)
        .where(PushOutbox.status == "pending", PushOutbox.next_attempt_at <= now)
        .order_by(PushOutbox.created_at)
        .limit(limit)
    ).all()
    sent = 0
    for row in rows:
        ok, error = providers.push.send(row.title, row.text)
        row.attempts += 1
        if ok:
            row.status = "sent"
            row.sent_at = now
            sent += 1
        else:
            row.last_error = error
            if row.attempts >= 5:
                row.status = "failed"
            else:
                row.next_attempt_at = now + timedelta(seconds=_backoff_seconds(row.attempts))
    return sent


def push_text(db: Session, user: User, text: str, providers: Providers, *, title: str = "OpenAssistant", force: bool = False):
    markdown = f"### {title}\n\n{text}" if not text.lstrip().startswith("#") else text
    return queue_push(db, user, title, markdown, providers, force=force)
