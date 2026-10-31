"""SQLAlchemy ORM models.

Identity labels are always one of ``user`` / ``other`` / ``unknown``; the
display labels ``[使用者]`` / ``[其他人]`` / ``[未知]`` are produced at the
API/serialisation layer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import default_timezone
from .db import Base
from .utils import new_id, now_utc

SPEAKER_LABELS = {"user": "[使用者]", "other": "[其他人]", "unknown": "[未知]"}


def _id(prefix: str):
    return lambda: new_id(prefix)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("usr"))
    display_name: Mapped[str] = mapped_column(String(120), default="我")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    timezone: Mapped[str] = mapped_column(String(64), default=default_timezone)
    api_key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    search_provider: Mapped[str] = mapped_column(String(32), default="builtin")
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    profile_text: Mapped[str] = mapped_column(Text, default="")
    profile_version: Mapped[int] = mapped_column(Integer, default=0)
    profile_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    profile_dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_source: Mapped[str] = mapped_column(String(16), default="ai")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    voiceprint: Mapped["Voiceprint | None"] = relationship(back_populates="user", uselist=False)
    cards: Mapped[list["Card"]] = relationship(back_populates="user")

    DEFAULT_SETTINGS = {
        "search_provider": None,  # falls back to user.search_provider
        "push_enabled": True,
        "auto_schedule": True,
        "auto_profile_update": True,
        "correction_notice": True,
    }

    def settings_with_defaults(self) -> dict[str, Any]:
        merged = dict(self.DEFAULT_SETTINGS)
        merged.update(self.settings or {})
        merged["search_provider"] = (self.settings or {}).get("search_provider") or self.search_provider
        return merged


class Voiceprint(Base):
    __tablename__ = "voiceprints"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("vpr"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    threshold: Mapped[float] = mapped_column(Float, default=60.0)
    quality: Mapped[float] = mapped_column(Float, default=0.0)
    sample_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    provider: Mapped[str] = mapped_column(String(32), default="spectral")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    user: Mapped[User] = relationship(back_populates="voiceprint")


class AudioSegment(Base):
    __tablename__ = "audio_segments"
    __table_args__ = (UniqueConstraint("user_id", "client_segment_id", name="uq_audio_client_segment"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("seg"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_segment_id: Mapped[str] = mapped_column(String(160), index=True)
    device_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64), default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    sample_rate: Mapped[int] = mapped_column(Integer, default=16000)
    channels: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    card_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (Index("ix_cards_user_occurred", "user_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("card"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    key_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    people: Mapped[list[str]] = mapped_column(JSON, default=list)
    type: Mapped[str] = mapped_column(String(24), default="conversation", index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    user_turns: Mapped[int] = mapped_column(Integer, default=0)
    other_turns: Mapped[int] = mapped_column(Integer, default=0)
    audio_segment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    meeting_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    user: Mapped[User] = relationship(back_populates="cards")
    turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", order_by="TranscriptTurn.seq"
    )


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("turn"))
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    start: Mapped[float] = mapped_column(Float, default=0.0)
    end: Mapped[float] = mapped_column(Float, default=0.0)
    speaker: Mapped[str] = mapped_column(String(16), default="other")
    text: Mapped[str] = mapped_column(Text, default="")
    words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    card: Mapped[Card] = relationship(back_populates="turns")


class ScheduleItem(Base):
    __tablename__ = "schedule_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("evt"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    location: Mapped[str | None] = mapped_column(String(240), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="manual")
    card_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="confirmed")
    remind_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Todo(Base):
    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("todo"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="manual")
    card_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("mtg"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    minutes: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decisions: Mapped[list[str]] = mapped_column(JSON, default=list)
    participants: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    action_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    card_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="ready")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("cmd"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    intent: Mapped[str] = mapped_column(String(48), default="none")
    status: Mapped[str] = mapped_column(String(24), default="ignored")
    result: Mapped[str] = mapped_column(Text, default="")
    pushed: Mapped[bool] = mapped_column(Boolean, default=False)
    card_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("job"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    cron: Mapped[str | None] = mapped_column(String(120), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default=default_timezone)
    prompt: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(24), default="agent")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class CardCorrection(Base):
    __tablename__ = "card_corrections"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("corr"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[str] = mapped_column(String(40), index=True)
    field: Mapped[str] = mapped_column(String(48), default="")
    before: Mapped[Any | None] = mapped_column(JSON, nullable=True, default=None)
    after: Mapped[Any | None] = mapped_column(JSON, nullable=True, default=None)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("aud"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(64), default="")
    entity: Mapped[str] = mapped_column(String(48), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    before: Mapped[Any | None] = mapped_column(JSON, nullable=True, default=None)
    after: Mapped[Any | None] = mapped_column(JSON, nullable=True, default=None)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class PushOutbox(Base):
    __tablename__ = "push_outbox"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("push"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), default="OpenAssistant")
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
