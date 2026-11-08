from datetime import UTC, datetime, timedelta

from job_hunter.config import SearchCriteria
from job_hunter.models import ContractType, JobPosting
from job_hunter.processing.dedupe import dedupe
from job_hunter.processing.filters import apply_filters
from job_hunter.processing.normalize import canonical_url, norm_text, normalize

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


def job(**kw: object) -> JobPosting:
    base: dict[str, object] = {"source": "test", "channel": "api", "title": "Market Risk Analyst"}
    return JobPosting(**{**base, **kw})  # type: ignore[arg-type]


def test_canonical_url_strips_tracking() -> None:
    url = "HTTPS://Example.com/job/1/?utm_source=x&id=5&trk=abc#top"
    assert canonical_url(url) == "https://example.com/job/1?id=5"


def test_norm_text_removes_gender_tags_and_accents() -> None:
    assert norm_text("Analyste Risque de Marché (H/F)") == "analyste risque de marche"


def test_contract_detected_only_from_explicit_title_words() -> None:
    assert normalize(job(title="XVA Quant - Freelance")).contract_type is ContractType.FREELANCE
    assert normalize(job(title="Analyste CDI")).contract_type is ContractType.PERMANENT
    assert normalize(job(title="Market Risk Analyst")).contract_type is ContractType.UNKNOWN
    # A contract stated by the source is never overwritten.
    stated = job(title="Analyste CDI", contract_type=ContractType.FREELANCE)
    assert normalize(stated).contract_type is ContractType.FREELANCE


def test_naive_datetimes_become_utc() -> None:
    assert job(published_at=datetime(2026, 1, 1)).published_at.tzinfo is UTC  # type: ignore[union-attr]  # noqa: DTZ001


def test_filters_give_reasons() -> None:
    criteria = SearchCriteria(
        queries=["risk"],
        include_keywords=["risque", "risk"],
        exclude_title_keywords=["stage", "pmo"],
        contract_types=[ContractType.PERMANENT, ContractType.UNKNOWN],
        max_age_days=14,
    )
    jobs = [
        job(title="Market Risk Analyst"),
        job(title="Stage Risque de Marché"),
        job(title="PMO Risques"),
        job(title="Comptable"),
        job(title="Risk Quant", contract_type=ContractType.FIXED_TERM),
        job(title="Risk Quant old", published_at=NOW - timedelta(days=30)),
        job(title="Analyste", description="Gestion des risques de liquidité"),
    ]
    kept, rejected = apply_filters(jobs, criteria, now=NOW)
    assert [j.title for j in kept] == ["Market Risk Analyst", "Analyste"]
    reasons = {r.job.title: r.reason for r in rejected}
    assert "excluded keyword 'stage'" in reasons["Stage Risque de Marché"]
    assert "excluded keyword 'pmo'" in reasons["PMO Risques"]
    assert reasons["Comptable"] == "no include keyword found"
    assert "not wanted" in reasons["Risk Quant"]
    assert "days ago" in reasons["Risk Quant old"]


def test_dedupe_across_sources_fills_gaps_without_overwriting() -> None:
    a = job(source="adzuna", title="Market Risk Analyst (H/F)", company="Banque X",
            location="Paris", url="https://a.example/1", salary_text="70k")
    b = job(source="indeed", title="Market risk analyst", company="BANQUE X",
            location="Paris (75)", url="https://b.example/9", description="VaR, ES")
    c = job(source="indeed", title="Other job", company="Banque X", url="https://b.example/10")
    result = dedupe([a, b, c])
    assert len(result) == 2
    merged = result[0]
    assert merged.salary_text == "70k" and merged.description == "VaR, ES"
    assert merged.also_seen_at == ["https://b.example/9"]
    assert merged.raw["merged_sources"] == ["adzuna", "indeed"]


def test_dedupe_same_url_without_company() -> None:
    a = job(source="linkedin", url="https://x.example/jobs/view/1?trk=a")
    b = job(source="linkedin", title="Market Risk Analyst - Paris", url="https://x.example/jobs/view/1")
    assert len(dedupe([a, b])) == 1
