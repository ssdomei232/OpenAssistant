"""Card summarisation: LLM first, deterministic rule based fallback.

The hard identity constraint is enforced twice: rule extraction only reads
``[使用者]`` turns, and LLM output is filtered through a user-support check so
that schedule/todo/command items hallucinated from ``[其他人]`` context are
dropped before they are persisted.

The summariser may also *discard* a segment instead of producing a card: the LLM
is allowed to mark audio as too short / meaningless (``discard: true`` in its
JSON reply), and a conservative deterministic fallback does the same whenever
there is no usable LLM.  Discarding never wins over extracted actions - a
schedule/todo/command (or a substantive transcript) always keeps the card.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from .. import prompts
from ..config import default_timezone, get_settings
from ..providers.llm import extract_json
from .align import Turn, transcript_text, user_only
from .intent import detect_intent
from .timeparse import (
    clean_title,
    parse_deadline,
    parse_time_mentions,
    split_sentences,
    to_local,
    to_utc,
)

SCHEDULE_VERBS = re.compile(
    r"开会|会议|见面|吃饭|评审|面试|出发|参加|上课|体检|看牙|复诊|医院|挂号|取药|出差|汇报|培训|约|聚|讨论|谈|团建|聚餐|婚礼|搬家"
)
PEOPLE_RE = re.compile(r"(?:和|跟|与|对|给|找)([\u4e00-\u9fa5]{2,4})(?:一起|开会|讨论|聊|说|见面|吃饭|确认|沟通|对接|汇报)")
PEOPLE_STOP = {"你", "我", "他", "她", "大家", "他们", "我们", "客户", "同事", "领导", "老板", "对方"}
MEDIA_HINTS = ("电视剧", "电影", "视频", "直播", "播客", "综艺", "短视频", "网课", "音频节目", "广播")

logger = logging.getLogger(__name__)

# 语气词/应答词的字符与词组：用于判断“只有语气词、没有有效信息”的音频
_FILLER_CHARS = frozenset("嗯啊哦噢呃额唉哎奥哈呵嘿喂对是行好谢拜呀吧呢的了嘛咯我你您他她它这那们个")
_FILLER_WORDS = (
    "知道了",
    "明白了",
    "了解了",
    "收到",
    "谢谢",
    "多谢",
    "再见",
    "拜拜",
    "不客气",
    "没关系",
    "没事",
    "可以",
    "好的",
    "行了",
    "okay",
    "ok",
    "hi",
    "hello",
)
# 出现这些信息信号（时间/数字/常见事务词/英文单词）就不算“简短无意义”
_INFO_SIGNAL_RE = re.compile(
    r"\d|今天|明天|后天|昨天|上午|下午|晚上|早上|中午|凌晨|周[一二三四五六日天]|星期|"
    r"[年月日号点分]|小时|分钟|会议|开会|项目|方案|工作|时间|地点|计划|任务|"
    r"[A-Za-z]{2,}"
)
# AI 判定丢弃时，转写有效字数达到该值即视为“有明显信息量”，忽略丢弃请求以防误删
LLM_DISCARD_MAX_CHARS = 160


@dataclass
class ScheduleDraft:
    title: str
    start_at: datetime
    end_at: datetime | None = None
    location: str | None = None
    description: str | None = None
    source_text: str = ""


@dataclass
class TodoDraft:
    title: str
    due_at: datetime | None = None
    source_text: str = ""


@dataclass
class CommandDraft:
    text: str
    intent: str = "none"


@dataclass
class CardDraft:
    title: str
    summary: str
    key_points: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    card_type: str = "conversation"
    schedule_items: list[ScheduleDraft] = field(default_factory=list)
    todos: list[TodoDraft] = field(default_factory=list)
    commands: list[CommandDraft] = field(default_factory=list)
    is_meeting: bool = False
    source: str = "rule"
    # 丢弃：音频过于简短或无意义，不生成卡片（title/summary 等字段无意义）
    discard: bool = False
    discard_reason: str = ""


# ── helpers ───────────────────────────────────────────────────────────────
def _iso_to_utc(value: Any, tz_name: str) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is not None:
        return _to_naive_utc(dt)
    # naive datetimes from the LLM are interpreted in the user's timezone
    return to_utc(dt, tz_name)


def _to_naive_utc(dt: datetime) -> datetime:
    from datetime import timezone as _tz

    return dt.astimezone(_tz.utc).replace(tzinfo=None)


def _shingles(text: str, size: int = 2) -> set[str]:
    compact = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[i : i + size] for i in range(len(compact) - size + 1)}


def user_support(candidate: str, user_text: str, threshold: float = 0.34) -> bool:
    """True when ``candidate`` is plausibly grounded in ``[使用者]`` speech."""
    if not user_text.strip():
        return False
    if candidate.strip() and candidate.strip() in user_text:
        return True
    cand = _shingles(candidate)
    if not cand:
        return False
    overlap = len(cand & _shingles(user_text)) / len(cand)
    return overlap >= threshold


def _first_sentence(text: str, limit: int = 80) -> str:
    for sentence in split_sentences(text):
        cleaned = sentence.strip()
        if cleaned:
            return cleaned[:limit]
    return text.strip()[:limit]


# ── discard heuristics ────────────────────────────────────────────────────
def _compact(text: str) -> str:
    """Remove whitespace/punctuation, keeping CJK characters and alphanumerics."""
    return re.sub(r"[\W_]+", "", text or "", flags=re.UNICODE)


def _is_filler_text(text: str) -> bool:
    """True when ``text`` consists only of interjections / acknowledgements."""
    compact = _compact(text).lower()
    if not compact:
        return True
    remainder = compact
    for word in sorted(_FILLER_WORDS, key=len, reverse=True):
        remainder = remainder.replace(word, "")
    return all(ch in _FILLER_CHARS for ch in remainder)


def _meaningful_chars(turns: Sequence[Turn]) -> int:
    return sum(len(_compact(turn.text)) for turn in turns)


def _has_info_signals(compact_text: str) -> bool:
    return bool(_INFO_SIGNAL_RE.search(compact_text))


def _rule_discard_reason(
    turns: Sequence[Turn],
    draft: CardDraft,
    settings: Any,
) -> str | None:
    """Conservative deterministic discard used when the LLM is unavailable.

    Only audio whose transcript carries no information at all (pure fillers, or
    a couple of characters without any time/number/entity signal) is dropped;
    anything actionable or substantive is always kept.
    """
    if not settings.summarizer_discard_enabled:
        return None
    if draft.schedule_items or draft.todos or draft.commands:
        return None
    if draft.card_type in {"meeting", "media"} or draft.is_meeting:
        return None
    texts = [turn.text for turn in turns if _compact(turn.text)]
    compact = "".join(_compact(text) for text in texts)
    if not compact:
        return "转写内容为空"
    if all(_is_filler_text(text) for text in texts):
        return "只有语气词或应答词，没有有效信息"
    if len(compact) <= settings.summarizer_discard_min_chars and not _has_info_signals(compact):
        return f"转写只有 {len(compact)} 个字，没有有效信息"
    return None


def _llm_discard_blocked(turns: Sequence[Turn], rule_draft: CardDraft) -> bool:
    """Never let the LLM throw away content with actions or substantial text."""
    if rule_draft.schedule_items or rule_draft.todos or rule_draft.commands or rule_draft.is_meeting:
        return True
    return _meaningful_chars(turns) >= LLM_DISCARD_MAX_CHARS


# ── rule based extraction ─────────────────────────────────────────────────
def _rule_extract(
    turns: Sequence[Turn],
    occurred_at: datetime,
    tz_name: str,
) -> CardDraft:
    users = user_only(turns)
    base_local = to_local(occurred_at, tz_name)
    user_text = "".join(t.text for t in users)

    schedule: list[ScheduleDraft] = []
    todos: list[TodoDraft] = []
    commands: list[CommandDraft] = []
    seen_schedule: set[tuple[str, str]] = set()
    seen_todo: set[str] = set()

    for turn in users:
        for sentence in split_sentences(turn.text) or [turn.text]:
            sentence = sentence.strip()
            if len(sentence) < 2:
                continue
            intent = detect_intent(sentence, base_local, tz_name)
            mentions = parse_time_mentions(sentence, base_local)
            deadline = parse_deadline(sentence, base_local)

            if intent.intent == "web_search" or intent.intent == "memory_query" or intent.intent == "schedule_query" or intent.intent == "data_query":
                commands.append(CommandDraft(text=sentence, intent=intent.intent))
                continue

            add_schedule = intent.intent == "schedule_add" or (
                bool(mentions) and bool(SCHEDULE_VERBS.search(sentence))
            )
            if add_schedule and mentions:
                mention = mentions[0]
                title = clean_title(sentence)
                start_utc = to_utc(mention.start, tz_name)
                end_utc = to_utc(mention.end, tz_name) if mention.end else None
                key = (title, start_utc.isoformat())
                if key not in seen_schedule:
                    seen_schedule.add(key)
                    schedule.append(
                        ScheduleDraft(
                            title=title,
                            start_at=start_utc,
                            end_at=end_utc,
                            location=_extract_location(sentence),
                            description="根据录音自动提取",
                            source_text=sentence,
                        )
                    )
                continue

            add_todo = intent.intent == "todo_add" or deadline is not None or any(
                marker in sentence for marker in ("提醒我", "记得", "别忘了", "待办")
            )
            if add_todo and deadline is not None:
                title = clean_title(sentence)
                if title not in seen_todo:
                    seen_todo.add(title)
                    todos.append(
                        TodoDraft(
                            title=title,
                            due_at=to_utc(deadline, tz_name),
                            source_text=sentence,
                        )
                    )
            elif intent.intent == "todo_add":
                title = clean_title(sentence)
                if title not in seen_todo:
                    seen_todo.add(title)
                    todos.append(TodoDraft(title=title, source_text=sentence))

    # all-speaker based title/summary
    all_text = "".join(t.text for t in turns)
    title_source = users[0].text if users else (turns[0].text if turns else "")
    title = clean_title(_first_sentence(title_source, 60), max_len=24) if title_source else "未命名对话"
    summary_parts: list[str] = []
    for turn in list(users) + [t for t in turns if t.speaker != "user"]:
        sentence = _first_sentence(turn.text, 90)
        if sentence and sentence not in summary_parts:
            summary_parts.append(sentence)
        if len("".join(summary_parts)) > 220:
            break
    summary = "".join(summary_parts)[:320] or "（无转写内容）"

    key_points: list[str] = []
    for turn in list(users) + [t for t in turns if t.speaker != "user"]:
        for sentence in split_sentences(turn.text):
            sentence = sentence.strip()
            if 6 <= len(sentence) <= 80 and sentence not in key_points:
                key_points.append(sentence)
            if len(key_points) >= 5:
                break
        if len(key_points) >= 5:
            break

    people: list[str] = []
    for match in PEOPLE_RE.finditer(user_text or all_text):
        name = match.group(1)
        if name not in PEOPLE_STOP and name not in people:
            people.append(name)

    speakers = {t.speaker for t in turns}
    is_meeting = len(turns) >= 2 and len(speakers - {"unknown"}) >= 2 and (turns[-1].end - turns[0].start) >= 60
    card_type = "meeting" if is_meeting else "conversation"
    if not is_meeting and any(hint in user_text for hint in MEDIA_HINTS) and not [t for t in turns if t.speaker != "user"]:
        card_type = "media"

    return CardDraft(
        title=title,
        summary=summary,
        key_points=key_points,
        people=people,
        card_type=card_type,
        schedule_items=schedule,
        todos=todos,
        commands=commands,
        is_meeting=is_meeting,
        source="rule",
    )


def _extract_location(sentence: str) -> str | None:
    match = re.search(r"(?:在|去|到)([\u4e00-\u9fa5A-Za-z0-9]{2,16}?)(?:开会|见面|讨论|吃饭|评审|面试|参加)", sentence)
    if match:
        return match.group(1)
    match = re.search(r"(?:会议室|办公室|公司|家里|线上|腾讯会议|飞书)", sentence)
    return match.group(0) if match else None


# ── LLM based extraction ──────────────────────────────────────────────────
def _llm_extract(
    turns: Sequence[Turn],
    occurred_at: datetime,
    tz_name: str,
    llm: Any,
    aliases: list[str],
    profile: str,
) -> CardDraft | None:
    if not getattr(llm, "available", False):
        return None
    transcript = transcript_text(turns)
    local_at = to_local(occurred_at, tz_name)
    try:
        raw = llm.complete(
            prompts.summarizer_system(aliases, profile),
            # 给 LLM 的时间必须是**用户本地时间**，否则“明天下午三点”会被按 UTC 换算偏移
            prompts.summarizer_user(transcript, local_at.isoformat(), tz_name),
            temperature=0.2,
        )
    except Exception:
        return None
    data = extract_json(raw)
    if not data:
        return None

    # The LLM may explicitly decline to summarise short / meaningless audio.
    if data.get("discard") is True or str(data.get("discard", "")).strip().lower() in {"true", "1", "yes"}:
        reason = str(data.get("discard_reason") or "").strip()[:200]
        return CardDraft(
            title="",
            summary="",
            discard=True,
            discard_reason=reason or "AI 判断该音频没有总结价值",
            source="llm",
        )

    users = user_only(turns)
    user_text = "".join(t.text for t in users)
    draft = CardDraft(
        title=str(data.get("title") or "").strip()[:60],
        summary=str(data.get("summary") or "").strip(),
        key_points=[str(x).strip() for x in (data.get("key_points") or []) if str(x).strip()][:5],
        people=[str(x).strip() for x in (data.get("people") or []) if str(x).strip()][:8],
        card_type=str(data.get("card_type") or "conversation"),
        source="llm",
    )
    if draft.card_type not in {"conversation", "meeting", "media", "other"}:
        draft.card_type = "conversation"

    for item in data.get("schedule_items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        start = _iso_to_utc(item.get("start_at"), tz_name)
        source_text = str(item.get("source_text") or "")
        if not title or start is None:
            continue
        if not user_support(source_text or title, user_text):
            continue
        draft.schedule_items.append(
            ScheduleDraft(
                title=title[:120],
                start_at=start,
                end_at=_iso_to_utc(item.get("end_at"), tz_name),
                location=(str(item["location"]).strip() if item.get("location") else None),
                description=(str(item["description"]).strip() if item.get("description") else None),
                source_text=source_text,
            )
        )
    for item in data.get("todos") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        source_text = str(item.get("source_text") or "")
        if not title:
            continue
        if not user_support(source_text or title, user_text):
            continue
        draft.todos.append(
            TodoDraft(title=title[:200], due_at=_iso_to_utc(item.get("due_at"), tz_name), source_text=source_text)
        )
    for item in data.get("commands") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        intent = str(item.get("intent") or "none")
        if not text or not user_support(text, user_text):
            continue
        draft.commands.append(CommandDraft(text=text, intent=intent))
    draft.is_meeting = bool(data.get("is_meeting")) or draft.card_type == "meeting"
    if turns and not draft.is_meeting:
        distinct = {t.speaker for t in turns}
        draft.is_meeting = len(distinct) >= 2 and (turns[-1].end - turns[0].start) >= 60
    if draft.is_meeting:
        draft.card_type = "meeting"
    if not draft.summary:
        return None
    if not draft.title:
        draft.title = clean_title(_first_sentence(draft.summary, 60), max_len=24)
    return draft


def summarize(
    turns: Sequence[Turn],
    *,
    occurred_at: datetime,
    tz_name: str | None = None,
    llm: Any = None,
    aliases: list[str] | None = None,
    profile: str = "",
) -> CardDraft:
    """Produce a card draft, enforcing the identity constraint on both paths.

    The returned draft may carry ``discard=True``, meaning the audio is too
    short / meaningless to be worth a card; callers must not persist it.

    Discard is decided by the LLM when available (``discard`` in its JSON
    reply).  Requests that would throw away actionable or substantial content
    are ignored, and a deterministic heuristic covers the no-LLM case.
    """
    aliases = aliases or []
    tz_name = tz_name or default_timezone()
    settings = get_settings()
    rule_draft = _rule_extract(turns, occurred_at, tz_name)
    llm_draft = _llm_extract(turns, occurred_at, tz_name, llm, aliases, profile) if llm is not None else None

    if llm_draft is not None and llm_draft.discard:
        if settings.summarizer_discard_enabled and not _llm_discard_blocked(turns, rule_draft):
            logger.info(
                "summarizer: AI discarded audio (%s chars): %s",
                _meaningful_chars(turns),
                llm_draft.discard_reason,
            )
            return llm_draft
        logger.info(
            "summarizer: ignored AI discard request (%s); keeping the card", llm_draft.discard_reason
        )
        llm_draft = None

    if llm_draft is not None:
        # Rule results are a floor: never lose schedule/todos that the
        # deterministic extractor is confident about.
        _merge_drafts(llm_draft, rule_draft)
        return llm_draft

    reason = _rule_discard_reason(turns, rule_draft, settings)
    if reason:
        logger.info("summarizer: discarded low-value audio: %s", reason)
        return CardDraft(
            title="",
            summary="",
            card_type=rule_draft.card_type,
            discard=True,
            discard_reason=reason,
            source="rule",
        )
    return rule_draft


def _merge_drafts(target: CardDraft, source: CardDraft) -> None:
    existing_schedule = {(i.title, i.start_at) for i in target.schedule_items}
    for item in source.schedule_items:
        if (item.title, item.start_at) not in existing_schedule:
            target.schedule_items.append(item)
    existing_todos = {i.title for i in target.todos}
    for item in source.todos:
        if item.title not in existing_todos:
            target.todos.append(item)
    existing_commands = {i.text for i in target.commands}
    for item in source.commands:
        if item.text not in existing_commands:
            target.commands.append(item)
    if not target.people:
        target.people = source.people
    if not target.key_points:
        target.key_points = source.key_points
    if not target.summary:
        target.summary = source.summary
    if not target.title:
        target.title = source.title
