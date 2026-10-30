"""Rule based command intent detection (works offline; LLM is an enhancement)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .timeparse import clean_title, parse_deadline, parse_time_mentions, to_utc


@dataclass
class Intent:
    intent: str = "none"
    slots: dict = field(default_factory=dict)
    reply_hint: str = ""


SEARCH_PATTERNS = [
    r"查一下", r"查查", r"查询", r"搜索", r"搜一下", r"搜搜", r"帮我查", r"给我查",
    r"查下", r"搜下", r"看看.{0,12}(怎么样|是什么|多少钱|天气|新闻|消息|进展)",
]
SCHEDULE_ADD_PATTERNS = [
    r"安排", r"添加.{0,6}日程", r"加到日程", r"新建.{0,4}日程",
    r"(?:\d|[一二三四五六七八九十两])\s*(?:点|:|：).{0,12}(?:开会|会议|见面|吃饭|出发|去|参加)",
    r"(?:明天|今天|后天|下周|本周|周[一二三四五六日天]).{0,20}(?:开会|会议|见面|吃饭|出发|参加|评审|面试)",
]
SCHEDULE_QUERY_PATTERNS = [r"我的日程", r"有什么(?:安排|会|日程)", r"接下来.{0,6}(?:日程|会议|安排)", r"今天.{0,8}(?:安排|日程)", r"看看.{0,4}日程"]
TODO_ADD_PATTERNS = [r"待办", r"提醒我", r"记得", r"别忘了", r"别忘", r"要记得"]
TODO_QUERY_PATTERNS = [r"我的待办", r"还有什么没做", r"有哪些待办", r"未完成的?任务"]
MEMORY_PATTERNS = [r"还记得", r"之前(?:说|聊|提)过", r"我们聊过", r"查一下.{0,8}(?:记忆|记录|说过|聊过)", r"我的记忆"]
DATA_PATTERNS = [r"统计", r"有多少(?:条|张|个)", r"最近.{0,6}(?:记录|卡片|总结)", r"多少条记录"]
NEGATIVE_PATTERNS = [r"不用了", r"算了", r"取消", r"没什么", r"不用查"]


def _match_any(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def _clean_query(text: str) -> str:
    query = text
    for noise in ("帮我查一下", "帮我查查", "帮我查", "给我查一下", "给我查", "查一下", "查查", "查询", "搜索一下", "搜索", "搜一下", "搜搜", "查下", "搜下", "请问", "我想知道", "告诉我"):
        if query.startswith(noise):
            query = query[len(noise) :]
            break
    return query.strip("，。！？、；：,.!?;: ")


def detect_intent(text: str, base_local: datetime, tz_name: str = "Asia/Shanghai") -> Intent:
    raw = (text or "").strip()
    if not raw:
        return Intent()
    if _match_any(NEGATIVE_PATTERNS, raw) and len(raw) <= 12:
        return Intent(intent="none", reply_hint="用户取消或无需操作")

    if _match_any(MEMORY_PATTERNS, raw):
        return Intent(intent="memory_query", slots={"query": _clean_query(raw)}, reply_hint="查询历史记忆")
    if _match_any(TODO_QUERY_PATTERNS, raw):
        return Intent(intent="todo_query", reply_hint="查询待办")
    if _match_any(SCHEDULE_QUERY_PATTERNS, raw):
        return Intent(intent="schedule_query", reply_hint="查询日程")
    if _match_any(DATA_PATTERNS, raw):
        return Intent(intent="data_query", slots={"query": _clean_query(raw)}, reply_hint="查询数据统计")
    if _match_any(SEARCH_PATTERNS, raw):
        return Intent(intent="web_search", slots={"query": _clean_query(raw) or raw}, reply_hint="联网/资料查询")
    if _match_any(TODO_ADD_PATTERNS, raw):
        mention = parse_time_mentions(raw, base_local)
        due = parse_deadline(raw, base_local)
        slots = {"title": clean_title(raw)}
        if due is not None:
            slots["due_at"] = to_utc(due, tz_name).isoformat()
        elif mention:
            slots["due_at"] = to_utc(mention[0].start, tz_name).isoformat()
        return Intent(intent="todo_add", slots=slots, reply_hint="添加待办")
    if _match_any(SCHEDULE_ADD_PATTERNS, raw):
        mentions = parse_time_mentions(raw, base_local)
        slots = {"title": clean_title(raw)}
        if mentions:
            mention = mentions[0]
            slots["start_at"] = to_utc(mention.start, tz_name).isoformat()
            if mention.end:
                slots["end_at"] = to_utc(mention.end, tz_name).isoformat()
        location = re.search(r"(?:在|去)([\u4e00-\u9fa5A-Za-z0-9]{2,20}?)(?:开会|见面|讨论|吃饭|评审)", raw)
        if location:
            slots["location"] = location.group(1)
        return Intent(intent="schedule_add", slots=slots, reply_hint="添加日程")
    return Intent(intent="none")
