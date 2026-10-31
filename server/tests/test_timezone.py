"""OA_TIMEZONE 配置、用户时区修改与「本地时间」传递。"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models import Card, User
from tests.conftest import AUTH


# ── 配置校验 ──────────────────────────────────────────────────────────────
def test_timezone_setting_validation():
    from app.config import Settings, normalize_timezone

    assert Settings(timezone="UTC").timezone == "UTC"
    assert Settings(timezone=" Asia/Shanghai ").timezone == "Asia/Shanghai"
    with pytest.raises(ValidationError):
        Settings(timezone="Mars/Olympus")
    with pytest.raises(ValueError):
        normalize_timezone("not-a-timezone")
    assert normalize_timezone("") == "Asia/Shanghai"


def test_timezone_is_explicit_only_when_configured(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("OA_TIMEZONE", raising=False)
    # 仓库里的 .env 没有 OA_TIMEZONE：忽略 .env 时只有默认值
    assert Settings(_env_file=None).timezone_explicit is False
    monkeypatch.setenv("OA_TIMEZONE", "UTC")
    settings = Settings(_env_file=None)
    assert settings.timezone == "UTC"
    assert settings.timezone_explicit is True


# ── 已有用户同步 ──────────────────────────────────────────────────────────
def test_apply_configured_timezone_updates_existing_users(client, db_session, monkeypatch):
    import app.security as security

    user = db_session.scalars(select(User).limit(1)).one()
    user.timezone = "Asia/Shanghai"
    db_session.commit()

    # 未显式配置：不动用户时区
    monkeypatch.setattr(
        security, "get_settings", lambda: SimpleNamespace(timezone="UTC", timezone_explicit=False)
    )
    assert security.apply_configured_timezone(db_session) == 0
    db_session.refresh(user)
    assert user.timezone == "Asia/Shanghai"

    # 显式配置 OA_TIMEZONE：启动时同步
    monkeypatch.setattr(
        security, "get_settings", lambda: SimpleNamespace(timezone="UTC", timezone_explicit=True)
    )
    assert security.apply_configured_timezone(db_session) >= 1
    db_session.refresh(user)
    assert user.timezone == "UTC"


def test_create_user_uses_configured_timezone(client, db_session, monkeypatch):
    import app.security as security

    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(api_key_pepper="pepper", timezone="America/New_York", timezone_explicit=True),
    )
    user, _ = security.create_user(db_session, display_name="纽约用户")
    db_session.commit()
    db_session.refresh(user)
    assert user.timezone == "America/New_York"


# ── API / CLI ─────────────────────────────────────────────────────────────
def test_patch_me_timezone_validates_iana_name(client):
    ok = client.patch("/api/v1/me", headers=AUTH, json={"timezone": "UTC"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["timezone"] == "UTC"

    bad = client.patch("/api/v1/me", headers=AUTH, json={"timezone": "Mars/Olympus"})
    assert bad.status_code == 422
    assert bad.json()["detail"] == "invalid_timezone"


def test_cli_set_timezone(client, db_session, capsys):
    from app.cli import main

    assert main(["set-timezone", "Asia/Shanghai"]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["timezone"] == "Asia/Shanghai"
    db_session.expire_all()
    assert {u.timezone for u in db_session.scalars(select(User)).all()} == {"Asia/Shanghai"}

    assert main(["set-timezone", "Mars/Olympus"]) == 2


# ── 交给 LLM / 展示的时间必须是本地时间 ───────────────────────────────────
class _CapturingLLM:
    name = "fake-llm"
    available = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.user_prompts: list[str] = []

    def complete(self, system: str, user: str, temperature: float = 0.3, max_tokens: int | None = None) -> str:
        self.user_prompts.append(user)
        return json.dumps(self.payload, ensure_ascii=False)


def test_summarizer_prompt_uses_local_time():
    from app.services.align import Turn
    from app.services.summarizer import summarize

    llm = _CapturingLLM({"title": "测试", "summary": "一段对话"})
    occurred_at = datetime(2026, 3, 5, 10, 0, 0)  # naive UTC
    turns = [Turn(start=0.0, end=1.0, speaker="user", text="明天下午三点开会")]
    summarize(turns, occurred_at=occurred_at, tz_name="Asia/Shanghai", llm=llm)

    prompt = llm.user_prompts[-1]
    # UTC 10:00 → 上海 18:00，提示词里必须是本地时间
    assert "2026-03-05T18:00:00" in prompt
    assert "2026-03-05T10:00:00" not in prompt


def test_meeting_minutes_use_local_time():
    from app.services.meetings import _rule_minutes

    user = User(display_name="我", aliases=["小明"], timezone="Asia/Shanghai")
    card = Card(
        title="排期会",
        summary="讨论排期",
        key_points=[],
        occurred_at=datetime(2026, 3, 5, 10, 0, 0),  # naive UTC
        duration_s=600.0,
    )
    payload = _rule_minutes(user, card, [])
    assert "2026-03-05 18:00" in payload["minutes_markdown"]
    assert "2026-03-05T10:00" not in payload["minutes_markdown"]
