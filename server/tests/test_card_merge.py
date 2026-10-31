"""AI 定时复盘合并：把话题相同/时间相近的近期卡片合并成一张并重新总结。"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from app.models import AuditLog, Card, ScheduleItem
from app.services.card_merge import merge_recent_cards
from tests.conftest import (
    AUTH,
    build_test_providers,
    concat,
    enroll_voiceprint,
    tone_wave,
    upload_segment,
)


class _MergeLLM:
    """Fake LLM：合并提示词返回指定分组，总结提示词返回普通卡片 JSON。"""

    name = "fake-llm"
    available = True

    def __init__(self) -> None:
        self.groups: list[list[str]] = []
        self.merge_prompts: list[str] = []

    def complete(self, system: str, user: str, temperature: float = 0.3, max_tokens: int | None = None) -> str:
        if "卡片合并" in system:
            self.merge_prompts.append(user)
            payload = {
                "groups": [
                    {"card_ids": ids, "reason": "同一场对话的不同片段"}
                    for ids in self.groups
                ]
            }
        else:
            payload = {
                "title": "合并后的对话",
                "summary": "两人讨论项目排期。",
                "key_points": ["排期"],
                "people": [],
                "card_type": "conversation",
                "schedule_items": [],
                "todos": [],
                "commands": [],
                "is_meeting": False,
            }
        return json.dumps(payload, ensure_ascii=False)


def _setup(client):
    from app.providers import set_providers

    providers = build_test_providers(
        user_text="帮我记一下明天下午三点和周磊开会。",
        other_text="好的，我提前把资料发你。",
    )
    llm = _MergeLLM()
    providers.llm = llm
    set_providers(providers)
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    return providers, llm


def _upload(client, client_segment_id: str, recorded_at: str) -> dict:
    response = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id=client_segment_id,
        recorded_at=recorded_at,
        process_now=True,
    )
    assert response.status_code == 202, response.text
    detail = client.get(f"/api/v1/audio/segments/{response.json()['id']}", headers=AUTH).json()
    assert detail["card_id"], detail
    return detail


def _two_cards(client):
    providers, llm = _setup(client)
    first = _upload(client, "ai-merge-1", "2026-03-05T10:00:00+08:00")
    second = _upload(client, "ai-merge-2", "2026-03-05T10:03:00+08:00")
    llm.groups = [[first["card_id"], second["card_id"]]]
    return providers, llm, first["card_id"], second["card_id"]


def test_ai_merges_same_conversation_cards(client, db_session):
    providers, llm, first_id, second_id = _two_cards(client)
    assert len(client.get("/api/v1/cards", headers=AUTH).json()["items"]) == 2

    result = merge_recent_cards(providers, force=True)
    assert result["candidates"] == 2
    assert result["merged"] == 1, result
    assert result["cards"][0]["primary_card_id"] == first_id
    assert result["cards"][0]["merged_card_ids"] == [second_id]
    assert llm.merge_prompts, "should have asked the LLM to review cards"

    db_session.expire_all()
    primary = db_session.get(Card, first_id)
    secondary = db_session.get(Card, second_id)
    assert primary is not None and primary.deleted_at is None
    assert primary.source_meta.get("ai_merged") is True
    assert primary.source_meta.get("ai_merged_card_ids") == [second_id]
    assert primary.title and primary.summary
    assert secondary is not None and secondary.deleted_at is not None

    # 列表只剩合并后的卡片，详情包含两段的转写
    items = client.get("/api/v1/cards", headers=AUTH).json()["items"]
    assert [item["id"] for item in items] == [first_id]
    card = client.get(f"/api/v1/cards/{first_id}", headers=AUTH).json()
    assert len(card["transcript"]) == 4  # 每段 user/other 各 1 turn
    assert card["source_meta"]["ai_merged"] is True

    # 审计日志记录合并操作
    logs = db_session.scalars(select(AuditLog).where(AuditLog.action == "card.merge")).all()
    assert {log.entity_id for log in logs} == {first_id, second_id}

    # 已产生的日程改挂到主卡片，不重复创建/删除
    schedules = db_session.scalars(
        select(ScheduleItem).where(ScheduleItem.deleted_at.is_(None))
    ).all()
    assert schedules
    assert {item.card_id for item in schedules} == {first_id}


def test_merge_does_not_execute_commands_again(client, db_session):
    providers, llm, first_id, second_id = _two_cards(client)
    commands_before = client.get("/api/v1/commands", headers=AUTH).json()["items"]
    merge_recent_cards(providers, force=True)
    commands_after = client.get("/api/v1/commands", headers=AUTH).json()["items"]
    assert len(commands_after) == len(commands_before)


def test_dry_run_returns_groups_without_merging(client, db_session):
    providers, _llm, first_id, second_id = _two_cards(client)
    result = merge_recent_cards(providers, force=True, dry_run=True)
    assert result["merged"] == 0
    assert result["groups"] == [{"card_ids": [first_id, second_id], "reason": "同一场对话的不同片段"}]
    assert len(client.get("/api/v1/cards", headers=AUTH).json()["items"]) == 2


def test_invalid_groups_are_ignored(client):
    providers, llm, first_id, second_id = _two_cards(client)
    llm.groups = [
        [first_id],  # 单张卡片不能合并
        [first_id, "card_not_exist"],  # 未知 id 被过滤后只剩一张
    ]
    result = merge_recent_cards(providers, force=True)
    assert result["merged"] == 0
    assert len(client.get("/api/v1/cards", headers=AUTH).json()["items"]) == 2


def test_no_llm_skips_ai_merge(client):
    from app.providers import set_providers
    from app.providers.llm import RuleBasedLLM

    providers, _llm = _setup(client)
    providers.llm = RuleBasedLLM()
    set_providers(providers)
    _upload(client, "ai-merge-rule-1", "2026-03-05T10:00:00+08:00")
    _upload(client, "ai-merge-rule-2", "2026-03-05T10:02:00+08:00")

    result = merge_recent_cards(providers, force=True)
    assert result["merged"] == 0
    assert result["groups"] == []
    assert len(client.get("/api/v1/cards", headers=AUTH).json()["items"]) == 2


def test_group_span_limit_rejects_far_apart_cards(client, monkeypatch):
    providers, llm, first_id, second_id = _two_cards(client)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "card_merge_max_span_s", 60.0)
    result = merge_recent_cards(providers, force=True)
    assert result["merged"] == 0
    assert result["skipped"]
    assert len(client.get("/api/v1/cards", headers=AUTH).json()["items"]) == 2


def test_worker_runs_ai_merge_on_interval(client, monkeypatch):
    from app.config import get_settings
    from app.providers import get_providers
    from app.services.jobs import BackgroundWorker

    _providers, _llm, first_id, second_id = _two_cards(client)
    monkeypatch.setattr(get_settings(), "card_merge_interval_s", 0.0)
    worker = BackgroundWorker(providers=get_providers())
    result = worker.tick()
    assert result.get("ai_merged") == 1
    assert worker.last_card_merge_result is not None
    assert worker.status()["card_merge"]["last_merged"] == 1
    assert [item["id"] for item in client.get("/api/v1/cards", headers=AUTH).json()["items"]] == [first_id]


# ── 手动合并（App「合并对话」入口）────────────────────────────────────────
def test_manual_merge_endpoint_merges_selected_cards(client, db_session):
    _providers, _llm, first_id, second_id = _two_cards(client)

    response = client.post(
        "/api/v1/cards/merge",
        headers=AUTH,
        json={"card_ids": [second_id, first_id], "note": "这两段是同一场讨论"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == first_id  # 保留时间最早的卡片为主卡片
    assert body["merged_card_ids"] == [second_id]
    assert body["merge_reason"] == "这两段是同一场讨论"
    assert body["source_meta"]["manual_merged"] is True
    assert len(body["transcript"]) == 4
    assert [item["id"] for item in client.get("/api/v1/cards", headers=AUTH).json()["items"]] == [first_id]

    db_session.expire_all()
    logs = db_session.scalars(select(AuditLog).where(AuditLog.action == "card.merge")).all()
    assert logs and all("手动" in (log.note or "") for log in logs)


def test_manual_merge_requires_at_least_two_cards(client):
    _providers, _llm, first_id, _second_id = _two_cards(client)
    response = client.post("/api/v1/cards/merge", headers=AUTH, json={"card_ids": [first_id]})
    assert response.status_code == 422


def test_manual_merge_rejects_unknown_card(client):
    _providers, _llm, first_id, _second_id = _two_cards(client)
    response = client.post(
        "/api/v1/cards/merge",
        headers=AUTH,
        json={"card_ids": [first_id, "card_not_exist"]},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "card_not_found"


def test_manual_merge_rejects_meeting_cards(client, db_session):
    from app.models import User

    _providers, _llm, first_id, _second_id = _two_cards(client)
    user = db_session.scalars(select(User).limit(1)).one()
    meeting_card = Card(
        user_id=user.id,
        title="会议卡片",
        summary="",
        type="meeting",
        meeting_id="mtg_demo",
        occurred_at=datetime.now(),
    )
    db_session.add(meeting_card)
    db_session.commit()
    db_session.refresh(meeting_card)

    response = client.post(
        "/api/v1/cards/merge",
        headers=AUTH,
        json={"card_ids": [first_id, meeting_card.id]},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "cannot_merge_meeting_cards"


def test_manual_merge_rejects_deleted_card(client):
    _providers, _llm, first_id, second_id = _two_cards(client)
    assert client.delete(f"/api/v1/cards/{second_id}", headers=AUTH).status_code == 200
    response = client.post(
        "/api/v1/cards/merge",
        headers=AUTH,
        json={"card_ids": [first_id, second_id]},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "card_deleted"
