"""Employer watchlist: full job descriptions from employers' own public hiring APIs.

Many firms publish their open roles through a hiring system (ATS) whose job board has
a public, unauthenticated read API designed for reuse on other sites:
  - Greenhouse: boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
  - Lever:      api.lever.co/v0/postings/{board}?mode=json
  - Ashby:      api.ashbyhq.com/posting-api/job-board/{board}
Find a firm's board name in the URL of its careers page (e.g. jobs.lever.co/<board>).

Field names for Lever and Ashby descriptions are read defensively (several known
variants), because they are not fully documented; check a live response once.
"""

from __future__ import annotations

import html as html_lib
import logging
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from job_hunter.config import Employer, EmployersConfig, SearchCriteria
from job_hunter.models import JobPosting
from job_hunter.sources.base import make_client, request_json

logger = logging.getLogger(__name__)

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{board}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true",
}


def _html_to_text(value: str | None) -> str | None:
    if not value:
        return None
    soup = BeautifulSoup(html_lib.unescape(value), "html.parser")
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return text or None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):  # Lever: epoch milliseconds
        from datetime import UTC
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _first(item: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if item.get(k):
            return item[k]
    return None


def from_greenhouse(item: dict[str, Any], employer: Employer) -> JobPosting:
    return JobPosting(
        source=f"greenhouse:{employer.board}",
        channel="api",
        title=item.get("title") or "",
        company=employer.name,
        url=item.get("absolute_url"),
        source_job_id=str(item["id"]) if item.get("id") is not None else None,
        location=(item.get("location") or {}).get("name"),
        description=_html_to_text(item.get("content")),
        published_at=_dt(_first(item, "first_published", "updated_at")),
        raw={"ats": "greenhouse"},
    )


def from_lever(item: dict[str, Any], employer: Employer) -> JobPosting:
    parts = [_first(item, "descriptionPlain") or _html_to_text(item.get("description"))]
    for block in item.get("lists") or []:
        body = _html_to_text(block.get("content"))
        if body:
            parts.append(f"{block.get('text', '')}\n{body}")
    parts.append(_first(item, "additionalPlain") or _html_to_text(item.get("additional")))
    categories = item.get("categories") or {}
    return JobPosting(
        source=f"lever:{employer.board}",
        channel="api",
        title=item.get("text") or "",
        company=employer.name,
        url=item.get("hostedUrl"),
        source_job_id=item.get("id"),
        location=categories.get("location"),
        description="\n\n".join(p for p in parts if p) or None,
        remote_text=item.get("workplaceType"),
        published_at=_dt(item.get("createdAt")),
        raw={"ats": "lever", "commitment": categories.get("commitment")},
    )


def from_ashby(item: dict[str, Any], employer: Employer) -> JobPosting:
    return JobPosting(
        source=f"ashby:{employer.board}",
        channel="api",
        title=item.get("title") or "",
        company=employer.name,
        url=_first(item, "jobUrl", "applyUrl"),
        source_job_id=item.get("id"),
        location=item.get("location") if isinstance(item.get("location"), str) else None,
        description=_first(item, "descriptionPlain") or _html_to_text(item.get("descriptionHtml")),
        remote_text="remote" if item.get("isRemote") else item.get("workplaceType"),
        published_at=_dt(item.get("publishedAt")),
        raw={"ats": "ashby", "employment_type": item.get("employmentType")},
    )


MAPPERS = {"greenhouse": from_greenhouse, "lever": from_lever, "ashby": from_ashby}


def _items(ats: str, data: Any) -> list[dict[str, Any]]:
    if ats == "lever":
        return data if isinstance(data, list) else []
    return list((data or {}).get("jobs", []))


class EmployerSource:
    name = "employers"

    def __init__(self, cfg: EmployersConfig, client: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.client = client or make_client()

    def fetch(self, criteria: SearchCriteria) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for employer in self.cfg.companies:
            url = URLS[employer.ats].format(board=employer.board)
            try:
                data = request_json(self.client, "GET", url)
            except httpx.HTTPError as exc:  # one employer down must not stop the others
                logger.error("employer %s (%s) failed: %s", employer.name, employer.ats, exc)
                continue
            for item in _items(employer.ats, data):
                try:
                    jobs.append(MAPPERS[employer.ats](item, employer))
                except ValueError:
                    logger.warning("skipping invalid %s item from %s", employer.ats, employer.name)
        return jobs
