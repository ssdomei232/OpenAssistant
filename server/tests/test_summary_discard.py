"""总结丢弃策略：过于简短/无意义的人声音频，AI（或规则兜底）可选择丢弃。"""
from __future__ import annotations

import json
from datetime import datetime

from app.services.align import Turn
from app.services.summarizer import summarize
from tests.conftest import (
    AUTH,
    build_test_providers,
    concat,
    enroll_voiceprint,
    tone_wave,
    upload_segment,
)

OCCURRED_AT = datetime(2026, 3, 5, 10, 0, 0)


class _FakeLLM:
    """Minimal LLM stub returning a fixed JSON payload."""

    name = "fake-llm"
    available = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete(self, system: str, user: str, temperature: float = 0.3, max_tokens: int | None = None) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


def _turn(start: float, end: float, text: str, speaker: str = "user") -> Turn:
    return Turn(start=start, end=end, speaker=speaker, text=text)


def _summarize(turns: list[Turn], llm=None):
    return summarize(turns, occurred_at=OCCURRED_AT, tz_name="Asia/Shanghai", llm=llm)


# ── rule based fallback ───────────────────────────────────────────────────
def test_rule_fallback_discards_filler_only_audio():
    draft = _summarize([_turn(0.0, 2.0, "嗯。"), _turn(2.0, 4.0, "哦。", speaker="other")])
    assert draft.discard is True
    assert draft.source == "rule"
    assert draft.discard_reason


def test_rule_fallback_discards_too_short_audio():
    draft = _summarize([_turn(0.0, 1.0, "走吧")])
    assert draft.discard is True
    assert "过短" in draft.discard_reason or "没有有效信息" in draft.discard_reason


def test_rule_fallback_keeps_meaningful_short_speech():
    assert _summarize([_turn(0.0, 2.0, "明天开会")]).discard is False
    assert _summarize([_turn(0.0, 2.0, "这个方案我觉得可以")]).discard is False


def test_rule_fallback_keeps_actionable_short_command():
    draft = _summarize([_turn(0.0, 2.0, "帮我查天气")])
    assert draft.discard is False
    assert draft.commands


def test_discard_can_be_disabled(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "summarizer_discard_enabled", False)
    draft = _summarize([_turn(0.0, 2.0, "嗯。")])
    assert draft.discard is False


# ── AI driven discard ─────────────────────────────────────────────────────
def test_ai_can_discard_meaningless_audio():
    llm = _FakeLLM({"discard": True, "discard_reason": "只有语气词，没有有效信息"})
    draft = _summarize([_turn(0.0, 2.0, "嗯嗯")], llm=llm)
    assert draft.discard is True
    assert draft.source == "llm"
    assert "语气词" in draft.discard_reason


def test_ai_discard_ignored_when_audio_has_actions():
    """AI 不能丢弃含日程/待办/指令的音频：规则抽取是安全底线。"""
    llm = _FakeLLM({"discard": True, "discard_reason": "太短"})
    draft = _summarize([_turn(0.0, 3.0, "帮我记一下明天下午三点和周磊开会。")], llm=llm)
    assert draft.discard is False
    assert draft.schedule_items


def test_ai_discard_ignored_for_substantive_transcript():
    llm = _FakeLLM({"discard": True, "discard_reason": "误判"})
    long_text = "我们今天详细讨论了项目排期、上线计划和风险点，" * 10
    draft = _summarize([_turn(0.0, 60.0, long_text)], llm=llm)
    assert draft.discard is False
    assert draft.summary


def test_ai_discard_ignored_when_disabled(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "summarizer_discard_enabled", False)
    llm = _FakeLLM({"discard": True, "discard_reason": "只有语气词"})
    draft = _summarize([_turn(0.0, 2.0, "嗯嗯")], llm=llm)
    assert draft.discard is False


# ── pipeline integration ──────────────────────────────────────────────────
def test_pipeline_discards_filler_audio_without_card(client):
    from app.providers import set_providers

    set_providers(build_test_providers(user_text="嗯。", other_text="哦。"))
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    upload = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-discard-filler",
    )
    assert upload.status_code == 202, upload.text
    segment = client.get(f"/api/v1/audio/segments/{upload.json()['id']}", headers=AUTH).json()
    assert segment["status"] == "done"
    assert segment["card_id"] is None
    assert segment["error"] == "discarded_low_value_audio"
    assert segment["discard"]["source"] == "rule"
    assert client.get("/api/v1/cards", headers=AUTH).json()["items"] == []


def test_pipeline_lets_ai_discard_meaningless_audio(client):
    from app.providers import set_providers

    providers = build_test_providers(user_text="嗯嗯", other_text="哦。")
    providers.llm = _FakeLLM({"discard": True, "discard_reason": "只有应答词"})
    set_providers(providers)
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    upload = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-discard-ai",
    )
    assert upload.status_code == 202, upload.text
    segment = client.get(f"/api/v1/audio/segments/{upload.json()['id']}", headers=AUTH).json()
    assert segment["status"] == "done"
    assert segment["card_id"] is None
    assert segment["error"] == "discarded_by_ai"
    assert segment["discard"]["source"] == "llm"
    assert segment["discard"]["reason"] == "只有应答词"
    assert segment["discard"]["turns"] >= 1
    assert client.get("/api/v1/cards", headers=AUTH).json()["items"] == []


def test_pipeline_keeps_short_audio_with_real_command(client):
    """即使 AI 想丢弃，短句里的真实指令仍要生成卡片并执行。"""
    from app.providers import set_providers

    providers = build_test_providers(user_text="帮我查天气。", other_text="")
    providers.llm = _FakeLLM({"discard": True, "discard_reason": "太短"})
    set_providers(providers)
    enroll_voiceprint(client, tone_wave(3.0, "user"))
    upload = upload_segment(
        client,
        concat(tone_wave(2.0, "user"), tone_wave(2.0, "other")),
        client_segment_id="seg-discard-command",
    )
    assert upload.status_code == 202, upload.text
    segment = client.get(f"/api/v1/audio/segments/{upload.json()['id']}", headers=AUTH).json()
    assert segment["status"] == "done"
    assert segment["card_id"], segment
    commands = client.get("/api/v1/commands", headers=AUTH).json()["items"]
    assert len(commands) == 1
    assert commands[0]["intent"] == "web_search"
