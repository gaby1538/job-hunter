"""Parse job-alert emails into JobPosting objects.

Everything site-specific lives in sources.yaml (EmailSiteRule). Two modes:

links  - a digest listing several jobs. Each job is a block of lines tied to a
         job link. In the text part the details come BEFORE the link
         (Indeed, LinkedIn, Free-Work); in the HTML part they come AFTER the
         title link (HelloWork). Lines are turned into fields with regex
         patterns; whatever is not recognised is kept as raw context.
single - the whole email is one offer (Malt). Fields come from a CSS selector,
         the subject line, and "label -> value" pairs.

Privacy: tracking links (which can embed your email address) are never stored;
only the decoded, cleaned job URL is kept.
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage, Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import Comment, NavigableString

from job_hunter.config import EmailConfig, EmailSiteRule
from job_hunter.models import ContractType, JobPosting
from job_hunter.processing.normalize import canonical_url, contract_from_text

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"'\[\]()]+")
_B64_RE = re.compile(r"[A-Za-z0-9_\-+/]{16,}={0,2}")
_FIELD_TARGETS = {"company", "location", "salary_text", "remote_text", "description"}
_SKIP_TAGS = ("style", "script", "head", "title")


# ---------------------------------------------------------------- helpers

def identify_site(msg: Message, cfg: EmailConfig) -> tuple[str, EmailSiteRule] | None:
    sender = parseaddr(str(msg.get("From", "")))[1].lower() or str(msg.get("From", "")).lower()
    subject = str(msg.get("Subject", ""))
    for name, rule in cfg.sites.items():
        if not any(p.lower() in sender for p in rule.sender_patterns):
            continue
        if rule.subject_patterns and not any(
            re.search(p, subject, re.IGNORECASE) for p in rule.subject_patterns
        ):
            continue
        return name, rule
    return None


def _body(msg: Message, subtype: str) -> str | None:
    if isinstance(msg, EmailMessage):
        part = msg.get_body(preferencelist=(subtype,))
        if part is not None and part.get_content_type() == f"text/{subtype}":
            content = part.get_content()
            return content if isinstance(content, str) else None
        return None
    for sub in msg.walk():
        if sub.get_content_type() == f"text/{subtype}":
            payload = sub.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(sub.get_content_charset() or "utf-8", errors="replace")
    return None


def _unwrap_redirect(url: str) -> str | None:
    """Find the real URL hidden in a click-tracking link (plain, %-encoded or base64)."""
    parts = urlsplit(url)
    candidates = [unquote(s) for s in parts.path.split("/") if s]
    candidates += [v for _, v in parse_qsl(parts.query)]
    for c in candidates:
        if c.startswith("http"):
            return c
        if _B64_RE.fullmatch(c):
            try:
                decoded = base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)).decode("utf-8", "ignore")
            except (ValueError, binascii.Error):
                continue
            m = _URL_RE.search(decoded)
            if m:
                return m.group(0)
    return None


def resolve_url(href: str, rule: EmailSiteRule) -> str:
    href = html_lib.unescape(href.strip())
    if rule.unwrap_redirects:
        return _unwrap_redirect(href) or href
    return href


def _is_job_url(url: str, rule: EmailSiteRule) -> bool:
    return any(re.search(p, url, re.IGNORECASE) for p in rule.job_url_patterns)


def _job_id(url: str, rule: EmailSiteRule) -> str | None:
    if not rule.job_id_pattern:
        return None
    m = re.search(rule.job_id_pattern, url)
    return m.group(1) if m else None


def _stable_url(url: str, job_id: str | None, rule: EmailSiteRule) -> str:
    if job_id and rule.canonical_url:
        return rule.canonical_url.format(id=job_id)
    return canonical_url(url)


def _norm_quotes(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def _is_ignored(text: str, cfg: EmailConfig) -> bool:
    low = _norm_quotes(text.lower())
    return any(_norm_quotes(w.lower()) in low for w in cfg.ignore_link_texts)


def _clean_line(line: str) -> str:
    return " ".join(line.replace("‌", "").replace("\xa0", " ").split())


_RELATIVE = re.compile(
    r"(\d+)\s*\+?\s*(heures?|hours?|h\b|jours?|days?|semaines?|weeks?|mois|months?)", re.I
)


def parse_relative_date(text: str, reference: datetime | None) -> datetime | None:
    """'il y a 10 jours' / '3 days ago' relative to the email date. None if unclear."""
    if reference is None:
        return None
    low = text.lower()
    if "aujourd" in low or "today" in low or "just posted" in low:
        return reference
    m = _RELATIVE.search(low)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith(("heure", "hour", "h")):
        return reference - timedelta(hours=n)
    if unit.startswith(("jour", "day")):
        return reference - timedelta(days=n)
    if unit.startswith(("semaine", "week")):
        return reference - timedelta(weeks=n)
    return reference - timedelta(days=30 * n)


# ---------------------------------------------------------------- job assembly

@dataclass
class _Block:
    url: str
    title: str | None = None
    lines: list[str] = field(default_factory=list)


@dataclass
class _Parsed:
    title: str | None
    fields: dict[str, str]
    context: list[str]


def _read_lines(block: _Block, rule: EmailSiteRule) -> _Parsed:
    skip = [re.compile(p, re.I) for p in rule.skip_line_patterns]
    pats = [re.compile(p, re.I) for p in rule.line_patterns]
    title = block.title
    fields: dict[str, str] = {}
    context: list[str] = []
    for raw in block.lines:
        line = _clean_line(raw)
        if not line or any(s.search(line) for s in skip):
            continue
        if title is None and rule.title_first:
            title = line
            continue
        claimed = False
        for p in pats:
            m = p.search(line)
            if m:
                found = {k: v.strip() for k, v in m.groupdict().items() if v and v.strip()}
                new = {k: v for k, v in found.items() if k not in fields}
                if new:
                    fields.update(new)
                    claimed = True
                    break
        if claimed:
            continue
        if title is None:
            title = line
            continue
        if len(context) < rule.max_context_lines:
            context.append(line)
    for name, value in zip(rule.context_fields, context, strict=False):
        fields.setdefault(name, value)
    return _Parsed(title, fields, context)


def _to_posting(
    site: str, rule: EmailSiteRule, url: str, parsed: _Parsed, meta: dict[str, Any],
    email_date: datetime | None,
) -> JobPosting | None:
    if not parsed.title or len(parsed.title) < 3:
        return None
    job_id = _job_id(url, rule)
    kwargs: dict[str, Any] = {k: v for k, v in parsed.fields.items() if k in _FIELD_TARGETS}
    details = {k: v for k, v in parsed.fields.items() if k not in _FIELD_TARGETS}
    contract = ContractType.UNKNOWN
    if "contract" in parsed.fields:
        contract = contract_from_text(parsed.fields["contract"])
    if contract is ContractType.UNKNOWN and rule.default_contract is not None:
        contract = rule.default_contract
        details["contract_source"] = "site default"
    published_at = None
    if "posted_text" in parsed.fields:
        published_at = parse_relative_date(parsed.fields["posted_text"], email_date)
    return JobPosting(
        source=site,
        channel="email",
        title=parsed.title,
        url=_stable_url(url, job_id, rule),
        source_job_id=job_id,
        contract_type=contract,
        published_at=published_at,
        salary_may_be_estimate=rule.salary_may_be_estimate and bool(kwargs.get("salary_text")),
        raw={**meta, "context": parsed.context, "details": details},
        **kwargs,
    )


# ---------------------------------------------------------------- links mode: text part

def _blocks_from_text(text: str, rule: EmailSiteRule) -> list[_Block]:
    """Job details sit above the job link; a block runs from the previous link to this one."""
    blocks: list[_Block] = []
    seen: set[str] = set()
    pending: list[str] = []
    for line in text.splitlines():
        urls = _URL_RE.findall(line)
        if not urls:
            pending.append(line)
            continue
        before = _URL_RE.sub("", line).replace("[]", "").strip(" :-|[]<>")
        resolved = [resolve_url(u, rule) for u in urls]
        job_urls = [u for u in resolved if _is_job_url(u, rule)]
        if job_urls:
            key = canonical_url(job_urls[0])
            if key not in seen:
                seen.add(key)
                blocks.append(_Block(url=job_urls[0], lines=[*pending, before]))
        pending = []  # any link closes the current block
    return blocks


# ---------------------------------------------------------------- links mode: HTML part

def _html_tokens(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """Document-order tokens: ('link', href, text) or ('text', '', text)."""
    tokens: list[tuple[str, str, str]] = []
    for el in soup.descendants:
        if isinstance(el, Tag) and el.name == "a":
            text = _clean_line(el.get_text(" ", strip=True))
            tokens.append(("link", str(el.get("href", "")), text))
        elif isinstance(el, NavigableString) and not isinstance(el, Comment):
            if el.find_parent("a") is not None or el.find_parent(_SKIP_TAGS) is not None:
                continue
            text = _clean_line(str(el))
            if text:
                tokens.append(("text", "", text))
    return tokens


def _blocks_from_html(
    html: str, rule: EmailSiteRule, cfg: EmailConfig
) -> list[_Block]:
    """Job details sit below the title link; a block runs until the next real link."""
    blocks: list[_Block] = []
    seen: set[str] = set()
    current: _Block | None = None
    recent: list[str] = []  # text seen since the last link
    for kind, href, text in _html_tokens(BeautifulSoup(html, "html.parser")):
        if kind == "text":
            recent.append(text)
            if current is not None:
                current.lines.append(text)
            continue
        if href in ("", "#") or href.startswith("mailto:"):
            continue  # decorative link inside a card: not a boundary
        url = resolve_url(href, rule)
        if _is_job_url(url, rule):
            key = canonical_url(url)
            if current is not None and key == canonical_url(current.url):
                current = None  # second link to the same job (e.g. "See offer"): end of card
                recent = []
                continue
            # Title is the link text, or - for icon links - the text just before the link.
            title = text or (recent[-1] if recent else "")
            if key not in seen and title and not _is_ignored(title, cfg):
                seen.add(key)
                if not text and current is not None and current.lines[-1:] == [title]:
                    current.lines.pop()
                current = _Block(url=url, title=title)
                blocks.append(current)
                recent = []
                continue
        if text or not _is_job_url(url, rule):
            current = None  # any other real link ("see all", footer) closes the card
        recent = []
    return blocks


# ---------------------------------------------------------------- single mode

def _single_offer(
    html: str, subject: str, site: str, rule: EmailSiteRule, meta: dict[str, Any],
    email_date: datetime | None,
) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")
    url = None
    for a in soup.find_all("a", href=True):
        candidate = resolve_url(str(a["href"]), rule)
        if _is_job_url(candidate, rule):
            url = candidate
            break
    if url is None:
        return []
    fields: dict[str, str] = {}
    if rule.subject_fields:
        m = re.search(rule.subject_fields, subject, re.IGNORECASE)
        if m:
            fields.update({k: v.strip() for k, v in m.groupdict().items() if v})
    wanted = {_norm_quotes(k.lower()): v for k, v in rule.labels.items()}
    for el in soup.find_all(string=True):
        label = _norm_quotes(_clean_line(str(el)).lower())
        target = wanted.get(label)
        if target is None or target in fields or el.parent is None:
            continue
        nxt = el.parent.find_next_sibling()
        if nxt is not None:
            value = _clean_line(nxt.get_text(" ", strip=True))
            if value:
                fields[target] = value
    title = None
    if rule.title_selector:
        node = soup.select_one(rule.title_selector)
        if node is not None:
            title = _clean_line(node.get_text(" ", strip=True))
    posting = _to_posting(site, rule, url, _Parsed(title, fields, []), meta, email_date)
    return [posting] if posting else []


# ---------------------------------------------------------------- entry point

def parse_alert_email(msg: Message, cfg: EmailConfig) -> list[JobPosting]:
    match = identify_site(msg, cfg)
    if match is None:
        return []
    site, rule = match
    email_date: datetime | None = None
    if msg.get("Date"):
        try:
            email_date = parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            email_date = None
    subject = str(msg.get("Subject", ""))
    meta = {
        "email_subject": subject,
        "email_date": email_date.isoformat() if email_date else None,
        "email_message_id": str(msg.get("Message-ID", "")),
    }
    html = _body(msg, "html")
    text = _body(msg, "plain")

    if rule.mode == "single":
        return _single_offer(html or "", subject, site, rule, meta, email_date) if html else []

    blocks: list[_Block] = []
    if rule.body == "text" and text:
        blocks = _blocks_from_text(text, rule)
    if not blocks and html:
        blocks = _blocks_from_html(html, rule, cfg)
    if not blocks and text and rule.body != "text":
        blocks = _blocks_from_text(text, rule)

    jobs: list[JobPosting] = []
    for block in blocks:
        posting = _to_posting(site, rule, block.url, _read_lines(block, rule), meta, email_date)
        if posting is not None:
            jobs.append(posting)
    return jobs
