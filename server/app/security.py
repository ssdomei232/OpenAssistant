"""API key authentication."""
from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import User
from .utils import generate_api_key, hash_api_key


def extract_api_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def authenticate(db: Session, api_key: str | None) -> User | None:
    if not api_key:
        return None
    pepper = get_settings().api_key_pepper
    digest = hash_api_key(api_key, pepper)
    return db.scalar(select(User).where(User.api_key_hash == digest))


def get_current_user(request: Request, db: Session) -> User:
    user = authenticate(db, extract_api_key(request))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_api_key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return user


def create_user(db: Session, *, display_name: str = "我", api_key: str | None = None, aliases: list[str] | None = None) -> tuple[User, str]:
    settings = get_settings()
    api_key = api_key or generate_api_key()
    user = User(
        display_name=display_name,
        aliases=aliases or [],
        api_key_hash=hash_api_key(api_key, settings.api_key_pepper),
        timezone=settings.timezone,
        settings={"push_enabled": True, "auto_schedule": True, "auto_profile_update": True, "correction_notice": True},
    )
    db.add(user)
    db.flush()
    return user, api_key


def apply_configured_timezone(db: Session) -> int:
    """把 ``OA_TIMEZONE`` 同步到已有用户（仅当它被显式配置时）。

    个人部署通常只有一个用户；在 ``.env`` 里写上 ``OA_TIMEZONE=Asia/Shanghai`` 后重启
    即可纠正历史用户时区。未显式配置时不做任何修改，App 通过 ``PATCH /me`` 设置的
    时区也不会被覆盖。
    """
    settings = get_settings()
    if not settings.timezone_explicit:
        return 0
    users = db.scalars(select(User).where(User.timezone != settings.timezone)).all()
    for user in users:
        user.timezone = settings.timezone
    if users:
        db.commit()
    return len(users)


def ensure_bootstrap_user(db: Session) -> User | None:
    """Create the configured development user if the database is empty."""
    settings = get_settings()
    if not settings.bootstrap_api_key:
        return None
    user = authenticate(db, settings.bootstrap_api_key)
    if user is not None:
        return user
    user, _ = create_user(db, display_name=settings.bootstrap_user_name, api_key=settings.bootstrap_api_key)
    db.commit()
    return user
