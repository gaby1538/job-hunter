import email
from email import policy
from pathlib import Path

from job_hunter.config import AppConfig
from job_hunter.sources.email_parser import identify_site, parse_alert_email


def _load(path: Path) -> email.message.Message:
    with path.open("rb") as fh:
        return email.message_from_binary_file(fh, policy=policy.default)


def test_efinancialcareers_html(config: AppConfig, emails_dir: Path) -> None:
    jobs = parse_alert_email(_load(emails_dir / "efinancialcareers_alert.eml"), config.sources.email)
    titles = [j.title for j in jobs]
    assert titles == [
        "Market Risk Quant Analyst (H/F)",
        "XVA Modeller - Freelance",
        "Stage Risque de Marché",
    ]
    first = jobs[0]
    assert first.source == "efinancialcareers"
    assert first.source_job_id == "12345678"
    assert "utm_source" not in (first.url or "")
    # Neighbouring lines are kept as raw context, not promoted to fields.
    assert first.raw["context"] == ["Banque Exemple CIB", "Paris, France"]
    assert first.company is None
    assert first.raw["email_subject"].startswith("3 new jobs")


def test_context_fields_mapping(config: AppConfig, emails_dir: Path) -> None:
    config.sources.email.sites["efinancialcareers"].context_fields = ["company", "location"]
    jobs = parse_alert_email(_load(emails_dir / "efinancialcareers_alert.eml"), config.sources.email)
    assert jobs[0].company == "Banque Exemple CIB"
    assert jobs[0].location == "Paris, France"


def test_indeed_ignores_image_links_and_dedupes(config: AppConfig, emails_dir: Path) -> None:
    jobs = parse_alert_email(_load(emails_dir / "indeed_alert.eml"), config.sources.email)
    assert len(jobs) == 3
    assert jobs[0].title == "Counterparty Risk Analyst CDI"
    assert jobs[0].source_job_id == "a1b2c3d4e5f6"
    assert "tk=" not in (jobs[0].url or "")  # tracking param removed
    assert "jk=a1b2c3d4e5f6" in (jobs[0].url or "")  # job id param kept


def test_text_only_email(config: AppConfig, emails_dir: Path) -> None:
    jobs = parse_alert_email(_load(emails_dir / "hellowork_alert_text.eml"), config.sources.email)
    assert [j.title for j in jobs] == [
        "Liquidity Risk Specialist H/F",
        "Risk Data Scientist - modélisation",
    ]
    assert jobs[0].source_job_id == "55554444"


def test_unknown_sender_is_skipped(config: AppConfig, emails_dir: Path) -> None:
    msg = _load(emails_dir / "newsletter_unknown_sender.eml")
    assert identify_site(msg, config.sources.email) is None
    assert parse_alert_email(msg, config.sources.email) == []
