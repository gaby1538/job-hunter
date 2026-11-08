"""Capture full job descriptions that you copy yourself.

When an ad looks interesting, copy its text from the page you are reading and email it
to your alert inbox with a subject starting with "JD" (configurable). Put the job's link
on the first line. The next run attaches the text to the matching job and re-analyses it.
The same works from the command line:  python -m job_hunter capture --url ... --file ...

No site is fetched automatically: the text comes from a page you opened yourself.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from email.message import EmailMessage, Message
from email.utils import parseaddr

from bs4 import BeautifulSoup

from job_hunter.config import CaptureConfig, EmailConfig
from job_hunter.models import JobPosting
from job_hunter.processing.normalize import canonical_url
from job_hunter.sources.email_parser import _is_job_url, _job_id, _stable_url, resolve_url
from job_hunter.store import JobStore, StoredJob

logger = logging.getLogger(__name__)
_URL_RE = re.compile(r"https?://[^\s<>\"'\[\]()]+")


@dataclass
class CaptureItem:
    text: str
    url: str | None = None
    title_hint: str | None = None


def is_capture_email(msg: Message, cfg: CaptureConfig, senders: list[str]) -> bool:
    if not cfg.enabled or not senders:
        return False
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    subject = str(msg.get("Subject", "")).strip()
    # Forwarded mails get "Fwd:"/"TR:" prefixes; tolerate them.
    subject = re.sub(r"^((fwd?|tr|re)\s*:\s*)+", "", subject, flags=re.I)
    return sender in senders and subject.upper().startswith(cfg.subject_prefix.upper())


def parse_capture_email(msg: Message, cfg: CaptureConfig) -> CaptureItem | None:
    body = None
    if isinstance(msg, EmailMessage):
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            if isinstance(content, str):
                body = content
                if part.get_content_type() == "text/html":
                    body = BeautifulSoup(content, "html.parser").get_text("\n")
    if not body:
        return None
    url_match = _URL_RE.search(body)
    url = url_match.group(0) if url_match else None
    text = _URL_RE.sub("", body, count=1).strip() if url else body.strip()
    subject = re.sub(r"^((fwd?|tr|re)\s*:\s*)+", "", str(msg.get("Subject", "")), flags=re.I)
    title = subject.strip()[len(cfg.subject_prefix):].strip(" :-") or None
    if len(text) < cfg.min_chars:
        logger.warning("capture email %r ignored: text too short", msg.get("Subject"))
        return None
    return CaptureItem(text=text, url=url, title_hint=title)


def stable_job_url(url: str, email_cfg: EmailConfig) -> str:
    """Rebuild the same stable URL the alert parser produces, whatever site it is."""
    for rule in email_cfg.sites.values():
        resolved = resolve_url(url, rule)
        if _is_job_url(resolved, rule):
            return _stable_url(resolved, _job_id(resolved, rule), rule)
    return canonical_url(url)


def apply_capture(store: JobStore, item: CaptureItem, email_cfg: EmailConfig) -> StoredJob:
    """Attach the text to the matching stored job, or store it as a new job."""
    rec = None
    if item.url:
        rec = store.find_by_url(stable_job_url(item.url, email_cfg))
    if rec is None:
        first_line = next((ln.strip() for ln in item.text.splitlines() if ln.strip()), "")
        job = JobPosting(
            source="manual",
            channel="email",
            title=item.title_hint or first_line[:150] or "Captured job",
            url=stable_job_url(item.url, email_cfg) if item.url else None,
        )
        rec, _ = store.upsert(job)
        logger.info("capture: no matching job, stored as new: %s", rec.job.title)
    else:
        logger.info("capture: description attached to %s", rec.job.title)
    store.attach_description(rec, item.text)
    return rec
