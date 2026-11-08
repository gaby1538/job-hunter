"""Secrets and runtime settings, read from environment variables (or a local .env)."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    job_hunter_config_dir: Path = Path("config")
    log_level: str = "INFO"

    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: SecretStr | None = None

    adzuna_app_id: str | None = None
    adzuna_app_key: SecretStr | None = None

    france_travail_client_id: str | None = None
    france_travail_client_secret: SecretStr | None = None


def get_settings() -> Settings:
    return Settings()
