"""处理队列恢复：worker 状态、卡住/失败片段重新入队、worker tick 处理。"""
from __future__ import annotations

from sqlalchemy import select

from app.models import AudioSegment, Card, User
from app.services.jobs import BackgroundWorker
from app.services.pipeline import requeue_failed_segments, requeue_stuck_segments
from tests.conftest import AUTH, concat, enroll_voiceprint, tone_wave, upload_segment


def _create_segment(db_session, client_segment_id: str, status: str) -> AudioSegment:
    user = db_session.scalars(select(User).limit(1)).one()
    segment = AudioSegment(
        user_id=user.id,
        client_segment_id=client_segment_id,
        path="audio/aa/bb/does-not-matter.wav",
        duration_s=1.0,
        sample_rate=48000,
        status=status,
        attempts=2 if status == "failed" else 1,
        error="old error" if status == "failed" else None,
    )
    db_session.add(segment)
    db_session.commit()
    db_session.refresh(segment)
    return segment


def test_health_reports_worker_and_audio_counts(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert "worker" in body
    assert body["worker"]["enabled"] is False  # 测试环境关闭 worker
    assert body["audio"]["queued"] == 0


def test_requeue_failed_endpoint(client, db_session):
    segment = _create_segment(db_session, "seg-recover-failed", "failed")
    response = client.post("/api/v1/audio/segments/requeue-failed", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["requeued"] == 1
    refreshed = db_session.get(AudioSegment, segment.id)
    db_session.refresh(refreshed)
    assert refreshed.status == "queued"
    assert refreshed.attempts == 0
    assert refreshed.error is None


def test_requeue_stuck_segments_service(db_session):
    _create_segment(db_session, "seg-recover-stuck", "processing")
    count = requeue_stuck_segments()
    assert count >= 1
    row = db_session.scalars(
        select(AudioSegment).where(AudioSegment.client_segment_id == "seg-recover-stuck")
    ).one()
    db_session.refresh(row)
    assert row.status == "queued"


def test_runtime_self_heal_starts_worker(client, monkeypatch):
    """模拟 lifespan 未执行（--lifespan off 等）时，首个请求能自愈启动 worker。"""
    import app.main as main_mod

    monkeypatch.setattr(main_mod.settings, "worker_enabled", True)
    client.app.state.worker = None
    try:
        main_mod._ensure_runtime(client.app)
        worker = client.app.state.worker
        assert worker is not None
        assert worker.alive is True
        assert worker.status()["enabled"] is True
    finally:
        if client.app.state.worker is not None:
            client.app.state.worker.stop()
            client.app.state.worker = None


def test_worker_tick_processes_queued_segment(client, providers, db_session):
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    response = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-worker-tick",
        process_now=False,
    )
    assert response.status_code == 202
    segment_id = response.json()["id"]

    worker = BackgroundWorker(providers=providers)
    summary = worker.tick()
    assert summary["processed"] >= 1

    db_session.expire_all()
    segment = db_session.get(AudioSegment, segment_id)
    assert segment.status == "done"
    assert segment.card_id
    card = db_session.get(Card, segment.card_id)
    assert card is not None and card.summary
