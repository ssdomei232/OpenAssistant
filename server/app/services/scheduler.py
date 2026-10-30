"""Scheduled tasks: APScheduler runtime + durable definitions in the database."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from .. import prompts
from ..config import Settings, get_settings
from ..db import session_scope
from ..models import ScheduledJob, ScheduleItem, Todo, User
from ..providers import Providers, get_providers
from ..providers.llm import extract_json
from ..utils import now_utc
from .commands import execute_command
from .push_service import push_text
from .timeparse import to_local

logger = logging.getLogger(__name__)


def _tz(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Shanghai")


class SchedulerService:
    def __init__(self, settings: Settings | None = None, providers: Providers | None = None) -> None:
        self.settings = settings or get_settings()
        self.providers = providers or get_providers()
        self.scheduler = BackgroundScheduler()
        self._started = False

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        if not self._started:
            self.scheduler.start()
            self._started = True
        self.sync_from_db()

    def shutdown(self) -> None:
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False

    # ── persistence helpers ──────────────────────────────────────────────
    def sync_from_db(self) -> None:
        with session_scope() as db:
            jobs = db.scalars(select(ScheduledJob).where(ScheduledJob.enabled.is_(True))).all()
            for job in jobs:
                try:
                    self._schedule_in_runtime(job)
                except Exception:
                    logger.exception("failed to schedule job %s", job.id)

    def _schedule_in_runtime(self, job: ScheduledJob) -> None:
        trigger = None
        if job.cron:
            trigger = CronTrigger.from_crontab(job.cron, timezone=_tz(job.timezone or "Asia/Shanghai"))
        elif job.run_at:
            run_at = job.run_at.replace(tzinfo=ZoneInfo("UTC"))
            if run_at <= datetime.now(tz=ZoneInfo("UTC")):
                return
            trigger = DateTrigger(run_date=run_at, timezone=_tz(job.timezone or "Asia/Shanghai"))
        if trigger is None:
            return
        self.scheduler.add_job(
            _execute_scheduled_job,
            trigger=trigger,
            id=f"oa-job-{job.id}",
            replace_existing=True,
            args=[job.user_id, job.id],
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    def schedule_job(self, job: ScheduledJob) -> None:
        self.unschedule_job(job.id)
        if job.enabled:
            self._schedule_in_runtime(job)

    def unschedule_job(self, job_id: str) -> None:
        runtime_id = f"oa-job-{job_id}"
        try:
            if self.scheduler.get_job(runtime_id):
                self.scheduler.remove_job(runtime_id)
        except Exception:
            logger.debug("failed to remove runtime job %s", runtime_id, exc_info=True)

    def next_run_at(self, job_id: str) -> datetime | None:
        runtime = self.scheduler.get_job(f"oa-job-{job_id}")
        return getattr(runtime, "next_run_time", None) if runtime else None

    # ── execution ────────────────────────────────────────────────────────
    def run_now(self, job_id: str) -> dict:
        with session_scope() as db:
            job = db.get(ScheduledJob, job_id)
            if job is None:
                raise ValueError("job_not_found")
            return self._run_job(db, job)

    def _run_job(self, db, job: ScheduledJob) -> dict:
        user = db.get(User, job.user_id)
        if user is None:
            return {"status": "failed", "error": "user_missing"}
        tz_name = user.timezone or "Asia/Shanghai"
        now = now_utc()
        try:
            result_text = self._build_result(db, user, job, now, tz_name)
            if job.action == "command":
                command = execute_command(db, user, job.prompt, self.providers, base_utc=now)
                result_text = command.result
                pushed = command.pushed
            else:
                pushed = push_text(
                    db, user, result_text, self.providers, title=f"OpenAssistant · {job.name or '定时提醒'}"
                ).status in {"sent", "pending"}
            job.last_run_at = now
            job.last_result = result_text[:1000]
            db.flush()
            return {"status": "done", "result": result_text, "pushed": pushed}
        except Exception as exc:  # noqa: BLE001
            logger.exception("scheduled job %s failed", job.id)
            job.last_run_at = now
            job.last_result = f"失败：{type(exc).__name__}: {exc}"
            db.flush()
            return {"status": "failed", "error": str(exc)}

    def _build_result(self, db, user: User, job: ScheduledJob, now: datetime, tz_name: str) -> str:
        if job.action == "push":
            return job.prompt
        # action == agent: build context, ask LLM for a short markdown message
        start = now
        end = now + timedelta(days=2)
        events = db.scalars(
            select(ScheduleItem)
            .where(
                ScheduleItem.user_id == user.id,
                ScheduleItem.deleted_at.is_(None),
                ScheduleItem.start_at >= start - timedelta(hours=1),
                ScheduleItem.start_at <= end,
            )
            .order_by(ScheduleItem.start_at)
        ).all()
        todos = db.scalars(
            select(Todo)
            .where(Todo.user_id == user.id, Todo.deleted_at.is_(None), Todo.status == "pending")
            .order_by(Todo.due_at.is_(None), Todo.due_at)
            .limit(10)
        ).all()
        event_lines = [
            f"- {to_local(e.start_at, tz_name).strftime('%m-%d %H:%M')} {e.title}" for e in events
        ] or ["- 暂无日程"]
        todo_lines = [f"- {t.title}" for t in todos] or ["- 暂无待办"]
        context = f"当前时间：{to_local(now, tz_name).isoformat()}\n任务：{job.prompt}\n\n近期日程：\n" + "\n".join(
            event_lines
        ) + "\n\n未完成待办：\n" + "\n".join(todo_lines)

        if getattr(self.providers.llm, "available", False):
            try:
                raw = self.providers.llm.complete(
                    prompts.scheduled_task_system(user.aliases or [], user.profile_text or ""),
                    context,
                    temperature=0.4,
                    max_tokens=500,
                )
                data = extract_json(raw) or {}
                if data.get("markdown"):
                    return str(data["markdown"])[:800]
            except Exception:
                logger.warning("LLM scheduled task generation failed", exc_info=True)

        lines = [f"**{job.name or '定时提醒'}**", "", "**近期日程**", *event_lines, "", "**未完成待办**", *todo_lines]
        return "\n".join(lines)


def _execute_scheduled_job(user_id: str, job_id: str) -> None:
    """Top-level job entry point (kept module level for picklability)."""
    service = get_scheduler()
    with session_scope() as db:
        job = db.get(ScheduledJob, job_id)
        if job is None or not job.enabled:
            return
        if job.run_at and not job.cron:
            job.enabled = False
            db.flush()
        service._run_job(db, job)


_scheduler: SchedulerService | None = None


def get_scheduler() -> SchedulerService:
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerService()
    return _scheduler


def set_scheduler(service: SchedulerService | None) -> None:
    global _scheduler
    _scheduler = service


def send_due_reminders(providers: Providers, limit: int = 20) -> int:
    """Push reminders for schedule items whose remind_at has passed."""
    now = now_utc()
    sent = 0
    with session_scope() as db:
        rows = db.scalars(
            select(ScheduleItem)
            .where(
                ScheduleItem.deleted_at.is_(None),
                ScheduleItem.reminded.is_(False),
                ScheduleItem.remind_at.is_not(None),
                ScheduleItem.remind_at <= now,
                ScheduleItem.start_at >= now - timedelta(hours=2),
            )
            .order_by(ScheduleItem.remind_at)
            .limit(limit)
        ).all()
        for item in rows:
            user = db.get(User, item.user_id)
            if user is None:
                continue
            tz_name = user.timezone or "Asia/Shanghai"
            local = to_local(item.start_at, tz_name)
            text = f"**日程提醒**\n\n{local.strftime('%m-%d %H:%M')} {item.title}"
            if item.location:
                text += f"\n\n地点：{item.location}"
            if item.description:
                text += f"\n\n备注：{item.description}"
            push_text(db, user, text, providers, title="OpenAssistant · 日程提醒")
            item.reminded = True
            sent += 1
    return sent
