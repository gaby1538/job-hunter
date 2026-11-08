"""Email parser tests. Fixtures mirror real alert layouts (see fixtures/make_fixtures.py)."""

import base64
import email
from datetime import UTC, datetime
from email import policy
from email.message import Message
from pathlib import Path

import pytest

from job_hunter.config import AppConfig
from job_hunter.models import ContractType, JobPosting
from job_hunter.sources.email_parser import (
    identify_site,
    parse_alert_email,
    parse_relative_date,
    resolve_url,
)


def _load(path: Path) -> Message:
    with path.open("rb") as fh:
        return email.message_from_binary_file(fh, policy=policy.default)


def _parse(config: AppConfig, emails_dir: Path, name: str) -> list[JobPosting]:
    return parse_alert_email(_load(emails_dir / f"{name}.eml"), config.sources.email)


def _no_personal_data(jobs: list[JobPosting]) -> None:
    for job in jobs:
        dumped = job.model_dump_json()
        assert "me@example.com" not in dumped
        assert "TOKEN" not in (job.url or "")


def test_indeed_text_alert(config: AppConfig, emails_dir: Path) -> None:
    jobs = _parse(config, emails_dir, "indeed_alert")
    assert [j.title for j in jobs] == [
        "Analyste quantitatif (F/H) - Risques de crédit",
        "Stage - 6 mois - Analyste Investissement F/H",
    ]
    first, second = jobs
    assert first.company == "CONFEDERATION NATIONALE DU CREDIT MUTUEL"
    assert first.location == "Paris (75)"
    assert first.salary_text == "De 60 000 € à 65 000 € par an"
    assert first.salary_may_be_estimate is True  # Indeed may estimate salaries
    assert first.published_at == datetime(2026, 10, 29, 16, 45, 21, tzinfo=UTC)
    assert first.source_job_id is None  # sponsored link carries no job id
    assert second.url == "https://fr.indeed.com/viewjob?jk=fd580c1a50dfdc62"
    assert second.salary_text is None and second.salary_may_be_estimate is False
    _no_personal_data(jobs)


def test_linkedin_text_alert(config: AppConfig, emails_dir: Path) -> None:
    jobs = _parse(config, emails_dir, "linkedin_alert")
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("CONSULTANTS RISQUE DE CREDIT", "Wise Associates", "Paris"),
        ("Consultant MOA Finance | Asset Management Expert", "OMICRONE", "Paris"),
    ]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4486648624"
    _no_personal_data(jobs)


def test_linkedin_recruiter_messages_are_not_alerts(config: AppConfig, emails_dir: Path) -> None:
    assert identify_site(_load(emails_dir / "linkedin_inmail.eml"), config.sources.email) is None


def test_hellowork_html_with_tracking_redirects(config: AppConfig, emails_dir: Path) -> None:
    jobs = _parse(config, emails_dir, "hellowork_alert")
    assert [j.source_job_id for j in jobs] == ["85099350", "85286442", "85115597", "84971991"]
    first = jobs[0]
    assert first.title == "Ingénieur Intelligence Artificielle H/F"
    assert first.company == "Amiltone"
    assert first.location == "Marseille - 13"
    assert first.contract_type is ContractType.PERMANENT
    assert first.url == "https://www.hellowork.com/fr-fr/emplois/85099350.html"
    badge = jobs[1]
    assert badge.company == "Prospectis RH"  # "Super recruteur" badge skipped
    assert badge.salary_text == "60 000 - 75 000 € / an"
    assert jobs[2].contract_type is ContractType.FREELANCE
    _no_personal_data(jobs)  # the redirect embeds the address; it must not leak


def test_freework_contract_line_before_title(config: AppConfig, emails_dir: Path) -> None:
    jobs = _parse(config, emails_dir, "freework_newsletter")
    assert [j.title for j in jobs] == ["Chef de projet Power BI", "Quant Risk Analyst - XVA"]
    xva = jobs[1]
    assert xva.contract_type is ContractType.FREELANCE
    assert xva.salary_text == "650 - 800 €⁄j"
    assert xva.location == "Paris"


def test_malt_single_offer(config: AppConfig, emails_dir: Path) -> None:
    (job,) = _parse(config, emails_dir, "malt_opportunity")
    assert job.title == "Consultant Risque de Marché"
    assert job.company == "CLIENT EXEMPLE"  # from the subject line
    assert job.location == "Paris, France"
    assert job.remote_text == "Oui (2 jours/semaine)"
    assert job.salary_text == "Selon profil"
    assert job.contract_type is ContractType.FREELANCE
    assert job.raw["details"]["contract_source"] == "site default"
    assert job.raw["details"]["duration"] == "6 mois"
    assert job.description and "Expected Shortfall" in job.description
    assert job.url == "https://www.malt.fr/messages/client-project-offer/6ad09891403102c7db82a9b8"


def test_malt_marketing_email_is_ignored(config: AppConfig, emails_dir: Path) -> None:
    assert identify_site(_load(emails_dir / "malt_webinar.eml"), config.sources.email) is None


def test_synthetic_efinancialcareers(config: AppConfig, emails_dir: Path) -> None:
    jobs = _parse(config, emails_dir, "efinancialcareers_alert")
    assert len(jobs) == 3
    assert jobs[0].source_job_id == "12345678"
    assert "utm_source" not in (jobs[0].url or "")


def test_unknown_sender_is_skipped(config: AppConfig, emails_dir: Path) -> None:
    msg = _load(emails_dir / "newsletter_unknown_sender.eml")
    assert identify_site(msg, config.sources.email) is None
    assert parse_alert_email(msg, config.sources.email) == []


def test_resolve_base64_redirect(config: AppConfig) -> None:
    rule = config.sources.email.sites["hellowork"]
    target = "me@x.com\U0001faa2https://www.hellowork.com/fr-fr/emplois/1.html?utm_source=a"
    payload = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    url = resolve_url(f"https://emails.hellowork.com/clic/u/1/h/{payload}", rule)
    assert url == "https://www.hellowork.com/fr-fr/emplois/1.html?utm_source=a"


@pytest.mark.parametrize(
    ("text", "days"),
    [("il y a 10 jours", 10), ("il y a 1 jour", 1), ("3 days ago", 3), ("Aujourd'hui", 0),
     ("il y a 30+ jours", 30), ("il y a 2 semaines", 14)],
)
def test_relative_dates(text: str, days: int) -> None:
    ref = datetime(2026, 11, 8, 12, tzinfo=UTC)
    result = parse_relative_date(text, ref)
    assert result is not None and (ref - result).days == days


def test_relative_date_unknown_stays_none() -> None:
    assert parse_relative_date("récemment", datetime(2026, 11, 8, tzinfo=UTC)) is None
    assert parse_relative_date("il y a 3 jours", None) is None
