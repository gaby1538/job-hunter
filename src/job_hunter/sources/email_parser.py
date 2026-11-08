"""Parse job-alert emails into JobPosting objects.

The parser is generic and driven by the per-site rules in sources.yaml:
a link is a job when its URL matches the site's job_url_patterns. The text
lines that sit next to the link in the same email block are kept as context,
and are mapped to fields (company, location...) only if context_fields says so.
"""

from __future__ import annotations

import logging
import re
from email.message import EmailMessage, Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

from job_hunter.config import EmailConfig, EmailSiteRule
from job_hunter.models import JobPosting
from job_hunter.processing.normalize import canonical_url

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_BLOCK_TAGS = ("td", "tr", "table", "div", "li", "p", "section")
_ALLOWED_FIELDS = {"company", "location", "salary_text"}


def identify_site(msg: Message, cfg: EmailConfig) -> tuple[str, EmailSiteRule] | None:
    sender = parseaddr(str(msg.get("From", "")))[1].lower() or str(msg.get("From", "")).lower()
    for name, rule in cfg.sites.items():
        if any(p.lower() in sender for p in rule.sender_patterns):
            return name, rule
    return None


def _body(msg: Message, subtype: str) -> str | None:
    if isinstance(msg, EmailMessage):
        part = msg.get_body(preferencelist=(subtype,))
        if part is not None:
            content = part.get_content()
            return content if isinstance(content, str) else None
        return None
    for sub in msg.walk():
        if sub.get_content_type() == f"text/{subtype}":
            payload = sub.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(sub.get_content_charset() or "utf-8", errors="replace")
    return None


def _is_job_url(url: str, rule: EmailSiteRule) -> bool:
    return any(re.search(p, url, re.IGNORECASE) for p in rule.job_url_patterns)


def _is_ignored(text: str, cfg: EmailConfig) -> bool:
    low = text.lower()
    return any(word.lower() in low for word in cfg.ignore_link_texts)


def _job_id(url: str, rule: EmailSiteRule) -> str | None:
    if not rule.job_id_pattern:
        return None
    m = re.search(rule.job_id_pattern, url)
    return m.group(1) if m else None


def _context_lines(anchor: Tag, title: str, is_job: Any) -> list[str]:
    """Text lines of the largest enclosing block that contains only this one job."""
    own_url = canonical_url(str(anchor.get("href", "")))
    best: Tag | None = None
    for parent in anchor.parents:
        if not isinstance(parent, Tag) or parent.name not in _BLOCK_TAGS:
            continue
        urls = {
            canonical_url(str(a.get("href", "")))
            for a in parent.find_all("a", href=True)
            if is_job(str(a.get("href", "")))
        }
        if urls - {own_url}:
            break
        best = parent
    if best is None:
        return []
    lines = [" ".join(s.split()) for s in best.stripped_strings]
    return [ln for ln in lines if ln and ln != title]


def _postings_from_html(
    html: str, site: str, rule: EmailSiteRule, cfg: EmailConfig, meta: dict[str, Any]
) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")

    def is_job(href: str) -> bool:
        return _is_job_url(href, rule)

    found: dict[str, JobPosting] = {}
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not is_job(href):
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 3 or _is_ignored(title, cfg):
            continue
        url = canonical_url(href)
        if url in found:
            continue
        context = [ln for ln in _context_lines(a, title, is_job) if not _is_ignored(ln, cfg)]
        fields: dict[str, Any] = {}
        for name, value in zip(rule.context_fields, context, strict=False):
            if name in _ALLOWED_FIELDS:
                fields[name] = value
        found[url] = JobPosting(
            source=site,
            channel="email",
            title=title,
            url=url,
            source_job_id=_job_id(href, rule),
            raw={**meta, "context": context, "original_href": href},
            **fields,
        )
    return list(found.values())


def _postings_from_text(
    text: str, site: str, rule: EmailSiteRule, cfg: EmailConfig, meta: dict[str, Any]
) -> list[JobPosting]:
    """Fallback for text-only emails: the title is the closest non-empty line above the URL."""
    found: dict[str, JobPosting] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for href in _URL_RE.findall(line):
            if not _is_job_url(href, rule):
                continue
            before = line[: line.find(href)].strip(" -:|\t")
            title = before
            j = i - 1
            while not title and j >= 0:
                title = lines[j].strip(" -:|\t")
                j -= 1
            if len(title) < 3 or _is_ignored(title, cfg):
                continue
            url = canonical_url(href)
            if url not in found:
                found[url] = JobPosting(
                    source=site,
                    channel="email",
                    title=title,
                    url=url,
                    source_job_id=_job_id(href, rule),
                    raw={**meta, "context": [], "original_href": href},
                )
    return list(found.values())


def parse_alert_email(msg: Message, cfg: EmailConfig) -> list[JobPosting]:
    match = identify_site(msg, cfg)
    if match is None:
        return []
    site, rule = match
    email_date = None
    if msg.get("Date"):
        try:
            email_date = parsedate_to_datetime(str(msg["Date"])).isoformat()
        except (TypeError, ValueError):
            email_date = None
    meta = {
        "email_subject": str(msg.get("Subject", "")),
        "email_from": str(msg.get("From", "")),
        "email_date": email_date,
        "email_message_id": str(msg.get("Message-ID", "")),
    }
    html = _body(msg, "html")
    if html:
        jobs = _postings_from_html(html, site, rule, cfg, meta)
        if jobs:
            return jobs
    text = _body(msg, "plain")
    if text:
        return _postings_from_text(text, site, rule, cfg, meta)
    return []
