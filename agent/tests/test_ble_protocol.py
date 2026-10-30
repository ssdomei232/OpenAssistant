"""BLE 协议：分片拼帧、JSON 校验、状态/队列路由；mock 传输端到端。"""

from __future__ import annotations

import json

import pytest

from openassistant_agent import ble_protocol as proto
from openassistant_agent.ble import BleManager, BlessBleServer, MockBleTransport
from openassistant_agent.ble_protocol import (
    BleProtocolError,
    ConfigAssembler,
    decode_json,
    encode_json,
    iter_chunks,
    parse_queue_command,
    route_queue_command,
    status_payload,
)
from openassistant_agent.config import AgentConfig
from openassistant_agent.spool import SpoolStore
from openassistant_agent.uploader import MockTransport, Uploader

from .helpers import sha256_bytes, write_wav_file


# ---------------------------------------------------------------------------
# ConfigAssembler
# ---------------------------------------------------------------------------


def test_config_assembler_reassembles_chunked_json():
    payload = {"device_id": "pi-livingroom", "upload": {"retry_max": 7}}
    raw = encode_json(payload)
    assembler = ConfigAssembler()
    chunks = [raw[i : i + 5] for i in range(0, len(raw), 5)]
    results = [assembler.feed(chunk) for chunk in chunks]
    assert all(result is None for result in results[:-1])
    assert results[-1] == payload
    assert assembler.pending_bytes == 0


def test_config_assembler_handles_split_multibyte_utf8():
    payload = {"device_id": "客厅树莓派", "wifi": {"ssid": "家里"}}
    raw = encode_json(payload)
    assembler = ConfigAssembler()
    results = []
    for index in range(0, len(raw), 3):  # 3 字节分片必然切到中文
        results.append(assembler.feed(raw[index : index + 3]))
    assert results[-1] == payload


def test_config_assembler_waits_for_complete_json():
    assembler = ConfigAssembler()
    assert assembler.feed(b'{"device_id": "pi"') is None
    assert assembler.feed(b", ") is None
    assert assembler.feed(b'"retry_max": 3}') == {"device_id": "pi", "retry_max": 3}


def test_config_assembler_rejects_oversize():
    assembler = ConfigAssembler(max_bytes=20)
    with pytest.raises(BleProtocolError) as excinfo:
        assembler.feed(b'{"device_id": "a-very-long-device-name"}')
    assert excinfo.value.code == "config_too_large"
    assert assembler.pending_bytes == 0


def test_decode_json_errors():
    with pytest.raises(BleProtocolError) as excinfo:
        decode_json(b"\xff\xfe")
    assert excinfo.value.code == "invalid_utf8"

    with pytest.raises(BleProtocolError) as excinfo:
        decode_json("[1, 2]")
    assert excinfo.value.code == "not_an_object"

    with pytest.raises(BleProtocolError) as excinfo:
        decode_json("")
    assert excinfo.value.code == "empty_payload"

    with pytest.raises(BleProtocolError) as excinfo:
        decode_json("not json")
    assert excinfo.value.code == "invalid_json"


def test_iter_chunks_respects_ble_limit():
    payload = encode_json({"device_id": "x" * 1200})
    chunks = list(iter_chunks(payload))
    assert all(len(chunk) <= proto.MAX_ATTRIBUTE_BYTES for chunk in chunks)
    assert b"".join(chunks) == payload
    with pytest.raises(ValueError):
        list(iter_chunks(payload, 0))


def test_status_payload_contract():
    payload = status_payload(
        device_id="pi-1",
        wifi_connected=True,
        queue={"pending": 2, "uploaded": 5},
        recording=True,
        uptime_s=12.34,
        retry_max=5,
        offline_buffer=True,
        extra={"firmware": "1.0.0"},
    )
    for key in (
        "device_id",
        "wifi_connected",
        "queue",
        "recording",
        "uptime_s",
        "retry_max",
        "offline_buffer",
    ):
        assert key in payload
    assert payload["uptime_s"] == pytest.approx(12.3)
    assert payload["firmware"] == "1.0.0"
    assert json.loads(encode_json(payload).decode()) == payload


# ---------------------------------------------------------------------------
# QUEUE 命令
# ---------------------------------------------------------------------------


def test_parse_queue_command_valid_and_invalid():
    assert parse_queue_command({"action": "list"}) == {"action": "list"}
    command = parse_queue_command(b'{"action": "RETRY", "id": 3, "limit": 5}')
    assert command == {"action": "retry", "id": 3, "limit": 5}

    with pytest.raises(BleProtocolError) as excinfo:
        parse_queue_command({"action": "drop"})
    assert excinfo.value.code == "invalid_action"

    with pytest.raises(BleProtocolError) as excinfo:
        parse_queue_command({"action": "retry", "id": "abc"})
    assert excinfo.value.code == "invalid_id"

    with pytest.raises(BleProtocolError):
        parse_queue_command(b"\x00\x01")


def test_route_queue_command():
    calls = []

    def handler(command):
        calls.append(command)
        return {"items": []}

    result = route_queue_command({"action": "list"}, {"list": handler})
    assert result["ok"] is True
    assert result["result"] == {"items": []}
    assert calls == [{"action": "list"}]

    assert route_queue_command({"action": "nope"}, {"list": handler})["error"] == "invalid_action"
    assert route_queue_command({"action": "clear"}, {})["error"] == "unsupported_action"

    def boom(_command):
        raise RuntimeError("db locked")

    failed = route_queue_command({"action": "retry"}, {"retry": boom})
    assert failed["ok"] is False
    assert failed["error"] == "action_failed"
    assert "db locked" in failed["message"]


# ---------------------------------------------------------------------------
# BleManager + MockBleTransport
# ---------------------------------------------------------------------------


def _make_ble(tmp_path):
    config = AgentConfig(
        server_url="http://127.0.0.1:8000",
        api_key="test-key",
        device_id="pi-ble",
        spool_path=str(tmp_path / "spool"),
    )
    config.validate()
    spool = SpoolStore(tmp_path / "spool")
    transport = MockTransport()
    uploader = Uploader(spool, transport, config)
    saved = []
    manager = BleManager(
        config=config,
        spool=spool,
        uploader=uploader,
        status_provider=lambda: {"recording": True, "uptime_s": 42.0},
        config_saver=lambda cfg: saved.append(cfg.to_dict()) or str(tmp_path / "agent.toml"),
    )
    return config, spool, transport, manager, MockBleTransport(manager), saved


def _enqueue_failed(spool, tmp_path, name="seg-fail"):
    path = write_wav_file(tmp_path / "segments" / f"{name}.wav", b"\x00\x00" * 400)
    item = spool.enqueue(
        client_segment_id=name,
        recorded_at="2026-03-05T09:30:00+08:00",
        duration_s=1.0,
        path=path,
        sha256=sha256_bytes(path.read_bytes()),
    )
    spool.mark_uploading(item.id)
    spool.mark_failed(item.id, "boom", retry_max=1)
    return item


def test_ble_config_write_updates_persists_and_backs_up(tmp_path):
    config, spool, transport, manager, ble, saved = _make_ble(tmp_path)

    result = ble.write_config(
        {
            "device_id": "pi-new",
            "upload": {"retry_max": 9},
            "wifi": {"ssid": "home", "password": "secret"},
        }
    )
    assert result is not None and result["ok"] is True
    assert result["saved"] is True
    assert manager.config.device_id == "pi-new"
    assert manager.config.upload.retry_max == 9
    assert saved and saved[0]["device_id"] == "pi-new"

    assert len(transport.config_pushes) == 1
    pushed = transport.config_pushes[0]
    assert pushed["path"] == "/api/v1/devices/pi-new/config"
    assert pushed["payload"]["retry_max"] == 9
    assert pushed["payload"]["wifi"] == {"ssid": "home", "password": "secret"}

    # CONFIG 读回新值
    assert ble.read_config()["device_id"] == "pi-new"


def test_ble_config_write_chunk_by_chunk(tmp_path):
    config, spool, transport, manager, ble, saved = _make_ble(tmp_path)
    raw = encode_json({"upload": {"retry_max": 4}})
    result = None
    for index in range(0, len(raw), 4):
        result = ble.write_config_chunk(raw[index : index + 4])
    assert result is not None and result["ok"] is True
    assert manager.config.upload.retry_max == 4


def test_ble_config_rejects_invalid_values(tmp_path):
    config, spool, transport, manager, ble, saved = _make_ble(tmp_path)
    result = ble.write_config({"record": {"sample_rate": 12345}})
    assert result is not None
    assert result["ok"] is False
    assert result["error"] == "invalid_config"
    assert manager.config.record.sample_rate == 16000
    assert saved == []


def test_ble_status_read_and_notify(tmp_path):
    config, spool, transport, manager, ble, _saved = _make_ble(tmp_path)
    _enqueue_failed(spool, tmp_path)

    status = ble.read_status()
    assert status["device_id"] == "pi-ble"
    assert status["recording"] is True
    assert status["uptime_s"] == pytest.approx(42.0)
    assert status["queue"]["failed"] == 1
    assert status["retry_max"] == config.upload.retry_max
    assert status["offline_buffer"] is True

    notified = ble.notify_status()
    assert notified["queue"]["failed"] == 1
    assert len(ble.notifications) == 1


def test_ble_queue_list_retry_clear(tmp_path):
    config, spool, transport, manager, ble, _saved = _make_ble(tmp_path)
    failed_item = _enqueue_failed(spool, tmp_path)

    listed = ble.write_queue({"action": "list"})
    assert listed["ok"] is True
    assert listed["result"]["items"][0]["client_segment_id"] == "seg-fail"

    retried = ble.write_queue({"action": "retry"})
    assert retried["ok"] is True
    assert retried["result"]["retried"] == 1
    assert spool.get(failed_item.id).status == "pending"

    # 准备一条已上传记录，clear 默认只清理 uploaded
    spool.mark_uploading(failed_item.id)
    spool.mark_uploaded(failed_item.id)
    pending_item = _enqueue_failed(spool, tmp_path, "seg-other")
    spool.mark_uploading(pending_item.id)
    spool.mark_failed(pending_item.id, "boom", retry_max=1)
    cleared = ble.write_queue({"action": "clear"})
    assert cleared["ok"] is True
    assert cleared["result"]["removed"] == 1
    assert spool.get(failed_item.id) is None
    assert spool.get(pending_item.id) is not None

    invalid = ble.write_queue({"action": "explode"})
    assert invalid["ok"] is False
    assert invalid["error"] == "invalid_action"


def test_ble_manager_build_status_without_uploader(tmp_path):
    config = AgentConfig(device_id="pi-solo", spool_path=str(tmp_path / "spool"))
    spool = SpoolStore(tmp_path / "spool")
    manager = BleManager(config=config, spool=spool)
    status = manager.build_status()
    assert status["device_id"] == "pi-solo"
    assert status["wifi_connected"] is False
    assert status["queue"]["total"] == 0


def test_bless_server_availability_reports_bool():
    assert isinstance(BlessBleServer.available(), bool)
