"""Demo-seeded end-to-end tests: cards, corrections, deletion, meetings, profile."""
from __future__ import annotations

import pytest

from tests.conftest import AUTH


@pytest.fixture()
def seeded(client, providers, db_session):
    from app.demo import seed_demo

    report = seed_demo(db_session, api_key="test-api-key")
    db_session.commit()
    return report


def test_demo_seed_creates_full_dataset(seeded):
    assert len(seeded["cards"]) >= 4
    assert seeded["meetings"] >= 2
    assert seeded["schedule_items"] >= 5
    assert seeded["todos"] >= 3
    assert seeded["commands"] >= 1
    assert seeded["profile"]


def test_cards_list_and_detail(seeded, client):
    cards = client.get("/api/v1/cards?limit=50", headers=AUTH).json()["items"]
    assert len(cards) >= 4
    types = {card["type"] for card in cards}
    assert "meeting" in types
    assert "media" in types

    target = next(card for card in cards if card["type"] == "meeting")
    detail = client.get(f"/api/v1/cards/{target['id']}", headers=AUTH).json()
    assert detail["transcript"]
    labels = {turn["speaker_label"] for turn in detail["transcript"]}
    assert "[使用者]" in labels
    assert detail["audio_available"] is True
    assert detail["audio_url"].startswith("/api/v1/audio/")

    transcript = client.get(f"/api/v1/cards/{target['id']}/transcript.txt", headers=AUTH)
    assert transcript.status_code == 200
    assert "[使用者]" in transcript.text

    audio = client.get(detail["audio_url"], headers=AUTH)
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")


def test_card_search_by_keyword(seeded, client):
    response = client.get("/api/v1/cards?q=排期", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()["items"]) >= 1
    assert client.get("/api/v1/cards?q=不存在的关键词xyz", headers=AUTH).json()["items"] == []


def test_media_card_correction_is_traceable(seeded, client, db_session):
    cards = client.get("/api/v1/cards?type=media&include_deleted=true", headers=AUTH).json()["items"]
    assert cards, "demo media card missing"
    card = client.get(f"/api/v1/cards/{cards[0]['id']}", headers=AUTH).json()
    assert card["has_correction"] is True
    assert card["corrections"], "correction history missing"
    assert any(c["field"] == "type" for c in card["corrections"])

    audit = client.get("/api/v1/audit?limit=100", headers=AUTH).json()["items"]
    assert any(item["action"] == "card.correct" for item in audit)


def test_correction_rederives_schedule_and_refreshes_profile(seeded, client, db_session):
    cards = client.get("/api/v1/cards?limit=50", headers=AUTH).json()["items"]
    target = next(card for card in cards if card["type"] == "conversation" and card["id"] in seeded["cards"])
    before = client.get(f"/api/v1/cards/{target['id']}", headers=AUTH).json()
    before_schedule_ids = {item["id"] for item in before["schedule_items"]}

    patched = client.patch(
        f"/api/v1/cards/{target['id']}",
        headers=AUTH,
        json={"summary": "用户和同事讨论 Q3 排期，已确认会议时间。", "note": "补充一句说明"},
    )
    assert patched.status_code == 200, patched.text
    detail = patched.json()
    assert detail["summary"].startswith("用户和同事讨论")
    assert detail["corrections"]
    after_schedule_ids = {item["id"] for item in detail["schedule_items"]}
    # old derived events are replaced, never left behind
    assert after_schedule_ids.isdisjoint(before_schedule_ids)
    assert len(after_schedule_ids) == len(before_schedule_ids)


def test_delete_card_cascades(seeded, client):
    cards = client.get("/api/v1/cards?limit=50", headers=AUTH).json()["items"]
    with_schedule = None
    for card in cards:
        detail = client.get(f"/api/v1/cards/{card['id']}", headers=AUTH).json()
        if detail["schedule_items"]:
            with_schedule = detail
            break
    assert with_schedule is not None
    card_id = with_schedule["id"]
    event_ids = {item["id"] for item in with_schedule["schedule_items"]}
    todo_ids = {item["id"] for item in with_schedule["todos"] if item["source"] == "card"}

    response = client.delete(f"/api/v1/cards/{card_id}?note=误识别", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["cascaded"]["schedule"] >= 1
    assert isinstance(body["profile_dirty"], bool)

    detail = client.get(f"/api/v1/cards/{card_id}", headers=AUTH).json()
    assert detail["deleted_at"] is not None
    all_schedule = client.get("/api/v1/schedule?include_deleted=true", headers=AUTH).json()["items"]
    for item in all_schedule:
        if item["id"] in event_ids:
            assert item["id"] not in {
                row["id"] for row in client.get("/api/v1/schedule", headers=AUTH).json()["items"]
            }
    remaining_todos = {
        item["id"] for item in client.get("/api/v1/todos?status=all", headers=AUTH).json()["items"]
    }
    assert todo_ids.isdisjoint(remaining_todos)
    audit = client.get("/api/v1/audit?limit=100", headers=AUTH).json()["items"]
    assert any(item["action"] == "card.delete" and item["entity_id"] == card_id for item in audit)

    restored = client.post(f"/api/v1/cards/{card_id}/restore", headers=AUTH)
    assert restored.status_code == 200
    assert client.get(f"/api/v1/cards/{card_id}", headers=AUTH).json()["deleted_at"] is None


def test_meetings_and_minutes(seeded, client):
    meetings = client.get("/api/v1/meetings", headers=AUTH).json()["items"]
    assert len(meetings) >= 2
    meeting = client.get(f"/api/v1/meetings/{meetings[0]['id']}", headers=AUTH).json()
    assert meeting["minutes"]
    assert meeting["topics"]
    assert meeting["decisions"]
    assert meeting["participants"]
    assert "[使用者]" in {p["label"] for p in meeting["participants"]}

    regenerated = client.post(f"/api/v1/meetings/{meeting['id']}/regenerate", headers=AUTH)
    assert regenerated.status_code == 200
    assert regenerated.json()["minutes"]

    patched = client.patch(
        f"/api/v1/meetings/{meeting['id']}", headers=AUTH, json={"title": "人工修订的纪要标题"}
    )
    assert patched.json()["title"] == "人工修订的纪要标题"


def test_profile_generated_and_short(seeded, client):
    profile = client.get("/api/v1/me/profile", headers=AUTH).json()
    assert profile["text"]
    assert len(profile["text"]) <= 600
    assert profile["source_card_count"] >= 4
