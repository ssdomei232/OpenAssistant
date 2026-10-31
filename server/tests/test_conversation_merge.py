"""对话合并：转写后等待一段时间，把时间相近的片段合并为一张卡片统一总结。"""
from __future__ import annotations

from datetime import timedelta

from app.config import get_settings
from app.services.pipeline import flush_transcribed_conversations, transcribe_segment
from app.utils import now_utc
from tests.conftest import AUTH, build_test_providers, concat, tone_wave, upload_segment

AUDIO = concat(tone_wave(2.0, "user"), tone_wave(2.0, "other"))


def _setup(client, *, user_text="这个方案我觉得可以。", other_text="好的，那我们继续推进。"):
    from app.providers import set_providers

    providers = build_test_providers(user_text=user_text, other_text=other_text)
    set_providers(providers)
    return providers


def _upload(client, client_segment_id: str, recorded_at: str, *, process_now: bool = False) -> dict:
    response = upload_segment(
        client,
        AUDIO,
        client_segment_id=client_segment_id,
        recorded_at=recorded_at,
        process_now=process_now,
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_nearby_segments_merge_into_one_card_after_window(client, monkeypatch):
    providers = _setup(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "conversation_merge_window_s", 10.0)
    monkeypatch.setattr(settings, "conversation_merge_gap_s", 45.0)

    first = _upload(client, "merge-1", "2026-03-05T10:00:00+08:00")
    second = _upload(client, "merge-2", "2026-03-05T10:00:20+08:00")

    assert transcribe_segment(first["id"], providers)["status"] == "transcribed"
    assert transcribe_segment(second["id"], providers)["status"] == "transcribed"

    # 等待窗口还没到：先不总结，片段停在 transcribed
    assert flush_transcribed_conversations(providers) == []
    pending = client.get(f"/api/v1/audio/segments/{first['id']}", headers=AUTH).json()
    assert pending["status"] == "transcribed"
    assert pending["card_id"] is None

    # 模拟等待结束：两段合并为一次对话、只生成一张卡片
    results = flush_transcribed_conversations(providers, now=now_utc() + timedelta(seconds=11))
    assert len(results) == 1, results
    assert results[0]["status"] == "done"
    assert results[0]["merged_segments"] == 2

    cards = client.get("/api/v1/cards", headers=AUTH).json()["items"]
    assert len(cards) == 1
    card = client.get(f"/api/v1/cards/{cards[0]['id']}", headers=AUTH).json()
    assert card["occurred_at"] == "2026-03-05T02:00:00Z"  # 10:00+08 = 02:00Z，取最早片段
    # 未注册声纹时每段合成一个 turn；合并后按时间线排开（第二段偏移约 20s）
    assert len(card["transcript"]) == 2
    starts = [turn["start"] for turn in card["transcript"]]
    assert starts[0] < 5
    assert 19 < starts[1] < 25
    assert len(card["source_meta"]["audio_segment_ids"]) == 2
    assert card["source_meta"]["merged"] is True
    for segment_id in (first["id"], second["id"]):
        segment = client.get(f"/api/v1/audio/segments/{segment_id}", headers=AUTH).json()
        assert segment["status"] == "done"
        assert segment["card_id"] == cards[0]["id"]


def test_far_apart_segments_keep_separate_cards(client, monkeypatch):
    providers = _setup(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "conversation_merge_window_s", 0.0)
    monkeypatch.setattr(settings, "conversation_merge_gap_s", 45.0)

    first = _upload(client, "merge-far-1", "2026-03-05T10:00:00+08:00")
    second = _upload(client, "merge-far-2", "2026-03-05T11:00:00+08:00")
    assert transcribe_segment(first["id"], providers)["status"] == "transcribed"
    assert transcribe_segment(second["id"], providers)["status"] == "transcribed"

    results = flush_transcribed_conversations(providers)
    assert len(results) == 2
    assert all(item["merged_segments"] == 1 for item in results)
    cards = client.get("/api/v1/cards", headers=AUTH).json()["items"]
    assert len(cards) == 2


def test_merge_can_be_disabled(client, monkeypatch):
    providers = _setup(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "conversation_merge_enabled", False)
    monkeypatch.setattr(settings, "conversation_merge_window_s", 60.0)

    first = _upload(client, "merge-off-1", "2026-03-05T10:00:00+08:00")
    second = _upload(client, "merge-off-2", "2026-03-05T10:00:10+08:00")
    transcribe_segment(first["id"], providers)
    transcribe_segment(second["id"], providers)

    # 关闭合并：不等待、每个片段各自一张卡片
    results = flush_transcribed_conversations(providers)
    assert len(results) == 2
    assert len(client.get("/api/v1/cards", headers=AUTH).json()["items"]) == 2


def test_process_now_still_summarizes_immediately(client, monkeypatch):
    """process_now=true（App 手动上传/测试）不等窗口，立即生成卡片。"""
    _setup(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "conversation_merge_window_s", 60.0)

    segment = _upload(client, "merge-now-1", "2026-03-05T10:00:00+08:00", process_now=True)
    detail = client.get(f"/api/v1/audio/segments/{segment['id']}", headers=AUTH).json()
    assert detail["status"] == "done"
    assert detail["card_id"]
    assert len(client.get("/api/v1/cards", headers=AUTH).json()["items"]) == 1


def test_worker_waits_for_window_before_summarizing(client, monkeypatch):
    from app.providers import get_providers
    from app.services.jobs import BackgroundWorker

    _setup(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "conversation_merge_window_s", 30.0)

    segment = _upload(client, "merge-worker-1", "2026-03-05T10:00:00+08:00")
    worker = BackgroundWorker(providers=get_providers())
    summary = worker.tick()
    assert summary["transcribed"] >= 1
    assert summary["processed"] == 0  # 还在等待窗口内

    detail = client.get(f"/api/v1/audio/segments/{segment['id']}", headers=AUTH).json()
    assert detail["status"] == "transcribed"
    assert client.get("/api/v1/cards", headers=AUTH).json()["items"] == []


def test_pending_sibling_blocks_premature_summary(client, monkeypatch):
    """同用户还有时间相邻的片段在排队时，先等它转写完再一起总结。"""
    providers = _setup(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "conversation_merge_window_s", 0.0)
    monkeypatch.setattr(settings, "conversation_merge_gap_s", 45.0)

    first = _upload(client, "merge-block-1", "2026-03-05T10:00:00+08:00")
    second = _upload(client, "merge-block-2", "2026-03-05T10:00:20+08:00")
    transcribe_segment(first["id"], providers)

    # 第二段仍在 queued：不能被提前总结
    assert flush_transcribed_conversations(providers) == []

    transcribe_segment(second["id"], providers)
    results = flush_transcribed_conversations(providers)
    assert len(results) == 1
    assert results[0]["merged_segments"] == 2
