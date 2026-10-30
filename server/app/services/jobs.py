"""Background worker: audio pipeline, push retries, reminders, profile refresh."""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import User
from ..providers import Providers, get_providers
from .pipeline import pending_segment_ids, process_segment
from .profile import refresh_profile
from .push_service import drain_outbox
from .scheduler import SchedulerService, get_scheduler, send_due_reminders

logger = logging.getLogger(__name__)


class BackgroundWorker:
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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="openassistant-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    # ── loop ─────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
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
