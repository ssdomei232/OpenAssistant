"""BLE GATT 协议编解码（纯标准库，不依赖 bless / 蓝牙硬件）。

职责：

* 把 App 分片写入的 CONFIG JSON 拼成完整对象（单次写入 ≤ 512 字节，
  以 ``}`` 结束且可解析即生效）；
* STATUS / QUEUE 的 JSON 编解码；
* QUEUE 命令校验与路由（``list`` / ``retry`` / ``clear``）。

所有函数都可以在没有蓝牙栈的环境里单元测试。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator, Mapping

__all__ = [
    "SERVICE_UUID",
    "CONFIG_CHAR_UUID",
    "STATUS_CHAR_UUID",
    "QUEUE_CHAR_UUID",
    "MAX_ATTRIBUTE_BYTES",
    "QUEUE_ACTIONS",
    "BleProtocolError",
    "ConfigAssembler",
    "decode_json",
    "encode_json",
    "iter_chunks",
    "parse_config_payload",
    "parse_queue_command",
    "route_queue_command",
    "status_payload",
]

# GATT 服务与特征 UUID（a001 服务；a002 配置；a003 状态；a004 队列）
SERVICE_UUID = "0000a001-0000-1000-8000-00805f9b34fb"
CONFIG_CHAR_UUID = "0000a002-0000-1000-8000-00805f9b34fb"
STATUS_CHAR_UUID = "0000a003-0000-1000-8000-00805f9b34fb"
QUEUE_CHAR_UUID = "0000a004-0000-1000-8000-00805f9b34fb"

# BLE 单次写入建议上限（App 需按此分片；超过则视为协议错误）
MAX_ATTRIBUTE_BYTES = 512
# 一个完整配置 JSON 的上限，防止内存被垃圾数据撑爆
MAX_CONFIG_BYTES = 16 * 1024

QUEUE_ACTIONS = ("list", "retry", "clear")


class BleProtocolError(ValueError):
    """BLE 载荷非法（非 JSON、超长、命令不支持等）。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message}


def encode_json(payload: Any) -> bytes:
    """紧凑 JSON 编码为 UTF-8 字节。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def iter_chunks(
    payload: bytes | bytearray | str, chunk_size: int = MAX_ATTRIBUTE_BYTES
) -> Iterator[bytes]:
    """把完整 JSON 切成 ≤ ``chunk_size`` 的 BLE 写入分片（App 侧同样逻辑）。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    for start in range(0, len(raw), chunk_size):
        yield raw[start : start + chunk_size]


def decode_json(payload: bytes | bytearray | str | Mapping[str, Any]) -> Any:
    """把 BLE 载荷解析为 Python 对象（合法 JSON 对象）。"""
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, (bytes, bytearray)):
        try:
            text = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BleProtocolError("invalid_utf8", str(exc)) from exc
    else:
        text = str(payload)
    text = text.strip("\x00").strip()
    if not text:
        raise BleProtocolError("empty_payload")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise BleProtocolError("invalid_json", str(exc)) from exc
    if not isinstance(data, dict):
        raise BleProtocolError("not_an_object", "payload must be a JSON object")
    return data


class ConfigAssembler:
    """CONFIG 特征的分片累加器。

    典型用法::

        assembler = ConfigAssembler()
        for chunk in app_chunks:
            data = assembler.feed(chunk)   # 未成帧返回 None
            if data is not None:
                apply(data)
    """

    def __init__(self, max_bytes: int = MAX_CONFIG_BYTES) -> None:
        self.max_bytes = int(max_bytes)
        self._buffer = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    @property
    def pending(self) -> bytes:
        return bytes(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, chunk: bytes | bytearray | str) -> dict[str, Any] | None:
        """追加一片数据；完整且可解析时返回配置 dict 并清空缓冲。"""
        if isinstance(chunk, str):
            raw = chunk.encode("utf-8")
        else:
            raw = bytes(chunk)
        if not raw:
            return None
        if len(self._buffer) + len(raw) > self.max_bytes:
            self.reset()
            raise BleProtocolError(
                "config_too_large", f"config exceeds {self.max_bytes} bytes"
            )
        self._buffer.extend(raw)

        stripped = bytes(self._buffer).rstrip()
        if not stripped.endswith(b"}"):
            return None
        try:
            text = bytes(self._buffer).decode("utf-8")
        except UnicodeDecodeError:
            # 多字节字符被分片截断，等待下一片
            return None
        try:
            data = json.loads(text)
        except ValueError:
            # 结束符已到但 JSON 仍不完整/非法：继续等（可能还有分片），
            # 超过上限时 feed 会抛 config_too_large。
            return None
        if not isinstance(data, dict):
            self.reset()
            raise BleProtocolError("not_an_object", "config must be a JSON object")
        self.reset()
        return data


def parse_config_payload(payload: bytes | bytearray | str | Mapping[str, Any]) -> dict[str, Any]:
    """把一次完整的 CONFIG 写入解析为 dict。"""
    return decode_json(payload)


def status_payload(
    *,
    device_id: str,
    wifi_connected: bool,
    queue: Mapping[str, Any],
    recording: bool,
    uptime_s: float,
    retry_max: int,
    offline_buffer: bool,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 STATUS 特征内容（契约字段固定，``extra`` 可附加调试信息）。"""
    payload: dict[str, Any] = {
        "device_id": device_id,
        "wifi_connected": bool(wifi_connected),
        "queue": dict(queue),
        "recording": bool(recording),
        "uptime_s": round(float(uptime_s), 1),
        "retry_max": int(retry_max),
        "offline_buffer": bool(offline_buffer),
    }
    if extra:
        payload.update(extra)
    return payload


def parse_queue_command(payload: bytes | bytearray | str | Mapping[str, Any]) -> dict[str, Any]:
    """解析并校验 QUEUE 命令：``{"action": "list|retry|clear"}``。"""
    data = decode_json(payload)
    action = data.get("action")
    if not isinstance(action, str) or action.strip().lower() not in QUEUE_ACTIONS:
        raise BleProtocolError(
            "invalid_action",
            f"action must be one of {', '.join(QUEUE_ACTIONS)}",
        )
    command: dict[str, Any] = {"action": action.strip().lower()}
    if "id" in data:
        try:
            command["id"] = int(data["id"])
        except (TypeError, ValueError) as exc:
            raise BleProtocolError("invalid_id", "id must be an integer") from exc
    if "limit" in data:
        try:
            command["limit"] = max(1, int(data["limit"]))
        except (TypeError, ValueError) as exc:
            raise BleProtocolError("invalid_limit", "limit must be an integer") from exc
    if "status" in data and data["status"] is not None:
        command["status"] = str(data["status"])
    return command


def route_queue_command(
    command: Mapping[str, Any],
    handlers: Mapping[str, Callable[[dict[str, Any]], Any]],
) -> dict[str, Any]:
    """把 QUEUE 命令路由到 handler，返回可写回 BLE 的结果 dict。"""
    action = str(command.get("action", "")).strip().lower()
    if action not in QUEUE_ACTIONS:
        return {"ok": False, "action": action, "error": "invalid_action"}
    handler = handlers.get(action)
    if handler is None:
        return {"ok": False, "action": action, "error": "unsupported_action"}
    try:
        result = handler(dict(command))
    except BleProtocolError as exc:
        return {"ok": False, "action": action, **exc.to_dict()}
    except Exception as exc:  # 队列操作失败也要回一个结构化结果
        return {
            "ok": False,
            "action": action,
            "error": "action_failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
    return {"ok": True, "action": action, "result": result}
