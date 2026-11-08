from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from job_hunter.config import AppConfig, load_config
from job_hunter.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def config() -> AppConfig:
    return load_config(ROOT / "config")


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,
        adzuna_app_id="test-id",
        adzuna_app_key=SecretStr("test-key"),
        france_travail_client_id="ft-id",
        france_travail_client_secret=SecretStr("ft-secret"),
    )


@pytest.fixture()
def emails_dir() -> Path:
    return FIXTURES / "emails"
