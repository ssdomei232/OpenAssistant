from __future__ import annotations

from tests.conftest import AUTH


def test_schedule_crud(client):
    create = client.post(
        "/api/v1/schedule",
        headers=AUTH,
        json={
            "title": "和产品经理开会",
            "start_at": "2026-03-06T15:00:00+08:00",
            "end_at": "2026-03-06T16:00:00+08:00",
            "location": "3 号会议室",
            "remind_minutes_before": 15,
        },
    )
    assert create.status_code == 201, create.text
    item = create.json()
    assert item["source"] == "manual"
    assert item["start_at"] == "2026-03-06T07:00:00Z"
    assert item["remind_at"] == "2026-03-06T06:45:00Z"

    listing = client.get(
        "/api/v1/schedule?from=2026-03-06T00:00:00%2B08:00&to=2026-03-07T00:00:00%2B08:00",
        headers=AUTH,
    )
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1

    patched = client.patch(
        f"/api/v1/schedule/{item['id']}",
        headers=AUTH,
        json={"title": "改到下午四点", "start_at": "2026-03-06T16:00:00+08:00"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "改到下午四点"
    assert patched.json()["start_at"] == "2026-03-06T08:00:00Z"

    deleted = client.delete(f"/api/v1/schedule/{item['id']}", headers=AUTH)
    assert deleted.status_code == 200
    assert client.get("/api/v1/schedule", headers=AUTH).json()["items"] == []
    assert client.get("/api/v1/schedule?include_deleted=true", headers=AUTH).json()["items"][0]["id"] == item["id"]


def test_schedule_requires_valid_time(client):
    response = client.post("/api/v1/schedule", headers=AUTH, json={"title": "x", "start_at": "not-a-date"})
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_start_at"


def test_todo_crud_and_completion(client):
    created = client.post(
        "/api/v1/todos",
        headers=AUTH,
        json={"title": "给张伟发排期表", "due_at": "2026-03-06T18:00:00+08:00", "priority": "high"},
    )
    assert created.status_code == 201
    todo = created.json()
    assert todo["status"] == "pending"
    assert todo["priority"] == "high"

    done = client.patch(f"/api/v1/todos/{todo['id']}", headers=AUTH, json={"status": "done"})
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["completed_at"] is not None

    pending = client.get("/api/v1/todos?status=pending", headers=AUTH).json()["items"]
    assert pending == []
    all_items = client.get("/api/v1/todos?status=all", headers=AUTH).json()["items"]
    assert len(all_items) == 1

    assert client.delete(f"/api/v1/todos/{todo['id']}", headers=AUTH).status_code == 200
    assert client.get("/api/v1/todos?status=all", headers=AUTH).json()["items"] == []


def test_todo_validation(client):
    assert client.post("/api/v1/todos", headers=AUTH, json={"title": ""}).status_code == 422
    assert client.patch("/api/v1/todos/missing", headers=AUTH, json={"status": "done"}).status_code == 404
