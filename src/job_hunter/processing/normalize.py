"""Text and URL normalization, explicit contract detection, deduplication keys."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_hunter.models import ContractType

if TYPE_CHECKING:
    from job_hunter.models import JobPosting

TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "trk", "trkemail", "refid", "trackingid",
    "lipi", "midtoken", "midsig", "eid", "otptoken", "ssid", "src", "source", "from",
    "alid", "tk", "sid", "tmtk", "qd", "rd", "bb", "camk", "xkcb", "jsa", "plid",
}
_GENDER_TAGS = re.compile(r"\(?\b[hfm]\s*/\s*[hfm](\s*/\s*[a-z])?\b\)?", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9]+")

# Contract words that, when present in the TITLE, state the contract explicitly.
_CONTRACT_PATTERNS: list[tuple[re.Pattern[str], ContractType]] = [
    (re.compile(r"\b(stage|stagiaire|internship|intern)\b", re.IGNORECASE), ContractType.INTERNSHIP),
    (re.compile(r"\b(freelance|free-lance|ind[ée]pendant|portage)\b", re.IGNORECASE), ContractType.FREELANCE),
    (re.compile(r"\bCDD\b"), ContractType.FIXED_TERM),
    (re.compile(r"\bCDI\b"), ContractType.PERMANENT),
]


def canonical_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def norm_text(value: str | None) -> str:
    if not value:
        return ""
    value = _GENDER_TAGS.sub(" ", value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return _NON_WORD.sub(" ", value.lower()).strip()


def clean_title(title: str) -> str:
    title = _GENDER_TAGS.sub(" ", title)
    return " ".join(title.split()).strip(" -|,")


_CONTRACT_WORDS = {
    "cdi": ContractType.PERMANENT,
    "permanent": ContractType.PERMANENT,
    "cdd": ContractType.FIXED_TERM,
    "freelance": ContractType.FREELANCE,
    "independant": ContractType.FREELANCE,
    "portage salarial": ContractType.FREELANCE,
    "stage": ContractType.INTERNSHIP,
    "alternance": ContractType.INTERNSHIP,
    "internship": ContractType.INTERNSHIP,
}


def contract_from_text(text: str) -> ContractType:
    """Map a contract label written by a site (e.g. 'CDI', 'Freelance') to a ContractType."""
    return _CONTRACT_WORDS.get(norm_text(text), ContractType.UNKNOWN)


def detect_contract_in_title(title: str) -> ContractType:
    for pattern, contract in _CONTRACT_PATTERNS:
        if pattern.search(title):
            return contract
    return ContractType.UNKNOWN


def _city(location: str | None) -> str:
    first = (location or "").split(",")[0].split("(")[0]
    return norm_text(first)


def dedupe_key(job: JobPosting) -> str:
    """Same title + company + city = same job, whatever the source."""
    title = norm_text(job.title)
    if job.company:
        return f"tcl|{title}|{norm_text(job.company)}|{_city(job.location)}"
    if job.source_job_id:
        return f"id|{job.source}|{job.source_job_id}"
    if job.url:
        return f"url|{canonical_url(job.url)}"
    return f"t|{job.source}|{title}"


def normalize(job: JobPosting) -> JobPosting:
    updates: dict[str, object] = {"title": clean_title(job.title) or job.title}
    if job.company:
        updates["company"] = " ".join(job.company.split())
    if job.location:
        updates["location"] = " ".join(job.location.split())
    if job.url:
        updates["url"] = canonical_url(job.url)
    if job.contract_type is ContractType.UNKNOWN:
        detected = detect_contract_in_title(job.title)
        if detected is not ContractType.UNKNOWN:
            updates["contract_type"] = detected
            updates["raw"] = {**job.raw, "contract_evidence": "title"}
    return job.model_copy(update=updates)
