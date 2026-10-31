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
from .card_merge import merge_recent_cards
from .pipeline import (
    flush_transcribed_conversations,
    pending_segment_ids,
    requeue_stuck_segments,
    transcribe_segment,
)
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
        self._card_merge_last_s = 0.0
        self.last_card_merge_at: datetime | None = None
        self.last_card_merge_result: dict | None = None

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
            "card_merge": {
                "enabled": bool(self.settings.card_merge_enabled),
                "interval_s": self.settings.card_merge_interval_s,
                "lookback_s": self.settings.card_merge_lookback_s,
                "last_run_at": iso_utc(self.last_card_merge_at),
                "last_merged": int((self.last_card_merge_result or {}).get("merged", 0)),
            },
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
        # 两阶段：先转写新片段，再统一总结「等待窗口已到、时间相近」的对话。
        # 转写阶段不阻塞在“等待”上，因此等待期间新的音频仍能及时转写。
        result = {"transcribed": 0, "processed": 0, "cards": 0, "pushed": 0, "reminded": 0, "profiles": 0}
        for segment_id in pending_segment_ids(limit=3):
            outcome = transcribe_segment(segment_id, self.providers)
            if outcome.get("status") in {"transcribed", "done", "failed"}:
                result["transcribed"] += 1
        for summary in flush_transcribed_conversations(self.providers):
            result["cards"] += 1 if summary.get("card_id") else 0
            result["processed"] += int(summary.get("segments", 1))
        result["pushed"] = self._drain_push()
        result["reminded"] = send_due_reminders(self.providers)
        ai_merge = self._maybe_ai_merge()
        if ai_merge is not None:
            result["ai_merged"] = int(ai_merge.get("merged", 0))
        result["profiles"] = self._refresh_dirty_profiles()
        self.ticks += 1
        self.processed_total += int(result["processed"])
        self.last_tick_at = now_utc()
        self.last_tick_s = time.time()
        return result

    def _maybe_ai_merge(self) -> dict | None:
        """按 ``OA_CARD_MERGE_INTERVAL_S`` 节流地让 AI 复盘并合并近期卡片。"""
        if not self.settings.card_merge_enabled:
            return None
        now_s = time.time()
        if now_s - self._card_merge_last_s < max(1.0, self.settings.card_merge_interval_s):
            return None
        self._card_merge_last_s = now_s
        try:
            outcome = merge_recent_cards(self.providers)
        except Exception:  # noqa: BLE001 - 合并失败不影响录音主流程
            logger.exception("AI card merge tick failed")
            return None
        self.last_card_merge_at = now_utc()
        self.last_card_merge_result = outcome
        if outcome.get("merged"):
            logger.info(
                "AI card merge: %s group(s), %s card(s) merged",
                len(outcome.get("groups") or []),
                outcome.get("merged"),
            )
        return outcome

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
