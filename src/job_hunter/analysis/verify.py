"""Check Claude's quotes against the job text. Unsupported facts are discarded.

This enforces "never invent missing information" in code rather than trusting the model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from job_hunter.analysis.schema import JobAnalysis

_SPACES = re.compile(r"\s+")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("…", "...")
    return _SPACES.sub(" ", text).strip().lower()


def quote_in_text(quote: str | None, text: str) -> bool:
    if not quote or not quote.strip():
        return False
    # Tolerate an ellipsis joining two exact fragments.
    fragments = [f for f in re.split(r"\.\.\.|…", _norm(quote)) if f.strip()]
    haystack = _norm(text)
    return bool(fragments) and all(f.strip() in haystack for f in fragments)


@dataclass
class Verification:
    discarded: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.discarded


def verify(analysis: JobAnalysis, text: str) -> tuple[JobAnalysis, Verification]:
    """Return a copy where every fact without a matching quote is reset to unknown."""
    a = analysis.model_copy(deep=True)
    v = Verification()

    if a.contract != "unknown" and not quote_in_text(a.contract_quote, text):
        v.discarded.append(f"contract={a.contract}")
        a.contract, a.contract_quote = "unknown", None
    if a.remote_policy != "unknown" and not quote_in_text(a.remote_quote, text):
        v.discarded.append(f"remote_policy={a.remote_policy}")
        a.remote_policy, a.remote_quote = "unknown", None
    if a.onsite_days_per_month is not None and not quote_in_text(a.remote_quote, text):
        v.discarded.append(f"onsite_days_per_month={a.onsite_days_per_month}")
        a.onsite_days_per_month = None
    if a.required_years_min is not None and not quote_in_text(a.years_quote, text):
        v.discarded.append(f"required_years_min={a.required_years_min}")
        a.required_years_min, a.years_quote = None, None
    if a.work_nature != "unknown" and not quote_in_text(a.work_nature_quote, text):
        v.discarded.append(f"work_nature={a.work_nature}")
        a.work_nature, a.work_nature_quote = "unknown", None

    kept = []
    for req in a.requirements:
        if quote_in_text(req.quote, text):
            kept.append(req)
        else:
            v.discarded.append(f"requirement '{req.requirement}'")
    a.requirements = kept
    return a, v
