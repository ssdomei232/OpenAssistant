"""配置合并顺序、JSON/TOML 序列化与校验。"""

from __future__ import annotations

import json

import pytest

from openassistant_agent.config import (
    AgentConfig,
    ConfigError,
    RecordConfig,
    UploadConfig,
    VadConfig,
    default_config_path,
    load_config,
)


def test_defaults():
    config = AgentConfig()
    assert config.record.sample_rate == 16000
    assert config.record.channels == 1
    assert config.record.block_ms == 30
    assert config.vad.max_segment_s == 60.0
    assert config.upload.retry_max == 5
    assert config.upload.endpoint == "/api/v1/audio/segments"
    assert config.spool_path
    config.validate()
    assert isinstance(config.record, RecordConfig)
    assert isinstance(config.vad, VadConfig)
    assert isinstance(config.upload, UploadConfig)


def test_merge_order_defaults_toml_env_overrides(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text(
        "\n".join(
            [
                'server_url = "http://toml.example"',
                'device_id = "toml-device"',
                "spool_path = " + json.dumps(str(tmp_path / "toml-spool")),
                "[vad]",
                "min_speech_ms = 250",
                "[upload]",
                "retry_max = 3",
                'offline_policy = "drop"',
            ]
        ),
        encoding="utf-8",
    )
    env = {
        "OA_SERVER_URL": "http://env.example",
        "OA_RETRY_MAX": "9",
        "OA_VAD_PREROLL_S": "0.5",
        "OA_UPLOAD_OFFLINE_BUFFER": "false",
        "OA_RECORD_SOURCE": "file",
        "OA_RECORD_INPUT": "/tmp/demo.wav",
    }
    config = AgentConfig.load(path, env=env, overrides={"device_id": "explicit-device"})
    assert config.server_url == "http://env.example"  # env 覆盖 toml
    assert config.device_id == "explicit-device"  # 显式参数最高
    assert config.upload.retry_max == 9  # OA_RETRY_MAX 短名
    assert config.upload.offline_policy == "drop"  # 保留 toml 未覆盖项
    assert config.vad.min_speech_ms == 250
    assert config.vad.preroll_s == 0.5
    assert config.upload.offline_buffer is False
    assert config.record.source == "file"
    assert config.record.input_path == "/tmp/demo.wav"
    assert config.config_path == str(path)


def test_json_roundtrip():
    config = AgentConfig()
    config.device_id = "pi-json"
    config.upload.retry_max = 11
    payload = config.to_json()
    decoded = json.loads(payload)
    assert decoded["device_id"] == "pi-json"
    restored = AgentConfig.from_json(payload)
    assert restored.to_dict() == config.to_dict()

    with pytest.raises(ConfigError):
        AgentConfig.from_json("{not json")
    with pytest.raises(ConfigError):
        AgentConfig.from_json("[1, 2]")

    merged = AgentConfig.from_json({"upload": {"retry_max": 2}, "wifi": {"ssid": "x"}})
    assert merged.upload.retry_max == 2


def test_toml_save_and_reload(tmp_path):
    config = AgentConfig()
    config.device_id = "pi-toml"
    config.server_url = "https://example.test/api/v1"
    config.api_key = "secret-key"
    config.vad.min_silence_ms = 700
    target = config.save(tmp_path / "nested" / "agent.toml")
    assert target.exists()
    assert config.config_path == str(target)

    reloaded = AgentConfig.load(target, env={})
    assert reloaded.device_id == "pi-toml"
    assert reloaded.server_url == "https://example.test/api/v1"
    assert reloaded.api_key == "secret-key"
    assert reloaded.vad.min_silence_ms == 700
    assert reloaded.record.sample_rate == 16000


def test_unknown_keys_are_ignored():
    config = AgentConfig()
    changed = config.merge_dict(
        {"wifi": {"ssid": "home"}, "unknown": 1, "upload": {"retry_max": 4}}
    )
    assert config.upload.retry_max == 4
    assert "upload" in changed


def test_validation_errors():
    config = AgentConfig()
    config.record.sample_rate = 12345
    with pytest.raises(ConfigError):
        config.validate()

    config = AgentConfig()
    config.upload.offline_policy = "explode"
    with pytest.raises(ConfigError):
        config.validate()

    config = AgentConfig()
    config.record.source = "file"
    config.record.input_path = ""
    with pytest.raises(ConfigError):
        config.validate()

    config = AgentConfig()
    config.vad.frame_ms = 25
    with pytest.raises(ConfigError):
        config.validate()


def test_load_missing_file_uses_defaults(tmp_path):
    target = tmp_path / "does-not-exist.toml"
    config = load_config(target, env={})
    assert config.server_url == AgentConfig().server_url
    assert config.config_path == str(target)
    assert not target.exists()


def test_default_config_path_is_absolute():
    assert default_config_path().is_absolute()
