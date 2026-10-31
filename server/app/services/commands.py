"""Command parsing and execution.  Only the user can issue them; callers must
pass ``[使用者]`` turns (the pipeline enforces this)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from .. import prompts
from ..config import default_timezone
from ..models import Card, Command, Meeting, Todo, User
from ..providers import Providers
from ..providers.llm import extract_json
from ..utils import now_utc, utc_from
from .align import Turn
from .entities import add_schedule, add_todo
from .intent import Intent, detect_intent
from .push_service import push_text
from .timeparse import clean_title, parse_time_mentions, to_local, to_utc

INTENT_LABELS = {
    "schedule_add": "添加日程",
    "schedule_query": "查询日程",
    "todo_add": "添加待办",
    "todo_query": "查询待办",
    "memory_query": "查询记忆",
    "data_query": "查询数据",
    "web_search": "资料查询",
    "none": "无需执行",
}


def parse_command(
    text: str,
    user: User,
    *,
    base_utc: datetime | None = None,
    providers: Providers | None = None,
) -> Intent:
    tz_name = _user_tz(user)
    base_utc = base_utc or now_utc()
    base_local = to_local(base_utc, tz_name)
    intent = detect_intent(text, base_local, tz_name)

    if providers is not None and getattr(providers.llm, "available", False):
        if intent.intent == "none" or intent.intent in {"schedule_add", "todo_add"}:
            try:
                raw = providers.llm.complete(
                    prompts.command_system(user.aliases or [], user.profile_text or ""),
                    f"当前时间：{base_local.isoformat()}\n指令：{text}",
                    temperature=0.1,
                    max_tokens=400,
                )
                data = extract_json(raw) or {}
                llm_intent = str(data.get("intent") or "none")
                if llm_intent != "none" and llm_intent in INTENT_LABELS:
                    slots = data.get("slots") if isinstance(data.get("slots"), dict) else {}
                    intent = Intent(intent=llm_intent, slots=slots, reply_hint=str(data.get("reply_hint") or ""))
            except Exception:
                pass
    return intent


def _slot_dt(value, tz_name: str) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return to_utc(dt, tz_name)
    return utc_from(dt)


def _user_tz(user: User) -> str:
    """用户时区，未设置时回落到服务器默认时区（OA_TIMEZONE）。"""
    return user.timezone or default_timezone()


def _format_schedule_line(item) -> str:
    local = to_local(item.start_at, _tz_of(item))
    when = local.strftime("%m-%d %H:%M")
    return f"- {when}｜{item.title}" + (f"（{item.location}）" if item.location else "")


def _tz_of(item) -> str:
    return getattr(item, "_tz_name", None) or default_timezone()


def _run_schedule_add(db: Session, user: User, intent: Intent, text: str, card_id: str | None, base_utc: datetime) -> tuple[str, bool]:
    tz_name = _user_tz(user)
    start_at = _slot_dt(intent.slots.get("start_at"), tz_name)
    end_at = _slot_dt(intent.slots.get("end_at"), tz_name)
    if start_at is None:
        mentions = parse_time_mentions(text, to_local(base_utc, tz_name))
        if mentions:
            start_at = to_utc(mentions[0].start, tz_name)
            end_at = to_utc(mentions[0].end, tz_name) if mentions[0].end else None
    if start_at is None:
        return ("我还没听清具体时间，请再说一下“什么时候、做什么”。", False)
    title = str(intent.slots.get("title") or clean_title(text))
    item = add_schedule(
        db,
        user,
        title=title,
        start_at=start_at,
        end_at=end_at,
        location=intent.slots.get("location"),
        description=intent.slots.get("description") or f"来自指令：{text}",
        source="command",
        card_id=card_id,
    )
    local = to_local(item.start_at, tz_name)
    result = f"已添加日程：{local.strftime('%Y-%m-%d %H:%M')} {item.title}"
    if item.location:
        result += f"（地点：{item.location}）"
    return (result, True)


def _run_todo_add(db: Session, user: User, intent: Intent, text: str, card_id: str | None, base_utc: datetime) -> tuple[str, bool]:
    tz_name = _user_tz(user)
    due_at = _slot_dt(intent.slots.get("due_at"), tz_name)
    title = str(intent.slots.get("title") or clean_title(text))
    item = add_todo(db, user, title=title, due_at=due_at, source="command", card_id=card_id)
    result = f"已添加待办：{item.title}"
    if due_at:
        result += f"（截止 {to_local(due_at, tz_name).strftime('%m-%d %H:%M')}）"
    return (result, True)


def _run_schedule_query(db: Session, user: User, base_utc: datetime) -> tuple[str, bool]:
    tz_name = _user_tz(user)
    start = base_utc - timedelta(hours=2)
    end = base_utc + timedelta(days=14)
    from ..models import ScheduleItem

    rows = db.scalars(
        select(ScheduleItem)
        .where(
            ScheduleItem.user_id == user.id,
            ScheduleItem.deleted_at.is_(None),
            ScheduleItem.start_at >= start,
            ScheduleItem.start_at <= end,
        )
        .order_by(ScheduleItem.start_at)
        .limit(20)
    ).all()
    if not rows:
        return ("接下来两周没有日程安排。", True)
    lines = [f"接下来两周共 {len(rows)} 个日程："]
    for row in rows:
        local = to_local(row.start_at, tz_name)
        lines.append(f"- {local.strftime('%m-%d %H:%M')}｜{row.title}" + (f"（{row.location}）" if row.location else ""))
    return ("\n".join(lines), True)


def _run_todo_query(db: Session, user: User) -> tuple[str, bool]:
    rows = db.scalars(
        select(Todo)
        .where(Todo.user_id == user.id, Todo.deleted_at.is_(None), Todo.status == "pending")
        .order_by(Todo.due_at.is_(None), Todo.due_at)
        .limit(20)
    ).all()
    if not rows:
        return ("目前没有未完成的待办。", True)
    tz_name = _user_tz(user)
    lines = [f"还有 {len(rows)} 个待办："]
    for row in rows:
        due = f"（截止 {to_local(row.due_at, tz_name).strftime('%m-%d %H:%M')}）" if row.due_at else ""
        lines.append(f"- {row.title}{due}")
    return ("\n".join(lines), True)


def _run_memory_query(db: Session, user: User, intent: Intent) -> tuple[str, bool]:
    query = str(intent.slots.get("query") or "").strip()
    conditions = [Card.user_id == user.id, Card.deleted_at.is_(None)]
    if query:
        like = f"%{query[:60]}%"
        conditions.append(
            or_(
                Card.title.like(like),
                Card.summary.like(like),
                cast(Card.key_points, String).like(like),
            )
        )
    rows = db.scalars(select(Card).where(*conditions).order_by(Card.occurred_at.desc()).limit(5)).all()
    if not rows:
        return (f"没有找到与“{query}”相关的记忆。", True)
    tz_name = _user_tz(user)
    lines = [f"找到 {len(rows)} 条相关记忆："]
    for row in rows:
        local = to_local(row.occurred_at, tz_name)
        lines.append(f"- {local.strftime('%m-%d')}｜{row.title}：{row.summary[:80]}")
    return ("\n".join(lines), True)


def _run_data_query(db: Session, user: User) -> tuple[str, bool]:
    cards = db.scalar(select(func.count(Card.id)).where(Card.user_id == user.id, Card.deleted_at.is_(None))) or 0
    pending = db.scalar(
        select(func.count(Todo.id)).where(Todo.user_id == user.id, Todo.deleted_at.is_(None), Todo.status == "pending")
    ) or 0
    meetings = db.scalar(
        select(func.count(Meeting.id)).where(Meeting.user_id == user.id, Meeting.deleted_at.is_(None))
    ) or 0
    from ..models import ScheduleItem

    upcoming = db.scalar(
        select(func.count(ScheduleItem.id)).where(
            ScheduleItem.user_id == user.id,
            ScheduleItem.deleted_at.is_(None),
            ScheduleItem.start_at >= now_utc(),
        )
    ) or 0
    return (f"目前共 {cards} 张总结卡片、{meetings} 场会议、{pending} 个未完成待办、{upcoming} 个未来日程。", True)


def _run_web_search(db: Session, user: User, intent: Intent, providers: Providers, text: str) -> tuple[str, bool]:
    query = str(intent.slots.get("query") or "").strip() or clean_title(text)
    provider = user.settings_with_defaults().get("search_provider") or "builtin"
    context = f"用户画像（供回答风格参考）：{user.profile_text}" if user.profile_text else ""
    try:
        search_provider = providers.search_for(provider)
        response = search_provider.search(query, limit=5, context=context)
    except Exception as exc:
        return (f"资料查询失败：{type(exc).__name__}", False)
    lines = [f"查询：{query}", "", response.answer or "（无结果）"]
    if response.results:
        lines.append("")
        lines.append("来源：")
        for result in response.results[:5]:
            lines.append(f"- [{result.title}]({result.url})")
    return ("\n".join(lines), True)


def execute_command(
    db: Session,
    user: User,
    text: str,
    providers: Providers,
    *,
    card_id: str | None = None,
    execute: bool = True,
    base_utc: datetime | None = None,
    intent: Intent | None = None,
) -> Command:
    base_utc = base_utc or now_utc()
    intent = intent or parse_command(text, user, base_utc=base_utc, providers=providers)
    command = Command(
        user_id=user.id,
        text=text,
        intent=intent.intent,
        status="ignored",
        card_id=card_id,
        payload={"slots": intent.slots, "reply_hint": intent.reply_hint},
    )
    db.add(command)
    db.flush()
    if not execute:
        command.status = "parsed"
        return command
    if intent.intent == "none":
        command.result = "未识别到需要执行的指令。"
        return command

    ok = False
    try:
        if intent.intent == "schedule_add":
            result, ok = _run_schedule_add(db, user, intent, text, card_id, base_utc)
        elif intent.intent == "todo_add":
            result, ok = _run_todo_add(db, user, intent, text, card_id, base_utc)
        elif intent.intent == "schedule_query":
            result, ok = _run_schedule_query(db, user, base_utc)
        elif intent.intent == "todo_query":
            result, ok = _run_todo_query(db, user)
        elif intent.intent == "memory_query":
            result, ok = _run_memory_query(db, user, intent)
        elif intent.intent == "data_query":
            result, ok = _run_data_query(db, user)
        elif intent.intent == "web_search":
            result, ok = _run_web_search(db, user, intent, providers, text)
        else:
            result, ok = (f"暂不支持的指令类型：{intent.intent}", False)
    except Exception as exc:
        result, ok = (f"执行失败：{type(exc).__name__}: {exc}", False)

    command.result = result
    command.status = "done" if ok else "failed"
    db.flush()

    if settings_push_allowed(user, intent.intent):
        title = f"OpenAssistant · {INTENT_LABELS.get(intent.intent, '指令结果')}"
        outbox = push_text(db, user, result, providers, title=title)
        command.pushed = outbox.status in {"sent", "pending"}
    return command


def settings_push_allowed(user: User, intent: str) -> bool:
    if intent == "none":
        return False
    return bool(user.settings_with_defaults().get("push_enabled", True))


def handle_user_turns(
    db: Session,
    user: User,
    turns: list[Turn],
    providers: Providers,
    *,
    card_id: str | None = None,
    base_utc: datetime | None = None,
) -> list[Command]:
    """Execute commands found in user turns (identity already enforced)."""
    commands: list[Command] = []
    for turn in turns:
        if turn.speaker != "user":
            continue
        for sentence in _sentences(turn.text):
            intent = detect_intent(sentence, to_local(base_utc or now_utc(), _user_tz(user)), _user_tz(user))
            if intent.intent == "none":
                continue
            commands.append(
                execute_command(
                    db,
                    user,
                    sentence,
                    providers,
                    card_id=card_id,
                    base_utc=base_utc,
                    intent=intent,
                )
            )
    return commands


def _sentences(text: str) -> list[str]:
    import re

    parts = re.split(r"(?<=[。！？!?；;])", text)
    return [p.strip() for p in parts if p.strip()]
