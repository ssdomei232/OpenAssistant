"""User profile: short, global, incrementally corrected."""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import prompts
from ..config import default_timezone, get_settings
from ..models import Card, TranscriptTurn, User
from ..providers import Providers
from ..providers.llm import extract_json
from ..utils import now_utc
from .align import Turn
from .timeparse import to_local


def _recent_cards(db: Session, user: User, limit: int = 8) -> list[Card]:
    return list(
        db.scalars(
            select(Card)
            .where(Card.user_id == user.id, Card.deleted_at.is_(None))
            .order_by(Card.occurred_at.desc())
            .limit(limit)
        ).all()
    )


def _card_turns(db: Session, card: Card) -> list[Turn]:
    rows = db.scalars(select(TranscriptTurn).where(TranscriptTurn.card_id == card.id).order_by(TranscriptTurn.seq)).all()
    return [Turn(start=t.start, end=t.end, speaker=t.speaker, text=t.text, words=t.words or []) for t in rows]


def empty_profile() -> str:
    return ""


def profile_digest(db: Session, user: User, limit: int = 8) -> str:
    lines: list[str] = []
    tz_name = user.timezone or default_timezone()
    for card in _recent_cards(db, user, limit):
        day = to_local(card.occurred_at, tz_name).strftime("%Y-%m-%d") if card.occurred_at else ""
        user_quotes = [t.text for t in _card_turns(db, card) if t.speaker == "user"][:3]
        quote = "；".join(user_quotes)[:220]
        lines.append(f"- [{day}] {card.title}：{card.summary[:160]}" + (f"（原话：{quote}）" if quote else ""))
    return "\n".join(lines)


def _rule_profile(db: Session, user: User) -> str:
    cards = _recent_cards(db, user, 20)
    if not cards:
        return empty_profile()
    words: Counter[str] = Counter()
    people: Counter[str] = Counter()
    types: Counter[str] = Counter()
    for card in cards:
        types[card.type] += 1
        for person in card.people or []:
            if person and person not in (user.aliases or []):
                people[person] += 1
        user_turns = [t.text for t in _card_turns(db, card) if t.speaker == "user"]
        for text in user_turns:
            compact = re.sub(r"[^\u4e00-\u9fa5]", "", text)
            for i in range(len(compact) - 1):
                words[compact[i : i + 2]] += 1
    stop = {
        "我们", "这个", "那个", "可以", "就是", "然后", "一个", "什么", "没有", "还是", "但是", "如果", "因为",
        "所以", "今天", "明天", "后天", "帮我", "给我", "一下", "现在", "已经", "知道", "觉得", "另外", "提醒",
        "记得", "忘了", "别忘了", "记得带", "下周", "上午", "下午", "晚上", "会议", "一起", "过来", "确定",
    }
    function_chars = set("我你他她它的了着过是在和与就都也还很太更最把被给对从到向为以及呢吗吧啊嗯哦")
    date_chars = set("零一二三四五六七八九十两月日年周点分号早晚午")
    topics = [
        w
        for w, c in words.most_common(80)
        if w not in stop
        and c >= 2
        and not any(ch in function_chars for ch in w)
        and not any(ch in date_chars for ch in w)
    ][:6]
    parts: list[str] = []
    name = (user.aliases or [user.display_name])[0] if (user.aliases or user.display_name) else "用户"
    parts.append(f"{name}，日常通过语音记录工作与生活。")
    if topics:
        parts.append("常谈话题：" + "、".join(topics) + "。")
    if people:
        parts.append("常提到的人：" + "、".join(p for p, _ in people.most_common(4)) + "。")
    if types:
        type_name = {"conversation": "对话", "meeting": "会议", "media": "影音内容", "other": "其他"}
        parts.append("记录类型以" + "、".join(type_name.get(t, t) for t, _ in types.most_common(3)) + "为主。")
    parts.append("沟通偏好简短直接。")
    profile = "".join(parts)
    return profile[: get_settings().max_profile_chars]


def refresh_profile(db: Session, user: User, providers: Providers, *, reason: str = "scheduled") -> str:
    settings = get_settings()
    digest = profile_digest(db, user)
    existing = user.profile_text or ""
    profile: str | None = None
    if getattr(providers.llm, "available", False) and digest:
        try:
            raw = providers.llm.complete(
                prompts.profile_system(user.aliases or [], settings.max_profile_chars),
                prompts.profile_user(existing, digest),
                temperature=0.2,
                max_tokens=600,
            )
            data = extract_json(raw) or {}
            candidate = str(data.get("profile") or "").strip()
            if candidate:
                profile = candidate
        except Exception:
            profile = None
    if profile is None:
        profile = _rule_profile(db, user)
    profile = profile[: settings.max_profile_chars]
    if profile != existing:
        user.profile_text = profile
        user.profile_version = (user.profile_version or 0) + 1
        user.profile_source = "ai"
        user.profile_updated_at = now_utc()
    user.profile_dirty = False
    return profile


def mark_profile_dirty(db: Session, user: User) -> None:
    user.profile_dirty = True
    db.flush()


def maybe_update_profile(db: Session, user: User, providers: Providers) -> None:
    settings = get_settings()
    card_count = db.scalar(
        select(func.count(Card.id)).where(Card.user_id == user.id, Card.deleted_at.is_(None))
    ) or 0
    if not user.profile_text:
        refresh_profile(db, user, providers, reason="initial")
        return
    if card_count == 0:
        return
    if card_count % max(1, settings.profile_update_every) == 0:
        refresh_profile(db, user, providers, reason="incremental")
