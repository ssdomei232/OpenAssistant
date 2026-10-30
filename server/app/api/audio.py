"""Audio ingestion API used by the Agent (and by the App for playback)."""
from __future__ import annotations

import base64
import json
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from ..config import get_settings
from ..deps import CurrentUser, DbSession
from ..models import AudioSegment
from ..serializers import segment_out
from ..services.pipeline import (
    pending_segment_ids,
    process_segment,
    reprocess_segment,
    requeue_failed_segments,
    requeue_stuck_segments,
)
from ..services.storage import get_storage
from ..utils import decode_audio, sha256_bytes, utc_from

router = APIRouter(tags=["audio"])
settings = get_settings()


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except Exception:
        raise HTTPException(status_code=422, detail="invalid_cursor")


@router.post("/audio/segments", status_code=202)
async def upload_segment(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    meta: str = Form(default="{}"),
    process_now: bool = Query(default=False),
) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="empty_file")
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")
    try:
        meta_data = json.loads(meta or "{}")
        if not isinstance(meta_data, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid_meta") from exc

    client_segment_id = str(meta_data.get("client_segment_id") or "").strip()
    if not client_segment_id:
        raise HTTPException(status_code=422, detail="missing_client_segment_id")

    existing = db.scalar(
        select(AudioSegment).where(
            AudioSegment.user_id == user.id, AudioSegment.client_segment_id == client_segment_id
        )
    )
    if existing is not None:
        return {**segment_out(existing), "duplicate": True}

    try:
        samples, sr = decode_audio(raw, target_sr=16000)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    duration = samples.size / sr if sr else 0.0
    if duration <= 0.05:
        raise HTTPException(status_code=422, detail="audio_too_short")

    digest = sha256_bytes(raw)
    if meta_data.get("sha256") and str(meta_data["sha256"]).lower() != digest:
        raise HTTPException(status_code=422, detail="checksum_mismatch")

    storage = get_storage()
    rel_path = storage.save(raw, suffix=".wav", prefix="audio")
    recorded_at = meta_data.get("recorded_at")
    if recorded_at:
        try:
            utc_from(datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00")))
        except ValueError:
            meta_data["recorded_at"] = None

    segment = AudioSegment(
        user_id=user.id,
        client_segment_id=client_segment_id,
        device_id=str(meta_data.get("device_id") or "") or None,
        path=rel_path,
        sha256=digest,
        duration_s=round(duration, 3),
        # 记录上传端原始采样率（内部 ASR/声纹统一重采样到 16 kHz）
        sample_rate=int(meta_data.get("sample_rate") or sr),
        channels=int(meta_data.get("channels") or 1),
        status="queued",
        meta=meta_data,
    )
    db.add(segment)
    db.commit()
    db.refresh(segment)

    if process_now:
        process_segment(segment.id)
        db.refresh(segment)
    return {**segment_out(segment), "duplicate": False}


@router.get("/audio/segments")
def list_segments(
    user: CurrentUser,
    db: DbSession,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> dict:
    offset = _decode_cursor(cursor)
    stmt = select(AudioSegment).where(AudioSegment.user_id == user.id)
    if status:
        stmt = stmt.where(AudioSegment.status == status)
    stmt = stmt.order_by(AudioSegment.received_at.desc()).offset(offset).limit(limit + 1)
    rows = list(db.scalars(stmt).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [segment_out(row) for row in rows],
        "next_cursor": _encode_cursor(offset + limit) if has_more else None,
    }


@router.get("/audio/segments/{segment_id}")
def get_segment(segment_id: str, user: CurrentUser, db: DbSession) -> dict:
    segment = db.get(AudioSegment, segment_id)
    if segment is None or segment.user_id != user.id:
        raise HTTPException(status_code=404, detail="segment_not_found")
    return segment_out(segment)


@router.get("/audio/{segment_id}/file")
def download_audio(segment_id: str, user: CurrentUser, db: DbSession):
    segment = db.get(AudioSegment, segment_id)
    if segment is None or segment.user_id != user.id:
        raise HTTPException(status_code=404, detail="segment_not_found")
    storage = get_storage()
    if not storage.exists(segment.path):
        raise HTTPException(status_code=404, detail="audio_file_missing")
    return FileResponse(
        storage.path(segment.path),
        media_type="audio/wav",
        filename=f"{segment.client_segment_id or segment.id}.wav",
    )


@router.post("/audio/segments/{segment_id}/reprocess")
def reprocess(segment_id: str, user: CurrentUser, db: DbSession, process_now: bool = Query(default=True)) -> dict:
    segment = db.get(AudioSegment, segment_id)
    if segment is None or segment.user_id != user.id:
        raise HTTPException(status_code=404, detail="segment_not_found")
    reprocess_segment(segment_id)
    if process_now:
        process_segment(segment_id)
    db.expire_all()
    segment = db.get(AudioSegment, segment_id)
    return segment_out(segment)


@router.post("/audio/segments/requeue-failed")
def requeue_failed(user: CurrentUser) -> dict:
    """把该用户的 failed 片段重新入队（模型/依赖修复后恢复历史失败数据）。"""
    count = requeue_failed_segments(user.id)
    return {"requeued": count, "status": "queued"}


@router.post("/audio/segments/requeue-stuck")
def requeue_stuck() -> dict:
    """把卡在 processing 的片段重新入队（Server 重启/崩溃后遗留）。"""
    count = requeue_stuck_segments()
    return {"requeued": count, "status": "queued"}


@router.get("/audio/segments/pending/count")
def pending_count(user: CurrentUser, db: DbSession) -> dict:
    from sqlalchemy import func

    queued = db.scalar(
        select(func.count(AudioSegment.id)).where(
            AudioSegment.user_id == user.id, AudioSegment.status == "queued"
        )
    ) or 0
    return {"queued": int(queued), "pending_ids": pending_segment_ids(limit=5)}
