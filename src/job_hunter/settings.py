"""Secrets and runtime settings, read from environment variables (or a local .env)."""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    job_hunter_config_dir: Path = Path("config")
    job_hunter_data_dir: Path = Path("data")
    log_level: str = "INFO"

    anthropic_api_key: SecretStr | None = None
    # Comma-separated addresses allowed to send "JD" capture emails (your own addresses)
    capture_senders: str = ""

    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: SecretStr | None = None

    # Report email (defaults: Gmail SMTP, your IMAP credentials, sent to yourself)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    report_to: str | None = None

    adzuna_app_id: str | None = None
    adzuna_app_key: SecretStr | None = None

    france_travail_client_id: str | None = None
    france_travail_client_secret: SecretStr | None = None


    @property
    def capture_sender_list(self) -> list[str]:
        return [s.strip().lower() for s in self.capture_senders.split(",") if s.strip()]


def get_settings() -> Settings:
    return Settings()
