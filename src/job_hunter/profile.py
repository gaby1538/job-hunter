"""Your professional profile and scoring parameters (config/profile.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from job_hunter.config import ConfigError
from job_hunter.models import ContractType

SkillLevel = Literal["expert", "strong", "working"]


class Location(BaseModel):
    base: str
    max_onsite_days_per_month: float = Field(ge=0)


class Preferences(BaseModel):
    prioritize: str = ""
    avoid: str = ""


class Weights(BaseModel):
    role_fit: float = 25
    requirements: float = 30
    modelling_depth: float = 20
    remote: float = 15
    contract: float = 5
    seniority: float = 5

    @model_validator(mode="after")
    def _sum_to_100(self) -> Weights:
        total = sum(self.model_dump().values())
        if abs(total - 100) > 1e-6:
            raise ValueError(f"scoring weights must sum to 100, got {total}")
        return self


class Verdict(BaseModel):
    apply_from: float = 70
    consider_from: float = 50
    max_missing_must_haves_to_apply: int = 1
    # Stated onsite days above your maximum: never "apply", at best "consider"
    onsite_limit_is_dealbreaker: bool = True


class Scoring(BaseModel):
    weights: Weights = Field(default_factory=Weights)
    coordination_heavy_penalty: float = 25
    verdict: Verdict = Field(default_factory=Verdict)


class AnalysisSettings(BaseModel):
    model: str = "claude-sonnet-5-5"
    max_tokens: int = Field(default=4000, ge=500)
    max_jobs_per_run: int = Field(default=40, ge=0)  # cost cap


class Profile(BaseModel):
    summary: str
    years_of_experience: float = Field(ge=0)
    skills: dict[str, SkillLevel] = Field(min_length=1)
    target_roles: list[str] = Field(min_length=1)
    adjacent_roles: list[str] = Field(default_factory=list)
    contracts: list[ContractType] = Field(min_length=1)
    location: Location
    preferences: Preferences = Field(default_factory=Preferences)
    scoring: Scoring = Field(default_factory=Scoring)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)

    def as_prompt(self) -> str:
        """Profile as plain text for the analysis prompt (scoring settings excluded)."""
        skills = "\n".join(f"- {name} ({level})" for name, level in self.skills.items())
        return (
            f"Summary: {self.summary.strip()}\n"
            f"Years of experience: {self.years_of_experience:g}\n"
            f"Skills:\n{skills}\n"
            f"Target roles: {', '.join(self.target_roles)}\n"
            f"Adjacent roles (acceptable): {', '.join(self.adjacent_roles) or 'none'}\n"
            f"Contracts wanted: {', '.join(c.value for c in self.contracts)}\n"
            f"Based in: {self.location.base}; at most "
            f"{self.location.max_onsite_days_per_month:g} onsite days per month\n"
            f"Prioritize: {self.preferences.prioritize}\n"
            f"Avoid: {self.preferences.avoid}\n"
        )


def load_profile(config_dir: Path) -> Profile:
    path = config_dir / "profile.yaml"
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Profile.model_validate(data)
    except (yaml.YAMLError, ValueError) as exc:
        raise ConfigError(f"invalid {path}: {exc}") from exc
