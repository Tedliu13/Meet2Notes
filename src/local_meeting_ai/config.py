from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Environment and command-line configurable application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="M2N_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Meet2Notes"
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    data_dir: Path | None = None
    models_dir: Path | None = None
    log_level: str = "INFO"
    max_upload_mb: int = Field(default=2048, ge=0)
    max_heavy_jobs: int = Field(default=1, ge=1, le=4)
    cpu_threads: int = Field(default=2, ge=1, le=64)
    hosted: bool = False
    chinese_transcript_script: Literal["original", "traditional"] = "original"
    allowed_hosts: str = ""
    auth_username: str = ""
    auth_password: str = Field(default="", repr=False)
    secrets_key: str = Field(default="", repr=False)
    ffmpeg_path: Path | None = None
    open_browser: bool = True
    testing: bool = False
    pyannote_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("M2N_PYANNOTE_TOKEN", "PYANNOTE_TOKEN"),
        repr=False,
    )

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Host cannot be empty")
        return value.strip()

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Unsupported log level")
        return normalized

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024
