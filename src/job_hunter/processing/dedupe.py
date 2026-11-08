"""Merge duplicate ads (same job seen in several alerts or sources)."""

from __future__ import annotations

from job_hunter.models import JobPosting
from job_hunter.processing.normalize import canonical_url, dedupe_key

_FILLABLE = ("company", "location", "description", "salary_text", "published_at", "source_job_id")


def _merge(primary: JobPosting, other: JobPosting) -> JobPosting:
    updates: dict[str, object] = {}
    # Fill gaps in the primary with facts the duplicate states (never overwrite).
    for field in _FILLABLE:
        if getattr(primary, field) is None and getattr(other, field) is not None:
            updates[field] = getattr(other, field)
    seen = list(primary.also_seen_at)
    if other.url and other.url != primary.url and other.url not in seen:
        seen.append(other.url)
    updates["also_seen_at"] = seen
    sources = primary.raw.get("merged_sources", [primary.source])
    if other.source not in sources:
        sources = [*sources, other.source]
    updates["raw"] = {**primary.raw, "merged_sources": sources}
    return primary.model_copy(update=updates)


def dedupe(jobs: list[JobPosting]) -> list[JobPosting]:
    by_key: dict[str, JobPosting] = {}
    url_to_key: dict[str, str] = {}
    for job in jobs:
        key = dedupe_key(job)
        url = canonical_url(job.url) if job.url else None
        if key not in by_key and url and url in url_to_key:
            key = url_to_key[url]
        if key in by_key:
            by_key[key] = _merge(by_key[key], job)
        else:
            by_key[key] = job
        if url:
            url_to_key.setdefault(url, key)
    return list(by_key.values())
