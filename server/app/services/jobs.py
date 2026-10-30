"""Background worker: audio pipeline, push retries, reminders, profile refresh."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import User
from ..providers import Providers, get_providers
from ..utils import iso_utc, now_utc
from .pipeline import pending_segment_ids, process_segment, requeue_stuck_segments
from .profile import refresh_profile
from .push_service import drain_outbox
from .scheduler import SchedulerService, get_scheduler, send_due_reminders

logger = logging.getLogger(__name__)


class BackgroundWorker:
    """进程内后台工作线程：音频管道、推送重试、提醒、画像刷新。

    设计为单实例（一个 Server 进程一个 worker）。启动时会：
    * 把上次进程遗留的 ``processing`` 片段重新入队（服务重启恢复）；
    * 打印明确的启动日志，便于确认 worker 是否真的在跑；
    * 提供 :meth:`status` 供 ``GET /health`` 展示存活状态与最近 tick 时间。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        providers: Providers | None = None,
        scheduler: SchedulerService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.providers = providers or get_providers()
        self.scheduler = scheduler or get_scheduler()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self.started_at = time.time()
        self.last_tick_at: datetime | None = None
        self.last_tick_s: float = 0.0
        self.last_error: str | None = None
        self.processed_total = 0
        self.ticks = 0

    # ── lifecycle ────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.alive:
            return
        self._stop.clear()
        recovered = requeue_stuck_segments()
        if recovered:
            logger.warning("requeued %s stuck 'processing' segments after restart", recovered)
        self._thread = threading.Thread(target=self._loop, name="openassistant-worker", daemon=True)
        self._thread.start()
        logger.info(
            "background worker started: poll=%.2fs asr=%s diarization=%s",
            self.settings.job_poll_seconds,
            getattr(self.providers.asr, "name", "?"),
            getattr(self.providers.diarization, "name", "?"),
        )

    def start_watchdog(self, interval_s: float = 30.0) -> None:
        """监控 worker 存活：线程意外退出时自动重启，长时间无 tick 时告警。"""
        if self._watchdog is not None and self._watchdog.is_alive():
            return

        def _watch() -> None:
            while not self._stop.wait(interval_s):
                if not self.alive:
                    logger.error("background worker is not running; restarting it")
                    try:
                        self.start()
                    except Exception:
                        logger.exception("failed to restart background worker")
                    continue
                stale_for = time.time() - self.last_tick_s if self.last_tick_s else 0
                if stale_for > 120:
                    logger.warning(
                        "background worker has not completed a tick for %.0fs "
                        "(a slow model load/inference may be blocking the pipeline; "
                        "queued segments will be processed afterwards)",
                        stale_for,
                    )

        self._watchdog = threading.Thread(target=_watch, name="openassistant-worker-watchdog", daemon=True)
        self._watchdog.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def status(self) -> dict:
        return {
            "enabled": bool(self.settings.worker_enabled),
            "alive": self.alive,
            "thread": self._thread.name if self._thread is not None else None,
            "uptime_s": round(time.time() - self.started_at, 1),
            "ticks": self.ticks,
            "processed_total": self.processed_total,
            "last_tick_at": iso_utc(self.last_tick_at),
            "last_tick_age_s": round(time.time() - self.last_tick_s, 1) if self.last_tick_s else None,
            "poll_seconds": self.settings.job_poll_seconds,
            "last_error": self.last_error,
        }

    # ── loop ─────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - worker 必须持续运行
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("background worker tick failed")
            self._stop.wait(max(0.2, self.settings.job_poll_seconds))

    def tick(self) -> dict:
        result = {"processed": 0, "pushed": 0, "reminded": 0, "profiles": 0}
        for segment_id in pending_segment_ids(limit=3):
            outcome = process_segment(segment_id, self.providers)
            if outcome.get("status") in {"done", "failed"}:
                result["processed"] += 1
        result["pushed"] = self._drain_push()
        result["reminded"] = send_due_reminders(self.providers)
        result["profiles"] = self._refresh_dirty_profiles()
        self.ticks += 1
        self.processed_total += int(result["processed"])
        self.last_tick_at = now_utc()
        self.last_tick_s = time.time()
        return result

    def _drain_push(self) -> int:
        with session_scope() as db:
            return drain_outbox(db, self.providers)

    def _refresh_dirty_profiles(self) -> int:
        count = 0
        with session_scope() as db:
            users = db.scalars(select(User).where(User.profile_dirty.is_(True))).all()
            for user in users:
                refresh_profile(db, user, self.providers, reason="dirty")
                count += 1
        return count
