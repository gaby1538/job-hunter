"""Load and validate the YAML parameter files (search.yaml, sources.yaml)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from job_hunter.models import ContractType


class ConfigError(Exception):
    pass


# ---------- search.yaml ----------

class LocationQuery(BaseModel):
    where: str = ""


class SearchCriteria(BaseModel):
    queries: list[str] = Field(min_length=1)
    country: str = "fr"
    locations: list[LocationQuery] = Field(default_factory=lambda: [LocationQuery()])
    include_keywords: list[str] = Field(default_factory=list)
    exclude_title_keywords: list[str] = Field(default_factory=list)
    contract_types: list[ContractType] = Field(default_factory=lambda: list(ContractType))
    max_age_days: int = Field(default=14, ge=1)
    max_results_per_source: int = Field(default=300, ge=1)


# ---------- sources.yaml ----------

def _check_regexes(patterns: list[str]) -> list[str]:
    for p in patterns:
        try:
            re.compile(p)
        except re.error as exc:
            raise ValueError(f"invalid regex {p!r}: {exc}") from exc
    return patterns


class EmailSiteRule(BaseModel):
    """How to read one site's alert emails. See sources.yaml for documented examples."""

    # Which emails belong to this site
    sender_patterns: list[str] = Field(min_length=1)
    subject_patterns: list[str] = Field(default_factory=list)

    # links: one job per job link (digests). single: the whole email is one job.
    mode: Literal["links", "single"] = "links"
    # Which part of the email to read: the text part or the HTML part.
    body: Literal["text", "html"] = "html"

    # Links
    job_url_patterns: list[str] = Field(min_length=1)
    job_id_pattern: str | None = None
    canonical_url: str | None = None  # template with {id}, e.g. https://x/jobs/{id}
    unwrap_redirects: bool = False  # decode click-tracking redirects to the real URL

    # Reading the lines of a job block
    title_first: bool = True  # False: the title is the first line no pattern claims
    skip_line_patterns: list[str] = Field(default_factory=list)
    line_patterns: list[str] = Field(default_factory=list)  # regex with named groups
    context_fields: list[str] = Field(default_factory=list)  # leftover lines, in order
    max_context_lines: int = Field(default=8, ge=1)

    # single mode (one offer per email)
    title_selector: str | None = None  # CSS selector
    subject_fields: str | None = None  # regex with named groups applied to the subject
    labels: dict[str, str] = Field(default_factory=dict)  # "Lieu": location

    # Facts about the channel itself
    default_contract: ContractType | None = None
    salary_may_be_estimate: bool = False

    @field_validator("subject_patterns", "job_url_patterns",
                     "skip_line_patterns", "line_patterns")
    @classmethod
    def _valid(cls, v: list[str]) -> list[str]:
        return _check_regexes(v)

    @field_validator("job_id_pattern", "subject_fields")
    @classmethod
    def _valid_one(cls, v: str | None) -> str | None:
        if v is not None:
            _check_regexes([v])
        return v


class EmailConfig(BaseModel):
    enabled: bool = True
    mailbox: str = "INBOX"
    lookback_days: int = Field(default=2, ge=1)
    ignore_link_texts: list[str] = Field(default_factory=list)
    sites: dict[str, EmailSiteRule] = Field(default_factory=dict)


class AdzunaConfig(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.adzuna.com/v1/api/jobs"
    results_per_page: int = Field(default=50, ge=1, le=50)
    max_pages: int = Field(default=2, ge=1)


class FranceTravailConfig(BaseModel):
    enabled: bool = False
    token_url: str
    search_url: str
    scope: str
    page_size: int = Field(default=150, ge=1, le=150)


class RssFeed(BaseModel):
    name: str
    url: str


class RssConfig(BaseModel):
    enabled: bool = False
    feeds: list[RssFeed] = Field(default_factory=list)


class CaptureConfig(BaseModel):
    """Descriptions you copy yourself and email to the alert inbox."""

    enabled: bool = True
    subject_prefix: str = "JD"
    min_chars: int = Field(default=200, ge=1)


class Employer(BaseModel):
    name: str
    ats: Literal["greenhouse", "lever", "ashby"]
    board: str = Field(description="Board token / site name used in the ATS public URL")


class EmployersConfig(BaseModel):
    """Employers' own public job APIs: full descriptions, published for reuse."""

    enabled: bool = False
    companies: list[Employer] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    email: EmailConfig = Field(default_factory=EmailConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    employers: EmployersConfig = Field(default_factory=EmployersConfig)
    adzuna: AdzunaConfig = Field(default_factory=AdzunaConfig)
    france_travail: FranceTravailConfig | None = None
    rss: RssConfig = Field(default_factory=RssConfig)


class AppConfig(BaseModel):
    search: SearchCriteria
    sources: SourcesConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at top level")
    return data


def load_config(config_dir: Path) -> AppConfig:
    try:
        return AppConfig(
            search=SearchCriteria.model_validate(_read_yaml(config_dir / "search.yaml")),
            sources=SourcesConfig.model_validate(_read_yaml(config_dir / "sources.yaml")),
        )
    except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
        raise ConfigError(str(exc)) from exc
