"""Apply the search.yaml criteria. Every rejection keeps its reason, for transparency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from job_hunter.config import SearchCriteria
from job_hunter.models import JobPosting
from job_hunter.processing.normalize import norm_text


@dataclass(frozen=True)
class Rejected:
    job: JobPosting
    reason: str


def _contains(haystack: str, keyword: str) -> bool:
    """Keyword matches at the start of a word, so 'risque' also matches 'risques'."""
    kw = norm_text(keyword)
    return bool(kw) and f" {kw}" in f" {haystack} "


def check(job: JobPosting, criteria: SearchCriteria, now: datetime | None = None) -> str | None:
    """Return a rejection reason, or None if the job is kept."""
    now = now or datetime.now(UTC)
    title = norm_text(job.title)
    for kw in criteria.exclude_title_keywords:
        if _contains(title, kw):
            return f"title contains excluded keyword '{kw}'"
    if criteria.include_keywords:
        context = " ".join(str(x) for x in job.raw.get("context", []))
        text = norm_text(f"{job.title} {job.description or ''} {context}")
        if not any(_contains(text, kw) for kw in criteria.include_keywords):
            return "no include keyword found"
    if job.contract_type not in criteria.contract_types:
        return f"contract type '{job.contract_type}' not wanted"
    if job.published_at and job.published_at < now - timedelta(days=criteria.max_age_days):
        return f"published more than {criteria.max_age_days} days ago"
    return None


def apply_filters(
    jobs: list[JobPosting], criteria: SearchCriteria, now: datetime | None = None
) -> tuple[list[JobPosting], list[Rejected]]:
    kept: list[JobPosting] = []
    rejected: list[Rejected] = []
    for job in jobs:
        reason = check(job, criteria, now)
        if reason is None:
            kept.append(job)
        else:
            rejected.append(Rejected(job, reason))
    return kept, rejected
