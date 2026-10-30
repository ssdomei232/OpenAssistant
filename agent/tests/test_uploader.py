"""上传器：成功/失败/幂等/离线暂存（全部 mock 传输，不触网）。"""

from __future__ import annotations

import pytest

from openassistant_agent.config import AgentConfig
from openassistant_agent.spool import SpoolStore
from openassistant_agent.uploader import (
    MockTransport,
    UploadResult,
    Uploader,
    build_meta,
    build_multipart,
    join_url,
)

from .helpers import FakeClock, sha256_bytes, write_wav_file


def _make_env(tmp_path, **upload_overrides):
    clock = FakeClock()
    spool = SpoolStore(tmp_path / "spool", clock=clock, jitter=lambda: 1.0)
    config = AgentConfig(spool_path=str(tmp_path / "spool"))
    config.upload.retry_base_s = 2.0
    config.upload.retry_max_s = 300.0
    config.upload.retry_max = 5
    config.device_id = "pi-test"
    for key, value in upload_overrides.items():
        setattr(config.upload, key, value)
    transport = MockTransport()
    uploader = Uploader(spool, transport, config, clock=clock, sleep=lambda _s: None)
    return config, spool, transport, uploader, clock


def _enqueue(spool, tmp_path, name="seg-1", *, trigger="auto"):
    pcm = b"\x01\x00" * 800
    path = write_wav_file(tmp_path / "segments" / f"{name}.wav", pcm)
    return spool.enqueue(
        client_segment_id=name,
        recorded_at="2026-03-05T09:30:00+08:00",
        duration_s=1.5,
        path=path,
        sha256=sha256_bytes(path.read_bytes()),
        speech_ratio=0.75,
        vad={"speech_ratio": 0.75, "segments": [[0.1, 1.2]]},
        trigger=trigger,
    )


def test_upload_success_meta_contract(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(tmp_path)
    item = _enqueue(spool, tmp_path)

    summary = uploader.upload_pending()
    assert summary["uploaded"] == 1
    assert summary["failed"] == 0
    assert summary["offline"] is False
    assert spool.get(item.id).status == "uploaded"

    assert len(transport.calls) == 1
    call = transport.calls[0]
    meta = call["meta"]
    assert meta["client_segment_id"] == "seg-1"
    assert meta["device_id"] == "pi-test"
    assert meta["recorded_at"] == "2026-03-05T09:30:00+08:00"
    assert meta["duration_s"] == pytest.approx(1.5)
    assert meta["sample_rate"] == 16000
    assert meta["channels"] == 1
    assert meta["sha256"] == item.sha256
    assert meta["vad"] == {"speech_ratio": 0.75, "segments": [[0.1, 1.2]]}
    assert meta["trigger"] == "auto"
    assert call["sha256"] == item.sha256
    assert call["path"].endswith("seg-1.wav")

    # 已上传的不会再次上传（幂等）
    assert uploader.upload_pending()["attempted"] == 0
    assert len(transport.calls) == 1


def test_duplicate_response_is_success(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(tmp_path)
    item = _enqueue(spool, tmp_path)
    transport.responses.append(UploadResult(ok=True, status_code=200, duplicate=True))

    summary = uploader.upload_pending()
    assert summary["uploaded"] == 1
    assert summary["duplicates"] == 1
    assert spool.get(item.id).status == "uploaded"


def test_retry_after_network_error(tmp_path):
    config, spool, transport, uploader, clock = _make_env(tmp_path)
    item = _enqueue(spool, tmp_path)
    transport.responses.append(ConnectionError("network unreachable"))

    summary = uploader.upload_pending()
    assert summary["attempted"] == 1
    assert summary["failed"] == 1
    pending = spool.get(item.id)
    assert pending.status == "pending"
    assert pending.attempts == 1
    assert pending.next_attempt_at == pytest.approx(clock.value + 2.0)
    assert pending.exists

    # 未到退避时间不重试
    assert uploader.upload_pending()["attempted"] == 0
    clock.advance(2.0)
    summary = uploader.upload_pending()
    assert summary["uploaded"] == 1
    assert spool.get(item.id).status == "uploaded"


def test_retry_exhausted_marks_failed_and_keeps_file(tmp_path):
    config, spool, transport, uploader, clock = _make_env(tmp_path, retry_max=2)
    item = _enqueue(spool, tmp_path)
    transport.responses.extend([ConnectionError("still down"), ConnectionError("still down")])

    uploader.upload_pending()
    clock.advance(2.0)
    uploader.upload_pending()

    failed = spool.get(item.id)
    assert failed.status == "failed"
    assert failed.attempts == 2
    assert failed.exists
    assert "still down" in (failed.last_error or "")


def test_permanent_http_error_marks_failed_immediately(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(tmp_path)
    item = _enqueue(spool, tmp_path)
    transport.responses.append(400)

    summary = uploader.upload_pending()
    assert summary["failed"] == 1
    failed = spool.get(item.id)
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.exists


def test_offline_buffers_segments_and_recovers(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(tmp_path)
    item = _enqueue(spool, tmp_path)
    transport.healthy = False

    summary = uploader.upload_pending()
    assert summary["offline"] is True
    assert summary["skipped_offline"] == 1
    assert transport.calls == []
    assert spool.get(item.id).status == "pending"

    transport.healthy = True
    summary = uploader.upload_pending()
    assert summary["uploaded"] == 1
    assert spool.get(item.id).status == "uploaded"


def test_offline_without_buffer_can_drop(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(
        tmp_path, offline_buffer=False, offline_policy="drop"
    )
    item = _enqueue(spool, tmp_path)
    transport.healthy = False

    summary = uploader.upload_pending()
    assert summary["offline"] is True
    assert spool.get(item.id) is None
    assert not (tmp_path / "segments" / "seg-1.wav").exists()


def test_offline_without_buffer_keeps_by_default(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(
        tmp_path, offline_buffer=False, offline_policy="keep"
    )
    item = _enqueue(spool, tmp_path)
    transport.healthy = False

    uploader.upload_pending()
    assert spool.get(item.id).status == "pending"
    assert spool.get(item.id).exists


def test_sha256_mismatch_is_permanent_failure(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(tmp_path)
    item = _enqueue(spool, tmp_path)
    path = tmp_path / "segments" / "seg-1.wav"
    path.write_bytes(b"corrupted file")

    summary = uploader.upload_pending()
    assert summary["failed"] == 1
    assert transport.calls == []
    failed = spool.get(item.id)
    assert failed.status == "failed"
    assert "sha256_mismatch" in (failed.last_error or "")


def test_missing_file_is_permanent_failure(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(tmp_path)
    item = _enqueue(spool, tmp_path)
    (tmp_path / "segments" / "seg-1.wav").unlink()

    uploader.upload_pending()
    failed = spool.get(item.id)
    assert failed.status == "failed"
    assert failed.last_error == "file_missing"


def test_push_config_requires_credentials(tmp_path):
    config, spool, transport, uploader, _clock = _make_env(tmp_path)
    result = uploader.push_config({"retry_max": 3})
    assert result.ok is False
    assert transport.config_pushes == []

    config.server_url = "http://127.0.0.1:8000"
    config.api_key = "test-key"
    result = uploader.push_config({"retry_max": 3})
    assert result.ok is True
    assert transport.config_pushes[0]["path"] == "/api/v1/devices/pi-test/config"
    assert transport.config_pushes[0]["payload"] == {"retry_max": 3}


def test_build_meta_trigger_and_multipart():
    from openassistant_agent.spool import SpoolItem

    config = AgentConfig(device_id="pi-x")
    item = SpoolItem(
        id=1,
        client_segment_id="seg-1",
        recorded_at="2026-03-05T09:30:00+08:00",
        duration_s=2.0,
        path="/tmp/x.wav",
        sha256="abc",
        speech_ratio=0.5,
        trigger="manual",
        vad={"speech_ratio": 0.5, "segments": [[0, 1.0]]},
    )
    meta = build_meta(item, config)
    assert meta["trigger"] == "manual"
    assert meta["sample_rate"] == 16000
    assert meta["vad"]["segments"] == [[0.0, 1.0]]

    body, content_type = build_multipart(meta, b"RIFFdata", filename="seg-1.wav")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="file"' in body
    assert b'name="meta"' in body
    assert b"RIFFdata" in body
    assert b'"client_segment_id":"seg-1"' in body


def test_join_url_variants():
    assert join_url("http://h:8000", "/api/v1/audio/segments") == (
        "http://h:8000/api/v1/audio/segments"
    )
    assert join_url("http://h:8000/api/v1/", "/api/v1/audio/segments") == (
        "http://h:8000/api/v1/audio/segments"
    )
    assert join_url("http://h:8000/api/v1", "audio/segments") == (
        "http://h:8000/api/v1/audio/segments"
    )
