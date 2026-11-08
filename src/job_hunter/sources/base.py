"""Common pieces for all sources: the Source protocol and a retrying HTTP helper."""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from job_hunter.config import SearchCriteria
from job_hunter.models import JobPosting

logger = logging.getLogger(__name__)

USER_AGENT = "job-hunter/0.1 (personal job search tool)"


class SourceError(Exception):
    """A source failed; the pipeline logs it and continues with other sources."""


class Source(Protocol):
    name: str

    def fetch(self, criteria: SearchCriteria) -> list[JobPosting]: ...


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    reraise=True,
)
def request_json(
    client: httpx.Client, method: str, url: str, **kwargs: Any
) -> Any:
    """HTTP call with retries on network errors, 429 and 5xx. Returns parsed JSON."""
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def make_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
