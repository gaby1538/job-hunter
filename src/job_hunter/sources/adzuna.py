"""Adzuna official API source (needs ADZUNA_APP_ID / ADZUNA_APP_KEY)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from job_hunter.config import AdzunaConfig, SearchCriteria
from job_hunter.models import ContractType, JobPosting
from job_hunter.settings import Settings
from job_hunter.sources.base import SourceError, make_client, request_json

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def to_posting(item: dict[str, Any]) -> JobPosting:
    # Adzuna estimates salaries when the ad gives none (salary_is_predicted = "1").
    # An estimate is not information from the ad, so it is not stored as salary.
    predicted = str(item.get("salary_is_predicted", "0")) == "1"
    contract = (
        ContractType.PERMANENT if item.get("contract_type") == "permanent" else ContractType.UNKNOWN
    )
    return JobPosting(
        source="adzuna",
        channel="api",
        title=item.get("title") or "",
        url=item.get("redirect_url"),
        source_job_id=str(item["id"]) if item.get("id") is not None else None,
        company=(item.get("company") or {}).get("display_name"),
        location=(item.get("location") or {}).get("display_name"),
        description=item.get("description"),
        contract_type=contract,
        salary_min=None if predicted else item.get("salary_min"),
        salary_max=None if predicted else item.get("salary_max"),
        published_at=_parse_dt(item.get("created")),
        raw=item,
    )


class AdzunaSource:
    name = "adzuna"

    def __init__(
        self, cfg: AdzunaConfig, settings: Settings, client: httpx.Client | None = None
    ) -> None:
        self.cfg = cfg
        self.settings = settings
        self.client = client or make_client()

    def fetch(self, criteria: SearchCriteria) -> list[JobPosting]:
        if not self.settings.adzuna_app_id or not self.settings.adzuna_app_key:
            raise SourceError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
        jobs: list[JobPosting] = []
        for query in criteria.queries:
            for loc in criteria.locations:
                for page in range(1, self.cfg.max_pages + 1):
                    params: dict[str, Any] = {
                        "app_id": self.settings.adzuna_app_id,
                        "app_key": self.settings.adzuna_app_key.get_secret_value(),
                        "what_phrase": query,
                        "results_per_page": self.cfg.results_per_page,
                        "max_days_old": criteria.max_age_days,
                        "content-type": "application/json",
                    }
                    if loc.where:
                        params["where"] = loc.where
                    url = f"{self.cfg.base_url}/{criteria.country}/search/{page}"
                    try:
                        data = request_json(self.client, "GET", url, params=params)
                    except httpx.HTTPError as exc:
                        raise SourceError(f"Adzuna request failed: {exc}") from exc
                    results = (data or {}).get("results", [])
                    for item in results:
                        try:
                            jobs.append(to_posting(item))
                        except ValueError:
                            logger.warning("skipping invalid Adzuna item %s", item.get("id"))
                    if len(results) < self.cfg.results_per_page:
                        break
                    if len(jobs) >= criteria.max_results_per_source:
                        return jobs
        return jobs
