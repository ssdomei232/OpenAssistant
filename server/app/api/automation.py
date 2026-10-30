"""Automation APIs: scheduled tasks, push test, devices, audit log."""
from __future__ import annotations

import base64
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..config import get_settings
from ..deps import CurrentUser, DbSession, ProvidersDep
from ..models import AuditLog, Device, ScheduledJob
from ..schemas import PushTestIn, RenameDeviceIn, ScheduledJobIn, ScheduledJobPatch
from ..serializers import audit_out, device_out, job_out, parse_dt
from ..services.push_service import queue_push
from ..services.scheduler import get_scheduler
from ..utils import decrypt_secret, encrypt_secret, now_utc

router = APIRouter(tags=["automation"])
settings = get_settings()


def _validate_cron(expr: str | None) -> None:
    if not expr:
        return
    try:
        CronTrigger.from_crontab(expr)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid_cron: {exc}") from exc


@router.get("/tasks/scheduled")
def list_jobs(user: CurrentUser, db: DbSession) -> dict:
    jobs = db.scalars(
        select(ScheduledJob).where(ScheduledJob.user_id == user.id).order_by(ScheduledJob.created_at.desc())
    ).all()
    scheduler = get_scheduler()
    return {
        "items": [
            job_out(job, scheduler.next_run_at(job.id) if job.enabled and settings.scheduler_enabled else None)
            for job in jobs
        ]
    }


@router.post("/tasks/scheduled", status_code=201)
def create_job(payload: ScheduledJobIn, user: CurrentUser, db: DbSession) -> dict:
    if not payload.cron and not payload.run_at:
        raise HTTPException(status_code=422, detail="cron_or_run_at_required")
    _validate_cron(payload.cron)
    run_at = parse_dt(payload.run_at)
    job = ScheduledJob(
        user_id=user.id,
        name=payload.name,
        cron=payload.cron,
        run_at=run_at,
        timezone=payload.timezone,
        prompt=payload.prompt,
        action=payload.action,
        enabled=payload.enabled,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    if job.enabled:
        get_scheduler().schedule_job(job)
    return job_out(job, get_scheduler().next_run_at(job.id))


@router.patch("/tasks/scheduled/{job_id}")
def patch_job(job_id: str, payload: ScheduledJobPatch, user: CurrentUser, db: DbSession) -> dict:
    job = db.get(ScheduledJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="job_not_found")
    updates = payload.model_dump(exclude_none=True)
    if "cron" in updates:
        _validate_cron(updates["cron"])
    if "run_at" in updates:
        updates["run_at"] = parse_dt(updates["run_at"])
    for key, value in updates.items():
        setattr(job, key, value)
    db.commit()
    db.refresh(job)
    scheduler = get_scheduler()
    scheduler.schedule_job(job) if job.enabled else scheduler.unschedule_job(job.id)
    return job_out(job, scheduler.next_run_at(job.id))


@router.delete("/tasks/scheduled/{job_id}")
def delete_job(job_id: str, user: CurrentUser, db: DbSession) -> dict:
    job = db.get(ScheduledJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="job_not_found")
    get_scheduler().unschedule_job(job.id)
    db.delete(job)
    db.commit()
    return {"deleted": True}


@router.post("/tasks/scheduled/{job_id}/run")
def run_job(job_id: str, user: CurrentUser, db: DbSession) -> dict:
    job = db.get(ScheduledJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="job_not_found")
    result = get_scheduler().run_now(job.id)
    db.expire_all()
    return {**result, "job_id": job_id}


@router.post("/push/test")
def push_test(payload: PushTestIn, user: CurrentUser, db: DbSession, providers: ProvidersDep) -> dict:
    outbox = queue_push(
        db,
        user,
        "OpenAssistant · 测试消息",
        payload.text,
        providers,
        force=True,
    )
    db.commit()
    return {
        "sent": outbox.status == "sent",
        "provider": providers.push.name,
        "status": outbox.status,
        "reason": None if outbox.status == "sent" else outbox.last_error,
    }


@router.get("/devices")
def list_devices(user: CurrentUser, db: DbSession) -> dict:
    rows = db.scalars(select(Device).where(Device.user_id == user.id)).all()
    return {"items": [device_out(row) for row in rows]}


def _upsert_device(db, user, device_id: str, name: str | None, config: dict) -> Device:
    device = db.get(Device, device_id)
    if device is not None and device.user_id != user.id:
        raise HTTPException(status_code=409, detail="device_belongs_to_other_user")
    if device is None:
        device = Device(id=device_id, user_id=user.id, name=name or device_id, config={})
        db.add(device)
    stored = dict(device.config or {})
    incoming = dict(config or {})
    wifi = dict(incoming.pop("wifi", None) or {})
    if wifi:
        password = wifi.pop("password", None)
        stored_wifi = dict(stored.get("wifi") or {})
        stored_wifi.update({k: v for k, v in wifi.items() if v is not None})
        if password:
            stored_wifi["password_enc"] = encrypt_secret(str(password), settings.api_key_pepper)
        stored["wifi"] = stored_wifi
    for key, value in incoming.items():
        stored[key] = value
    device.config = stored
    if name:
        device.name = name
    device.last_seen = now_utc()
    return device


@router.get("/devices/{device_id}")
def get_device(device_id: str, user: CurrentUser, db: DbSession) -> dict:
    device = db.get(Device, device_id)
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=404, detail="device_not_found")
    return device_out(device)


@router.put("/devices/{device_id}/config")
def put_device_config(
    device_id: str,
    payload: dict,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    """Accept both the documented flat config and a ``{"config": {...}}`` wrapper.

    Flat (docs/api.md):
        {"wifi": {...}, "retry_max": 5, "offline_buffer": true, "name": "客厅树莓派"}
    Wrapped (used by some clients):
        {"name": "...", "config": {...}}
    """
    body = dict(payload or {})
    name = body.pop("name", None)
    nested = body.pop("config", None)
    config: dict = {}
    if isinstance(nested, dict):
        config.update(nested)
    # remaining top-level keys are treated as config entries (documented flat form)
    config.update(body)
    device = _upsert_device(db, user, device_id, name, config)
    db.commit()
    db.refresh(device)
    return {"saved": True, "updated_at": device.last_seen.isoformat() + "Z", "device": device_out(device)}


@router.patch("/devices/{device_id}")
def rename_device(device_id: str, payload: RenameDeviceIn, user: CurrentUser, db: DbSession) -> dict:
    device = db.get(Device, device_id)
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=404, detail="device_not_found")
    device.name = payload.name
    db.commit()
    return device_out(device)


@router.get("/audit")
def list_audit(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> dict:
    before: datetime | None = None
    if cursor:
        try:
            before = parse_dt(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:
            raise HTTPException(status_code=422, detail="invalid_cursor")
    stmt = select(AuditLog).where(AuditLog.user_id == user.id)
    if before:
        stmt = stmt.where(AuditLog.created_at < before)
    rows = list(db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [audit_out(row) for row in rows],
        "next_cursor": base64.urlsafe_b64encode(rows[-1].created_at.isoformat().encode()).decode()
        if has_more and rows
        else None,
    }


# keep the helper importable for tests that need to verify secret storage
def decrypt_device_password(device: Device) -> str:
    wifi = (device.config or {}).get("wifi") or {}
    return decrypt_secret(wifi.get("password_enc", ""), settings.api_key_pepper)
