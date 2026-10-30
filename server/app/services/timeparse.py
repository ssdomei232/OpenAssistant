"""Chinese date/time expression parsing used by the rule based extractor.

Handles the common spoken forms:

* 日期：今天/明天/后天/大后天、周X/星期X/下周一/本周三、3月5日、3/5
* 时间：上午/下午/晚上 + 三点/三点半/15:30/14点20分
* 区间：三点到四点、14:00-15:30
* 截止：周五之前、明天中午前

The production path uses the LLM for extraction; this module guarantees that
schedule/todo extraction still works offline and in tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}

PERIOD_HOURS = {"凌晨": (0, 6), "早上": (6, 11), "早晨": (6, 11), "上午": (6, 12), "中午": (11, 14), "下午": (12, 18), "傍晚": (17, 19), "晚上": (18, 23), "夜里": (20, 23), "夜间": (20, 23)}


@dataclass
class TimeMention:
    start: datetime  # local time
    end: datetime | None
    has_date: bool
    has_time: bool
    matched: str


def tzinfo_for(name: str) -> timezone:
    try:
        return ZoneInfo(name)  # type: ignore[return-value]
    except (ZoneInfoNotFoundError, ValueError):
        return timezone(timedelta(hours=8))


def to_local(dt_utc: datetime, tz_name: str) -> datetime:
    aware = dt_utc.replace(tzinfo=timezone.utc) if dt_utc.tzinfo is None else dt_utc
    return aware.astimezone(tzinfo_for(tz_name))


def to_utc(local_dt: datetime, tz_name: str) -> datetime:
    if local_dt.tzinfo is None:
        local_dt = local_dt.replace(tzinfo=tzinfo_for(tz_name))
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def cn_to_int(token: str) -> int | None:
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token in CN_DIGITS:
        return CN_DIGITS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in CN_DIGITS:
        return 10 + CN_DIGITS[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in CN_DIGITS:
        return CN_DIGITS[token[0]] * 10
    if len(token) == 3 and token[1] == "十" and token[0] in CN_DIGITS and token[2] in CN_DIGITS:
        return CN_DIGITS[token[0]] * 10 + CN_DIGITS[token[2]]
    return None


_NUM_TOKEN = r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})"
_NUM_RE = re.compile(_NUM_TOKEN)
_TIME_RE = re.compile(
    r"(?P<period>凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|夜间)?\s*"
    r"(?P<hour>" + _NUM_TOKEN + r")\s*(?:点|时|:|：)\s*(?P<minute>" + _NUM_TOKEN + r")?\s*分?(?P<half>半)?"
)
_COLON_TIME_RE = re.compile(r"(?P<hour>\d{1,2})[:：](?P<minute>\d{2})")
_DATE_WEEK_RE = re.compile(r"(?P<prefix>下下|下|这|本)?\s*(?:周|星期|礼拜)\s*(?P<day>[一二三四五六日天1-7])")
_DATE_MONTH_RE = re.compile(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*[日号]?")
_DATE_SLASH_RE = re.compile(r"(?<!\d)(?P<month>\d{1,2})\s*[/\-]\s*(?P<day>\d{1,2})(?!\d)")
_RELATIVE_DAYS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3, "昨天": -1, "前天": -2}
_RANGE_RE = re.compile(r"(?:到|至|~|－|-)\s*(?P<period2>凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)?\s*(?P<hour2>" + _NUM_TOKEN + r")\s*(?:点|时|:|：)\s*(?P<minute2>" + _NUM_TOKEN + r")?\s*(?P<half2>半)?")


def _resolve_time(period: str | None, hour_text: str, minute_text: str | None, half: str | None) -> tuple[int, int] | None:
    hour = cn_to_int(hour_text)
    if hour is None:
        return None
    minute = cn_to_int(minute_text) if minute_text else 0
    if minute is None:
        minute = 0
    if half:
        minute = 30
    if period in ("下午", "傍晚", "晚上", "夜里", "夜间") and hour < 12:
        hour += 12
    elif period == "中午":
        if hour < 11:
            hour += 12
    elif period == "凌晨":
        if hour == 12:
            hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _resolve_date(text: str, base: datetime) -> tuple[datetime | None, str]:
    for word, delta in _RELATIVE_DAYS.items():
        if word in text:
            return (base + timedelta(days=delta)).replace(hour=0, minute=0, second=0, microsecond=0), word
    match = _DATE_WEEK_RE.search(text)
    if match:
        day_token = match.group("day")
        target = WEEKDAY_MAP.get(day_token)
        if target is None:
            return None, ""
        monday = (base - timedelta(days=base.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        prefix = match.group("prefix") or ""
        if prefix.startswith("下下"):
            date = monday + timedelta(days=14 + target)
        elif prefix == "下":
            date = monday + timedelta(days=7 + target)
        elif prefix in ("这", "本"):
            date = monday + timedelta(days=target)
        else:
            days_ahead = (target - base.weekday()) % 7
            date = base + timedelta(days=days_ahead)
            date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        return date, match.group(0)
    match = _DATE_MONTH_RE.search(text)
    if match:
        month, day = int(match.group("month")), int(match.group("day"))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None, ""
        year = base.year
        try:
            date = base.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return None, ""
        if (date - base).days < -180:
            date = date.replace(year=year + 1)
        return date, match.group(0)
    match = _DATE_SLASH_RE.search(text)
    if match:
        month, day = int(match.group("month")), int(match.group("day"))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None, ""
        year = base.year
        try:
            date = base.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return None, ""
        if (date - base).days < -180:
            date = date.replace(year=year + 1)
        return date, match.group(0)
    return None, ""


def parse_time_mentions(text: str, base_local: datetime) -> list[TimeMention]:
    """Extract all date/time mentions from ``text`` relative to ``base_local``."""
    mentions: list[TimeMention] = []
    date, date_text = _resolve_date(text, base_local)

    for match in _TIME_RE.finditer(text):
        hour_text, minute_text, half = match.group("hour"), match.group("minute"), match.group("half")
        resolved = _resolve_time(match.group("period"), hour_text, minute_text, half)
        if resolved is None:
            continue
        hour, minute = resolved
        day = (date or base_local).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if date is None and day < base_local - timedelta(minutes=30):
            day += timedelta(days=1)  # "下午三点" said after 15:30 refers to tomorrow
        end: datetime | None = None
        tail = text[match.end() : match.end() + 30]
        range_match = _RANGE_RE.search(tail)
        if range_match:
            # "下午三点到四点" -> the end inherits the start's period
            period2 = range_match.group("period2") or match.group("period")
            resolved_end = _resolve_time(
                period2,
                range_match.group("hour2"),
                range_match.group("minute2"),
                range_match.group("half2"),
            )
            if resolved_end:
                end = day.replace(hour=resolved_end[0], minute=resolved_end[1])
                if end < day:
                    end += timedelta(days=1)
        mentions.append(
            TimeMention(
                start=day,
                end=end or (day + timedelta(hours=1)),
                has_date=date is not None,
                has_time=True,
                matched=(date_text + " " + match.group(0)).strip(),
            )
        )
        break

    # date-only mention
    if not mentions and date is not None:
        day = date.replace(hour=9, minute=0)
        mentions.append(TimeMention(start=day, end=day + timedelta(hours=1), has_date=True, has_time=False, matched=date_text))

    # colon form (no 点) such as 15:30
    if not mentions:
        for match in _COLON_TIME_RE.finditer(text):
            hour, minute = int(match.group("hour")), int(match.group("minute"))
            if hour > 23 or minute > 59:
                continue
            day = (date or base_local).replace(hour=hour, minute=minute, second=0, microsecond=0)
            mentions.append(TimeMention(start=day, end=day + timedelta(hours=1), has_date=date is not None, has_time=True, matched=match.group(0)))
            break
    return mentions


def parse_deadline(text: str, base_local: datetime, default_hour: int = 18) -> datetime | None:
    """Parse deadline phrases such as ``周五之前`` / ``明天中午前``."""
    has_marker = any(marker in text for marker in ("之前", "以前", "前完成", "截止", "deadline", "due"))
    if not has_marker:
        has_marker = bool(
            re.search(
                r"(?:今天|明天|后天|大后天|本周|这周|下周|周[一二三四五六日天]|星期[一二三四五六日天]|\d+月\d+日|\d+/\d+|中午|下午|晚上|上午|早上|傍晚|凌晨|点钟|点)\s*前",
                text,
            )
        )
    if not has_marker:
        return None
    cleaned = text
    for marker in ("之前", "以前", "前完成", "截止", "deadline", "due"):
        cleaned = cleaned.replace(marker, " ")
    mentions = parse_time_mentions(cleaned, base_local)
    if not mentions:
        return None
    mention = mentions[0]
    if mention.has_time:
        return mention.start
    # "明天中午前" / "今天晚上之前" carry the implied hour in the period word
    period_hours = {"凌晨": 6, "早上": 10, "早晨": 10, "上午": 12, "中午": 12, "下午": 18, "傍晚": 19, "晚上": 21, "夜里": 22, "夜间": 22}
    for period, hour in period_hours.items():
        if period in text:
            return mention.start.replace(hour=hour, minute=0)
    return mention.start.replace(hour=default_hour, minute=0)


def strip_time_expressions(text: str) -> str:
    """Remove parsed date/time tokens so they don't leak into titles."""
    cleaned = text
    for word in _RELATIVE_DAYS:
        cleaned = cleaned.replace(word, " ")
    cleaned = _DATE_WEEK_RE.sub(" ", cleaned)
    cleaned = _DATE_MONTH_RE.sub(" ", cleaned)
    cleaned = _DATE_SLASH_RE.sub(" ", cleaned)
    cleaned = re.sub(r"(?:凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|夜间)\s*前", " ", cleaned)
    cleaned = _TIME_RE.sub(" ", cleaned)
    cleaned = _RANGE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


TITLE_NOISE = [
    "帮我", "给我", "请帮我", "麻烦", "提醒我", "记得", "记一下", "记录一下", "添加", "新增",
    "安排", "创建", "在日历里", "把", "我要", "我需要", "然后", "另外", "顺便", "一下",
    "之前", "以前", "截止", "别忘了", "记着",
    "我认为", "我觉得", "我想", "可以", "好的", "那么", "其实", "所以", "因此", "还有", "同时",
]


def clean_title(text: str, max_len: int = 48) -> str:
    cleaned = text
    for noise in TITLE_NOISE:
        cleaned = cleaned.replace(noise, "")
    cleaned = strip_time_expressions(cleaned)
    cleaned = cleaned.strip("，。！？、；：,.!?;: \t\n")
    cleaned = re.sub(r"^(?:到|至|在|于|和|跟)\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("，。！？")
    return cleaned or "未命名事项"


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])", text)
    return [p.strip() for p in parts if p.strip()]
