"""Deterministic score out of 100 from the (verified) analysis and profile.yaml weights.

Claude extracts and judges facts; this module turns them into points with fixed,
documented rules, so the same facts always give the same score.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from job_hunter.analysis.schema import JobAnalysis
from job_hunter.models import ContractType
from job_hunter.profile import Profile

VerdictT = Literal["apply", "consider", "skip"]

ROLE = {"target": 1.0, "adjacent": 0.5, "off_target": 0.0}
WORK = {"modelling_heavy": 1.0, "mixed": 0.6, "coordination_heavy": 0.0, "unknown": 0.4}
COVERAGE = {"strong": 1.0, "partial": 0.5, "missing": 0.0}
IMPORTANCE = {"must_have": 2.0, "nice_to_have": 1.0}
UNKNOWN = 0.4  # share of points given when a fact is not stated


class ScoreResult(BaseModel):
    total: float
    parts: dict[str, float]        # points earned per criterion
    max_parts: dict[str, float]    # points available per criterion
    penalties: dict[str, float]
    verdict: VerdictT
    reasons: list[str]
    missing_must_haves: list[str]
    confidence: Literal["high", "medium", "low"]
    needs_full_text: bool


def _remote_share(a: JobAnalysis, max_days: float) -> tuple[float, str]:
    if a.onsite_days_per_month is not None:
        ok = a.onsite_days_per_month <= max_days
        return (1.0 if ok else 0.0), f"{a.onsite_days_per_month:g} onsite days/month"
    return {
        "full_remote": (1.0, "full remote"),
        "mostly_remote": (0.7, "mostly remote, onsite days not stated"),
        "hybrid": (0.2, "hybrid, onsite days not stated"),
        "onsite": (0.0, "onsite"),
        "unknown": (UNKNOWN, "remote policy not stated"),
    }[a.remote_policy]


def _requirements_share(a: JobAnalysis) -> float:
    if not a.requirements:
        return UNKNOWN
    total = sum(IMPORTANCE[r.importance] for r in a.requirements)
    got = sum(IMPORTANCE[r.importance] * COVERAGE[r.coverage] for r in a.requirements)
    return got / total


def score(
    a: JobAnalysis, profile: Profile, *, has_full_text: bool,
    source_contract: ContractType = ContractType.UNKNOWN,
) -> ScoreResult:
    w = profile.scoring.weights
    reasons: list[str] = []

    contract = a.contract if a.contract != "unknown" else source_contract.value
    if contract == "unknown":
        contract_share = 0.5
    else:
        contract_share = 1.0 if ContractType(contract) in profile.contracts else 0.0

    years = a.required_years_min
    if years is None:
        seniority_share = 0.5
    elif years <= profile.years_of_experience + 1:
        seniority_share = 1.0
    else:
        seniority_share = 0.3
        reasons.append(f"asks {years}+ years")

    remote_share, remote_label = _remote_share(a, profile.location.max_onsite_days_per_month)
    shares = {
        "role_fit": ROLE[a.role_match],
        "requirements": _requirements_share(a),
        "modelling_depth": WORK[a.work_nature],
        "remote": remote_share,
        "contract": contract_share,
        "seniority": seniority_share,
    }
    max_parts = {k: float(getattr(w, k)) for k in shares}
    parts = {k: round(shares[k] * max_parts[k], 1) for k in shares}

    penalties: dict[str, float] = {}
    if a.work_nature == "coordination_heavy":
        penalties["coordination_heavy"] = profile.scoring.coordination_heavy_penalty
    total = max(0.0, min(100.0, sum(parts.values()) - sum(penalties.values())))
    total = round(total, 1)

    missing = [r.requirement for r in a.requirements
               if r.importance == "must_have" and r.coverage == "missing"]
    v = profile.scoring.verdict
    verdict: VerdictT
    if contract == "internship":
        verdict = "skip"
        reasons.append("internship")
    elif (total >= v.apply_from and len(missing) <= v.max_missing_must_haves_to_apply
          and not (v.onsite_limit_is_dealbreaker and remote_share == 0.0
                   and a.onsite_days_per_month is not None)):
        verdict = "apply"
    elif total >= v.consider_from:
        verdict = "consider"
    else:
        verdict = "skip"

    reasons.append(f"role: {a.role_match.replace('_', ' ')}")
    reasons.append(f"work: {a.work_nature.replace('_', ' ')}")
    reasons.append(remote_label)
    if remote_share == 0.0:
        reasons.append("too many onsite days")
        if verdict == "consider" and total >= v.apply_from:
            reasons.append("would be 'apply' if remote can be negotiated")
    if missing:
        reasons.append(f"{len(missing)} must-have(s) missing")

    stated = sum(x is not None and x != "unknown" for x in (
        a.contract if a.contract != "unknown" else (None if contract == "unknown" else contract),
        a.remote_policy, a.work_nature, a.required_years_min,
    )) + (1 if a.requirements else 0)
    if has_full_text and stated >= 4:
        confidence: Literal["high", "medium", "low"] = "high"
    elif has_full_text or stated >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    return ScoreResult(
        total=total, parts=parts, max_parts=max_parts, penalties=penalties, verdict=verdict,
        reasons=reasons, missing_must_haves=missing, confidence=confidence,
        needs_full_text=not has_full_text and verdict != "skip",
    )
