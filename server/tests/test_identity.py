"""The hard requirement: only ``[使用者]`` turns may trigger schedules/commands."""
from __future__ import annotations

from tests.conftest import (
    AUTH,
    build_test_providers,
    concat,
    enroll_voiceprint,
    tone_wave,
    upload_segment,
)


def _setup_enrolled(client, recording_push):
    response = enroll_voiceprint(client, tone_wave(3.0, "user"))
    assert response.status_code == 200, response.text

    providers = build_test_providers(
        user_text="帮我记一下明天下午三点和周磊开会。",
        other_text="明天下午四点我们一起去看电影吧。",
        push=recording_push,
    )
    from app.providers import set_providers

    set_providers(providers)
    return providers



def test_identity_labels_and_user_only_schedule(client, db_session):
    from app.providers import set_providers
    from app.providers.push import RecordingPush

    recording = RecordingPush()
    set_providers(
        build_test_providers(
            user_text="帮我记一下明天下午三点和周磊开会。",
            other_text="明天下午四点我们一起去看电影吧。",
            push=recording,
        )
    )
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    upload = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-identity-1",
        recorded_at="2026-03-05T10:00:00+08:00",
    )
    assert upload.status_code == 202, upload.text
    segment = upload.json()
    detail = client.get(f"/api/v1/audio/segments/{segment['id']}", headers=AUTH).json()
    assert detail["status"] == "done"
    assert detail["card_id"]

    card = client.get(f"/api/v1/cards/{detail['card_id']}", headers=AUTH).json()
    speakers = {turn["speaker"] for turn in card["transcript"]}
    assert speakers == {"user", "other"}
    user_turns = [t for t in card["transcript"] if t["speaker"] == "user"]
    other_turns = [t for t in card["transcript"] if t["speaker"] == "other"]
    assert any("开会" in t["text"] for t in user_turns)
    assert any("电影" in t["text"] for t in other_turns)
    assert card["labels"]["user_turns"] == len(user_turns)
    assert card["labels"]["other_turns"] == len(other_turns)

    # only the user's "明天下午三点开会" becomes an event; the other speaker's
    # "明天下午四点看电影" must never be scheduled
    schedules = card["schedule_items"]
    assert len(schedules) == 1, schedules
    assert "电影" not in schedules[0]["title"]
    assert "开会" in schedules[0]["title"]


def test_other_speaker_cannot_trigger_commands(client):
    from app.providers import set_providers
    from app.providers.push import RecordingPush

    recording = RecordingPush()
    set_providers(
        build_test_providers(
            user_text="今天天气不错。",
            other_text="帮我查一下上海明天的天气怎么样。",
            push=recording,
        )
    )
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    upload = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-other-command",
    )
    segment = upload.json()
    detail = client.get(f"/api/v1/audio/segments/{segment['id']}", headers=AUTH).json()
    assert detail["card_id"]
    commands = client.get("/api/v1/commands", headers=AUTH).json()["items"]
    assert commands == []
    assert recording.messages == []

    card = client.get(f"/api/v1/cards/{detail['card_id']}", headers=AUTH).json()
    assert card["schedule_items"] == []


def test_user_speaker_triggers_command_and_push(client):
    from app.providers import set_providers
    from app.providers.push import RecordingPush

    recording = RecordingPush()
    set_providers(
        build_test_providers(
            user_text="帮我查一下上海明天的天气怎么样。",
            other_text="好的，我知道了。",
            push=recording,
        )
    )
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    upload = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-user-command",
    )
    segment = upload.json()
    detail = client.get(f"/api/v1/audio/segments/{segment['id']}", headers=AUTH).json()
    assert detail["status"] == "done"
    commands = client.get("/api/v1/commands", headers=AUTH).json()["items"]
    assert len(commands) == 1
    command = commands[0]
    assert command["intent"] == "web_search"
    assert command["status"] == "done"
    assert "示例" in command["result"] or "查询" in command["result"]
    assert command["pushed"] is True
    assert len(recording.messages) == 1
    assert "查询" in recording.messages[0][1] or "示例" in recording.messages[0][1]


def test_unknown_speaker_does_not_trigger(client):
    """Without an enrolled voiceprint every speaker is [未知] → no actions."""
    from app.providers import set_providers
    from app.providers.push import RecordingPush

    recording = RecordingPush()
    set_providers(
        build_test_providers(
            user_text="帮我记一下明天下午三点和周磊开会。",
            other_text="提醒我明天八点买牛奶。",
            push=recording,
        )
    )
    upload = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-unknown",
    )
    segment = upload.json()
    detail = client.get(f"/api/v1/audio/segments/{segment['id']}", headers=AUTH).json()
    card = client.get(f"/api/v1/cards/{detail['card_id']}", headers=AUTH).json()
    assert {t["speaker"] for t in card["transcript"]} == {"unknown"}
    assert card["transcript"][0]["speaker_label"] == "[未知]"
    assert card["schedule_items"] == []
    assert card["todos"] == []
    assert client.get("/api/v1/commands", headers=AUTH).json()["items"] == []
