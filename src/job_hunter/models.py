"""Core data model for a collected job advertisement.

Rule: fields are only filled from what the source states explicitly.
Anything not stated stays None / UNKNOWN - never guessed.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ContractType(StrEnum):
    PERMANENT = "permanent"
    FREELANCE = "freelance"
    FIXED_TERM = "fixed_term"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class SalaryPeriod(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"


Channel = Literal["email", "api", "rss"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobPosting(BaseModel):
    source: str = Field(description="Job site the ad comes from, e.g. 'indeed', 'adzuna'")
    channel: Channel
    title: str
    url: str | None = None
    source_job_id: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    contract_type: ContractType = ContractType.UNKNOWN
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: SalaryPeriod | None = None
    salary_text: str | None = Field(default=None, description="Salary as written by the source")
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=utc_now)
    also_seen_at: list[str] = Field(default_factory=list, description="Other URLs of duplicates")
    raw: dict[str, Any] = Field(default_factory=dict, description="Original payload, for audit")

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("title must not be blank")
        return v

    @field_validator("published_at", "collected_at")
    @classmethod
    def _to_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @property
    def fingerprint(self) -> str:
        """Stable identity used for deduplication across sources."""
        from job_hunter.processing.normalize import dedupe_key

        return hashlib.sha256(dedupe_key(self).encode()).hexdigest()[:16]
