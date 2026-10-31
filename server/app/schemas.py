"""Pydantic request models for the public API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import default_timezone, normalize_timezone


class MePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    aliases: list[str] | None = None
    timezone: str | None = None
    search_provider: Literal["builtin", "deepseek"] | None = None


class SettingsPatch(BaseModel):
    search_provider: Literal["builtin", "deepseek"] | None = None
    push_enabled: bool | None = None
    auto_schedule: bool | None = None
    auto_profile_update: bool | None = None
    correction_notice: bool | None = None


class ProfilePut(BaseModel):
    text: str = Field(min_length=0, max_length=5000)


class CardPatch(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    summary: str | None = None
    key_points: list[str] | None = None
    people: list[str] | None = None
    type: Literal["conversation", "meeting", "media", "other"] | None = None
    note: str = ""


class CardMergeIn(BaseModel):
    card_ids: list[str] = Field(min_length=2, max_length=20)
    note: str = Field(default="", max_length=300)


class ScheduleIn(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    start_at: str
    end_at: str | None = None
    location: str | None = None
    description: str | None = None
    remind_minutes_before: int = 30


class SchedulePatch(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    start_at: str | None = None
    end_at: str | None = None
    location: str | None = None
    description: str | None = None
    status: Literal["confirmed", "cancelled"] | None = None
    remind_minutes_before: int | None = None


class TodoIn(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    due_at: str | None = None
    priority: Literal["low", "normal", "high"] = "normal"


class TodoPatch(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    status: Literal["pending", "done"] | None = None
    due_at: str | None = None
    priority: Literal["low", "normal", "high"] | None = None


class MeetingPatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    minutes: str | None = None
    action_items: list[dict[str, Any]] | None = None


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    execute: bool = True


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    provider: Literal["builtin", "deepseek"] | None = None
    limit: int = Field(default=5, ge=1, le=10)


class ScheduledJobIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    cron: str | None = None
    run_at: str | None = None
    timezone: str = Field(default_factory=default_timezone)
    prompt: str = Field(default="", max_length=4000)
    action: Literal["agent", "push", "command"] = "agent"
    enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        return normalize_timezone(value)


class ScheduledJobPatch(BaseModel):
    name: str | None = None
    cron: str | None = None
    run_at: str | None = None
    timezone: str | None = None
    prompt: str | None = None
    action: Literal["agent", "push", "command"] | None = None
    enabled: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        return normalize_timezone(value) if value is not None else None


class PushTestIn(BaseModel):
    text: str = Field(default="这是来自 OpenAssistant 的测试消息。", max_length=1000)


class DeviceConfigIn(BaseModel):
    name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class RenameDeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
