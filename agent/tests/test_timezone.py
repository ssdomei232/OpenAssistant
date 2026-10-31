"""Agent 时区：配置、进程 TZ、recorded_at / 片段 ID 使用配置时区。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from openassistant_agent.capture import SyntheticSource
from openassistant_agent.config import AgentConfig, ConfigError, normalize_timezone
from openassistant_agent.segmenter import SpeechSegment
from openassistant_agent.service import AgentService
from openassistant_agent.spool import SpoolStore
from openassistant_agent.uploader import MockTransport

SPEECH_PATTERN = [("speech", 1.0), ("silence", 0.5)]


def _config(tmp_path) -> AgentConfig:
    config = AgentConfig(device_id="pi-tz", spool_path=str(tmp_path / "spool"))
    config.record.source = "synthetic"
    config.vad.min_speech_ms = 200
    config.vad.min_silence_ms = 300
    config.vad.preroll_s = 0.1
    config.vad.postroll_s = 0.1
    config.validate()
    return config


def test_default_timezone_and_validation():
    assert AgentConfig().timezone == "Asia/Shanghai"
    assert normalize_timezone(" UTC ") == "UTC"
    with pytest.raises(ConfigError):
        normalize_timezone("Mars/Olympus")

    config = AgentConfig()
    config.timezone = "Mars/Olympus"
    with pytest.raises(ConfigError):
        config.validate()


def test_timezone_from_env_and_toml_roundtrip(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text('timezone = "America/New_York"\n', encoding="utf-8")
    assert AgentConfig.load(path, env={}).timezone == "America/New_York"
    # 环境变量优先级高于 TOML
    assert AgentConfig.load(path, env={"OA_TIMEZONE": "UTC"}).timezone == "UTC"

    config = AgentConfig()
    config.timezone = "Asia/Tokyo"
    config.save(path)
    assert 'timezone = "Asia/Tokyo"' in path.read_text(encoding="utf-8")
    assert AgentConfig.load(path, env={}).timezone == "Asia/Tokyo"


def test_apply_timezone_sets_process_tz(monkeypatch):
    original = os.environ.get("TZ")
    try:
        config = AgentConfig()
        config.timezone = "UTC"
        config.apply_timezone()
        assert os.environ["TZ"] == "UTC"
        assert config.now_local().tzinfo == ZoneInfo("UTC")
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original


def test_next_identity_uses_configured_timezone_not_process_tz(tmp_path):
    """systemd 以 UTC 运行时，recorded_at / 片段 ID 仍应是配置的本地时区。"""
    config = _config(tmp_path)
    config.timezone = "Asia/Shanghai"
    service = AgentService(config)
    service.spool = SpoolStore(tmp_path / "spool")
    try:
        service._recording_started_at = datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc)
        segment = SpeechSegment(start=1.5, end=2.5, pcm=b"\x00\x00" * 100)
        recorded_at, client_id = service._next_identity(segment)
        assert recorded_at == "2026-03-05T18:00:01+08:00"
        assert client_id.startswith("seg-20260305T180001-")
    finally:
        service.spool.close()


def test_upload_meta_recorded_at_carries_local_offset(tmp_path):
    config = _config(tmp_path)
    config.timezone = "Asia/Shanghai"
    transport = MockTransport(healthy=True)
    service = AgentService(config, transport=transport, upload_interval_s=0.0)
    service.setup()
    try:
        summary = service.run(
            source=SyntheticSource(
                sample_rate=config.record.sample_rate,
                block_ms=config.record.block_ms,
                pattern=SPEECH_PATTERN,
            ),
            upload=True,
            install_signals=False,
        )
        assert summary["segments"] >= 1
        assert transport.calls
        for call in transport.calls:
            recorded_at = call["meta"]["recorded_at"]
            assert recorded_at.endswith("+08:00"), recorded_at
    finally:
        service.close()
