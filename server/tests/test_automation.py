from __future__ import annotations

from tests.conftest import AUTH


def test_scheduled_job_lifecycle(client):
    created = client.post(
        "/api/v1/tasks/scheduled",
        headers=AUTH,
        json={
            "name": "晚间日程提醒",
            "cron": "0 21 * * *",
            "timezone": "Asia/Shanghai",
            "prompt": "汇总明天的日程和待办",
            "action": "agent",
        },
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["enabled"] is True
    assert job["cron"] == "0 21 * * *"

    listing = client.get("/api/v1/tasks/scheduled", headers=AUTH).json()["items"]
    assert len(listing) == 1

    patched = client.patch(f"/api/v1/tasks/scheduled/{job['id']}", headers=AUTH, json={"enabled": False})
    assert patched.json()["enabled"] is False

    run = client.post(f"/api/v1/tasks/scheduled/{job['id']}/run", headers=AUTH)
    assert run.status_code == 200
    assert run.json()["status"] == "done"
    assert run.json()["result"]

    refreshed = client.get("/api/v1/tasks/scheduled", headers=AUTH).json()["items"][0]
    assert refreshed["last_run_at"] is not None

    assert client.delete(f"/api/v1/tasks/scheduled/{job['id']}", headers=AUTH).status_code == 200
    assert client.get("/api/v1/tasks/scheduled", headers=AUTH).json()["items"] == []


def test_scheduled_job_validation(client):
    assert client.post("/api/v1/tasks/scheduled", headers=AUTH, json={"name": "x"}).status_code == 422
    bad_cron = client.post(
        "/api/v1/tasks/scheduled",
        headers=AUTH,
        json={"name": "x", "cron": "not a cron", "prompt": "y"},
    )
    assert bad_cron.status_code == 422
    assert "invalid_cron" in bad_cron.json()["detail"]

    one_off = client.post(
        "/api/v1/tasks/scheduled",
        headers=AUTH,
        json={"name": "一次性提醒", "run_at": "2099-01-01T09:00:00+08:00", "prompt": "提醒我", "action": "push"},
    )
    assert one_off.status_code == 201
    assert one_off.json()["run_at"] is not None


def test_push_test_with_recording_provider(client, providers):
    response = client.post("/api/v1/push/test", headers=AUTH, json={"text": "测试消息"})
    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is True
    assert body["provider"] == "recording"
    assert providers.push.messages[-1][1] == "测试消息"


def test_push_when_not_configured(client):
    from app.providers import set_providers
    from app.providers.push import NullPush

    from tests.conftest import build_test_providers

    instance = build_test_providers()
    instance.push = NullPush()
    set_providers(instance)
    response = client.post("/api/v1/push/test", headers=AUTH, json={"text": "x"})
    assert response.status_code == 200
    assert response.json()["sent"] is False
    assert response.json()["reason"] == "not_configured"


def test_device_config_roundtrip_and_masking(client):
    response = client.put(
        "/api/v1/devices/pi-livingroom/config",
        headers=AUTH,
        # docs/api.md 的扁平形式（App 实际发送的形态）
        json={
            "name": "客厅树莓派",
            "wifi": {"ssid": "home-5g", "password": "s3cret"},
            "retry_max": 5,
            "offline_buffer": True,
            "upload_endpoint": "http://192.168.1.10:8000/api/v1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    device = body["device"]
    assert device["config"]["wifi"]["ssid"] == "home-5g"
    assert device["config"]["wifi"]["password"] == "******"
    assert device["config"]["retry_max"] == 5

    # 嵌套形式也要兼容
    nested = client.put(
        "/api/v1/devices/pi-desk/config",
        headers=AUTH,
        json={"config": {"retry_max": 3, "offline_buffer": False}},
    )
    assert nested.status_code == 200
    assert nested.json()["device"]["config"]["retry_max"] == 3

    listing = client.get("/api/v1/devices", headers=AUTH).json()["items"]
    assert len(listing) == 2
    assert client.get("/api/v1/devices/pi-livingroom", headers=AUTH).status_code == 200

    renamed = client.patch("/api/v1/devices/pi-livingroom", headers=AUTH, json={"name": "新名字"})
    assert renamed.json()["name"] == "新名字"


def test_device_config_encrypts_wifi_password(client, db_session):
    client.put(
        "/api/v1/devices/pi-device/config",
        headers=AUTH,
        json={"config": {"wifi": {"ssid": "home", "password": "plain-password"}}},
    )
    from app.models import Device

    device = db_session.get(Device, "pi-device")
    assert device is not None
    stored = device.config["wifi"]
    assert "password" not in stored
    assert stored["password_enc"]
    assert "plain-password" not in stored["password_enc"]

    from app.api.automation import decrypt_device_password

    assert decrypt_device_password(device) == "plain-password"


def test_audit_log_lists_changes(client):
    client.put("/api/v1/me/profile", headers=AUTH, json={"text": "新画像"})
    audit = client.get("/api/v1/audit", headers=AUTH).json()["items"]
    assert any(item["action"] == "profile.update" for item in audit)
