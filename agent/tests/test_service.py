"""主服务一条龙：合成音频 -> 切片 -> wav -> 队列 -> mock 上传。"""

from __future__ import annotations

import hashlib
import io
import wave

import pytest

from openassistant_agent.capture import SyntheticSource
from openassistant_agent.config import AgentConfig
from openassistant_agent.segmenter import SpeechSegment
from openassistant_agent.service import AgentService
from openassistant_agent.spool import SpoolStore
from openassistant_agent.uploader import MockTransport

from .helpers import sha256_bytes

SPEECH_PATTERN = [("speech", 1.0), ("silence", 0.6), ("speech", 0.8), ("silence", 0.4)]


def _config(tmp_path) -> AgentConfig:
    config = AgentConfig(device_id="pi-test", spool_path=str(tmp_path / "spool"))
    config.record.source = "synthetic"
    config.record.block_ms = 30
    config.vad.min_speech_ms = 200
    config.vad.min_silence_ms = 300
    config.vad.preroll_s = 0.1
    config.vad.postroll_s = 0.1
    config.vad.max_segment_s = 30.0
    config.upload.batch_size = 4
    config.upload.retry_base_s = 0.01
    config.validate()
    return config


def _source(config: AgentConfig, repeat: int = 1) -> SyntheticSource:
    return SyntheticSource(
        sample_rate=config.record.sample_rate,
        block_ms=config.record.block_ms,
        pattern=SPEECH_PATTERN,
        repeat=repeat,
    )


def test_run_produces_spool_entries_and_uploads(tmp_path):
    config = _config(tmp_path)
    transport = MockTransport(healthy=True)
    service = AgentService(config, transport=transport, upload_interval_s=0.0)
    service.setup()

    summary = service.run(source=_source(config), upload=True, install_signals=False)
    try:
        assert summary["blocks"] > 0
        assert summary["segments"] >= 2
        stats = service.spool.stats()
        assert stats["total"] == summary["segments"]
        assert stats["uploaded"] == summary["segments"]
        assert stats["pending"] == 0
        assert len(transport.calls) == summary["segments"]

        # 每个上传的 meta 满足 API 契约
        for call in transport.calls:
            meta = call["meta"]
            assert meta["device_id"] == "pi-test"
            assert meta["sample_rate"] == 16000
            assert meta["channels"] == 1
            assert meta["trigger"] == "auto"
            assert meta["duration_s"] > 0
            assert meta["vad"]["speech_ratio"] > 0
            assert meta["vad"]["segments"]
            assert call["sha256"] == meta["sha256"]

        # wav 文件真实存在且与队列校验和一致
        files = sorted((tmp_path / "spool" / "segments").glob("*.wav"))
        assert len(files) == summary["segments"]
        for path in files:
            with wave.open(str(path), "rb") as handle:
                assert handle.getframerate() == 16000
                assert handle.getnchannels() == 1
                assert handle.getsampwidth() == 2
                assert handle.getnframes() > 0
            item = next(
                entry for entry in service.spool.list() if entry.path == str(path)
            )
            assert item.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

        status = service.status()
        assert status["device_id"] == "pi-test"
        assert status["recording"] is False
        assert status["segments_total"] == summary["segments"]
        assert status["queue"]["uploaded"] == summary["segments"]
    finally:
        service.close()


def test_offline_run_buffers_and_recovers(tmp_path):
    config = _config(tmp_path)
    transport = MockTransport(healthy=False)
    service = AgentService(config, transport=transport, upload_interval_s=0.0)
    service.setup()
    try:
        summary = service.run(source=_source(config), upload=True, install_signals=False)
        assert summary["segments"] >= 2
        assert transport.calls == []
        stats = service.spool.stats()
        assert stats["pending"] == summary["segments"]
        assert service.status()["wifi_connected"] is False
        assert service.uploader is not None
        assert service.uploader.online is False

        # 网络恢复后补传
        transport.healthy = True
        flush = service.flush_queue()
        assert flush["uploaded"] == summary["segments"]
        assert service.spool.stats()["pending"] == 0
    finally:
        service.close()


def test_setup_recovers_interrupted_uploads(tmp_path):
    config = _config(tmp_path)
    spool = SpoolStore(tmp_path / "spool")
    path = tmp_path / "spool" / "segments" / "seg-crash.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF")
    item = spool.enqueue(
        client_segment_id="seg-crash",
        recorded_at="2026-03-05T09:30:00+08:00",
        duration_s=1.0,
        path=path,
        sha256=sha256_bytes(path.read_bytes()),
    )
    spool.mark_uploading(item.id)
    assert spool.get(item.id).status == "uploading"

    service = AgentService(config)
    service.setup()
    try:
        recovered = service.spool.get(item.id)
        assert recovered.status == "pending"
    finally:
        service.close()


def test_ingest_segment_writes_wav_and_queues(tmp_path):
    config = _config(tmp_path)
    service = AgentService(config, transport=MockTransport())
    service.setup()
    try:
        pcm = b"\x10\x00" * 16000
        segment = SpeechSegment(
            start=0.0,
            end=1.0,
            pcm=pcm,
            speech_ratio=0.9,
            speech_spans=[(0.0, 1.0)],
        )
        item = service.ingest_segment(segment)
        assert item.client_segment_id.startswith("seg-")
        assert item.status == "pending"
        assert item.duration_s == pytest.approx(1.0)
        assert item.vad["segments"] == [[0.0, 1.0]]
        target = tmp_path / "spool" / "segments" / f"{item.client_segment_id}.wav"
        assert target.exists()
        with wave.open(str(target), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
        assert frames == pcm
        assert item.sha256 == sha256_bytes(target.read_bytes())
    finally:
        service.close()


def test_process_block_buffers_partial_frames(tmp_path):
    config = _config(tmp_path)
    service = AgentService(config, transport=MockTransport())
    service.setup()
    try:
        frame_bytes = service.segmenter.frame_bytes
        assert service.process_block(b"\x00\x00" * 10) == []
        assert len(service._pending_pcm) == 20
        assert service.process_block(b"\x00" * (frame_bytes // 2)) == []
        assert len(service._pending_pcm) == 20 + frame_bytes // 2
    finally:
        service.close()


def test_run_with_max_blocks_and_no_upload(tmp_path):
    config = _config(tmp_path)
    transport = MockTransport()
    service = AgentService(config, transport=transport, upload_interval_s=10.0)
    service.setup()
    try:
        summary = service.run(
            source=_source(config), max_blocks=3, upload=False, install_signals=False
        )
        assert summary["blocks"] == 3
        assert transport.calls == []
        assert "upload" not in summary
    finally:
        service.close()


def test_short_input_yields_no_segment(tmp_path):
    config = _config(tmp_path)
    transport = MockTransport()
    service = AgentService(config, transport=transport, upload_interval_s=0.0)
    service.setup()
    try:
        summary = service.run(
            source=SyntheticSource(pattern=[("silence", 0.3)]),
            upload=True,
            install_signals=False,
        )
        assert summary["segments"] == 0
        assert service.spool.stats()["total"] == 0
        assert transport.calls == []
    finally:
        service.close()
