"""Application settings.

Every setting can be overridden with an ``OA_``-prefixed environment variable
(see ``.env.example``) or a ``.env`` file next to the server directory.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent


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

    # ── storage / database ────────────────────────────────────────────────
    database_url: str = f"sqlite:///{(SERVER_DIR / 'data' / 'openassistant.db').as_posix()}"
    storage_dir: Path = SERVER_DIR / "data" / "storage"
    public_base_url: str = ""  # e.g. https://assistant.example.com

    # ── auth ──────────────────────────────────────────────────────────────
    api_key_pepper: str = "change-me-in-production"
    bootstrap_api_key: str = ""  # dev convenience: create this key on startup
    bootstrap_user_name: str = "我"

    # ── providers ─────────────────────────────────────────────────────────
    asr_provider: str = "auto"  # auto | faster_whisper | mock
    diarization_provider: str = "auto"  # auto | pyannote | energy
    speaker_provider: str = "auto"  # auto | campplus | pyannote | spectral
    llm_provider: str = "auto"  # auto | openai_compatible | rule
    search_provider: str = "auto"  # auto | builtin | deepseek
    push_provider: str = "auto"  # auto | dingtalk | null

    # ASR
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    hf_token: str = ""  # pyannote model download token

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

    # ── dev / demo ────────────────────────────────────────────────────────
    demo_mode: bool = False
    cors_origins: str = "*"

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
    return settings
