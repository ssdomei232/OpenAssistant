from __future__ import annotations

from tests.conftest import AUTH


def test_health_public(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "mode" in body
    assert body["providers"]["asr"]


def test_ready(client):
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_auth_required(client):
    for path in ("/api/v1/me", "/api/v1/cards", "/api/v1/todos", "/api/v1/schedule"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["detail"] == "invalid_api_key"


def test_auth_via_bearer_header(client):
    response = client.get("/api/v1/me", headers={"Authorization": "Bearer test-api-key"})
    assert response.status_code == 200
    assert response.json()["display_name"] == "测试用户"


def test_me_and_aliases(client):
    response = client.get("/api/v1/me", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["voiceprint"]["enrolled"] is False
    assert body["search_provider"] == "builtin"

    updated = client.patch(
        "/api/v1/me",
        headers=AUTH,
        json={"aliases": ["小明", "明明", "小明", " Xiao Ming "], "display_name": "小明"},
    )
    assert updated.status_code == 200
    aliases = updated.json()["aliases"]
    assert aliases == ["小明", "明明", "Xiao Ming"]
    assert updated.json()["display_name"] == "小明"


def test_alias_validation(client):
    too_many = [f"alias{i}" for i in range(25)]
    response = client.patch("/api/v1/me", headers=AUTH, json={"aliases": too_many})
    assert response.status_code == 422
    assert response.json()["detail"] == "too_many_aliases"


def test_settings_toggle_and_search_provider(client):
    response = client.get("/api/v1/me/settings", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["search_provider"] == "builtin"

    updated = client.patch(
        "/api/v1/me/settings",
        headers=AUTH,
        json={"search_provider": "deepseek", "auto_schedule": False},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["search_provider"] == "deepseek"
    assert body["auto_schedule"] is False

    me = client.get("/api/v1/me", headers=AUTH).json()
    assert me["search_provider"] == "deepseek"


def test_profile_update_and_limit(client):
    response = client.put("/api/v1/me/profile", headers=AUTH, json={"text": "30 岁软件工程师，关注 AI。"})
    assert response.status_code == 200
    assert response.json()["text"] == "30 岁软件工程师，关注 AI。"
    assert response.json()["version"] >= 1

    too_long = "字" * 601
    response = client.put("/api/v1/me/profile", headers=AUTH, json={"text": too_long})
    assert response.status_code == 422
    assert response.json()["detail"] == "profile_too_long"


def test_root_and_openapi(client):
    assert client.get("/").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/cards" in schema["paths"]
    assert "/api/v1/audio/segments" in schema["paths"]
