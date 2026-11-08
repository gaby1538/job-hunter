"""Job store: every job seen, its best description, its analysis and score.

A JSON file for the prototype (data/jobs.json). The interface is small on purpose,
so it can be swapped for PostgreSQL (Supabase) later without touching callers.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from job_hunter.models import JobPosting, utc_now
from job_hunter.processing.dedupe import _merge
from job_hunter.processing.normalize import canonical_url

DescriptionSource = Literal["none", "source", "capture"]

# Below this length a description is treated as a snippet, not the full ad.
FULL_TEXT_MIN_CHARS = 800


class StoredJob(BaseModel):
    fingerprint: str
    job: JobPosting
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    captured_description: str | None = None
    captured_at: datetime | None = None
    analysis: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    analyzed_text_hash: str | None = None
    analyzed_at: datetime | None = None
    status: str = "new"

    # ---- text used for analysis -------------------------------------------------
    @property
    def description_source(self) -> DescriptionSource:
        if self.captured_description:
            return "capture"
        if self.job.description:
            return "source"
        return "none"

    @property
    def has_full_text(self) -> bool:
        if self.captured_description:
            return True  # you copied the whole ad yourself
        return len(self.job.description or "") >= FULL_TEXT_MIN_CHARS

    def analysis_text(self) -> str:
        """Everything known about the job, labelled, for the analysis prompt."""
        j = self.job
        lines = [f"Title: {j.title}"]
        for label, value in (
            ("Company", j.company), ("Location", j.location),
            ("Contract (as published)", j.raw.get("details", {}).get("contract")),
            ("Salary / rate (as published)", j.salary_text),
            ("Remote policy (as published)", j.remote_text), ("Source", j.source),
        ):
            if value:
                lines.append(f"{label}: {value}")
        details = {k: v for k, v in j.raw.get("details", {}).items()
                   if k not in ("contract", "contract_source", "posted_text")}
        for k, v in details.items():
            lines.append(f"{k.replace('_', ' ').capitalize()}: {v}")
        body = self.captured_description or j.description
        if body:
            lines.append("\nJob description:\n" + body.strip())
        elif j.raw.get("context"):
            lines.append("\nExcerpt from the alert (not the full ad):\n" + "\n".join(j.raw["context"]))
        return "\n".join(lines)

    def text_hash(self) -> str:
        return hashlib.sha256(self.analysis_text().encode()).hexdigest()[:16]

    @property
    def needs_analysis(self) -> bool:
        return self.analyzed_text_hash != self.text_hash()


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.jobs: dict[str, StoredJob] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.jobs = {fp: StoredJob.model_validate(rec) for fp, rec in data.items()}

    # ---- lookups ------------------------------------------------------------------
    def find_by_url(self, url: str) -> StoredJob | None:
        target = canonical_url(url)
        for rec in self.jobs.values():
            urls = [rec.job.url or "", *rec.job.also_seen_at]
            if any(u and canonical_url(u) == target for u in urls):
                return rec
        return None

    # ---- writes -------------------------------------------------------------------
    def upsert(self, job: JobPosting) -> tuple[StoredJob, bool]:
        """Add a job or merge it into the one already stored. Returns (record, is_new)."""
        existing = self.jobs.get(job.fingerprint) or (
            self.find_by_url(job.url) if job.url else None
        )
        if existing is None:
            rec = StoredJob(fingerprint=job.fingerprint, job=job)
            self.jobs[rec.fingerprint] = rec
            return rec, True
        existing.job = _merge(existing.job, job)
        existing.last_seen_at = utc_now()
        return existing, False

    def attach_description(self, rec: StoredJob, text: str) -> None:
        rec.captured_description = text.strip()
        rec.captured_at = utc_now()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {fp: rec.model_dump(mode="json") for fp, rec in self.jobs.items()}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # atomic: never leaves a half-written store
