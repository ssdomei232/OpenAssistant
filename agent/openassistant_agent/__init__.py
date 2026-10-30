"""OpenAssistant Agent（树莓派录音端）。

采集 16-bit mono 16 kHz PCM -> VAD 切片 -> 本地 SQLite 队列 -> HTTPS 上传；
同时通过 BLE GATT 向 App 暴露 CONFIG / STATUS / QUEUE 管理通道。
"""

from __future__ import annotations

__version__ = "1.0.0"

from .config import AgentConfig, ConfigError, RecordConfig, UploadConfig, VadConfig

__all__ = [
    "__version__",
    "AgentConfig",
    "ConfigError",
    "RecordConfig",
    "UploadConfig",
    "VadConfig",
]
