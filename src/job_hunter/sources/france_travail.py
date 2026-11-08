"""France Travail 'Offres d'emploi v2' API source (OAuth2 client credentials).

Disabled by default: France Travail has paused new API access requests.
Endpoints and scope are parameters in sources.yaml.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from job_hunter.config import FranceTravailConfig, SearchCriteria
from job_hunter.models import ContractType, JobPosting
from job_hunter.settings import Settings
from job_hunter.sources.base import SourceError, make_client, request_json

logger = logging.getLogger(__name__)

# Only codes whose meaning is unambiguous are mapped; the rest stay unknown.
_CONTRACT_MAP = {
    "CDI": ContractType.PERMANENT,
    "CDD": ContractType.FIXED_TERM,
    "LIB": ContractType.FREELANCE,
}
_ALLOWED_PUBLISHED_SINCE = (1, 3, 7, 14, 31)
DETAIL_URL = "https://candidat.francetravail.fr/offres/recherche/detail/{id}"


def _published_since(max_age_days: int) -> int:
    for d in _ALLOWED_PUBLISHED_SINCE:
        if d >= max_age_days:
            return d
    return _ALLOWED_PUBLISHED_SINCE[-1]


def to_posting(item: dict[str, Any]) -> JobPosting:
    job_id = item.get("id")
    url = (item.get("origineOffre") or {}).get("urlOrigine") or (
        DETAIL_URL.format(id=job_id) if job_id else None
    )
    created = item.get("dateCreation")
    return JobPosting(
        source="france_travail",
        channel="api",
        title=item.get("intitule") or "",
        url=url,
        source_job_id=str(job_id) if job_id else None,
        company=(item.get("entreprise") or {}).get("nom"),
        location=(item.get("lieuTravail") or {}).get("libelle"),
        description=item.get("description"),
        contract_type=_CONTRACT_MAP.get(str(item.get("typeContrat", "")), ContractType.UNKNOWN),
        salary_text=(item.get("salaire") or {}).get("libelle"),
        published_at=datetime.fromisoformat(created) if created else None,
        raw=item,
    )


class FranceTravailSource:
    name = "france_travail"

    def __init__(
        self, cfg: FranceTravailConfig, settings: Settings, client: httpx.Client | None = None
    ) -> None:
        self.cfg = cfg
        self.settings = settings
        self.client = client or make_client()

    def _token(self) -> str:
        if not self.settings.france_travail_client_id or not self.settings.france_travail_client_secret:
            raise SourceError("FRANCE_TRAVAIL_CLIENT_ID / _SECRET not set")
        try:
            data = request_json(
                self.client,
                "POST",
                self.cfg.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.france_travail_client_id,
                    "client_secret": self.settings.france_travail_client_secret.get_secret_value(),
                    "scope": self.cfg.scope,
                },
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"France Travail auth failed: {exc}") from exc
        token = (data or {}).get("access_token")
        if not token:
            raise SourceError("France Travail auth returned no access_token")
        return str(token)

    def fetch(self, criteria: SearchCriteria) -> list[JobPosting]:
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        jobs: list[JobPosting] = []
        for query in criteria.queries:
            params = {
                "motsCles": query,
                "range": f"0-{self.cfg.page_size - 1}",
                "publieeDepuis": _published_since(criteria.max_age_days),
            }
            try:
                data = request_json(
                    self.client, "GET", self.cfg.search_url, params=params, headers=headers
                )
            except httpx.HTTPError as exc:
                raise SourceError(f"France Travail search failed: {exc}") from exc
            for item in (data or {}).get("resultats", []):
                try:
                    jobs.append(to_posting(item))
                except ValueError:
                    logger.warning("skipping invalid France Travail item %s", item.get("id"))
            if len(jobs) >= criteria.max_results_per_source:
                break
        return jobs
