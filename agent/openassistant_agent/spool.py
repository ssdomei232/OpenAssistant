"""本地持久化队列（SQLite）。

每个待上传片段一条记录，断电/重启不丢队列：

* 入队时状态 ``pending``；
* 上传前 ``claim_pending`` 原子地把状态改为 ``uploading``（避免重复上传）；
* 成功后 ``uploaded``；失败按指数退避写 ``next_attempt_at``，超过
  ``retry_max`` 标记 ``failed``（**文件保留**，可由 BLE QUEUE retry 重试）；
* 进程启动时 ``reset_uploading`` 把上次中断的 ``uploading`` 恢复成 ``pending``。
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__all__ = ["SpoolItem", "SpoolStore", "resolve_db_path"]

logger = logging.getLogger(__name__)

STATUSES = ("pending", "uploading", "uploaded", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    client_segment_id TEXT    NOT NULL UNIQUE,
    recorded_at       TEXT    NOT NULL,
    duration_s        REAL    NOT NULL DEFAULT 0,
    path              TEXT    NOT NULL,
    sha256            TEXT    NOT NULL DEFAULT '',
    speech_ratio      REAL    NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'pending',
    attempts          INTEGER NOT NULL DEFAULT 0,
    next_attempt_at   REAL    NOT NULL DEFAULT 0,
    last_error        TEXT,
    created_at        REAL    NOT NULL,
    updated_at        REAL    NOT NULL,
    uploaded_at       REAL,
    trigger           TEXT    NOT NULL DEFAULT 'auto',
    vad_json          TEXT
);
CREATE INDEX IF NOT EXISTS idx_segments_status ON segments(status, next_attempt_at, id);
CREATE INDEX IF NOT EXISTS idx_segments_created ON segments(created_at);
"""


def resolve_db_path(path: str | Path) -> Path:
    """``spool_path`` 可以是目录（用其中的 spool.db）或直接的 .db 文件。"""
    target = Path(path)
    if target.suffix.lower() == ".db":
        db_path = target
    else:
        db_path = target / "spool.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


@dataclass
class SpoolItem:
    id: int
    client_segment_id: str
    recorded_at: str
    duration_s: float
    path: str
    sha256: str
    speech_ratio: float = 0.0
    status: str = "pending"
    attempts: int = 0
    next_attempt_at: float = 0.0
    last_error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    uploaded_at: float | None = None
    trigger: str = "auto"
    vad: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "client_segment_id": self.client_segment_id,
            "recorded_at": self.recorded_at,
            "duration_s": self.duration_s,
            "path": self.path,
            "sha256": self.sha256,
            "speech_ratio": self.speech_ratio,
            "status": self.status,
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "uploaded_at": self.uploaded_at,
            "trigger": self.trigger,
            "vad": self.vad,
            "file_size": self.file_size,
        }

    @property
    def file_size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)


class SpoolStore:
    """SQLite 队列。每个操作使用独立连接，可跨线程（BLE 线程 + 主循环）。"""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = resolve_db_path(path)
        self.root = self.db_path.parent
        self._clock = clock
        self._jitter = jitter or (lambda: random.uniform(0.6, 1.0))
        self._init_schema()

    # -- 基础设施 --------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def now(self) -> float:
        return float(self._clock())

    def close(self) -> None:
        """无长连接；保留接口便于统一生命周期管理。"""

    def __enter__(self) -> "SpoolStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 入队 ------------------------------------------------------------
    def enqueue(
        self,
        client_segment_id: str,
        recorded_at: str,
        duration_s: float,
        path: str | Path,
        sha256: str,
        *,
        speech_ratio: float = 0.0,
        vad: dict[str, Any] | None = None,
        status: str = "pending",
        trigger: str = "auto",
        next_attempt_at: float | None = None,
    ) -> SpoolItem:
        """入队；同一 ``client_segment_id`` 重复入队返回已有记录（幂等）。"""
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        if trigger not in ("auto", "manual"):
            raise ValueError(f"invalid trigger: {trigger}")
        existing = self.get_by_client_id(client_segment_id)
        if existing is not None:
            return existing

        import json as _json

        now = self.now
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO segments (
                    client_segment_id, recorded_at, duration_s, path, sha256,
                    speech_ratio, status, attempts, next_attempt_at,
                    created_at, updated_at, trigger, vad_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    client_segment_id,
                    recorded_at,
                    float(duration_s),
                    str(path),
                    sha256,
                    float(speech_ratio),
                    status,
                    now if next_attempt_at is None else float(next_attempt_at),
                    now,
                    now,
                    trigger,
                    _json.dumps(vad, ensure_ascii=False) if vad else None,
                ),
            )
            item_id = int(cursor.lastrowid)
        item = self.get(item_id)
        assert item is not None
        return item

    # -- 查询 ------------------------------------------------------------
    def get(self, item_id: int) -> SpoolItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM segments WHERE id = ?", (item_id,)).fetchone()
        return _row_to_item(row) if row is not None else None

    def get_by_client_id(self, client_segment_id: str) -> SpoolItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM segments WHERE client_segment_id = ?", (client_segment_id,)
            ).fetchone()
        return _row_to_item(row) if row is not None else None

    def list(
        self,
        status: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SpoolItem]:
        if status is not None and status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        query = "SELECT * FROM segments"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id ASC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def pending(self, limit: int = 100) -> list[SpoolItem]:
        return self.list("pending", limit=limit)

    def failed(self, limit: int = 100) -> list[SpoolItem]:
        return self.list("failed", limit=limit)

    # -- 上传状态机 ------------------------------------------------------
    def claim_pending(self, limit: int = 1, *, now: float | None = None) -> list[SpoolItem]:
        """原子领取到期的 pending 记录并置为 uploading。"""
        moment = self.now if now is None else float(now)
        limit = max(1, int(limit))
        claimed: list[SpoolItem] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM segments
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at ASC, id ASC LIMIT ?
                """,
                (moment, limit),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE segments SET status = 'uploading', updated_at = ? WHERE id = ?",
                    (moment, row["id"]),
                )
            conn.commit()
        for row in rows:
            item = _row_to_item(row)
            item.status = "uploading"
            claimed.append(item)
        return claimed

    def mark_uploading(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE segments SET status = 'uploading', updated_at = ? WHERE id = ?",
                (self.now, item_id),
            )

    def release_uploading(self, item_ids: Iterable[int]) -> int:
        """把已领取但未处理的 uploading 记录放回 pending（网络中断时用）。"""
        ids = [int(item_id) for item_id in item_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE segments
                SET status = 'pending', next_attempt_at = 0, updated_at = ?
                WHERE status = 'uploading' AND id IN ({placeholders})
                """,
                (self.now, *ids),
            )
            return int(cursor.rowcount or 0)

    def mark_uploaded(self, item_id: int, *, now: float | None = None) -> None:
        moment = self.now if now is None else float(now)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE segments
                SET status = 'uploaded', uploaded_at = ?, updated_at = ?,
                    last_error = NULL, next_attempt_at = 0
                WHERE id = ?
                """,
                (moment, moment, item_id),
            )

    def mark_failed(
        self,
        item_id: int,
        error: str | None,
        *,
        retry_max: int = 5,
        retry_base_s: float = 1.0,
        retry_max_s: float = 300.0,
        permanent: bool = False,
        now: float | None = None,
    ) -> SpoolItem | None:
        """记录一次失败：未达 ``retry_max`` 回到 pending 并指数退避。"""
        moment = self.now if now is None else float(now)
        item = self.get(item_id)
        if item is None:
            return None
        attempts = item.attempts + 1
        exhausted = permanent or retry_max <= 0 or attempts >= int(retry_max)
        status = "failed" if exhausted else "pending"
        if exhausted:
            next_at = 0.0
        else:
            delay = min(float(retry_base_s) * (2 ** (attempts - 1)), float(retry_max_s))
            next_at = moment + delay * max(0.0, float(self._jitter()))
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE segments
                SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, attempts, next_at, error, moment, item_id),
            )
        updated = self.get(item_id)
        if updated is not None and status == "failed":
            logger.warning(
                "segment %s marked failed after %s attempts: %s",
                updated.client_segment_id,
                attempts,
                error,
            )
        return updated

    def retry(self, item_id: int | None = None, *, statuses: Sequence[str] = ("failed",)) -> int:
        """手动重试：把 failed（或指定状态）重置为 pending。返回受影响行数。"""
        now = self.now
        with self._connect() as conn:
            if item_id is not None:
                cursor = conn.execute(
                    """
                    UPDATE segments
                    SET status = 'pending', attempts = 0, next_attempt_at = ?,
                        last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, item_id),
                )
            else:
                placeholders = ",".join("?" for _ in statuses)
                cursor = conn.execute(
                    f"""
                    UPDATE segments
                    SET status = 'pending', attempts = 0, next_attempt_at = ?,
                        last_error = NULL, updated_at = ?
                    WHERE status IN ({placeholders})
                    """,
                    (now, now, *statuses),
                )
            return int(cursor.rowcount or 0)

    def reset_uploading(self) -> int:
        """启动时对账：上次进程中断留下的 uploading 全部恢复为 pending。"""
        now = self.now
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE segments
                SET status = 'pending', next_attempt_at = 0, updated_at = ?
                WHERE status = 'uploading'
                """,
                (now,),
            )
            count = int(cursor.rowcount or 0)
        if count:
            logger.info("recovered %s interrupted upload(s) back to pending", count)
        return count

    # -- 清理 ------------------------------------------------------------
    def delete(self, item_id: int, *, delete_file: bool = True) -> bool:
        item = self.get(item_id)
        if item is None:
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM segments WHERE id = ?", (item_id,))
        if delete_file:
            _remove_file(item.path)
        return True

    def purge_uploaded(self, *, older_than_s: float | None = None, now: float | None = None) -> int:
        """删除已上传记录及其音频文件（可只清理早于某时间的）。"""
        moment = self.now if now is None else float(now)
        with self._connect() as conn:
            if older_than_s is None:
                rows = conn.execute("SELECT * FROM segments WHERE status = 'uploaded'").fetchall()
            else:
                cutoff = moment - float(older_than_s)
                rows = conn.execute(
                    "SELECT * FROM segments WHERE status = 'uploaded' AND uploaded_at <= ?",
                    (cutoff,),
                ).fetchall()
            ids = [row["id"] for row in rows]
            for item_id in ids:
                conn.execute("DELETE FROM segments WHERE id = ?", (item_id,))
        for row in rows:
            _remove_file(row["path"])
        return len(rows)

    # -- 统计 ------------------------------------------------------------
    def stats(self, *, now: float | None = None) -> dict[str, Any]:
        moment = self.now if now is None else float(now)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n, COALESCE(SUM(duration_s), 0) AS d FROM segments GROUP BY status"
            ).fetchall()
            pending_rows = conn.execute(
                "SELECT path FROM segments WHERE status IN ('pending', 'uploading')"
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM segments WHERE status = 'pending'"
            ).fetchone()[0]
        counts = {status: 0 for status in STATUSES}
        durations = {status: 0.0 for status in STATUSES}
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
            durations[str(row["status"])] = float(row["d"] or 0.0)
        pending_bytes = 0
        for row in pending_rows:
            try:
                pending_bytes += os.path.getsize(row["path"])
            except OSError:
                pass
        return {
            "total": sum(counts.values()),
            **counts,
            "pending_bytes": pending_bytes,
            "duration_s": round(sum(durations.values()), 3),
            "oldest_pending_at": oldest,
            "next_pending_at": self._next_pending_at(moment),
        }

    def _next_pending_at(self, moment: float) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(next_attempt_at) FROM segments WHERE status = 'pending'"
            ).fetchone()
        value = row[0] if row is not None else None
        if value is None:
            return None
        return max(0.0, float(value) - moment)

    def __len__(self) -> int:
        return int(self.stats()["total"])

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"SpoolStore({self.db_path})"


def _row_to_item(row: sqlite3.Row) -> SpoolItem:
    import json as _json

    keys = set(row.keys())
    vad: dict[str, Any] = {}
    if "vad_json" in keys and row["vad_json"]:
        try:
            decoded = _json.loads(row["vad_json"])
            if isinstance(decoded, dict):
                vad = decoded
        except ValueError:  # pragma: no cover - 数据损坏时忽略
            vad = {}
    return SpoolItem(
        id=int(row["id"]),
        client_segment_id=str(row["client_segment_id"]),
        recorded_at=str(row["recorded_at"]),
        duration_s=float(row["duration_s"]),
        path=str(row["path"]),
        sha256=str(row["sha256"]),
        speech_ratio=float(row["speech_ratio"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        next_attempt_at=float(row["next_attempt_at"]),
        last_error=row["last_error"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        uploaded_at=row["uploaded_at"],
        trigger=(row["trigger"] if "trigger" in keys and row["trigger"] else "auto"),
        vad=vad,
    )


def _remove_file(path: str | Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - 权限等
        logger.warning("cannot delete spool file %s: %s", path, exc)
