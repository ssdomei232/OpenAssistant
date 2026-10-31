"""AI 定时复盘合并卡片。

片段级对话合并（pipeline）能避免同一段录音被 VAD 切片拆成多张卡片，但网络延迟、
上传失败重传、合并窗口刚好错过等情况仍可能留下多张「同一场对话」的卡片。

本模块每隔一段时间（worker tick 中按 ``OA_CARD_MERGE_INTERVAL_S`` 节流）让 LLM 复盘
最近 ``OA_CARD_MERGE_LOOKBACK_S`` 内新增的卡 conversation 卡片，若判断话题相同/时间相近，
就把它们合并成一张卡片并重新总结：

* 保留时间最早的主卡片，其余卡片软删除（``deleted_at``），写 ``card.merge`` 审计日志；
* 合并各卡片的转写 turns 并按时间线重排，重新调用总结生成统一的标题/摘要/关键信息；
* 已产生的日程/待办/指令**不重新执行**，只把归属的 ``card_id`` 改到主卡片，避免重复推送/创建；
* 合并后用户画像置 dirty，由 worker 在下一轮刷新。

AI 不可用时直接跳过（不做基于规则的跨卡片合并，避免误合并不同话题）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import select

from .. import prompts
from ..config import default_timezone, get_settings
from ..db import session_scope
from ..models import AuditLog, Card, Command, ScheduleItem, Todo, TranscriptTurn, User
from ..providers import Providers, get_providers
from ..providers.llm import extract_json
from ..utils import iso_utc, now_utc
from .align import Turn
from .summarizer import summarize
from .timeparse import to_local

logger = logging.getLogger(__name__)


def _card_turns(db, card: Card) -> list[Turn]:
    rows = db.scalars(
        select(TranscriptTurn).where(TranscriptTurn.card_id == card.id).order_by(TranscriptTurn.seq)
    ).all()
    return [
        Turn(start=float(row.start), end=float(row.end), speaker=row.speaker, text=row.text, words=row.words or [])
        for row in rows
    ]


def _card_snapshot(card: Card, tz_name: str) -> dict[str, Any]:
    local_at = to_local(card.occurred_at, tz_name)
    return {
        "id": card.id,
        "title": card.title,
        "summary": card.summary,
        "key_points": list(card.key_points or []),
        "people": list(card.people or []),
        "occurred_at": local_at.isoformat(timespec="seconds"),
        "occurred_ts": card.occurred_at.isoformat(),
        "duration_s": round(float(card.duration_s or 0.0), 1),
        "audio_segment_ids": list((card.source_meta or {}).get("audio_segment_ids") or []),
    }


def _candidate_cards(db, user: User, lookback_s: float, moment: datetime) -> list[Card]:
    settings = get_settings()
    since = moment - timedelta(seconds=max(lookback_s, 0.0))
    rows = db.scalars(
        select(Card)
        .where(
            Card.user_id == user.id,
            Card.deleted_at.is_(None),
            Card.type == "conversation",
            Card.created_at >= since,
        )
        .order_by(Card.created_at.desc())
        .limit(max(2, int(settings.card_merge_max_cards)))
    ).all()
    return sorted(rows, key=lambda card: (card.occurred_at, card.id))


def _digest(snapshots: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in snapshots:
        people = "、".join(item.get("people") or []) or "无"
        lines.append(
            f"- id={item['id']} 时间={item['occurred_at']} 时长={item['duration_s']}s "
            f"标题={item['title']} 人物={people} 摘要={str(item['summary'])[:200]}"
        )
    return "\n".join(lines)


def _parse_groups(raw: str, valid_ids: set[str]) -> list[tuple[list[str], str]]:
    data = extract_json(raw)
    if not isinstance(data, dict):
        return []
    result: list[tuple[list[str], str]] = []
    used: set[str] = set()
    for item in data.get("groups") or []:
        if not isinstance(item, dict):
            continue
        ids: list[str] = []
        for value in item.get("card_ids") or []:
            card_id = str(value)
            if card_id in valid_ids and card_id not in used and card_id not in ids:
                ids.append(card_id)
        if len(ids) < 2:
            continue
        used.update(ids)
        result.append((ids, str(item.get("reason") or "").strip()[:300]))
    return result


def _ask_llm(
    providers: Providers,
    user: User,
    snapshots: Sequence[dict[str, Any]],
    moment: datetime,
) -> list[tuple[list[str], str]]:
    if not getattr(providers.llm, "available", False):
        return []
    tz_name = user.timezone or default_timezone()
    try:
        raw = providers.llm.complete(
            prompts.card_merge_system(list(user.aliases or []), user.profile_text or ""),
            prompts.card_merge_user(
                _digest(snapshots),
                to_local(moment, tz_name).isoformat(timespec="seconds"),
                tz_name,
            ),
            temperature=0.1,
            max_tokens=800,
        )
    except Exception:  # noqa: BLE001 - LLM 失败不应影响主流程
        logger.warning("card merge: LLM call failed", exc_info=True)
        return []
    return _parse_groups(raw, {item["id"] for item in snapshots})


def _merge_turns(cards: Sequence[Card], turns_by_card: dict[str, list[Turn]]) -> tuple[list[Turn], datetime]:
    base = min(card.occurred_at for card in cards)
    merged: list[Turn] = []
    ordered = sorted(cards, key=lambda card: (card.occurred_at, card.id))
    for card in ordered:
        offset = (card.occurred_at - base).total_seconds()
        for turn in turns_by_card.get(card.id, []):
            merged.append(
                Turn(
                    start=round(offset + turn.start, 3),
                    end=round(offset + turn.end, 3),
                    speaker=turn.speaker,
                    text=turn.text,
                    words=list(turn.words or []),
                )
            )
    merged.sort(key=lambda turn: (turn.start, turn.end))
    return merged, base


def _merge_group(
    db,
    user: User,
    cards: Sequence[Card],
    reason: str,
    providers: Providers,
    *,
    manual: bool = False,
) -> dict[str, Any] | None:
    cards = sorted(cards, key=lambda card: (card.occurred_at, card.id))
    primary, others = cards[0], list(cards[1:])
    if not others:
        return None
    if primary.meeting_id is not None or any(card.meeting_id is not None for card in others):
        return None  # 会议卡片由会议纪要流程管理，不参与普通卡片合并
    if any(card.type == "meeting" for card in cards):
        return None
    tz_name = user.timezone or default_timezone()
    turns_by_card = {card.id: _card_turns(db, card) for card in cards}
    merged_turns, occurred_at = _merge_turns(cards, turns_by_card)
    if not merged_turns:
        return None

    draft = summarize(
        merged_turns,
        occurred_at=occurred_at,
        tz_name=tz_name,
        llm=providers.llm,
        aliases=list(user.aliases or []),
        profile=user.profile_text or "",
    )
    if draft.discard:
        if not manual:
            return None  # 合并后判断为无意义内容：保持原样，避免误删
        # 用户手动指定合并时尊重用户意图：退回规则摘要，仍然完成合并
        from .summarizer import _rule_extract

        draft = _rule_extract(merged_turns, occurred_at, tz_name)

    # ── 主卡片：更新为合并后的统一总结 ───────────────────────────────────
    primary.title = (draft.title or primary.title or "未命名对话")[:240]
    primary.summary = draft.summary or primary.summary
    primary.key_points = list(draft.key_points or primary.key_points or [])
    primary.people = list(draft.people or primary.people or [])
    primary.type = "conversation"
    primary.occurred_at = occurred_at
    primary.duration_s = float(merged_turns[-1].end - merged_turns[0].start)
    primary.user_turns = sum(1 for turn in merged_turns if turn.speaker == "user")
    primary.other_turns = sum(1 for turn in merged_turns if turn.speaker != "user")
    if not primary.audio_segment_id:
        primary.audio_segment_id = next(
            (card.audio_segment_id for card in cards if card.audio_segment_id), None
        )

    audio_ids: list[str] = []
    segments: list[dict] = []
    identities: list[dict] = []
    for card in cards:
        meta = card.source_meta or {}
        for segment_id in meta.get("audio_segment_ids") or (
            [card.audio_segment_id] if card.audio_segment_id else []
        ):
            if segment_id not in audio_ids:
                audio_ids.append(segment_id)
        for segment in meta.get("segments") or []:
            if isinstance(segment, dict) and segment.get("id") not in {s.get("id") for s in segments}:
                segments.append(segment)
        for identity in meta.get("identity") or []:
            if isinstance(identity, dict):
                identities.append({**identity, "card_id": card.id})
    now = now_utc()
    other_ids = [card.id for card in others]
    primary.source_meta = {
        **(primary.source_meta or {}),
        "audio_segment_ids": audio_ids,
        "segments": segments,
        "identity": identities,
        "merged": True,
        "merge_reason": reason,
        "merged_at": iso_utc(now),
        "merged_card_ids": other_ids,
        **(  # 标记来源：用户手动 / AI 复盘
            {
                "manual_merged": True,
                "manual_merge_reason": reason,
            }
            if manual
            else {
                "ai_merged": True,
                "ai_merge_reason": reason,
                "ai_merged_at": iso_utc(now),
                "ai_merged_card_ids": other_ids,
            }
        ),
    }
    primary.updated_at = now

    # ── 转写：删除原卡片的 turns，按合并时间线重建到主卡片 ───────────────
    for card in cards:
        for row in db.scalars(select(TranscriptTurn).where(TranscriptTurn.card_id == card.id)).all():
            db.delete(row)
    db.flush()
    for seq, turn in enumerate(merged_turns):
        db.add(
            TranscriptTurn(
                card_id=primary.id,
                seq=seq,
                start=turn.start,
                end=turn.end,
                speaker=turn.speaker,
                text=turn.text,
                words=turn.words,
            )
        )

    # ── 已产生的日程/待办/指令改挂到主卡片（不重新执行，避免重复推送）────
    for model in (ScheduleItem, Todo, Command):
        rows = db.scalars(select(model).where(model.card_id.in_(other_ids))).all()
        for row in rows:
            row.card_id = primary.id

    audit_note = "用户手动合并" if manual else "AI 复盘合并为同一对话"
    # ── 其余卡片软删除 + 审计 ────────────────────────────────────────────
    for card in others:
        before = {
            "title": card.title,
            "summary": card.summary,
            "occurred_at": card.occurred_at.isoformat(),
            "card_ids": [],
        }
        card.deleted_at = now
        db.add(
            AuditLog(
                user_id=user.id,
                action="card.merge",
                entity="card",
                entity_id=card.id,
                before=before,
                after={"merged_into": primary.id, "reason": reason},
                note=audit_note,
            )
        )
    db.add(
        AuditLog(
            user_id=user.id,
            action="card.merge",
            entity="card",
            entity_id=primary.id,
            before={"card_ids": [card.id for card in cards]},
            after={
                "merged_card_ids": other_ids,
                "reason": reason,
                "occurred_at": primary.occurred_at.isoformat(),
            },
            note=audit_note,
        )
    )
    user.profile_dirty = True
    db.flush()
    logger.info(
        "%s merged cards %s into %s (%s)",
        "manually" if manual else "AI",
        other_ids,
        primary.id,
        reason,
    )
    return {
        "primary_card_id": primary.id,
        "merged_card_ids": other_ids,
        "reason": reason,
        "turns": len(merged_turns),
        "manual": manual,
    }


def merge_cards_by_ids(
    user_id: str,
    card_ids: Sequence[str],
    providers: Providers | None = None,
    *,
    note: str = "",
) -> dict[str, Any]:
    """手动合并用户选中的卡片并重新总结（App「合并对话」入口）。

    与 AI 定时复盘不同，这里不做话题判断，直接按用户选择合并；但同样：
    保留最早卡片为主卡片、其余软删除、日程/待办/指令只改挂不重新执行、写审计日志。

    失败时抛 ``ValueError``：``too_few_cards`` / ``user_missing`` /
    ``card_not_found`` / ``card_deleted`` / ``cannot_merge_meeting_cards`` / ``merge_rejected``。
    """
    providers = providers or get_providers()
    ids = list(dict.fromkeys(str(card_id) for card_id in card_ids if str(card_id).strip()))
    if len(ids) < 2:
        raise ValueError("too_few_cards")
    reason = note.strip()[:300] or "用户手动合并"
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise ValueError("user_missing")
        cards: list[Card] = []
        for card_id in ids:
            card = db.get(Card, card_id)
            if card is None or card.user_id != user.id:
                raise ValueError("card_not_found")
            if card.deleted_at is not None:
                raise ValueError("card_deleted")
            cards.append(card)
        if any(card.meeting_id is not None or card.type == "meeting" for card in cards):
            raise ValueError("cannot_merge_meeting_cards")
        outcome = _merge_group(db, user, cards, reason, providers, manual=True)
    if outcome is None:
        raise ValueError("merge_rejected")
    return outcome


def merge_recent_cards(
    providers: Providers | None = None,
    *,
    user_id: str | None = None,
    lookback_s: float | None = None,
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """让 LLM 复盘近期新增卡片，把判定为同一对话的卡片合并。

    ``force=True`` 用于 CLI 手动执行（即使 ``OA_CARD_MERGE_ENABLED=false``）。
    ``dry_run=True`` 只返回 AI 判定的分组，不修改数据库。
    """
    providers = providers or get_providers()
    settings = get_settings()
    moment = now or now_utc()
    report: dict[str, Any] = {
        "enabled": bool(settings.card_merge_enabled),
        "candidates": 0,
        "groups": [],
        "merged": 0,
        "cards": [],
        "skipped": [],
    }
    if not force and not settings.card_merge_enabled:
        return report

    lookback = settings.card_merge_lookback_s if lookback_s is None else float(lookback_s)
    with session_scope() as db:
        if user_id:
            user = db.get(User, user_id)
            users = [user] if user is not None else []
        else:
            users = list(db.scalars(select(User)).all())

    for user in users:
        with session_scope() as db:
            user_db = db.get(User, user.id)
            if user_db is None:
                continue
            cards = _candidate_cards(db, user_db, lookback, moment)
            snapshots = [_card_snapshot(card, user_db.timezone or default_timezone()) for card in cards]
        if len(snapshots) < 2:
            continue
        report["candidates"] += len(snapshots)
        groups = _ask_llm(providers, user, snapshots, moment)
        snapshot_by_id = {item["id"]: item for item in snapshots}
        valid_groups: list[tuple[list[str], str]] = []
        for ids, reason in groups:
            points = [
                datetime.fromisoformat(snapshot_by_id[card_id]["occurred_ts"]) for card_id in ids
            ]
            span = (max(points) - min(points)).total_seconds()
            if span > settings.card_merge_max_span_s:
                report["skipped"].append(
                    {"card_ids": ids, "reason": f"span {span:.0f}s exceeds {settings.card_merge_max_span_s}s"}
                )
                continue
            valid_groups.append((ids, reason))
        report["groups"].extend({"card_ids": ids, "reason": reason} for ids, reason in valid_groups)
        if dry_run:
            continue
        for ids, reason in valid_groups:
            with session_scope() as db:
                user_db = db.get(User, user.id)
                cards = [db.get(Card, card_id) for card_id in ids]
                if user_db is None or any(
                    card is None or card.deleted_at is not None or card.user_id != user_db.id
                    for card in cards
                ):
                    report["skipped"].append({"card_ids": ids, "reason": "state_changed"})
                    continue
                try:
                    outcome = _merge_group(db, user_db, cards, reason, providers)
                except Exception:  # noqa: BLE001 - 单组失败不影响其他分组
                    logger.exception("AI card merge failed for %s", ids)
                    outcome = None
            if outcome is not None:
                report["merged"] += 1
                report["cards"].append(outcome)
    return report
