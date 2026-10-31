"""Application settings.

Every setting can be overridden with an ``OA_``-prefixed environment variable
(see ``.env.example``) or a ``.env`` file next to the server directory.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent


def normalize_timezone(name: str | None) -> str:
    """校验并规范化 IANA 时区名，非法时抛 ``ValueError``。"""
    value = (name or "").strip() or "Asia/Shanghai"
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"invalid timezone {value!r}: 请使用 IANA 时区名，例如 Asia/Shanghai、UTC"
        ) from exc
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OA_",
        env_file=str(SERVER_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── application ───────────────────────────────────────────────────────
    app_name: str = "OpenAssistant"
    version: str = "1.0.0"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # ── 时区 ──────────────────────────────────────────────────────────────
    # 服务器默认时区（IANA 名称，如 Asia/Shanghai、UTC、America/New_York）。
    # 作用：
    #   1. 新建用户的默认时区；
    #   2. ``user.timezone`` 为空/非法时的兜底（日程解析、指令、画像、会议、调度）；
    #   3. 显式配置（写在 .env 或环境变量里）时，启动会把已有用户同步为该时区，
    #      之后 App 仍可通过 PATCH /me 单独修改。
    timezone: str = "Asia/Shanghai"

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        return normalize_timezone(value)

    @property
    def timezone_explicit(self) -> bool:
        """时区是否由 .env / 环境变量显式配置（用于启动时同步已有用户）。"""
        return "timezone" in self.model_fields_set

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    # ── storage / database ────────────────────────────────────────────────
    database_url: str = f"sqlite:///{(SERVER_DIR / 'data' / 'openassistant.db').as_posix()}"
    storage_dir: Path = SERVER_DIR / "data" / "storage"
    public_base_url: str = ""  # e.g. https://assistant.example.com

    # ── auth ──────────────────────────────────────────────────────────────
    api_key_pepper: str = "change-me-in-production"
    bootstrap_api_key: str = ""  # dev convenience: create this key on startup
    bootstrap_user_name: str = "我"

    # ── providers ─────────────────────────────────────────────────────────
    asr_provider: str = "auto"  # auto | funasr | faster_whisper | mock
    diarization_provider: str = "auto"  # auto | pyannote | energy
    speaker_provider: str = "auto"  # auto | campplus | pyannote | spectral
    llm_provider: str = "auto"  # auto | openai_compatible | rule
    search_provider: str = "auto"  # auto | builtin | deepseek
    push_provider: str = "auto"  # auto | dingtalk | null

    # ASR — FunASR（默认：Paraformer-zh + FSMN-VAD + CT-Punc）
    funasr_model: str = "paraformer-zh"
    funasr_vad_model: str = "fsmn-vad"  # 留空可关闭内置 VAD（片段已由 Server 切分）
    funasr_punc_model: str = "ct-punc"  # 标点恢复，留空则输出无标点文本
    funasr_device: str = "cuda"  # cuda / cuda:0 / cpu
    funasr_hub: str = "ms"  # ms（ModelScope，国内推荐）/ hf
    funasr_language: str = "zh"  # SenseVoice 使用；auto 表示自动识别
    funasr_use_itn: bool = True  # 逆文本正则化：数字/日期等口语转书面
    funasr_hotword: str = ""  # 热词表（空格分隔），可提升人名/专有名词识别
    funasr_batch_size_s: int = 300  # 长音频按秒切 batch，片段通常远小于该值
    # ASR 旧默认（faster-whisper）：仅在 OA_ASR_PROVIDER=faster_whisper 时使用
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    hf_token: str = ""  # pyannote model download token
    diarization_model: str = "pyannote/speaker-diarization-3.1"
    embedding_model: str = "pyannote/embedding"
    whisper_language: str = "zh"  # 固定语言可减少短片段语言识别错误
    # Windows 下 CUDA 12/cuDNN 9 DLL 目录（分号分隔）；留空自动探测常见位置
    cuda_dll_dirs: str = ""

    # LLM (DeepSeek / any OpenAI-compatible endpoint)
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_s: float = 60.0
    llm_max_tokens: int = 1600

    # Search
    search_builtin_endpoint: str = "https://html.duckduckgo.com/html/"
    search_timeout_s: float = 15.0
    search_max_results: int = 5

    # ── DingTalk ──────────────────────────────────────────────────────────
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    push_enabled: bool = True
    push_max_attempts: int = 5

    # ── pipeline behaviour ────────────────────────────────────────────────
    voiceprint_threshold: float = 60.0
    max_profile_chars: int = 600
    profile_update_every: int = 3
    meeting_min_seconds: float = 60.0
    meeting_min_speakers: int = 2
    max_upload_mb: int = 64
    job_poll_seconds: float = 1.0
    job_max_attempts: int = 3
    scheduler_enabled: bool = True
    worker_enabled: bool = True
    # 低于该 RMS（float，0..1）的片段视为静音，不做 ASR/总结（避免 ASR 对静音幻觉）
    min_speech_rms: float = 0.004
    # 极短片段不做 pyannote 说话人分离（直接视为单一说话人区间），避免模型在过短输入上
    # 出现 std/mean-of-empty 等数值退化告警；声纹 embedding 不足最短长度时反射填充。
    diarization_min_seconds: float = 1.0
    embedding_min_seconds: float = 0.5
    # 总结丢弃策略：过于简短/无意义的人声音频，允许 AI 选择丢弃、不生成卡片；
    # 关闭后所有有转写的片段都会生成卡片。min_chars 是规则兜底阈值（无 LLM 时，
    # 转写有效字数不超过该值且不含时间/数字等有效信息，则视为无意义音频丢弃）。
    summarizer_discard_enabled: bool = True
    summarizer_discard_min_chars: int = 3

    # 对话合并：转写完成后先等待一小段时间，看是否还有时间相近的音频也完成转写，
    # 有的话合并成一个对话统一总结（只生成一张卡片），避免把一段对话切碎成多张卡片。
    conversation_merge_enabled: bool = True
    conversation_merge_window_s: float = 20.0  # 转写完成后等待多久（秒）
    conversation_merge_gap_s: float = 45.0  # 相邻片段录制时间间隔 ≤ 该值视为同一对话
    conversation_merge_max_segments: int = 12  # 单个对话最多合并的片段数
    conversation_merge_max_duration_s: float = 900.0  # 单个对话最大时长（秒），超出拆分为新对话

    # AI 定时复盘合并：每隔一段时间让 LLM 复盘近期新增的卡片，把话题相同/时间相近的卡片
    # 合并成一张卡片并重新总结（片段级合并的补充，防止同一对话仍被拆成多张卡）。
    card_merge_enabled: bool = True
    card_merge_interval_s: float = 300.0  # 后台每隔多久复盘一次（秒）
    card_merge_lookback_s: float = 1800.0  # 只看最近多久内新增的卡片
    card_merge_max_cards: int = 12  # 单次复盘最多送给 LLM 的卡片数
    card_merge_max_span_s: float = 3600.0  # 同一组卡片的录制时间跨度上限（秒）

    # ── dev / demo ────────────────────────────────────────────────────────
    demo_mode: bool = False
    cors_origins: str = "*"

    # ── HuggingFace / ModelScope 下载（国内网络建议配置镜像/缓存目录） ─────
    # OA_HF_ENDPOINT=https://hf-mirror.com 可显著加速/稳定模型下载
    hf_endpoint: str = ""
    hf_home: str = ""
    # FunASR 模型（Paraformer / FSMN-VAD / CT-Punc）默认缓存在 ~/.cache/modelscope；
    # 建议指向部署目录（如 ./data/modelscope）便于备份与离线部署。
    modelscope_cache: str = ""

    # ── derived helpers ───────────────────────────────────────────────────
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def sqlalchemy_url(self) -> str:
        if self.is_sqlite:
            path = self.database_url.removeprefix("sqlite:///")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    # huggingface_hub / modelscope 读取的是进程环境变量，pydantic 读取 .env 不会自动导出；
    # 这里把 OA_HF_ENDPOINT / OA_HF_HOME / OA_MODELSCOPE_CACHE 转成对应环境变量，
    # 便于在 .env 里配置国内镜像（如 https://hf-mirror.com）与模型缓存目录。
    if settings.hf_endpoint:
        os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
    if settings.hf_home:
        os.environ.setdefault("HF_HOME", settings.hf_home)
    if settings.modelscope_cache:
        cache = Path(settings.modelscope_cache).expanduser()
        if not cache.is_absolute():
            cache = SERVER_DIR / cache
        os.environ.setdefault("MODELSCOPE_CACHE", str(cache))
    return settings


def default_timezone() -> str:
    """服务器默认时区名（OA_TIMEZONE），供各处 ``user.timezone`` 兜底使用。"""
    return get_settings().timezone

