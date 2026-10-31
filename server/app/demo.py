"""Demo data seeder.

Produces a realistic, fully offline dataset so the App and API can be
explored without a GPU or API keys:

* a user with aliases, voiceprint, profile
* several audio segments processed through the real pipeline
  (identity labels, card, schedule, todos, commands, meeting minutes)
* a corrected card (勘误) and a deleted card (删除) with audit trail
* calendar entries, devices and a scheduled reminder job

Run with ``OA_DEMO_MODE=1 python -m app.cli seed-demo``.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import default_timezone, get_settings
from .models import AuditLog, AudioSegment, Card, Command, Device, Meeting, ScheduleItem, ScheduledJob, Todo, User
from .providers import get_providers
from .providers.speaker import SpectralEmbedding
from .security import authenticate, create_user
from .services.corrections import apply_correction, delete_card
from .services.entities import add_schedule, add_todo
from .services.pipeline import process_segment
from .services.storage import get_storage
from .services.timeparse import tzinfo_for
from .utils import encode_wav, now_utc, utc_from


def _tone_audio(seconds: float = 8.0, freqs: tuple[float, ...] = (180.0, 360.0, 540.0), sr: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    signal = np.zeros_like(t)
    for idx, freq in enumerate(freqs):
        signal += (0.45 / (idx + 1)) * np.sin(2 * math.pi * freq * t)
    # light amplitude modulation so the energy VAD sees speech-like islands
    envelope = 0.55 + 0.45 * np.sin(2 * math.pi * 2.2 * t) ** 2
    noise = np.random.default_rng(42).normal(0, 0.004, t.shape)
    return np.clip((signal * envelope + noise) * 0.6, -1.0, 1.0).astype(np.float32)


USER_AUDIO = _tone_audio(freqs=(180.0, 360.0, 540.0))
OTHER_AUDIO = _tone_audio(freqs=(300.0, 600.0, 900.0))


def _local_now(tz_name: str) -> datetime:
    # 先把「当前 UTC 时刻」转到用户本地时区（之前直接 replace 会把 UTC 时钟当成本地时间）
    return datetime.now(timezone.utc).astimezone(tzinfo_for(tz_name))


def _iso(aware: datetime) -> str:
    return aware.isoformat()


def _utc_naive(aware: datetime) -> datetime:
    return utc_from(aware) or now_utc()


def _find_or_create_user(db: Session, api_key: str | None) -> tuple[User, str | None]:
    settings = get_settings()
    user = None
    if api_key:
        user = authenticate(db, api_key)
    created_key = None
    if user is None and settings.bootstrap_api_key:
        user = authenticate(db, settings.bootstrap_api_key)
    if user is None:
        user = db.scalar(select(User).limit(1))
    if user is None:
        key = api_key or settings.bootstrap_api_key or None
        user, created_key = create_user(
            db,
            display_name="小明",
            api_key=key,
            aliases=["小明", "明明", "Xiao Ming"],
        )
        db.flush()
    elif api_key and not created_key:
        # make sure the caller knows the key it just supplied still works
        created_key = api_key
    return user, created_key


def _store_segment(
    db: Session,
    user: User,
    *,
    client_segment_id: str,
    recorded_at: datetime,
    demo_turns: list[dict],
    audio: np.ndarray,
    device_id: str = "pi-livingroom",
) -> AudioSegment:
    storage = get_storage()
    wav = encode_wav(audio, 16000)
    rel = storage.save(wav, suffix=".wav", prefix="demo")
    segment = AudioSegment(
        user_id=user.id,
        client_segment_id=client_segment_id,
        device_id=device_id,
        path=rel,
        duration_s=round(audio.size / 16000, 2),
        sample_rate=16000,
        channels=1,
        status="queued",
        meta={
            "client_segment_id": client_segment_id,
            "device_id": device_id,
            "recorded_at": _iso(recorded_at),
            "duration_s": round(audio.size / 16000, 2),
            "trigger": "auto",
            "demo_turns": demo_turns,
            "demo_audio": True,
        },
    )
    db.add(segment)
    db.flush()
    return segment


def _turns(items: list[tuple[float, float, str, str]]) -> list[dict]:
    return [{"start": a, "end": b, "speaker": sp, "text": text} for a, b, sp, text in items]


def _polish_meeting(db: Session, card_id: str | None, *, title: str, summary: str | None = None) -> None:
    """Give the rule-generated demo meetings a human friendly title."""
    if not card_id:
        return
    card = db.get(Card, card_id)
    if card is None:
        return
    card.title = title
    if summary:
        card.summary = summary
    if card.meeting_id:
        meeting = db.get(Meeting, card.meeting_id)
        if meeting is not None:
            meeting.title = title
            if summary:
                meeting.summary = summary
    db.flush()


def seed_demo(db: Session, api_key: str | None = None) -> dict:
    settings = get_settings()
    # demo transcripts are only honoured while demo mode is on
    settings.demo_mode = True
    user, created_key = _find_or_create_user(db, api_key)
    tz_name = user.timezone or default_timezone()
    now_local = _local_now(tz_name)
    providers = get_providers()
    report: dict = {"user_id": user.id, "api_key": created_key, "cards": [], "meetings": 0, "corrections": 0, "deleted": 0}

    # ── voiceprint ────────────────────────────────────────────────────────
    embedder = providers.speaker if getattr(providers.speaker, "name", "") != "spectral" else SpectralEmbedding()
    from .models import Voiceprint

    voiceprint = db.scalar(select(Voiceprint).where(Voiceprint.user_id == user.id))
    embedding = embedder.embed(USER_AUDIO, 16000)
    if voiceprint is None:
        voiceprint = Voiceprint(user_id=user.id, embedding=embedding, threshold=60.0, quality=0.86, sample_seconds=8.0)
        db.add(voiceprint)
    else:
        voiceprint.embedding = embedding
        voiceprint.threshold = 60.0
        voiceprint.quality = 0.86
        voiceprint.sample_seconds = 8.0
    db.flush()

    # ── scenario A: conversation with schedule / todo / search command ────
    a_time = now_local - timedelta(hours=3)
    a_turns = _turns(
        [
            (0.4, 5.0, "user", "帮我记一下明天下午三点和周磊在三号会议室讨论 Q3 排期。"),
            (5.4, 9.2, "other", "好的，那我把资料提前发给你，你看看还有没有要补充的。"),
            (9.6, 14.0, "user", "另外提醒我周五之前把预算表交给财务，别忘了。"),
            (14.4, 18.6, "user", "帮我查一下上海明天的天气怎么样，要不要带伞。"),
            (19.0, 22.0, "other", "我这边下午也有空，可以一起过一遍。"),
            (22.4, 25.0, "user", "那就这样定了，回头见。"),
        ]
    )
    seg_a = _store_segment(db, user, client_segment_id="demo-conv-q3-planning", recorded_at=a_time, demo_turns=a_turns, audio=USER_AUDIO)
    db.commit()
    result_a = process_segment(seg_a.id, providers)
    report["cards"].append(result_a.get("card_id"))

    # ── scenario B: 3-speaker meeting with minutes + schedule ──────────────
    b_time = now_local - timedelta(hours=26)
    b_turns = _turns(
        [
            (0.0, 28.0, "user", "今天主要过一下 Q3 的排期，看看各个模块的时间点能不能对上。"),
            (28.0, 70.0, "other", "我这边负责的推荐模块大概需要两周，六月中旬可以提测。"),
            (70.0, 112.0, "other", "数据侧要等埋点需求确认，我建议排到六月下旬。"),
            (112.0, 152.0, "user", "好，那我们就确定推荐模块六月中提测，数据侧六月下旬。"),
            (152.0, 196.0, "other", "那我这边再补一份详细的风险清单，周五之前发出来。"),
            (196.0, 232.0, "user", "可以，会议纪要记得发我一份。另外下周一上午十点去体检，记得带医保卡。"),
        ]
    )
    seg_b = _store_segment(db, user, client_segment_id="demo-meeting-q3-review", recorded_at=b_time, demo_turns=b_turns, audio=USER_AUDIO)
    db.commit()
    result_b = process_segment(seg_b.id, providers)
    report["cards"].append(result_b.get("card_id"))
    _polish_meeting(
        db,
        result_b.get("card_id"),
        title="Q3 排期评审会议",
        summary="对齐 Q3 各模块排期：推荐模块六月中旬提测，数据侧六月下旬；补充风险清单。",
    )

    # ── scenario C: media (video) that the user later corrects ────────────
    c_time = now_local - timedelta(days=2, hours=1)
    c_turns = _turns(
        [
            (0.0, 30.0, "user", "今天听了一个讲 AI 编程助手的节目，讲了几个开源模型的对比，挺有意思的。"),
            (30.0, 52.0, "user", "里面提到本地模型和云端模型的取舍，我觉得可以再研究一下。"),
        ]
    )
    seg_c = _store_segment(db, user, client_segment_id="demo-media-ai-video", recorded_at=c_time, demo_turns=c_turns, audio=USER_AUDIO)
    db.commit()
    result_c = process_segment(seg_c.id, providers)
    card_c = db.get(Card, result_c.get("card_id")) if result_c.get("card_id") else None
    if card_c is not None:
        apply_correction(
            db,
            user,
            card_c,
            {
                "title": "收看的 AI 编程助手视频（非对话）",
                "summary": "用户收看的流媒体视频声音：介绍 AI 编程助手与几个开源模型的对比。",
                "type": "media",
            },
            providers,
            note="这段其实是收看的视频声音，不是用户对话",
        )
        report["corrections"] += 1
        report["cards"].append(card_c.id)

    # ── scenario D: second meeting (2 speakers, 4 min) ────────────────────
    d_time = now_local - timedelta(days=2, hours=6)
    d_turns = _turns(
        [
            (0.0, 32.0, "other", "这次主要对齐一下客户侧的需求优先级。"),
            (32.0, 75.0, "user", "我认为支付流程的体验问题应该排第一，先做这部分。"),
            (75.0, 120.0, "other", "同意，那我来跟进支付模块的埋点数据，周三之前给你。"),
            (120.0, 150.0, "user", "好，决定把支付体验优化排进下个迭代，我来整理需求文档。"),
        ]
    )
    seg_d = _store_segment(db, user, client_segment_id="demo-meeting-customer-priority", recorded_at=d_time, demo_turns=d_turns, audio=OTHER_AUDIO)
    db.commit()
    result_d = process_segment(seg_d.id, providers)
    report["cards"].append(result_d.get("card_id"))
    _polish_meeting(
        db,
        result_d.get("card_id"),
        title="客户需求优先级对齐会",
        summary="确认支付体验优化为最高优先级，排入下个迭代；行动项与责任人已明确。",
    )

    # ── scenario E: reminder note ─────────────────────────────────────────
    e_time = now_local - timedelta(days=4)
    e_turns = _turns(
        [
            (0.0, 12.0, "user", "下下周一上午九点半去协和医院复诊，提醒我带上次的检查报告。"),
        ]
    )
    seg_e = _store_segment(db, user, client_segment_id="demo-note-hospital", recorded_at=e_time, demo_turns=e_turns, audio=USER_AUDIO)
    db.commit()
    result_e = process_segment(seg_e.id, providers)
    report["cards"].append(result_e.get("card_id"))

    # ── scenario F: card that the user deletes (cascade demo) ─────────────
    f_time = now_local - timedelta(days=1, hours=2)
    f_turns = _turns(
        [
            (0.0, 18.0, "user", "下周五晚上六点团建，在楼下的烧烤店，记得早点走。"),
            (18.0, 24.0, "other", "好嘞，那我来订位置。"),
        ]
    )
    seg_f = _store_segment(db, user, client_segment_id="demo-team-building", recorded_at=f_time, demo_turns=f_turns, audio=USER_AUDIO)
    db.commit()
    result_f = process_segment(seg_f.id, providers)
    card_f = db.get(Card, result_f.get("card_id")) if result_f.get("card_id") else None
    if card_f is not None:
        delete_card(db, user, card_f, providers, note="团建取消，内容作废")
        report["deleted"] += 1

    # ── calendar / todo / devices / scheduled jobs ────────────────────────
    existing = db.scalars(
        select(ScheduleItem).where(ScheduleItem.user_id == user.id, ScheduleItem.source == "manual")
    ).first()
    if existing is None:
        add_schedule(db, user, title="团队周会", start_at=_utc_naive(now_local - timedelta(days=1, hours=4)), end_at=_utc_naive(now_local - timedelta(days=1, hours=3)), location="线上腾讯会议", source="manual")
        add_schedule(db, user, title="健身房训练", start_at=_utc_naive(now_local + timedelta(hours=4)), end_at=_utc_naive(now_local + timedelta(hours=5)), location="楼下健身房", source="manual")
        add_schedule(db, user, title="季度述职", start_at=_utc_naive(now_local + timedelta(days=1, hours=2)), end_at=_utc_naive(now_local + timedelta(days=1, hours=3)), location="公司 5F 大会议室", source="manual")
        add_schedule(db, user, title="和朋友吃饭", start_at=_utc_naive(now_local + timedelta(days=3, hours=11)), end_at=_utc_naive(now_local + timedelta(days=3, hours=13)), location="外滩某餐厅", source="manual")
    if db.scalars(
        select(Todo).where(Todo.user_id == user.id, Todo.source == "manual")
    ).first() is None:
        add_todo(db, user, title="整理本周复盘笔记", due_at=_utc_naive(now_local + timedelta(days=1)), source="manual")
        add_todo(db, user, title="给张伟发 Q3 排期表", due_at=_utc_naive(now_local + timedelta(days=2)), source="manual", priority="high")

    for device in (
        {"id": "pi-livingroom", "name": "客厅树莓派"},
        {"id": "pi-desk", "name": "书桌录音端"},
    ):
        row = db.get(Device, device["id"])
        if row is None:
            db.add(
                Device(
                    id=device["id"],
                    user_id=user.id,
                    name=device["name"],
                    config={
                        "wifi": {"ssid": "home-5g", "password_enc": ""},
                        "retry_max": 5,
                        "offline_buffer": True,
                        "upload_endpoint": "http://192.168.1.10:8000/api/v1",
                        "vad": {"aggressiveness": 2, "min_speech_ms": 300},
                    },
                    last_seen=now_utc(),
                )
            )

    job = db.scalar(select(ScheduledJob).where(ScheduledJob.user_id == user.id, ScheduledJob.name == "晚间日程提醒"))
    if job is None:
        job = ScheduledJob(
            user_id=user.id,
            name="晚间日程提醒",
            cron="0 21 * * *",
            timezone=tz_name,
            prompt="汇总明天的日程和未完成待办，用钉钉提醒我。",
            action="agent",
            enabled=True,
        )
        db.add(job)
    db.flush()

    from .services.scheduler import get_scheduler

    try:
        get_scheduler().run_now(job.id)
    except Exception:
        pass

    db.add(AuditLog(user_id=user.id, action="demo.seed", entity="system", entity_id="demo", after={"cards": len([c for c in report["cards"] if c])}))
    db.commit()

    report["meetings"] = len(db.scalars(select(Meeting).where(Meeting.user_id == user.id, Meeting.deleted_at.is_(None))).all())
    report["cards"] = [card_id for card_id in report["cards"] if card_id]
    report["schedule_items"] = len(db.scalars(select(ScheduleItem).where(ScheduleItem.user_id == user.id, ScheduleItem.deleted_at.is_(None))).all())
    report["todos"] = len(db.scalars(select(Todo).where(Todo.user_id == user.id, Todo.deleted_at.is_(None))).all())
    report["commands"] = len(db.scalars(select(Command).where(Command.user_id == user.id)).all())
    report["profile"] = user.profile_text
    report["api_key"] = report["api_key"] or "（沿用已有用户，请使用其原有 API Key）"
    return report
