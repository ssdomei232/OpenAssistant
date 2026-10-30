"""SQLite 本地队列：入队、领取、退避、崩溃恢复、清理。"""

from __future__ import annotations

import sqlite3

import pytest

from openassistant_agent.spool import SpoolItem, SpoolStore, resolve_db_path

from .helpers import FakeClock, sha256_bytes, write_wav_file


def _make_store(tmp_path, clock=None):
    clock = clock or FakeClock()
    store = SpoolStore(tmp_path / "spool", clock=clock, jitter=lambda: 1.0)
    return store, clock


def _enqueue(store, tmp_path, name: str = "seg-1", *, trigger: str = "auto") -> SpoolItem:
    path = write_wav_file(tmp_path / "segments" / f"{name}.wav", b"\x00\x00" * 1600)
    return store.enqueue(
        client_segment_id=name,
        recorded_at="2026-03-05T09:30:00+08:00",
        duration_s=1.5,
        path=path,
        sha256=sha256_bytes(path.read_bytes()),
        speech_ratio=0.8,
        vad={"speech_ratio": 0.8, "segments": [[0.0, 1.2]]},
        trigger=trigger,
    )


def test_db_path_resolution(tmp_path):
    assert resolve_db_path(tmp_path / "data").name == "spool.db"
    assert resolve_db_path(tmp_path / "x.db").name == "x.db"
    assert (tmp_path / "data").is_dir()


def test_enqueue_is_idempotent_and_persistent(tmp_path):
    store, _clock = _make_store(tmp_path)
    first = _enqueue(store, tmp_path, "seg-a")
    assert first.status == "pending"
    assert first.sha256
    assert store.get_by_client_id("seg-a").id == first.id

    duplicate = _enqueue(store, tmp_path, "seg-a")
    assert duplicate.id == first.id
    assert len(store.list()) == 1

    # 重新打开（模拟进程重启）后记录仍在
    reopened = SpoolStore(tmp_path / "spool")
    try:
        item = reopened.get_by_client_id("seg-a")
        assert item is not None
        assert item.vad["speech_ratio"] == 0.8
        assert item.trigger == "auto"
        assert isinstance(reopened.stats(), dict)
    finally:
        reopened.close()


def test_claim_pending_is_atomic_and_marks_uploading(tmp_path):
    store, _clock = _make_store(tmp_path)
    first = _enqueue(store, tmp_path, "seg-1")
    _enqueue(store, tmp_path, "seg-2")

    claimed = store.claim_pending(limit=1)
    assert len(claimed) == 1
    assert claimed[0].id == first.id
    assert claimed[0].status == "uploading"
    assert store.get(first.id).status == "uploading"

    # 已被领取的记录不会再次被领取
    assert len(store.claim_pending(limit=5)) == 1
    assert store.stats()["uploading"] == 2


def test_next_attempt_gating(tmp_path):
    store, clock = _make_store(tmp_path)
    item = _enqueue(store, tmp_path)
    store.mark_uploading(item.id)
    store.mark_failed(
        item.id,
        "boom",
        retry_max=5,
        retry_base_s=2.0,
        retry_max_s=300.0,
    )
    updated = store.get(item.id)
    assert updated.attempts == 1
    assert updated.next_attempt_at == pytest.approx(clock.value + 2.0)
    # 还没到重试时间
    assert store.claim_pending(limit=1) == []
    clock.advance(2.0)
    claimed = store.claim_pending(limit=1)
    assert len(claimed) == 1


def test_exponential_backoff_then_failed_file_retained(tmp_path):
    store, clock = _make_store(tmp_path)
    item = _enqueue(store, tmp_path, "seg-backoff")
    delays = []
    for _ in range(2):
        store.mark_uploading(item.id)
        store.mark_failed(item.id, "network down", retry_max=3, retry_base_s=2.0)
        current = store.get(item.id)
        delays.append(round(current.next_attempt_at - clock.value, 6))
        clock.advance(current.next_attempt_at - clock.value)

    assert delays == [2.0, 4.0]
    assert store.get(item.id).status == "pending"

    store.mark_uploading(item.id)
    store.mark_failed(item.id, "network down", retry_max=3, retry_base_s=2.0)
    current = store.get(item.id)
    assert current.status == "failed"
    assert current.attempts == 3
    assert current.last_error == "network down"
    assert current.next_attempt_at == 0
    assert current.exists, "failed segment file must be kept for manual retry"


def test_permanent_failure_and_manual_retry(tmp_path):
    store, _clock = _make_store(tmp_path)
    item = _enqueue(store, tmp_path)
    store.mark_uploading(item.id)
    store.mark_failed(item.id, "bad request", permanent=True)
    assert store.get(item.id).status == "failed"

    assert store.failed()[0].id == item.id
    assert store.retry(item.id) == 1
    retried = store.get(item.id)
    assert retried.status == "pending"
    assert retried.attempts == 0
    assert retried.last_error is None


def test_crash_recovery_resets_uploading(tmp_path):
    store, _clock = _make_store(tmp_path)
    item = _enqueue(store, tmp_path, "seg-crash")
    claimed = store.claim_pending(limit=1)
    assert claimed and claimed[0].id == item.id
    assert store.get(item.id).status == "uploading"

    # 模拟断电重启：新的 SpoolStore 实例执行对账
    restarted = SpoolStore(tmp_path / "spool")
    try:
        assert restarted.reset_uploading() == 1
        recovered = restarted.get(item.id)
        assert recovered.status == "pending"
        assert recovered.next_attempt_at == 0
        assert restarted.reset_uploading() == 0
    finally:
        restarted.close()


def test_mark_uploaded_stats_and_purge(tmp_path):
    store, _clock = _make_store(tmp_path)
    item = _enqueue(store, tmp_path, "seg-upload")
    store.mark_uploading(item.id)
    store.mark_uploaded(item.id)

    uploaded = store.get(item.id)
    assert uploaded.status == "uploaded"
    assert uploaded.uploaded_at is not None
    stats = store.stats()
    assert stats["uploaded"] == 1
    assert stats["pending"] == 0
    assert stats["total"] == 1
    assert stats["duration_s"] == pytest.approx(1.5)

    file_path = tmp_path / "segments" / "seg-upload.wav"
    assert file_path.exists()
    assert store.purge_uploaded() == 1
    assert store.get(item.id) is None
    assert not file_path.exists()


def test_list_filters_and_database_schema(tmp_path):
    store, _clock = _make_store(tmp_path)
    pending_item = _enqueue(store, tmp_path, "seg-pending")
    failed_item = _enqueue(store, tmp_path, "seg-failed")
    store.mark_uploading(failed_item.id)
    store.mark_failed(failed_item.id, "nope", retry_max=1)

    assert [item.id for item in store.list("pending")] == [pending_item.id]
    assert [item.id for item in store.failed()] == [failed_item.id]
    assert len(store.list()) == 2
    with pytest.raises(ValueError):
        store.list("bogus")

    with sqlite3.connect(store.db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(segments)")}
    for required in (
        "id",
        "client_segment_id",
        "recorded_at",
        "duration_s",
        "path",
        "sha256",
        "speech_ratio",
        "status",
        "attempts",
        "next_attempt_at",
        "last_error",
    ):
        assert required in columns


def test_delete_removes_row_and_file(tmp_path):
    store, _clock = _make_store(tmp_path)
    item = _enqueue(store, tmp_path, "seg-delete")
    assert store.delete(item.id) is True
    assert store.get(item.id) is None
    assert not (tmp_path / "segments" / "seg-delete.wav").exists()
    assert store.delete(item.id) is False
