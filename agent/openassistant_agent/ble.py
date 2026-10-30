"""BLE 管理通道。

服务 ``0000a001-...``：

* ``a002`` CONFIG（read/write）：UTF-8 JSON 配置，分片追加写；
* ``a003`` STATUS（read/notify）：运行状态 JSON；
* ``a004`` QUEUE（read/write）：``{"action": "list|retry|clear"}``。

实现分两层：

* :class:`BleManager` —— 与传输无关的业务逻辑（配置合并/落盘/回传 Server、
  状态构造、队列命令执行）；
* :class:`BlessBleServer` —— 基于可选包 ``bless`` 的 GATT Server（Tree莓派
  BlueZ / Windows 均可），缺少 bless 或蓝牙栈时不影响其他功能；
* :class:`MockBleTransport` —— 内存传输，用于无蓝牙环境的测试和自检。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import threading
import time
from typing import Any, Callable, Mapping

from . import ble_protocol as proto
from .ble_protocol import (
    CONFIG_CHAR_UUID,
    QUEUE_CHAR_UUID,
    SERVICE_UUID,
    STATUS_CHAR_UUID,
    BleProtocolError,
    ConfigAssembler,
    encode_json,
    parse_queue_command,
    route_queue_command,
    status_payload,
)
from .config import AgentConfig, ConfigError
from .spool import SpoolStore

__all__ = [
    "BleManager",
    "BleUnavailableError",
    "BlessBleServer",
    "MockBleTransport",
    "SERVICE_UUID",
    "CONFIG_CHAR_UUID",
    "STATUS_CHAR_UUID",
    "QUEUE_CHAR_UUID",
]

logger = logging.getLogger(__name__)


class BleUnavailableError(RuntimeError):
    """蓝牙栈 / bless 不可用。"""


class BleManager:
    """BLE 服务端业务逻辑，可完全脱离蓝牙硬件测试。"""

    def __init__(
        self,
        *,
        config: AgentConfig,
        spool: SpoolStore,
        status_provider: Callable[[], Mapping[str, Any]] | None = None,
        uploader: Any | None = None,
        on_config_applied: Callable[[AgentConfig], None] | None = None,
        config_saver: Callable[[AgentConfig], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.spool = spool
        self.status_provider = status_provider
        self.uploader = uploader
        self.on_config_applied = on_config_applied
        self.config_saver = config_saver
        self.clock = clock
        self.assembler = ConfigAssembler()
        self.last_queue_result: dict[str, Any] = {}
        self._started_at = clock()

    # -- CONFIG ----------------------------------------------------------
    def handle_config_write(self, chunk: bytes | bytearray | str) -> dict[str, Any] | None:
        """处理一片 CONFIG 写入；成帧后返回处理结果，否则 ``None``。"""
        try:
            payload = self.assembler.feed(chunk)
        except BleProtocolError as exc:
            logger.warning("invalid config chunk: %s", exc)
            return {**exc.to_dict(), "saved": False}
        if payload is None:
            return None
        return self.apply_config(payload)

    def apply_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """应用完整配置：校验 -> 更新运行时 -> 落盘 -> 回传 Server。"""
        candidate = self.config.copy()
        changed = candidate.merge_dict(payload, allow_unknown=True)
        try:
            candidate.validate()
        except ConfigError as exc:
            logger.warning("rejected BLE config: %s", exc)
            return {"ok": False, "error": "invalid_config", "message": str(exc), "saved": False}

        self.config = candidate
        if self.on_config_applied is not None:
            try:
                self.on_config_applied(candidate)
            except Exception:  # pragma: no cover - 回调不应影响 BLE 响应
                logger.exception("on_config_applied callback failed")
        # 上传器持有配置引用，需要同步为新配置（device_id / server_url / 重试参数）
        if self.uploader is not None:
            try:
                update = getattr(self.uploader, "update_config", None)
                if callable(update):
                    update(candidate)
                elif hasattr(self.uploader, "config"):
                    self.uploader.config = candidate
            except Exception:  # pragma: no cover
                logger.debug("cannot update uploader config", exc_info=True)

        save_error: str | None = None
        try:
            if self.config_saver is not None:
                saved_to = self.config_saver(candidate)
            else:
                saved_to = candidate.save()
            logger.info("BLE config persisted to %s", saved_to)
        except Exception as exc:  # 权限不足等，运行时配置仍然生效
            save_error = f"{type(exc).__name__}: {exc}"
            logger.warning("cannot persist BLE config: %s", save_error)

        backup: dict[str, Any] | None = None
        if self.uploader is not None:
            try:
                result = self.uploader.push_config(self._device_config_payload(payload, candidate))
                backup = result.to_dict()
            except Exception as exc:  # pragma: no cover - 备份失败不影响本地配置
                backup = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        return {
            "ok": True,
            "saved": save_error is None,
            "save_error": save_error,
            "changed": changed,
            "config": candidate.to_dict(),
            "server_backup": backup,
        }

    @staticmethod
    def _device_config_payload(
        incoming: Mapping[str, Any], config: AgentConfig
    ) -> dict[str, Any]:
        """按 API 契约（PUT /devices/{id}/config）整理回传内容。"""
        payload: dict[str, Any] = {
            "retry_max": config.upload.retry_max,
            "offline_buffer": config.upload.offline_buffer,
            "upload_endpoint": config.upload.endpoint,
            "vad": {
                "aggressiveness": config.vad.aggressiveness,
                "min_speech_ms": config.vad.min_speech_ms,
                "min_silence_ms": config.vad.min_silence_ms,
                "max_segment_s": config.vad.max_segment_s,
                "preroll_s": config.vad.preroll_s,
                "postroll_s": config.vad.postroll_s,
            },
            "device_id": config.device_id,
        }
        wifi = incoming.get("wifi")
        if isinstance(wifi, Mapping):
            payload["wifi"] = dict(wifi)
        return payload

    def read_config(self) -> bytes:
        return encode_json(self.config.to_dict())

    # -- STATUS ----------------------------------------------------------
    def build_status(self) -> dict[str, Any]:
        stats = self.spool.stats()
        snapshot: dict[str, Any] = {
            "device_id": self.config.device_id,
            "wifi_connected": bool(getattr(self.uploader, "online", False)),
            "queue": stats,
            "recording": False,
            "uptime_s": round(self.clock() - self._started_at, 1),
            "retry_max": self.config.upload.retry_max,
            "offline_buffer": self.config.upload.offline_buffer,
        }
        if self.status_provider is not None:
            try:
                provided = dict(self.status_provider())
            except Exception:  # pragma: no cover - 状态回调异常
                logger.exception("status_provider failed")
                provided = {}
            for key in snapshot:
                if key in provided:
                    snapshot[key] = provided[key]
        # 只保留契约字段，控制 notify 载荷大小（BLE MTU 有限）
        return status_payload(
            device_id=str(snapshot["device_id"]),
            wifi_connected=bool(snapshot["wifi_connected"]),
            queue=dict(snapshot["queue"]),
            recording=bool(snapshot["recording"]),
            uptime_s=float(snapshot["uptime_s"]),
            retry_max=int(snapshot["retry_max"]),
            offline_buffer=bool(snapshot["offline_buffer"]),
        )

    def read_status(self) -> bytes:
        return encode_json(self.build_status())

    # -- QUEUE -----------------------------------------------------------
    def handle_queue_write(self, payload: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
        try:
            command = parse_queue_command(payload)
        except BleProtocolError as exc:
            result = {**exc.to_dict(), "ok": False}
            self.last_queue_result = result
            return result
        result = route_queue_command(command, self._queue_handlers())
        self.last_queue_result = result
        return result

    def _queue_handlers(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        return {
            "list": self._queue_list,
            "retry": self._queue_retry,
            "clear": self._queue_clear,
        }

    def _queue_list(self, command: dict[str, Any]) -> dict[str, Any]:
        status = command.get("status")
        limit = int(command.get("limit", 20))
        items = self.spool.list(status if status in ("pending", "uploading", "uploaded", "failed") else None, limit=limit)
        return {
            "items": [item.to_dict() for item in items],
            "stats": self.spool.stats(),
        }

    def _queue_retry(self, command: dict[str, Any]) -> dict[str, Any]:
        item_id = command.get("id")
        statuses = ("failed",)
        requested = command.get("status")
        if requested in ("failed", "pending", "uploaded"):
            statuses = (requested,)
        count = self.spool.retry(item_id, statuses=statuses)
        return {"retried": count, "stats": self.spool.stats()}

    def _queue_clear(self, command: dict[str, Any]) -> dict[str, Any]:
        item_id = command.get("id")
        if item_id is not None:
            removed = 1 if self.spool.delete(int(item_id), delete_file=True) else 0
        elif command.get("status") == "failed":
            removed = 0
            for item in self.spool.failed(limit=10000):
                if self.spool.delete(item.id, delete_file=True):
                    removed += 1
        else:
            # 默认只清理已上传记录（保留待上传/失败项）
            removed = self.spool.purge_uploaded()
        return {"removed": removed, "stats": self.spool.stats()}


# ---------------------------------------------------------------------------
# Mock 传输（无蓝牙环境）
# ---------------------------------------------------------------------------


class MockBleTransport:
    """内存 BLE 传输：直接调用 :class:`BleManager`，可模拟 App 行为。"""

    def __init__(self, manager: BleManager, *, device_name: str = "OpenAssistant") -> None:
        self.manager = manager
        self.device_name = device_name
        self.notifications: list[bytes] = []
        self.config_writes: list[dict[str, Any]] = []

    def write_config(self, payload: bytes | str | Mapping[str, Any]) -> dict[str, Any] | None:
        """整包写入（内部按 512 字节分片，模拟 BLE 写入）。"""
        raw = payload if isinstance(payload, Mapping) else None
        if raw is not None:
            data = encode_json(raw)
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            data = bytes(payload)
        result: dict[str, Any] | None = None
        for chunk in proto.iter_chunks(data):
            result = self.manager.handle_config_write(chunk)
        if result is not None:
            self.config_writes.append(result)
        return result

    def write_config_chunk(self, chunk: bytes | str) -> dict[str, Any] | None:
        result = self.manager.handle_config_write(chunk)
        if result is not None:
            self.config_writes.append(result)
        return result

    def read_config(self) -> dict[str, Any]:
        return json.loads(self.manager.read_config().decode("utf-8"))

    def read_status(self) -> dict[str, Any]:
        return json.loads(self.manager.read_status().decode("utf-8"))

    def write_queue(self, payload: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
        return self.manager.handle_queue_write(payload)

    def read_queue(self) -> dict[str, Any]:
        return dict(self.manager.last_queue_result)

    def notify_status(self) -> dict[str, Any]:
        payload = encode_json(self.manager.build_status())
        self.notifications.append(payload)
        return json.loads(payload.decode("utf-8"))


# ---------------------------------------------------------------------------
# bless GATT Server
# ---------------------------------------------------------------------------


class BlessBleServer:
    """基于 ``bless`` 的 GATT Server（可选依赖）。

    ``bless`` 或系统蓝牙栈不可用时 :meth:`run` 抛
    :class:`BleUnavailableError`，调用方可降级为 mock 或仅运行录音。
    """

    def __init__(
        self,
        manager: BleManager,
        *,
        device_name: str = "OpenAssistant",
        adapter: str | None = None,
        notify_interval_s: float = 10.0,
    ) -> None:
        self.manager = manager
        self.device_name = device_name
        self.adapter = adapter
        self.notify_interval_s = float(notify_interval_s)
        self.server: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_notify = 0.0

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("bless") is not None

    def run(self, stop_event: threading.Event | None = None) -> None:
        """阻塞运行 GATT Server（在独立线程中调用）。"""
        if not self.available():
            raise BleUnavailableError(
                "bless is not installed; install 'bless' or use MockBleTransport"
            )
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._serve(stop_event))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
                self._loop = None

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: None)

    async def _serve(self, stop_event: threading.Event | None) -> None:
        from bless import (  # type: ignore
            GATTAttributePermissions,
            GATTCharacteristicProperties,
            BlessServer,
        )

        server = BlessServer(name=self.device_name, loop=asyncio.get_event_loop())
        self.server = server

        async def read_request(characteristic: Any, **kwargs: Any) -> bytes:
            uuid = str(getattr(characteristic, "uuid", "")).lower()
            if uuid == CONFIG_CHAR_UUID.lower():
                return self._safe_read(self.manager.read_config)
            if uuid == STATUS_CHAR_UUID.lower():
                return self._safe_read(self.manager.read_status)
            if uuid == QUEUE_CHAR_UUID.lower():
                return encode_json(self.manager.last_queue_result or {"ok": True})
            return b""

        async def write_request(characteristic: Any, **kwargs: Any) -> None:
            uuid = str(getattr(characteristic, "uuid", "")).lower()
            value = bytes(getattr(characteristic, "value", b"") or b"")
            if uuid == CONFIG_CHAR_UUID.lower():
                result = self.manager.handle_config_write(value)
                if result is not None:
                    characteristic.value = encode_json(result)
            elif uuid == QUEUE_CHAR_UUID.lower():
                result = self.manager.handle_queue_write(value)
                characteristic.value = encode_json(result)

        server.read_request_func = read_request
        server.write_request_func = write_request

        await server.add_new_service(SERVICE_UUID)
        permissions = (
            GATTAttributePermissions.readable | GATTAttributePermissions.writeable
        )
        config_flags = (
            GATTCharacteristicProperties.read
            | GATTCharacteristicProperties.write
            | GATTCharacteristicProperties.write_without_response
        )
        status_flags = GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify
        queue_flags = (
            GATTCharacteristicProperties.read
            | GATTCharacteristicProperties.write
            | GATTCharacteristicProperties.write_without_response
        )
        await server.add_new_characteristic(
            SERVICE_UUID, CONFIG_CHAR_UUID, config_flags, None, permissions
        )
        await server.add_new_characteristic(
            SERVICE_UUID, STATUS_CHAR_UUID, status_flags, None, permissions
        )
        await server.add_new_characteristic(
            SERVICE_UUID, QUEUE_CHAR_UUID, queue_flags, None, permissions
        )
        server.get_characteristic(CONFIG_CHAR_UUID).value = self.manager.read_config()
        server.get_characteristic(STATUS_CHAR_UUID).value = self.manager.read_status()
        server.get_characteristic(QUEUE_CHAR_UUID).value = encode_json({"ok": True})

        await server.start()
        logger.info("BLE GATT server started as %r", self.device_name)
        try:
            while stop_event is None or not stop_event.is_set():
                await asyncio.sleep(min(self.notify_interval_s, 0.5))
                if self.notify_interval_s > 0:
                    await self._maybe_notify(server)
        finally:
            await server.stop()
            logger.info("BLE GATT server stopped")

    async def _maybe_notify(self, server: Any) -> None:
        """按间隔刷新并发送 STATUS notify（浏览器/App 订阅后可见）。"""
        now = time.monotonic()
        last = getattr(self, "_last_notify", 0.0)
        if now - last < self.notify_interval_s:
            return
        self._last_notify = now
        try:
            server.get_characteristic(STATUS_CHAR_UUID).value = self.manager.read_status()
            await server.update_value(SERVICE_UUID, STATUS_CHAR_UUID)
        except Exception:  # pragma: no cover - 无订阅者时可能报错
            logger.debug("status notify failed", exc_info=True)

    @staticmethod
    def _safe_read(fn: Callable[[], bytes]) -> bytes:
        try:
            return fn()
        except Exception:  # pragma: no cover - 读取失败返回错误 JSON
            logger.exception("BLE characteristic read failed")
            return encode_json({"ok": False, "error": "read_failed"})
