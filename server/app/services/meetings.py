"""Meeting detection and minutes generation."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import prompts
from ..config import default_timezone, get_settings
from ..models import Card, Meeting, Todo, User
from ..providers import Providers
from ..providers.llm import extract_json
from ..utils import now_utc
from .align import Turn, transcript_text
from .timeparse import to_local

DECISION_MARKERS = ("决定", "确定", "定了", "结论", "同意", "就这么", "拍板", "统一")
ACTION_MARKERS = ("负责", "跟进", "我来", "你来", "记得", "需要", "安排", "提交", "发我", "整理")
RULE_STOP = {"我们", "这个", "那个", "然后", "可以", "就是", "一个"}


def _user_tz(user: User) -> str:
    return user.timezone or default_timezone()


def _local(user: User, dt: datetime) -> datetime:
    """naive UTC → 用户本地时间（会议纪要/提示词里的时间必须是本地时间）。"""
    return to_local(dt, _user_tz(user))


def _local_str(user: User, dt: datetime) -> str:
    return _local(user, dt).strftime("%Y-%m-%d %H:%M")


def _format_due(user: User, value) -> str:
    """把 datetime / ISO 字符串按用户时区格式化；无法解析时原样返回。"""
    if not value:
        return ""
    dt = value
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    return _local_str(user, dt)


def _alias_name(user: User) -> str:
    aliases = [a for a in (user.aliases or []) if a]
    return aliases[0] if aliases else (user.display_name or "使用者")


def build_participants(user: User, card: Card) -> list[dict]:
    participants = [{"label": "[使用者]", "name": _alias_name(user)}]
    names = [p for p in (card.people or []) if p]
    if names:
        for name in names[:6]:
            participants.append({"label": "[其他人]", "name": name})
    else:
        participants.append({"label": "[其他人]", "name": "其他说话人"})
    return participants


def _rule_minutes(user: User, card: Card, turns: list[Turn]) -> dict:
    topics: list[dict] = []
    decisions: list[str] = []
    action_items: list[dict] = []
    for point in card.key_points or []:
        if any(marker in point for marker in DECISION_MARKERS):
            decisions.append(point)
        elif any(marker in point for marker in ACTION_MARKERS):
            action_items.append({"text": point, "owner": None, "due_at": None})
        else:
            topics.append({"title": point[:30], "points": [point]})
    if not topics:
        topics = [{"title": card.title or "会议议题", "points": [card.summary[:120]]}]

    # Todos already extracted from this card become tracked action items.
    for todo in _card_todos(card):
        if not any(todo.title == item["text"] for item in action_items):
            action_items.append(
                {"text": todo.title, "owner": _alias_name(user), "due_at": todo.due_at}
            )
    if not decisions:
        decisions = ["本次会议未记录明确决议。"]

    markdown_lines = [f"## {card.title or '会议纪要'}", "", f"**时间**：{_local_str(user, card.occurred_at)}"]
    markdown_lines.append(f"**参与者**：{_alias_name(user)}、其他说话人")
    markdown_lines.append("")
    markdown_lines.append("### 摘要")
    markdown_lines.append(card.summary or "")
    markdown_lines.append("")
    markdown_lines.append("### 议题")
    for topic in topics:
        markdown_lines.append(f"- **{topic['title']}**：" + "；".join(topic["points"]))
    markdown_lines.append("")
    markdown_lines.append("### 决议")
    for decision in decisions:
        markdown_lines.append(f"- {decision}")
    markdown_lines.append("")
    markdown_lines.append("### 行动项")
    for item in action_items:
        owner = f"（{item['owner']}）" if item.get("owner") else ""
        due = f"，截止 {_format_due(user, item['due_at'])}" if item.get("due_at") else ""
        markdown_lines.append(f"- [ ] {item['text']}{owner}{due}")
    return {
        "summary": card.summary,
        "topics": topics,
        "decisions": decisions,
        "action_items": action_items,
        "minutes_markdown": "\n".join(markdown_lines),
    }


def _card_todos(card: Card) -> list[Todo]:
    # populated by caller via db in generate_minutes
    return getattr(card, "_todos", [])


def _llm_minutes(user: User, card: Card, turns: list[Turn], providers: Providers) -> dict | None:
    if not getattr(providers.llm, "available", False):
        return None
    try:
        raw = providers.llm.complete(
            prompts.meeting_system(user.aliases or [], user.profile_text or ""),
            f"会议时间：{_local_str(user, card.occurred_at)}\n标题：{card.title}\n\n{transcript_text(turns)}",
            temperature=0.2,
            max_tokens=1800,
        )
    except Exception:
        return None
    data = extract_json(raw)
    if not data:
        return None
    return {
        "title": str(data.get("title") or card.title or "会议纪要")[:120],
        "summary": str(data.get("summary") or card.summary or ""),
        "topics": data.get("topics") if isinstance(data.get("topics"), list) else [],
        "decisions": data.get("decisions") if isinstance(data.get("decisions"), list) else [],
        "action_items": data.get("action_items") if isinstance(data.get("action_items"), list) else [],
        "participants": data.get("participants") if isinstance(data.get("participants"), list) else None,
        "minutes_markdown": str(data.get("minutes_markdown") or ""),
    }


def _minutes_markdown(user: User, card: Card, payload: dict) -> str:
    if payload.get("minutes_markdown"):
        return payload["minutes_markdown"]
    lines = [f"## {payload.get('title') or card.title or '会议纪要'}", "", f"**时间**：{_local_str(user, card.occurred_at)}"]
    participants = payload.get("participants") or build_participants(user, card)
    lines.append("**参与者**：" + "、".join(p.get("name", "") for p in participants if isinstance(p, dict)))
    lines += ["", "### 摘要", payload.get("summary") or card.summary or ""]
    topics = payload.get("topics") or []
    if topics:
        lines += ["", "### 议题"]
        for topic in topics:
            if isinstance(topic, dict):
                points = topic.get("points") or []
                lines.append(f"- **{topic.get('title', '')}**：" + "；".join(str(p) for p in points))
    decisions = payload.get("decisions") or []
    if decisions:
        lines += ["", "### 决议"]
        lines.extend(f"- {d}" for d in decisions)
    actions = payload.get("action_items") or []
    if actions:
        lines += ["", "### 行动项"]
        for item in actions:
            if isinstance(item, dict):
                owner = f"（{item.get('owner')}）" if item.get("owner") else ""
                due = f"，截止 {_format_due(user, item.get('due_at'))}" if item.get("due_at") else ""
                lines.append(f"- [ ] {item.get('text', '')}{owner}{due}")
    return "\n".join(lines)


def should_be_meeting(card: Card, turns: list[Turn], min_seconds: float | None = None, min_speakers: int | None = None) -> bool:
    settings = get_settings()
    min_seconds = settings.meeting_min_seconds if min_seconds is None else min_seconds
    min_speakers = settings.meeting_min_speakers if min_speakers is None else min_speakers
    if len(turns) < 2:
        return False
    duration = (turns[-1].end - turns[0].start) if turns else 0.0
    if duration < min_seconds and card.duration_s < min_seconds:
        return False
    speakers = {t.speaker for t in turns}
    return len(speakers) >= min_speakers


def attach_meeting(
    db: Session,
    user: User,
    card: Card,
    turns: list[Turn],
    providers: Providers,
) -> Meeting | None:
    if not should_be_meeting(card, turns):
        card.meeting_id = None
        return None

    todos = db.scalars(
        select(Todo).where(Todo.card_id == card.id, Todo.deleted_at.is_(None))
    ).all()
    card._todos = list(todos)  # type: ignore[attr-defined]

    payload = _llm_minutes(user, card, turns, providers) or _rule_minutes(user, card, turns)
    participants = payload.get("participants") or build_participants(user, card)
    title = payload.get("title") or card.title or "会议"

    existing: Meeting | None = None
    window_start = card.occurred_at - timedelta(minutes=45)
    window_end = card.occurred_at + timedelta(minutes=45)
    candidates = db.scalars(
        select(Meeting)
        .where(
            Meeting.user_id == user.id,
            Meeting.deleted_at.is_(None),
            Meeting.started_at >= window_start,
            Meeting.started_at <= window_end,
        )
        .order_by(Meeting.started_at.desc())
    ).all()
    if candidates:
        existing = candidates[0]

    if existing is not None:
        card_ids = list(existing.card_ids or [])
        if card.id not in card_ids:
            card_ids.append(card.id)
        existing.card_ids = card_ids
        existing.ended_at = max(existing.ended_at or card.occurred_at, card.occurred_at)
        existing.updated_at = now_utc()
        existing.summary = payload.get("summary") or existing.summary
        existing.topics = payload.get("topics") or existing.topics
        existing.decisions = payload.get("decisions") or existing.decisions
        existing.action_items = payload.get("action_items") or existing.action_items
        existing.participants = _merge_participants(existing.participants or [], participants)
        existing.minutes = _minutes_markdown(user, card, payload)
        existing.status = "ready"
        meeting = existing
    else:
        meeting = Meeting(
            user_id=user.id,
            title=title[:240],
            started_at=card.occurred_at,
            ended_at=card.occurred_at + timedelta(seconds=max(card.duration_s, 0)),
            summary=payload.get("summary") or card.summary,
            minutes=_minutes_markdown(user, card, payload),
            topics=payload.get("topics") or [],
            decisions=payload.get("decisions") or [],
            participants=participants,
            action_items=payload.get("action_items") or [],
            card_ids=[card.id],
            status="ready",
        )
        db.add(meeting)
        db.flush()
    card.meeting_id = meeting.id
    card.type = "meeting"
    db.flush()
    return meeting


def _merge_participants(existing: list, new: list) -> list:
    merged = {str(p.get("name")): p for p in existing if isinstance(p, dict)}
    for item in new:
        if isinstance(item, dict) and str(item.get("name")) not in merged:
            merged[str(item.get("name"))] = item
    return list(merged.values())


def regenerate_minutes(db: Session, user: User, meeting: Meeting, providers: Providers) -> Meeting:
    cards = db.scalars(
        select(Card).where(Card.id.in_(meeting.card_ids or []), Card.deleted_at.is_(None))
    ).all()
    turns: list[Turn] = []
    from ..models import TranscriptTurn

    for card in sorted(cards, key=lambda c: c.occurred_at):
        rows = db.scalars(
            select(TranscriptTurn).where(TranscriptTurn.card_id == card.id).order_by(TranscriptTurn.seq)
        ).all()
        turns.extend(Turn(start=r.start, end=r.end, speaker=r.speaker, text=r.text) for r in rows)
    transcript = transcript_text(turns) if turns else meeting.summary
    payload = None
    if getattr(providers.llm, "available", False):
        try:
            raw = providers.llm.complete(
                prompts.meeting_system(user.aliases or [], user.profile_text or ""),
                f"会议时间：{_local_str(user, meeting.started_at)}\n标题：{meeting.title}\n\n{transcript}",
                temperature=0.2,
                max_tokens=1800,
            )
            data = extract_json(raw)
            if data:
                payload = {
                    "title": str(data.get("title") or meeting.title),
                    "summary": str(data.get("summary") or meeting.summary),
                    "topics": data.get("topics") or [],
                    "decisions": data.get("decisions") or [],
                    "action_items": data.get("action_items") or [],
                    "participants": data.get("participants"),
                    "minutes_markdown": str(data.get("minutes_markdown") or ""),
                }
        except Exception:
            payload = None
    if payload is None:
        primary = cards[0] if cards else Card(title=meeting.title, summary=meeting.summary, occurred_at=meeting.started_at, key_points=[], duration_s=0.0)
        payload = _rule_minutes(user, primary, turns)
    meeting.title = payload.get("title") or meeting.title
    meeting.summary = payload.get("summary") or meeting.summary
    meeting.topics = payload.get("topics") or meeting.topics
    meeting.decisions = payload.get("decisions") or meeting.decisions
    meeting.action_items = payload.get("action_items") or meeting.action_items
    if payload.get("participants"):
        meeting.participants = payload["participants"]
    meeting.minutes = _minutes_markdown(user, cards[0] if cards else None, payload) if cards else payload.get("minutes_markdown", "")
    meeting.status = "ready"
    meeting.updated_at = now_utc()
    db.flush()
    return meeting
