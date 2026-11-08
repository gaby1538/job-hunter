"""RSS 2.0 / Atom feed source, for job boards that publish feeds."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from job_hunter.config import RssConfig, SearchCriteria
from job_hunter.models import JobPosting
from job_hunter.sources.base import SourceError, make_client

logger = logging.getLogger(__name__)
_ATOM = "{http://www.w3.org/2005/Atom}"


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _text(html: str | None) -> str | None:
    if not html:
        return None
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split()) or None


def parse_feed(xml_text: str, feed_name: str) -> list[JobPosting]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(f"feed {feed_name}: invalid XML ({exc})") from exc
    jobs: list[JobPosting] = []
    for item in root.iter("item"):  # RSS 2.0
        title = item.findtext("title") or ""
        if not title.strip():
            continue
        jobs.append(
            JobPosting(
                source=feed_name,
                channel="rss",
                title=title,
                url=(item.findtext("link") or "").strip() or None,
                source_job_id=item.findtext("guid"),
                description=_text(item.findtext("description")),
                published_at=_date(item.findtext("pubDate")),
                raw={"feed": feed_name},
            )
        )
    for entry in root.iter(f"{_ATOM}entry"):  # Atom
        title = entry.findtext(f"{_ATOM}title") or ""
        if not title.strip():
            continue
        link = entry.find(f"{_ATOM}link")
        jobs.append(
            JobPosting(
                source=feed_name,
                channel="rss",
                title=title,
                url=link.get("href") if link is not None else None,
                source_job_id=entry.findtext(f"{_ATOM}id"),
                description=_text(
                    entry.findtext(f"{_ATOM}summary") or entry.findtext(f"{_ATOM}content")
                ),
                published_at=_date(
                    entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")
                ),
                raw={"feed": feed_name},
            )
        )
    return jobs


class RssSource:
    name = "rss"

    def __init__(self, cfg: RssConfig, client: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.client = client or make_client()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _get(self, url: str) -> str:
        r = self.client.get(url)
        r.raise_for_status()
        return r.text

    def fetch(self, criteria: SearchCriteria) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for feed in self.cfg.feeds:
            try:
                jobs.extend(parse_feed(self._get(feed.url), feed.name))
            except (httpx.HTTPError, SourceError) as exc:
                logger.error("RSS feed %s failed: %s", feed.name, exc)
        return jobs
