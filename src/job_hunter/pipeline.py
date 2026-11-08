"""Pipeline: fetch sources -> normalize -> filter -> dedupe -> store -> captures -> analyse -> report."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from job_hunter.config import AppConfig
from job_hunter.models import JobPosting
from job_hunter.processing.dedupe import dedupe
from job_hunter.processing.filters import Rejected, apply_filters
from job_hunter.processing.normalize import normalize
from job_hunter.settings import Settings
from job_hunter.sources.adzuna import AdzunaSource
from job_hunter.sources.base import Source
from job_hunter.sources.email_alerts import EmailAlertSource
from job_hunter.sources.employers import EmployerSource
from job_hunter.sources.france_travail import FranceTravailSource
from job_hunter.sources.rss import RssSource

logger = logging.getLogger(__name__)
PARIS = ZoneInfo("Europe/Paris")


@dataclass
class RunResult:
    started_at: datetime
    kept: list[JobPosting]
    rejected: list[Rejected]
    per_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    duplicates_merged: int = 0


def build_sources(
    config: AppConfig,
    settings: Settings,
    only: set[str] | None = None,
    eml_dir: Path | None = None,
) -> list[Source]:
    s = config.sources
    candidates: list[tuple[bool, Source]] = [
        (s.email.enabled, EmailAlertSource(s.email, settings, eml_dir, s.capture)),
        (s.adzuna.enabled, AdzunaSource(s.adzuna, settings)),
        (s.employers.enabled, EmployerSource(s.employers)),
        (s.rss.enabled, RssSource(s.rss)),
    ]
    if s.france_travail is not None:
        candidates.append((s.france_travail.enabled, FranceTravailSource(s.france_travail, settings)))
    if only:
        return [src for _, src in candidates if src.name in only]
    return [src for enabled, src in candidates if enabled]


def run(config: AppConfig, sources: list[Source], now: datetime | None = None) -> RunResult:
    started = now or datetime.now(UTC)
    collected: list[JobPosting] = []
    result = RunResult(started_at=started, kept=[], rejected=[])
    for source in sources:
        try:
            jobs = source.fetch(config.search)[: config.search.max_results_per_source]
        except Exception as exc:  # noqa: BLE001 - a failing source must not stop the others
            logger.error("source %s failed: %s", source.name, exc)
            result.errors[source.name] = str(exc)
            continue
        for job in jobs:
            result.per_source[job.source] = result.per_source.get(job.source, 0) + 1
        collected.extend(jobs)

    normalized = [normalize(j) for j in collected]
    kept, rejected = apply_filters(normalized, config.search, now=started)
    unique = dedupe(kept)
    result.kept = unique
    result.rejected = rejected
    result.duplicates_merged = len(kept) - len(unique)
    logger.info(
        "collected=%d kept=%d rejected=%d duplicates_merged=%d errors=%d",
        len(collected), len(unique), len(rejected), result.duplicates_merged, len(result.errors),
    )
    return result


_CSV_COLUMNS = [
    "fingerprint", "source", "channel", "title", "company", "location", "contract_type",
    "salary_min", "salary_max", "salary_text", "published_at_paris", "url", "also_seen_at",
]


def _paris(dt: datetime | None) -> str:
    return dt.astimezone(PARIS).strftime("%Y-%m-%d %H:%M") if dt else ""


def export(result: RunResult, out_dir: Path) -> Path:
    """Write jobs.jsonl (full data, UTC), jobs.csv (readable, Paris time), rejected.csv, summary.json."""
    run_dir = out_dir / result.started_at.astimezone(PARIS).strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    with (run_dir / "jobs.jsonl").open("w", encoding="utf-8") as fh:
        for job in result.kept:
            record: dict[str, Any] = job.model_dump(mode="json")
            record["fingerprint"] = job.fingerprint
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    with (run_dir / "jobs.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for job in result.kept:
            writer.writerow({
                "fingerprint": job.fingerprint,
                "source": job.source,
                "channel": job.channel,
                "title": job.title,
                "company": job.company or "",
                "location": job.location or "",
                "contract_type": job.contract_type.value,
                "salary_min": job.salary_min if job.salary_min is not None else "",
                "salary_max": job.salary_max if job.salary_max is not None else "",
                "salary_text": job.salary_text or "",
                "published_at_paris": _paris(job.published_at),
                "url": job.url or "",
                "also_seen_at": " ".join(job.also_seen_at),
            })

    with (run_dir / "rejected.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        rej_writer = csv.writer(fh)
        rej_writer.writerow(["source", "title", "reason", "url"])
        for r in result.rejected:
            rej_writer.writerow([r.job.source, r.job.title, r.reason, r.job.url or ""])

    summary = {
        "started_at_utc": result.started_at.isoformat(),
        "started_at_paris": _paris(result.started_at),
        "collected_per_source": result.per_source,
        "kept": len(result.kept),
        "rejected": len(result.rejected),
        "duplicates_merged": result.duplicates_merged,
        "source_errors": result.errors,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------- daily run

@dataclass
class DailyResult:
    extraction: RunResult
    new_jobs: int
    captures_applied: int
    analysis: Any  # AnalysisRun | None
    shortlist: Path | None


def daily(
    config: AppConfig,
    sources: list[Source],
    store: Any,      # JobStore
    analyzer: Any,   # Analyzer | None (None = skip analysis, e.g. no API key)
    out_dir: Path,
    analysis_limit: int,
    now: datetime | None = None,
) -> DailyResult:
    from job_hunter.analysis.report import write_reports
    from job_hunter.capture import apply_capture

    result = run(config, sources, now=now)
    new_jobs = sum(1 for job in result.kept if store.upsert(job)[1])

    captures = 0
    for src in sources:
        for item in getattr(src, "captures", []):
            apply_capture(store, item, config.sources.email)
            captures += 1

    analysis = analyzer.analyse_pending(store, analysis_limit) if analyzer else None
    store.save()
    run_dir = export(result, out_dir)
    shortlist, _ = write_reports(store, run_dir, result.started_at)
    logger.info("new jobs=%d captures=%d", new_jobs, captures)
    return DailyResult(result, new_jobs, captures, analysis, shortlist)
