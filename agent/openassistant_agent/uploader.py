"""上传器：按 Server API 契约把 spool 中的片段 POST 到 ``/audio/segments``。

要点：

* multipart 字段 ``file`` + ``meta``（JSON 字符串），带 ``X-API-Key``；
* 幂等：Server 对重复 ``client_segment_id`` 返回 200/``duplicate``，视为成功；
* 失败按指数退避 + 抖动重试，``retry_max`` 后置 ``failed`` 但保留文件；
* 上传前用 ``GET {server}/health`` 探测网络；离线时只入队不上传，
  ``offline_buffer=false`` 时按 ``offline_policy`` 丢弃或保留（默认保留）；
* HTTP 客户端优先 ``httpx``，缺失时退回标准库 ``urllib.request``；
* 所有网络调用都可注入 :class:`MockTransport`，测试不触网。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import AgentConfig, UploadConfig
from .spool import SpoolItem, SpoolStore

__all__ = [
    "HttpTransport",
    "MockTransport",
    "UploadResult",
    "Uploader",
    "build_meta",
    "build_multipart",
    "join_url",
    "http_request_json",
]

logger = logging.getLogger(__name__)

_OK_STATUS = (200, 201, 202)
_SUCCESS_STATUS = (200, 201, 202, 409)
_RETRYABLE_STATUS = (408, 425, 429, 500, 502, 503, 504)


@dataclass
class UploadResult:
    ok: bool = False
    status_code: int | None = None
    duplicate: bool = False
    permanent: bool = False
    error: str | None = None
    body: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "duplicate": self.duplicate,
            "permanent": self.permanent,
            "error": self.error,
            "body": self.body,
        }


# ---------------------------------------------------------------------------
# URL / HTTP 工具
# ---------------------------------------------------------------------------


def join_url(base: str, path: str) -> str:
    """拼接 server_url 与 API 路径，容忍 server_url 已包含 ``/api/v1``。"""
    base = (base or "").strip().rstrip("/")
    path = "/" + (path or "").strip().lstrip("/")
    for prefix in ("/api/v1", "/api"):
        if base.endswith(prefix) and path.startswith(prefix + "/"):
            path = path[len(prefix) :]
            break
    return base + path


def _health_url(base: str, health_endpoint: str) -> str:
    root = (base or "").strip().rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    endpoint = health_endpoint or "/health"
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return root + endpoint


def _request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int | None, bytes | None, str | None]:
    """底层 HTTP 请求：优先 httpx，缺失/失败时退回 urllib。

    返回 ``(status_code, body, error)``；网络错误时 status 为 ``None``。
    """
    request_headers = dict(headers or {})
    try:
        import httpx  # type: ignore
    except ModuleNotFoundError:
        httpx = None  # type: ignore[assignment]

    if httpx is not None:
        try:
            with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
                response = client.request(method, url, content=body, headers=request_headers)
            return response.status_code, response.content, None
        except Exception as exc:  # 网络/协议错误
            logger.debug("httpx request failed: %s", exc)
            return None, None, f"{type(exc).__name__}: {exc}"

    try:
        request = urllib.request.Request(
            url, data=body, headers=request_headers, method=method.upper()
        )
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            return int(response.status), response.read(), None
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
        except Exception:  # pragma: no cover
            payload = None
        return int(exc.code), payload, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"


def http_request_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> UploadResult:
    """发送 JSON 请求并解析响应（用于设备配置回传等辅助接口）。"""
    body = None
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    status, raw, error = _request(
        method, url, body=body, headers=request_headers, timeout=timeout
    )
    parsed = _parse_json(raw)
    if status is None:
        return UploadResult(ok=False, error=error or "network_error")
    if 200 <= status < 300:
        return UploadResult(ok=True, status_code=status, body=parsed)
    permanent = status not in _RETRYABLE_STATUS
    return UploadResult(
        ok=False,
        status_code=status,
        permanent=permanent,
        error=f"http_{status}",
        body=parsed,
    )


def _parse_json(raw: bytes | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def build_multipart(
    meta: Mapping[str, Any],
    file_bytes: bytes,
    *,
    filename: str = "segment.wav",
    content_type: str = "audio/wav",
    meta_field: str = "meta",
    file_field: str = "file",
) -> tuple[bytes, str]:
    """构造 multipart/form-data 请求体，返回 ``(body, content_type_header)``。"""
    boundary = "----OpenAssistant" + uuid.uuid4().hex
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    chunks: list[bytes] = []

    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{meta_field}"\r\n'
            "Content-Type: application/json; charset=utf-8\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(meta_json)
    chunks.append(b"\r\n")

    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def build_meta(item: SpoolItem, config: AgentConfig) -> dict[str, Any]:
    """组装 API 契约中的 ``meta`` JSON。"""
    vad = item.vad or {}
    ratio = item.speech_ratio
    spans = vad.get("segments") if isinstance(vad, dict) else None
    if not isinstance(spans, list):
        spans = []
    return {
        "client_segment_id": item.client_segment_id,
        "device_id": config.device_id,
        "recorded_at": item.recorded_at,
        "duration_s": round(float(item.duration_s), 3),
        "sample_rate": int(config.record.sample_rate),
        "channels": int(config.record.channels),
        "sha256": item.sha256,
        "vad": {
            "speech_ratio": round(float(ratio), 4),
            "segments": [
                [round(float(span[0]), 3), round(float(span[1]), 3)]
                for span in spans
                if isinstance(span, (list, tuple)) and len(span) == 2
            ],
        },
        "trigger": item.trigger if item.trigger in ("auto", "manual") else "auto",
    }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class HttpTransport:
    """真实 HTTP 传输（httpx 优先，urllib 兜底）。"""

    def __init__(
        self,
        server_url: str,
        api_key: str = "",
        *,
        endpoint: str = "/api/v1/audio/segments",
        health_endpoint: str = "/health",
        timeout_s: float = 30.0,
    ) -> None:
        self.server_url = server_url
        self.api_key = api_key
        self.endpoint = endpoint
        self.health_endpoint = health_endpoint
        self.timeout_s = float(timeout_s)

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def health(self) -> bool:
        url = _health_url(self.server_url, self.health_endpoint)
        try:
            status, _raw, error = _request(
                "GET", url, headers=self.headers, timeout=min(self.timeout_s, 5.0)
            )
        except Exception:  # pragma: no cover - 极端网络错误
            return False
        if error is not None:
            return False
        return status is not None and 200 <= status < 300

    def post_segment(
        self,
        meta: Mapping[str, Any],
        file_path: str | Path,
        *,
        filename: str | None = None,
    ) -> UploadResult:
        path = Path(file_path)
        try:
            file_bytes = path.read_bytes()
        except OSError as exc:
            return UploadResult(
                ok=False, permanent=True, error=f"file_unreadable: {exc}"
            )
        body, content_type = build_multipart(
            meta, file_bytes, filename=filename or path.name
        )
        headers = {**self.headers, "Content-Type": content_type}
        url = join_url(self.server_url, self.endpoint)
        status, raw, error = _request(
            "POST", url, body=body, headers=headers, timeout=self.timeout_s
        )
        parsed = _parse_json(raw)
        if status is None:
            return UploadResult(ok=False, error=error or "network_error")
        if status in _SUCCESS_STATUS:
            duplicate = bool(parsed.get("duplicate")) if parsed else status == 409
            return UploadResult(ok=True, status_code=status, duplicate=duplicate, body=parsed)
        permanent = status not in _RETRYABLE_STATUS
        return UploadResult(
            ok=False,
            status_code=status,
            permanent=permanent,
            error=_error_message(parsed) or f"http_{status}",
            body=parsed,
        )

    def put_json(self, path: str, payload: Mapping[str, Any]) -> UploadResult:
        return http_request_json(
            "PUT",
            join_url(self.server_url, path),
            payload=payload,
            headers=self.headers,
            timeout=self.timeout_s,
        )

    def close(self) -> None:
        """无持久连接。"""


class MockTransport:
    """测试用传输：记录调用，可按脚本返回状态/异常，禁止真实网络。"""

    def __init__(
        self,
        *,
        healthy: bool = True,
        responses: list[Any] | None = None,
        health_results: list[bool] | None = None,
        always_raise: Exception | None = None,
    ) -> None:
        self.healthy = healthy
        self.responses = list(responses or [])
        self.health_results = list(health_results or [])
        self.always_raise = always_raise
        self.calls: list[dict[str, Any]] = []
        self.health_calls: list[float] = []
        self.config_pushes: list[dict[str, Any]] = []

    def health(self) -> bool:
        self.health_calls.append(time.time())
        if self.always_raise is not None:
            raise self.always_raise
        if self.health_results:
            return bool(self.health_results.pop(0))
        return self.healthy

    def post_segment(
        self,
        meta: Mapping[str, Any],
        file_path: str | Path,
        *,
        filename: str | None = None,
    ) -> UploadResult:
        path = Path(file_path)
        self.calls.append(
            {
                "meta": dict(meta),
                "path": str(path),
                "filename": filename or path.name,
                "size": path.stat().st_size if path.exists() else 0,
                "sha256": sha256_file(path) if path.exists() else None,
            }
        )
        if self.always_raise is not None:
            raise self.always_raise
        if not self.responses:
            return UploadResult(ok=True, status_code=202, body={"status": "queued"})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, int):
            return UploadResult(
                ok=item in _SUCCESS_STATUS,
                status_code=item,
                permanent=item not in _SUCCESS_STATUS and item not in _RETRYABLE_STATUS,
                error=None if item in _SUCCESS_STATUS else f"http_{item}",
            )
        if isinstance(item, UploadResult):
            return item
        raise TypeError(f"unsupported mock response: {item!r}")

    def put_json(self, path: str, payload: Mapping[str, Any]) -> UploadResult:
        self.config_pushes.append({"path": path, "payload": dict(payload)})
        if self.always_raise is not None:
            raise self.always_raise
        return UploadResult(ok=True, status_code=200, body={"saved": True})

    def close(self) -> None:
        """无操作。"""


def _error_message(body: Mapping[str, Any] | None) -> str | None:
    if not body:
        return None
    detail = body.get("detail")
    message = body.get("message")
    if detail and message:
        return f"{detail}: {message}"
    if detail:
        return str(detail)
    if message:
        return str(message)
    return None


# ---------------------------------------------------------------------------
# Uploader
# ---------------------------------------------------------------------------


class Uploader:
    """把 spool 队列按批上传，处理重试/离线/幂等。"""

    def __init__(
        self,
        spool: SpoolStore,
        transport: Any,
        config: AgentConfig,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        online_ttl_s: float = 15.0,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.spool = spool
        self.transport = transport
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self.online_ttl_s = float(online_ttl_s)
        self.log = logger_ or logger
        self._online: bool | None = None
        self._checked_at = 0.0
        self.last_error: str | None = None

    # -- 网络探测 --------------------------------------------------------
    @property
    def upload_config(self) -> UploadConfig:
        return self.config.upload

    @property
    def online(self) -> bool:
        return bool(self._online)

    def probe_online(self, *, force: bool = False) -> bool:
        """探测 Server 是否可达（带 TTL 缓存，避免每批都发健康检查）。"""
        now = self.clock()
        if not force and self._online is not None and now - self._checked_at < self.online_ttl_s:
            return self._online
        try:
            online = bool(self.transport.health())
        except Exception as exc:  # 探测失败视为离线
            self.log.debug("health probe failed: %s", exc)
            online = False
        if online != self._online:
            if online:
                self.log.info("server reachable again: %s", self.config.server_url)
            else:
                self.log.warning("server unreachable, entering offline mode: %s", self.config.server_url)
        self._online = online
        self._checked_at = now
        return online

    def is_online(self, *, force: bool = False) -> bool:
        return self.probe_online(force=force)

    # -- 上传 ------------------------------------------------------------
    def upload_item(self, item: SpoolItem) -> UploadResult:
        """上传单条记录，并按结果推进状态机。"""
        upload_config = self.config.upload
        path = Path(item.path)
        if not path.exists():
            result = UploadResult(ok=False, permanent=True, error="file_missing")
            self.spool.mark_failed(
                item.id,
                result.error,
                retry_max=1,
                retry_base_s=upload_config.retry_base_s,
                retry_max_s=upload_config.retry_max_s,
                permanent=True,
            )
            self.log.error("spool file missing for %s: %s", item.client_segment_id, path)
            return result

        try:
            actual = sha256_file(path)
        except OSError as exc:  # pragma: no cover - 权限等
            actual = ""
            self.log.error("cannot hash %s: %s", path, exc)
        if item.sha256 and actual and actual != item.sha256:
            error = f"sha256_mismatch: expected {item.sha256[:12]}, got {actual[:12]}"
            self.spool.mark_failed(
                item.id,
                error,
                retry_max=1,
                retry_base_s=upload_config.retry_base_s,
                retry_max_s=upload_config.retry_max_s,
                permanent=True,
            )
            self.log.error("segment %s corrupted: %s", item.client_segment_id, error)
            return UploadResult(ok=False, permanent=True, error=error)

        meta = build_meta(item, self.config)
        try:
            result = self.transport.post_segment(meta, path, filename=path.name)
        except Exception as exc:
            result = UploadResult(ok=False, error=f"{type(exc).__name__}: {exc}")
            # 网络异常 -> 立刻离线，避免整批空转
            self._online = False
            self._checked_at = self.clock()

        if result.ok:
            self.spool.mark_uploaded(item.id)
            self.last_error = None
            self.log.info(
                "uploaded %s (%.2fs, %s%s)",
                item.client_segment_id,
                item.duration_s,
                f"http {result.status_code}",
                ", duplicate" if result.duplicate else "",
            )
            return result

        self.last_error = result.error
        permanent = result.permanent
        self.spool.mark_failed(
            item.id,
            result.error,
            retry_max=1 if permanent else upload_config.retry_max,
            retry_base_s=upload_config.retry_base_s,
            retry_max_s=upload_config.retry_max_s,
            permanent=permanent,
        )
        self.log.warning(
            "upload failed for %s: %s%s",
            item.client_segment_id,
            result.error,
            " (permanent)" if permanent else "",
        )
        return result

    def _mark_offline(self) -> None:
        self._online = False
        self._checked_at = self.clock()

    def upload_pending(self, limit: int | None = None) -> dict[str, Any]:
        """上传一批 ``pending`` 记录（不超过 batch_size）。"""
        upload_config = self.config.upload
        batch = int(limit or upload_config.batch_size)
        summary: dict[str, Any] = {
            "attempted": 0,
            "uploaded": 0,
            "failed": 0,
            "duplicates": 0,
            "offline": False,
            "skipped_offline": 0,
            "dropped_offline": 0,
            "pending": 0,
            "online": bool(self._online),
        }

        if not self.probe_online(force=True):
            summary["offline"] = True
            summary["online"] = False
            pending = self.spool.stats()["pending"]
            summary["pending"] = pending
            summary["skipped_offline"] = pending
            self._handle_offline_backlog()
            return summary

        items = self.spool.claim_pending(limit=batch)
        for index, item in enumerate(items):
            summary["attempted"] += 1
            result = self.upload_item(item)
            if result.ok:
                summary["uploaded"] += 1
                if result.duplicate:
                    summary["duplicates"] += 1
                continue
            summary["failed"] += 1
            if not result.permanent and self._online is False:
                # 网络中断：本轮到此为止，剩余记录放回 pending
                self._mark_offline()
                remaining = [rest.id for rest in items[index + 1 :]]
                if remaining:
                    self.spool.release_uploading(remaining)
                break

        stats = self.spool.stats()
        summary["pending"] = stats["pending"]
        summary["online"] = bool(self._online)
        summary["offline"] = not bool(self._online)
        return summary

    def flush(self, *, max_rounds: int = 5, limit: int | None = None) -> dict[str, Any]:
        """反复上传直到没有到期的 pending 或达到轮次上限。"""
        total: dict[str, Any] = {
            "rounds": 0,
            "attempted": 0,
            "uploaded": 0,
            "failed": 0,
            "duplicates": 0,
            "offline": False,
            "pending": 0,
        }
        for _ in range(max(1, int(max_rounds))):
            summary = self.upload_pending(limit=limit)
            total["rounds"] += 1
            for key in ("attempted", "uploaded", "failed", "duplicates"):
                total[key] += summary[key]
            total["offline"] = summary["offline"]
            total["pending"] = summary["pending"]
            total["online"] = summary["online"]
            if summary["offline"] or summary["attempted"] == 0 or summary["uploaded"] == 0:
                break
        return total

    # -- 离线策略 --------------------------------------------------------
    def _handle_offline_backlog(self) -> None:
        upload_config = self.config.upload
        if upload_config.offline_buffer:
            self.log.debug("offline: %s segment(s) buffered locally", self.spool.stats()["pending"])
            return
        if upload_config.offline_policy == "drop":
            dropped = 0
            for item in self.spool.list("pending", limit=10000):
                if self.spool.delete(item.id, delete_file=True):
                    dropped += 1
            self.log.warning(
                "offline_buffer=false and offline_policy=drop: discarded %s segment(s)",
                dropped,
            )
        else:
            self.log.warning(
                "offline_buffer=false: keeping %s pending segment(s) on disk (policy=keep)",
                self.spool.stats()["pending"],
            )

    # -- 配置热更新 ------------------------------------------------------
    def update_config(self, config: AgentConfig) -> None:
        """BLE 写入新配置后同步：必要时重建 HTTP 传输并重置在线缓存。"""
        self.config = config
        if isinstance(self.transport, HttpTransport):
            self.transport = HttpTransport(
                config.server_url,
                config.api_key,
                endpoint=config.upload.endpoint,
                health_endpoint=config.upload.health_endpoint,
                timeout_s=config.upload.timeout_s,
            )
        self._online = None
        self._checked_at = 0.0
        self.log.info("uploader config updated (device_id=%s)", config.device_id)

    # -- 设备配置备份 ----------------------------------------------------
    def push_config(self, payload: Mapping[str, Any]) -> UploadResult:
        """BLE CONFIG 写入后回传 Server 备份（``PUT /devices/{id}/config``）。"""
        if not self.config.server_url or not self.config.api_key:
            self.log.info("server_url/api_key not configured, skip config backup")
            return UploadResult(ok=False, permanent=True, error="not_configured")
        path = f"/api/v1/devices/{self.config.device_id}/config"
        try:
            result = self.transport.put_json(path, payload)
        except Exception as exc:
            return UploadResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if result.ok:
            self.log.info("device config backed up to server")
        else:
            self.log.warning("device config backup failed: %s", result.error)
        return result

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:  # pragma: no cover - 关闭失败不影响退出
            pass
