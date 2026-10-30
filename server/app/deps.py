"""FastAPI dependencies."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User, Voiceprint
from .providers import Providers, get_providers
from .security import get_current_user

DbSession = Annotated[Session, Depends(get_db)]


def _providers() -> Providers:
    return get_providers()


ProvidersDep = Annotated[Providers, Depends(_providers)]


def _current_user(request: Request, db: DbSession) -> User:
    return get_current_user(request, db)


CurrentUser = Annotated[User, Depends(_current_user)]


def voiceprint_for(db: Session, user: User) -> Voiceprint | None:
    return db.scalar(select(Voiceprint).where(Voiceprint.user_id == user.id))
