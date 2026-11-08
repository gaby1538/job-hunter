"""What Claude must return for each job (validated JSON via structured outputs).

Every fact carries a verbatim quote from the job text. Quotes are checked in Python
(verify.py); a fact whose quote is not found in the text is discarded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Requirement(BaseModel):
    requirement: str = Field(description="One requirement of the job, short, in English")
    importance: Literal["must_have", "nice_to_have"]
    quote: str = Field(description="Exact words copied from the job text stating it")
    coverage: Literal["strong", "partial", "missing"] = Field(
        description="How well the candidate profile covers it"
    )
    coverage_reason: str = Field(description="One short sentence citing the profile")


class JobAnalysis(BaseModel):
    role_match: Literal["target", "adjacent", "off_target"]
    role_match_reason: str
    closest_target_role: str | None = Field(
        description="Closest of the candidate's target or adjacent roles, or null"
    )

    work_nature: Literal["modelling_heavy", "mixed", "coordination_heavy", "unknown"] = Field(
        description="Hands-on quantitative modelling vs project coordination / PMO"
    )
    work_nature_quote: str | None

    contract: Literal["permanent", "freelance", "fixed_term", "internship", "unknown"]
    contract_quote: str | None

    remote_policy: Literal["full_remote", "mostly_remote", "hybrid", "onsite", "unknown"]
    onsite_days_per_month: float | None = Field(
        description="Only if the text states onsite or remote days; 1 day/week = 4.3/month"
    )
    remote_quote: str | None

    required_years_min: int | None
    years_quote: str | None

    location: str | None
    risk_domains: list[str] = Field(
        description="Risk areas the job covers, e.g. market, counterparty, liquidity, XVA, credit"
    )
    requirements: list[Requirement]
    languages_required: list[str]
    red_flags: list[str] = Field(description="Concerns for this candidate, from the text only")
    strengths_to_highlight: list[str] = Field(
        description="Profile elements to put forward when applying"
    )
    gaps_to_address: list[str] = Field(description="Missing elements to prepare or explain")
    summary: str = Field(description="Two sentences: what the job is and how well it fits")
