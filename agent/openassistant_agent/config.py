"""Agent 配置系统。

合并顺序（后者覆盖前者）：

1. 内置默认值
2. TOML 配置文件（默认 ``/etc/openassistant/agent.toml``，可用 ``--config`` /
   ``OA_CONFIG`` 指定）
3. 环境变量（``OA_*``，也兼容 App 端使用的 ``OA_RETRY_MAX`` /
   ``OA_OFFLINE_BUFFER`` 等短名）
4. 显式参数（``AgentConfig.load(..., overrides={...})`` 或 BLE 写入）

配置对象同时提供 ``to_json`` / ``from_json``，供 BLE CONFIG 特征读写。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # Python >= 3.11
    import tomllib as _tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 回退
    try:
        import tomli as _tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        _tomllib = None  # type: ignore[assignment]

__all__ = [
    "AgentConfig",
    "ConfigError",
    "RecordConfig",
    "UploadConfig",
    "VadConfig",
    "default_config_path",
    "default_data_dir",
    "load_config",
]

APP_NAME = "openassistant"
SAMPLE_RATES = (8000, 16000, 32000, 48000)


class ConfigError(ValueError):
    """配置非法或无法读写。"""


def default_config_path() -> Path:
    """默认配置文件路径：POSIX 为 /etc/openassistant/agent.toml。"""
    env = os.environ.get("OA_CONFIG_PATH")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":  # pragma: no cover - Windows 开发机
        base = os.environ.get("PROGRAMDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME / "agent.toml"
    return Path("/etc") / APP_NAME / "agent.toml"


def default_data_dir() -> Path:
    """默认数据目录（spool.db、音频片段、日志）。"""
    env = os.environ.get("OA_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":  # pragma: no cover - Windows 开发机
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return Path("/var/lib") / APP_NAME


# ---------------------------------------------------------------------------
# 子配置
# ---------------------------------------------------------------------------


@dataclass
class RecordConfig:
    """录音参数。DJI Mic Mini 作为 USB 麦克风（UAC）输入。"""

    device: str = "DJI Mic Mini"
    sample_rate: int = 16000
    channels: int = 1
    block_ms: int = 30
    source: str = "device"  # device | file | synthetic
    input_path: str = ""
    # PortAudio/ALSA 缓冲：'high' 约 200ms，抗调度抖动；也可写秒数（如 0.2）
    latency: str = "high"
    # 回调线程 → 主线程的块队列长度（max 块数）。30ms/块时 32 块 ≈ 0.96s 余量
    input_queue_blocks: int = 32

    @property
    def block_frames(self) -> int:
        return max(1, int(self.sample_rate * self.block_ms / 1000))

    @property
    def frame_bytes(self) -> int:
        return self.block_frames * 2 * self.channels


@dataclass
class VadConfig:
    """VAD 与切片参数（同时被 App BLE CONFIG 使用）。"""

    aggressiveness: int = 2  # 0..3，webrtcvad / 能量门限灵敏度
    frame_ms: int = 30  # VAD 帧长：webrtcvad 仅支持 10/20/30
    min_speech_ms: int = 300  # 短于此的“语音”整段丢弃
    min_silence_ms: int = 500  # 静音超过此值才断句（同时用于合并短静音）
    max_segment_s: float = 60.0  # 单段最长秒数，超出强制切分
    preroll_s: float = 0.3  # 语音起点前保留
    postroll_s: float = 0.3  # 语音终点后保留
    energy_factor: float = 2.5  # 能量门限 = 噪声底 * factor
    absolute_floor: float = 120.0  # 绝对 RMS 下限，避免安静环境误触发
    adaptive_noise: bool = True


@dataclass
class UploadConfig:
    """上传、重试与离线策略。"""

    endpoint: str = "/api/v1/audio/segments"
    health_endpoint: str = "/health"
    retry_max: int = 5
    retry_base_s: float = 1.0
    retry_max_s: float = 300.0
    timeout_s: float = 30.0
    offline_buffer: bool = True
    offline_policy: str = "keep"  # keep | drop（仅 offline_buffer=false 时生效）
    batch_size: int = 4


# ---------------------------------------------------------------------------
# 主配置
# ---------------------------------------------------------------------------

_TOP_LEVEL = ("server_url", "api_key", "device_id", "spool_path", "log_level")

# 顶层磁盘/JSON 键 -> (section, field) 别名（App 的 PUT /devices/{id}/config 使用扁平键）
_FLAT_ALIASES: dict[str, tuple[str | None, str]] = {
    "upload_endpoint": ("upload", "endpoint"),
    "health_endpoint": ("upload", "health_endpoint"),
    "retry_max": ("upload", "retry_max"),
    "retry_base_s": ("upload", "retry_base_s"),
    "retry_max_s": ("upload", "retry_max_s"),
    "timeout_s": ("upload", "timeout_s"),
    "offline_buffer": ("upload", "offline_buffer"),
    "offline_policy": ("upload", "offline_policy"),
    "batch_size": ("upload", "batch_size"),
}

# 环境变量 -> (section, field)；section=None 表示顶层
_ENV_MAP: dict[str, tuple[str | None, str]] = {
    "OA_SERVER_URL": (None, "server_url"),
    "OA_API_KEY": (None, "api_key"),
    "OA_DEVICE_ID": (None, "device_id"),
    "OA_SPOOL_PATH": (None, "spool_path"),
    "OA_LOG_LEVEL": (None, "log_level"),
    "OA_RECORD_DEVICE": ("record", "device"),
    "OA_RECORD_SAMPLE_RATE": ("record", "sample_rate"),
    "OA_RECORD_CHANNELS": ("record", "channels"),
    "OA_RECORD_BLOCK_MS": ("record", "block_ms"),
    "OA_RECORD_SOURCE": ("record", "source"),
    "OA_RECORD_INPUT": ("record", "input_path"),
    "OA_RECORD_LATENCY": ("record", "latency"),
    "OA_RECORD_INPUT_QUEUE_BLOCKS": ("record", "input_queue_blocks"),
    "OA_VAD_AGGRESSIVENESS": ("vad", "aggressiveness"),
    "OA_VAD_FRAME_MS": ("vad", "frame_ms"),
    "OA_VAD_MIN_SPEECH_MS": ("vad", "min_speech_ms"),
    "OA_VAD_MIN_SILENCE_MS": ("vad", "min_silence_ms"),
    "OA_VAD_MAX_SEGMENT_S": ("vad", "max_segment_s"),
    "OA_VAD_PREROLL_S": ("vad", "preroll_s"),
    "OA_VAD_POSTROLL_S": ("vad", "postroll_s"),
    "OA_VAD_ENERGY_FACTOR": ("vad", "energy_factor"),
    "OA_VAD_ABSOLUTE_FLOOR": ("vad", "absolute_floor"),
    "OA_VAD_ADAPTIVE_NOISE": ("vad", "adaptive_noise"),
    "OA_UPLOAD_ENDPOINT": ("upload", "endpoint"),
    "OA_UPLOAD_HEALTH_ENDPOINT": ("upload", "health_endpoint"),
    "OA_UPLOAD_RETRY_MAX": ("upload", "retry_max"),
    "OA_UPLOAD_RETRY_BASE_S": ("upload", "retry_base_s"),
    "OA_UPLOAD_RETRY_MAX_S": ("upload", "retry_max_s"),
    "OA_UPLOAD_TIMEOUT_S": ("upload", "timeout_s"),
    "OA_UPLOAD_OFFLINE_BUFFER": ("upload", "offline_buffer"),
    "OA_UPLOAD_OFFLINE_POLICY": ("upload", "offline_policy"),
    "OA_UPLOAD_BATCH_SIZE": ("upload", "batch_size"),
    # App 端扁平短名
    "OA_RETRY_MAX": ("upload", "retry_max"),
    "OA_OFFLINE_BUFFER": ("upload", "offline_buffer"),
}

# to_json / to_dict 时排除的字段
_SERIALIZE_EXCLUDE = {"config_path"}


@dataclass
class AgentConfig:
    """Agent 全量运行配置。"""

    server_url: str = "http://127.0.0.1:8000"
    api_key: str = ""
    device_id: str = "pi-openassistant"
    spool_path: str = field(default_factory=lambda: str(default_data_dir()))
    log_level: str = "INFO"
    record: RecordConfig = field(default_factory=RecordConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    config_path: str = ""  # 实际生效的配置文件路径（不参与序列化）

    # -- 序列化 ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for f in fields(self):
            if f.name in _SERIALIZE_EXCLUDE:
                continue
            value = getattr(self, f.name)
            if is_dataclass(value):
                data[f.name] = {
                    inner.name: getattr(value, inner.name) for inner in fields(value)
                }
            else:
                data[f.name] = value
        return data

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "AgentConfig":
        config = cls()
        if data:
            config.merge_dict(data)
        return config

    @classmethod
    def from_json(cls, payload: str | bytes | Mapping[str, Any]) -> "AgentConfig":
        if isinstance(payload, (str, bytes, bytearray)):
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"invalid config json: {exc}") from exc
        else:
            data = payload
        if not isinstance(data, Mapping):
            raise ConfigError("config json must be an object")
        return cls.from_dict(data)

    def copy(self) -> "AgentConfig":
        return AgentConfig.from_dict(self.to_dict())

    # -- 合并 ------------------------------------------------------------
    def merge_dict(
        self, data: Mapping[str, Any], *, allow_unknown: bool = True
    ) -> dict[str, list[str]]:
        """把嵌套/扁平 dict 合并进当前配置，返回 ``{section: [changed fields]}``。

        未知键默认忽略（BLE 写入可能包含 ``wifi`` 等 App 专用字段）。
        """
        if not isinstance(data, Mapping):
            raise ConfigError("config payload must be an object")
        changed: dict[str, list[str]] = {}

        def mark(section: str | None, name: str) -> None:
            key = section or "root"
            changed.setdefault(key, [])
            if name not in changed[key]:
                changed[key].append(name)

        # 1) 别名扁平键
        for key, (section, name) in _FLAT_ALIASES.items():
            if key not in data:
                continue
            target = self if section is None else getattr(self, section)
            if _assign(target, name, data[key]):
                mark(section, name)

        # 2) 顶层字段
        for name in _TOP_LEVEL:
            if name in data and _assign(self, name, data[name]):
                mark(None, name)

        # 3) 子配置块
        for section in ("record", "vad", "upload"):
            block = data.get(section)
            if not isinstance(block, Mapping):
                continue
            target = getattr(self, section)
            for name, value in block.items():
                if _assign(target, name, value):
                    mark(section, name)

        return changed

    def changed_fields(self, other: "AgentConfig") -> list[str]:
        left, right = self.to_dict(), other.to_dict()
        result: list[str] = []
        for key in left:
            if left[key] != right.get(key):
                result.append(key)
        return result

    # -- 文件 ------------------------------------------------------------
    def to_toml(self) -> str:
        """序列化为 TOML（固定 schema，简单可靠）。"""
        lines: list[str] = []

        def render(value: Any) -> str:
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, (int, float)):
                return repr(value)
            return json.dumps(str(value), ensure_ascii=False)

        lines.append("# OpenAssistant Agent 配置（由 Agent 写出，可手工编辑）")
        for name in _TOP_LEVEL:
            lines.append(f"{name} = {render(getattr(self, name))}")
        for section in ("record", "vad", "upload"):
            lines.append("")
            lines.append(f"[{section}]")
            for f in fields(getattr(self, section)):
                lines.append(f"{f.name} = {render(getattr(getattr(self, section), f.name))}")
        return "\n".join(lines) + "\n"

    def save(self, path: str | Path | None = None) -> Path:
        """原子写入 TOML；目录不可写时回退到用户配置目录。"""
        target = Path(path or self.config_path or default_config_path()).expanduser()
        text = self.to_toml()
        try:
            _atomic_write(target, text)
        except OSError as exc:
            fallback = Path.home() / ".config" / APP_NAME / "agent.toml"
            if target == fallback:
                raise ConfigError(f"cannot write config {target}: {exc}") from exc
            _atomic_write(fallback, text)
            target = fallback
        self.config_path = str(target)
        return target

    # -- 校验 ------------------------------------------------------------
    def validate(self) -> None:
        if not (0 <= self.record.block_ms <= 1000):
            raise ConfigError("record.block_ms must be within 0..1000")
        if self.record.sample_rate not in SAMPLE_RATES:
            raise ConfigError(f"record.sample_rate must be one of {SAMPLE_RATES}")
        if self.record.channels not in (1, 2):
            raise ConfigError("record.channels must be 1 or 2")
        if self.record.source not in ("device", "file", "synthetic"):
            raise ConfigError("record.source must be device|file|synthetic")
        if self.record.source == "file" and not self.record.input_path:
            raise ConfigError("record.input_path is required for file source")
        if str(self.record.latency).strip().lower() not in ("low", "high"):
            try:
                latency_s = float(str(self.record.latency).strip())
            except (TypeError, ValueError) as exc:
                raise ConfigError("record.latency must be 'low'|'high' or seconds (0.01..5)") from exc
            if not (0.01 <= latency_s <= 5.0):
                raise ConfigError("record.latency seconds must be within 0.01..5")
        if not (2 <= int(self.record.input_queue_blocks) <= 2000):
            raise ConfigError("record.input_queue_blocks must be within 2..2000")
        if not (0 <= self.vad.aggressiveness <= 3):
            raise ConfigError("vad.aggressiveness must be within 0..3")
        if self.vad.frame_ms not in (10, 20, 30):
            raise ConfigError("vad.frame_ms must be 10, 20 or 30")
        if self.vad.min_speech_ms < 0 or self.vad.min_silence_ms < 0:
            raise ConfigError("vad.min_speech_ms / min_silence_ms must be >= 0")
        if self.vad.max_segment_s <= 0:
            raise ConfigError("vad.max_segment_s must be > 0")
        if self.vad.preroll_s < 0 or self.vad.postroll_s < 0:
            raise ConfigError("vad.preroll_s / postroll_s must be >= 0")
        if self.upload.retry_max < 0:
            raise ConfigError("upload.retry_max must be >= 0")
        if self.upload.retry_base_s <= 0 or self.upload.retry_max_s < self.upload.retry_base_s:
            raise ConfigError("upload retry backoff bounds are invalid")
        if self.upload.offline_policy not in ("keep", "drop"):
            raise ConfigError("upload.offline_policy must be keep|drop")
        if self.upload.batch_size < 1:
            raise ConfigError("upload.batch_size must be >= 1")
        if self.upload.timeout_s <= 0:
            raise ConfigError("upload.timeout_s must be > 0")

    # -- 加载 ------------------------------------------------------------
    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> "AgentConfig":
        """默认值 < TOML < 环境变量 < 显式参数。"""
        environ = os.environ if env is None else env
        path = Path(
            config_path
            or environ.get("OA_CONFIG")
            or environ.get("OA_CONFIG_PATH")
            or default_config_path()
        ).expanduser()

        config = cls()
        if path.exists():
            data = _read_toml(path)
            config.merge_dict(data)
            config.config_path = str(path)
        else:
            config.config_path = str(path)

        env_data: dict[str, Any] = {}
        for var, (section, name) in _ENV_MAP.items():
            if var not in environ:
                continue
            value = environ[var]
            if value == "":
                continue
            if section is None:
                env_data[name] = value
            else:
                env_data.setdefault(section, {})[name] = value
        config.merge_dict(env_data)

        if overrides:
            config.merge_dict(overrides)
        return config


def load_config(
    config_path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> AgentConfig:
    """``AgentConfig.load`` 的便捷函数。"""
    return AgentConfig.load(config_path, env=env, overrides=overrides)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _coerce(value: Any, current: Any) -> Any:
    """按当前值类型做宽松转换；失败抛 ConfigError。"""
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on", "y", "t"):
            return True
        if text in ("0", "false", "no", "off", "n", "f"):
            return False
        raise ConfigError(f"invalid boolean value: {value!r}")
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid integer value: {value!r}") from exc
    if isinstance(current, float):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid float value: {value!r}") from exc
    return str(value)


def _assign(target: Any, name: str, value: Any) -> bool:
    """赋值并返回是否发生变化；未知字段抛错由调用方决定。"""
    if not hasattr(target, name):
        return False
    current = getattr(target, name)
    if is_dataclass(current):
        return False
    new_value = _coerce(value, current)
    if new_value == current:
        return False
    setattr(target, name, new_value)
    return True


def _read_toml(path: Path) -> dict[str, Any]:
    if _tomllib is None:  # pragma: no cover - 需要 tomli 的旧 Python
        raise ConfigError(
            "TOML support requires Python 3.11+ (tomllib) or the 'tomli' package"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    try:
        data = _tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:  # tomllib.TOMLDecodeError / UnicodeDecodeError
        raise ConfigError(f"invalid TOML config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"invalid TOML config {path}: expected a table")
    return data


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if os.name != "nt":
        # 配置含 API Key，仅所有者可读写
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - 文件系统不支持
            pass
    os.replace(tmp, path)
