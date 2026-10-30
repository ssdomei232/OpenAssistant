"""Core API: health, account, settings, profile, voiceprint."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from .. import __version__
from ..config import get_settings
from ..deps import CurrentUser, DbSession, ProvidersDep, voiceprint_for
from ..models import AuditLog, Voiceprint
from ..schemas import MePatch, ProfilePut, SettingsPatch
from ..serializers import user_me, voiceprint_out
from ..utils import decode_audio, now_utc, rms
from ..services.profile import refresh_profile

router = APIRouter(tags=["core"])
settings = get_settings()

ALLOWED_ALIASES = 20


@router.get("/health")
def health() -> dict:
    from ..providers import get_providers

    providers = get_providers()
    return {
        "status": "ok",
        "version": __version__,
        "mode": providers.modes,
        "providers": providers.describe(),
        "scheduler": "enabled" if settings.scheduler_enabled else "disabled",
        "time": now_utc().isoformat() + "Z",
    }


@router.get("/ready")
def ready(db: DbSession) -> dict:
    checks = {"database": False, "storage": False, "scheduler": settings.scheduler_enabled}
    try:
        db.execute(select(1))
        checks["database"] = True
    except Exception:
        pass
    try:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.storage_dir / ".ready"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["storage"] = True
    except Exception:
        pass
    status = "ready" if checks["database"] and checks["storage"] else "degraded"
    return {"status": status, "checks": checks}


@router.get("/me")
def get_me(user: CurrentUser, db: DbSession) -> dict:
    return user_me(user, voiceprint_for(db, user))


@router.patch("/me")
def patch_me(payload: MePatch, user: CurrentUser, db: DbSession) -> dict:
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.aliases is not None:
        aliases = []
        for alias in payload.aliases:
            alias = str(alias).strip()
            if alias and alias not in aliases:
                aliases.append(alias[:64])
        if len(aliases) > ALLOWED_ALIASES:
            raise HTTPException(status_code=422, detail="too_many_aliases")
        user.aliases = aliases
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.search_provider is not None:
        user.search_provider = payload.search_provider
        merged = dict(user.settings or {})
        merged["search_provider"] = payload.search_provider
        user.settings = merged
    user.updated_at = now_utc()
    db.commit()
    db.refresh(user)
    return user_me(user, voiceprint_for(db, user))


@router.get("/me/settings")
def get_settings_route(user: CurrentUser) -> dict:
    return user.settings_with_defaults()


@router.patch("/me/settings")
def patch_settings(payload: SettingsPatch, user: CurrentUser, db: DbSession) -> dict:
    merged = dict(user.settings or {})
    updates = payload.model_dump(exclude_none=True)
    if "search_provider" in updates:
        user.search_provider = updates["search_provider"]
    merged.update(updates)
    user.settings = merged
    db.commit()
    return user.settings_with_defaults()


@router.get("/me/profile")
def get_profile(user: CurrentUser, db: DbSession) -> dict:
    from ..models import Card
    from sqlalchemy import func

    count = db.scalar(select(func.count(Card.id)).where(Card.user_id == user.id, Card.deleted_at.is_(None))) or 0
    return {
        "text": user.profile_text or "",
        "version": user.profile_version or 0,
        "updated_at": (user.profile_updated_at.isoformat() + "Z") if user.profile_updated_at else None,
        "source_card_count": count,
        "dirty": bool(user.profile_dirty),
    }


@router.put("/me/profile")
def put_profile(payload: ProfilePut, user: CurrentUser, db: DbSession) -> dict:
    if len(payload.text) > settings.max_profile_chars:
        raise HTTPException(status_code=422, detail="profile_too_long")
    user.profile_text = payload.text
    user.profile_version = (user.profile_version or 0) + 1
    user.profile_source = "user"
    user.profile_updated_at = now_utc()
    user.profile_dirty = False
    db.add(AuditLog(user_id=user.id, action="profile.update", entity="user", entity_id=user.id, after={"text": payload.text}))
    db.commit()
    return get_profile(user, db)


@router.post("/me/profile/refresh")
def refresh_profile_route(user: CurrentUser, db: DbSession, providers: ProvidersDep) -> dict:
    refresh_profile(db, user, providers, reason="manual")
    db.commit()
    return get_profile(user, db)


@router.get("/me/voiceprint")
def get_voiceprint(user: CurrentUser, db: DbSession) -> dict:
    return voiceprint_out(voiceprint_for(db, user))


@router.post("/me/voiceprint")
async def enroll_voiceprint(
    user: CurrentUser,
    db: DbSession,
    providers: ProvidersDep,
    file: UploadFile = File(...),
    threshold: float = Form(default=60.0),
) -> dict:
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")
    try:
        samples, sr = decode_audio(data, target_sr=16000)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    duration = samples.size / sr if sr else 0.0
    if duration < 3.0:
        raise HTTPException(status_code=422, detail="too_short")
    if duration > 120.0:
        raise HTTPException(status_code=422, detail="too_long")
    level = rms(samples)
    if level < 0.002:
        raise HTTPException(status_code=422, detail="no_speech")
    threshold = max(0.0, min(100.0, float(threshold)))
    embedding = providers.speaker.embed(samples, sr)
    quality = max(0.0, min(1.0, level * 40.0))
    record = voiceprint_for(db, user)
    if record is None:
        record = Voiceprint(user_id=user.id, embedding=embedding)
        db.add(record)
    record.embedding = embedding
    record.threshold = threshold
    record.quality = quality
    record.sample_seconds = duration
    record.provider = getattr(providers.speaker, "name", "spectral")
    record.updated_at = now_utc()
    db.add(AuditLog(user_id=user.id, action="voiceprint.enroll", entity="voiceprint", entity_id=record.id or "", after={"quality": quality, "duration_s": duration}))
    db.commit()
    return {
        "enrolled": True,
        "threshold": threshold,
        "quality": round(quality, 3),
        "duration_s": round(duration, 2),
        "updated_at": record.updated_at.isoformat() + "Z",
    }


@router.delete("/me/voiceprint")
def delete_voiceprint(user: CurrentUser, db: DbSession) -> dict:
    record = voiceprint_for(db, user)
    if record is not None:
        db.delete(record)
        db.add(AuditLog(user_id=user.id, action="voiceprint.delete", entity="voiceprint", entity_id=record.id, before={"enrolled": True}))
        db.commit()
    return {"deleted": True}


@router.post("/me/voiceprint/verify")
async def verify_voiceprint(
    user: CurrentUser,
    db: DbSession,
    providers: ProvidersDep,
    file: UploadFile = File(...),
) -> dict:
    record = voiceprint_for(db, user)
    if record is None:
        raise HTTPException(status_code=409, detail="voiceprint_not_enrolled")
    data = await file.read()
    try:
        samples, sr = decode_audio(data, target_sr=16000)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    embedding = providers.speaker.embed(samples, sr)
    import numpy as np

    similarity = float(np.dot(np.asarray(embedding), np.asarray(record.embedding))) * 100.0
    return {
        "matched": similarity >= record.threshold,
        "similarity": round(similarity, 2),
        "threshold": record.threshold,
    }
